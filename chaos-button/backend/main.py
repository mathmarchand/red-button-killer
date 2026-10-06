"""
Chaos Button backend API.

Provides:
  * CRUD endpoints for "resources" (MAAS machines or Kubernetes pods),
    persisted as Kubernetes Secrets in the release namespace.
  * A /api/kill endpoint ("the big red button") that picks a random
    registered resource and destroys it:
      - MAAS machine  -> calls the MAAS API to power off the machine.
      - Kubernetes pod -> connects to the target cluster using the stored
        kubeconfig and deletes the pod.

Everything of interest is logged to stdout so it shows up via
`kubectl logs` on the backend pod.
"""

import base64
import logging
import os
import random
import re
import sys
import tempfile
from typing import List, Literal, Union

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, ValidationError
from requests_oauthlib import OAuth1

# --------------------------------------------------------------------------
# Logging - stdout only, so `kubectl logs <backend-pod>` shows every kill.
# --------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s chaos-button: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("chaos-button")

# --------------------------------------------------------------------------
# Constants / helpers for storing resources as Kubernetes Secrets
# --------------------------------------------------------------------------
APP_LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
APP_LABEL_VALUE = "chaos-button"
LABEL_IS_RESOURCE = "chaos-button/resource"
LABEL_TYPE = "chaos-button/type"
ANNOTATION_NAME = "chaos-button/display-name"

SECRET_LABEL_SELECTOR = f"{APP_LABEL_MANAGED_BY}={APP_LABEL_VALUE},{LABEL_IS_RESOURCE}=true"


def _load_k8s_api() -> k8s_client.CoreV1Api:
    """Load the Pod's own ServiceAccount to manage Secrets in its namespace."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        # Fallback for local dev / testing outside the cluster.
        k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


def _current_namespace() -> str:
    ns_file = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    if os.path.exists(ns_file):
        with open(ns_file) as f:
            return f.read().strip()
    return os.environ.get("POD_NAMESPACE", "default")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise HTTPException(status_code=400, detail="name must contain at least one alphanumeric character")
    return slug[:40]


def _secret_name(name: str) -> str:
    return f"chaos-res-{_slugify(name)}"


NAMESPACE = _current_namespace()

# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class MaasResource(BaseModel):
    type: Literal["maas"] = "maas"
    name: str
    url: str
    oauth_key: str
    system_id: str


class K8sPodResource(BaseModel):
    type: Literal["k8s_pod"] = "k8s_pod"
    name: str
    kubeconfig: str
    namespace: str
    pod_name: str


ResourceCreate = Union[MaasResource, K8sPodResource]


class ResourceSummary(BaseModel):
    name: str
    type: str
    secret_name: str
    details: dict


class KillResult(BaseModel):
    killed: str
    type: str
    message: str


# --------------------------------------------------------------------------
# Storage layer (Kubernetes Secrets)
# --------------------------------------------------------------------------
def _decode_secret_data(secret) -> dict:
    return {k: base64.b64decode(v).decode() for k, v in (secret.data or {}).items()}


def _secret_to_summary(secret) -> ResourceSummary:
    data = _decode_secret_data(secret)
    rtype = secret.metadata.labels.get(LABEL_TYPE, "unknown")
    display_name = (secret.metadata.annotations or {}).get(ANNOTATION_NAME, secret.metadata.name)

    if rtype == "maas":
        details = {
            "url": data.get("url"),
            "system_id": data.get("system_id"),
            "oauth_key": "***redacted***",
        }
    elif rtype == "k8s_pod":
        details = {
            "namespace": data.get("namespace"),
            "pod_name": data.get("pod_name"),
            "kubeconfig": "***redacted***",
        }
    else:
        details = {}

    return ResourceSummary(
        name=display_name,
        type=rtype,
        secret_name=secret.metadata.name,
        details=details,
    )


def list_resources() -> List[ResourceSummary]:
    api = _load_k8s_api()
    secrets = api.list_namespaced_secret(NAMESPACE, label_selector=SECRET_LABEL_SELECTOR)
    return [_secret_to_summary(s) for s in secrets.items]


def _get_resource_secret(secret_name: str):
    api = _load_k8s_api()
    try:
        return api.read_namespaced_secret(secret_name, NAMESPACE)
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def create_resource(payload: dict) -> ResourceSummary:
    api = _load_k8s_api()
    name = payload["name"]
    secret_name = _secret_name(name)

    if _get_resource_secret(secret_name):
        raise HTTPException(status_code=409, detail=f"resource '{name}' already exists")

    rtype = payload["type"]
    string_data = {k: str(v) for k, v in payload.items()}

    secret = k8s_client.V1Secret(
        metadata=k8s_client.V1ObjectMeta(
            name=secret_name,
            namespace=NAMESPACE,
            labels={
                APP_LABEL_MANAGED_BY: APP_LABEL_VALUE,
                LABEL_IS_RESOURCE: "true",
                LABEL_TYPE: rtype,
            },
            annotations={ANNOTATION_NAME: name},
        ),
        string_data=string_data,
        type="Opaque",
    )
    created = api.create_namespaced_secret(NAMESPACE, secret)
    log.info("Registered new resource '%s' (type=%s, secret=%s)", name, rtype, secret_name)
    return _secret_to_summary(created)


def delete_resource(name: str) -> None:
    api = _load_k8s_api()
    secret_name = _secret_name(name)
    try:
        api.delete_namespaced_secret(secret_name, NAMESPACE)
        log.info("Removed resource '%s' (secret=%s)", name, secret_name)
    except ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail=f"resource '{name}' not found")
        raise


# --------------------------------------------------------------------------
# "The Big Red Button" - kill logic
# --------------------------------------------------------------------------
def _kill_maas(data: dict) -> str:
    url = data["url"].rstrip("/")
    system_id = data["system_id"]
    oauth_key = data["oauth_key"]

    try:
        consumer_key, token_key, token_secret = oauth_key.split(":")
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail="oauth_key must be in 'consumer_key:token_key:token_secret' format",
        )

    auth = OAuth1(
        consumer_key,
        resource_owner_key=token_key,
        resource_owner_secret=token_secret,
        signature_method="PLAINTEXT",
    )
    endpoint = f"{url}/api/2.0/machines/{system_id}/?op=power_off"
    resp = requests.post(endpoint, auth=auth, timeout=15)

    if not resp.ok:
        log.error("MAAS power_off failed for system_id=%s: %s %s", system_id, resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail=f"MAAS API error: {resp.status_code} {resp.text}")

    return f"MAAS machine (system_id={system_id}) powered off"


def _kill_k8s_pod(data: dict) -> str:
    kubeconfig_yaml = data["kubeconfig"]
    namespace = data["namespace"]
    pod_name = data["pod_name"]

    fd, kubeconfig_path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(kubeconfig_yaml)

        target_config = k8s_client.Configuration()
        k8s_config.load_kube_config(config_file=kubeconfig_path, client_configuration=target_config)
        target_api = k8s_client.CoreV1Api(k8s_client.ApiClient(target_config))
        target_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as e:
        log.error("Failed to delete pod %s/%s: %s", namespace, pod_name, e)
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {e.reason}")
    finally:
        os.unlink(kubeconfig_path)

    return f"Pod '{pod_name}' in namespace '{namespace}' deleted"


def press_the_button() -> KillResult:
    api = _load_k8s_api()
    secrets = api.list_namespaced_secret(NAMESPACE, label_selector=SECRET_LABEL_SELECTOR).items

    if not secrets:
        log.warning("Big red button pressed but no resources are registered")
        raise HTTPException(status_code=404, detail="no resources registered to kill")

    secret = random.choice(secrets)
    data = _decode_secret_data(secret)
    rtype = secret.metadata.labels.get(LABEL_TYPE)
    display_name = (secret.metadata.annotations or {}).get(ANNOTATION_NAME, secret.metadata.name)

    log.info("Big red button pressed -> randomly selected resource '%s' (type=%s)", display_name, rtype)

    if rtype == "maas":
        message = _kill_maas(data)
    elif rtype == "k8s_pod":
        message = _kill_k8s_pod(data)
    else:
        raise HTTPException(status_code=500, detail=f"unknown resource type '{rtype}'")

    log.info("KILLED resource '%s' (type=%s): %s", display_name, rtype, message)
    return KillResult(killed=display_name, type=rtype, message=message)


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------
app = FastAPI(title="Chaos Button API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Make sure anything unexpected is logged and returns clean JSON
    instead of leaking a raw stack trace to the frontend."""
    log.exception("Unhandled error while processing %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"internal error: {exc}"})


@app.get("/api/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/api/resources", response_model=List[ResourceSummary])
def api_list_resources():
    return list_resources()


@app.post("/api/resources", response_model=ResourceSummary, status_code=201)
def api_create_resource(payload: dict):
    rtype = payload.get("type")
    try:
        if rtype == "maas":
            model: ResourceCreate = MaasResource(**payload)
        elif rtype == "k8s_pod":
            model = K8sPodResource(**payload)
        else:
            raise HTTPException(status_code=400, detail="type must be 'maas' or 'k8s_pod'")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    return create_resource(model.model_dump())


@app.delete("/api/resources/{name}", status_code=204)
def api_delete_resource(name: str):
    delete_resource(name)
    return None


@app.post("/api/kill", response_model=KillResult)
def api_kill():
    return press_the_button()
