# Chaos Button

A small chaos-engineering app: register "resources" (MAAS machines,
Kubernetes pods, or Kubernetes Deployments), then press the **Big Red
Button** to destroy one of them, chosen at random.

- **MAAS machine** resource → the button calls the MAAS API to power off
  the machine (`system_id`).
- **Kubernetes pod** resource → the button connects to the target
  cluster using the stored kubeconfig and deletes that specific pod. If
  nothing recreates it, this is the last time this resource can usefully
  be killed.
- **Kubernetes Deployment** resource → the button connects to the target
  cluster, finds the Deployment's current replica pods, and deletes one
  of them at random — never the Deployment itself. Its controller
  reschedules the replica automatically, so the resource stays valid and
  can be killed again and again.

Every kill (what was picked, and the outcome) is logged to stdout on the
backend pod, so `kubectl logs` on that pod is your audit trail.

## Architecture

```
                 external traffic (LoadBalancer)
                            │
                            ▼
┌───────────────────────────────────────┐        /api/*        ┌─────────────┐
│  frontend (nginx)                       │ ───────────────────▶ │  backend     │
│  - serves the static UI                 │   (in-cluster only)   │ (FastAPI)    │
│  - reverse-proxies "/api/*" to backend  │                      │              │
└───────────────────────────────────────┘                      └──────┬───────┘
                                                                        │
                                              reads/writes              │  Secrets in its own
                                              resource catalog          ▼  namespace (RBAC-scoped)
                                                                 Kubernetes Secrets
                                                                        │
                                 ┌──────────────────────────┴──────────────────────────┐
                                 │                                                        │
                          MAAS resource secret                        k8s_pod / k8s_deployment resource
                    (url, oauth_key, system_id)                      secret (kubeconfig, namespace, and
                                 │                                   pod_name or deployment_name)
                                 ▼                                                        │
                         MAAS API: power_off                                             ▼
                         (via requests + OAuth1)                      Target cluster API: delete a pod
                                                                     (that exact pod, or a random replica
                                                                      of the Deployment) using the stored
                                                                                kubeconfig
```

Both the frontend and backend are separate Deployments/Services so they
scale and update independently. The frontend's nginx is the *only*
externally-exposed piece (via a `LoadBalancer` Service) — it serves the
static UI and reverse-proxies `/api/*` to the backend's `ClusterIP`
Service from inside the cluster, so the frontend's relative
`fetch('/api/...')` calls just work, and the backend is never directly
reachable from outside the cluster.

Resources are stored as **Kubernetes Secrets** (one per resource) in the
release namespace, labeled so the backend can list them with a label
selector. The backend's ServiceAccount is only ever granted RBAC to
manage Secrets in its own namespace — it does **not** need any
permissions in the target MAAS instance or Kubernetes cluster beyond what
is embedded in the resource's own credentials.

> ⚠️ **Security note:** Secrets store sensitive material (MAAS OAuth
> tokens, raw kubeconfigs with embedded credentials). Treat the release
> namespace as sensitive: enable [encryption at rest for
> Secrets](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/),
> restrict who can `get`/`list` Secrets in that namespace, and consider a
> dedicated namespace just for this app.

## Repository layout

```
chaos-button/
├── backend/            FastAPI app (resource CRUD + kill logic)
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/            Static HTML/JS/CSS UI with the big red button
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── Dockerfile
└── helm/chaos-button/   Helm chart to deploy everything
```

## API

| Method | Path                     | Description                                             |
|--------|--------------------------|----------------------------------------------------------|
| GET    | `/api/resources`         | List registered resources (secrets are never shown raw) |
| POST   | `/api/resources`         | Create a resource (see payloads below)                  |
| DELETE | `/api/resources/{name}`  | Delete a resource                                        |
| POST   | `/api/kill`              | Pick a random resource and kill it                       |
| GET    | `/api/healthz`           | Liveness/readiness probe                                 |

### Create a MAAS machine resource

```bash
curl -X POST http://<host>/api/resources \
  -H 'Content-Type: application/json' \
  -d '{
        "type": "maas",
        "name": "rack-server-1",
        "url": "http://maas.example.com:5240/MAAS",
        "oauth_key": "consumer_key:token_key:token_secret",
        "system_id": "abc123"
      }'
```

### Create a Kubernetes pod resource

```bash
curl -X POST http://<host>/api/resources \
  -H 'Content-Type: application/json' \
  -d '{
        "type": "k8s_pod",
        "name": "payments-worker",
        "namespace": "payments",
        "pod_name": "payments-worker-7f8d9-abcde",
        "kubeconfig": "apiVersion: v1\nkind: Config\n..."
      }'
```

### Create a Kubernetes Deployment resource

Killing this resource deletes one random replica pod of the Deployment —
not the Deployment itself — so the Deployment's controller reschedules
the replica and the resource is still there to kill again next time.

```bash
curl -X POST http://<host>/api/resources \
  -H 'Content-Type: application/json' \
  -d '{
        "type": "k8s_deployment",
        "name": "payments-api",
        "namespace": "payments",
        "deployment_name": "payments-api",
        "kubeconfig": "apiVersion: v1\nkind: Config\n..."
      }'
```

### Press the button

```bash
curl -X POST http://<host>/api/kill
# {"killed":"payments-worker","type":"k8s_pod","message":"Pod 'payments-worker-7f8d9-abcde' in namespace 'payments' deleted"}

# ...or, for a k8s_deployment resource:
# {"killed":"payments-api","type":"k8s_deployment","message":"Pod 'payments-api-6f9998dd98-bjxnj' (one of 3 replicas of Deployment 'payments-api' in namespace 'payments') deleted; the Deployment will reschedule it"}
```

## Building the images

> **Note on the base image:** `ubuntu:26.04` ("Resolute") is a future
> Ubuntu release and may not be published to Docker Hub yet. Both
> Dockerfiles use it as requested; if the pull fails, just change
> `FROM ubuntu:26.04` to `FROM ubuntu:24.04` in each Dockerfile — nothing
> else needs to change.

```bash
# Backend
docker build -t <registry>/chaos-button-backend:0.1.0 backend/
docker push <registry>/chaos-button-backend:0.1.0

# Frontend
docker build -t <registry>/chaos-button-frontend:0.1.0 frontend/
docker push <registry>/chaos-button-frontend:0.1.0
```

## Deploying with Helm

```bash
helm install chaos-button helm/chaos-button \
  --namespace chaos-button --create-namespace \
  --set image.registry=<registry>/ \
  --set image.backend.tag=0.1.0 \
  --set image.frontend.tag=0.1.0
```

The frontend Service defaults to `type: LoadBalancer`. Once your cloud
provider assigns it an external IP (see the post-install `NOTES.txt`
printed by Helm):

```bash
kubectl -n chaos-button get svc chaos-button-frontend -w
```

...browse to `http://<EXTERNAL-IP>/`. On clusters without a cloud
load-balancer (plain kind/minikube without MetalLB), `EXTERNAL-IP` stays
`<pending>` — fall back to port-forwarding instead:

```bash
kubectl -n chaos-button port-forward svc/chaos-button-frontend 8080:80
```

(No need to separately port-forward the backend — nginx inside the
frontend pod proxies `/api/*` to it internally.)

### Bootstrapping resources via Helm values

You can pre-populate resources at install time via `values.yaml` (they're
turned into the same kind of Secrets the API creates, and can still be
managed/deleted later through the UI/API):

```yaml
resources:
  - type: maas
    name: rack-server-1
    url: http://maas.example.com:5240/MAAS
    oauthKey: "consumerKey:tokenKey:tokenSecret"
    systemId: abc123
  - type: k8s_pod
    name: payments-worker
    namespace: payments
    podName: payments-worker-7f8d9-abcde
    kubeconfig: |
      apiVersion: v1
      kind: Config
      ...
  - type: k8s_deployment
    name: payments-api
    namespace: payments
    deploymentName: payments-api
    kubeconfig: |
      apiVersion: v1
      kind: Config
      ...
```

### Watching the kill log

```bash
kubectl -n chaos-button logs -f deploy/chaos-button-backend
```

Example log lines:

```
2024-01-01 12:00:00 INFO chaos-button: Registered new resource 'payments-worker' (type=k8s_pod, secret=chaos-res-payments-worker)
2024-01-01 12:05:00 INFO chaos-button: Big red button pressed -> randomly selected resource 'payments-worker' (type=k8s_pod)
2024-01-01 12:05:00 INFO chaos-button: KILLED resource 'payments-worker' (type=k8s_pod): Pod 'payments-worker-7f8d9-abcde' in namespace 'payments' deleted
```

## Helm chart values reference

| Key                        | Default                     | Description                                      |
|-----------------------------|------------------------------|---------------------------------------------------|
| `image.registry`             | `""`                          | Prefix prepended to image repositories             |
| `image.backend.repository`   | `chaos-button-backend`       | Backend image repo                                 |
| `image.backend.tag`          | `0.1.0`                       | Backend image tag                                  |
| `image.frontend.repository`  | `chaos-button-frontend`      | Frontend image repo                                |
| `image.frontend.tag`         | `0.1.0`                       | Frontend image tag                                 |
| `backend.replicaCount`       | `1`                            | Backend replica count                              |
| `backend.logLevel`           | `INFO`                        | Python logging level                               |
| `frontend.replicaCount`      | `1`                            | Frontend replica count                             |
| `service.backend.type`       | `ClusterIP`                    | Backend Service type (internal only, don't change) |
| `service.backend.port`       | `8080`                         | Backend Service port                               |
| `service.frontend.type`      | `LoadBalancer`                 | Frontend Service type (the external entry point)   |
| `service.frontend.port`      | `80`                           | Frontend Service port                              |
| `serviceAccount.create`      | `true`                         | Create a dedicated ServiceAccount for the backend  |
| `rbac.create`                | `true`                         | Create the Role/RoleBinding for managing Secrets   |
| `resources`                  | `[]`                           | Resources to bootstrap as Secrets at install time  |

## Local development (outside the cluster)

The backend falls back to your local kubeconfig (`~/.kube/config`) when
it's not running inside a pod, so you can run it locally against a dev
cluster:

```bash
cd backend
pip install -r requirements.txt
export POD_NAMESPACE=default
uvicorn main:app --reload --port 8080
```

Serve the frontend separately — e.g. build and run its Docker image
pointed at your locally-running backend:

```bash
cd frontend
docker build -t chaos-button-frontend:dev .
docker run --rm -p 8081:8080 \
  -e BACKEND_HOST=host.docker.internal -e BACKEND_PORT=8080 \
  chaos-button-frontend:dev
```

Then browse to `http://localhost:8081` — nginx proxies its `/api/*`
requests straight through to the backend running on your host. (Or just
`curl` the backend directly on `:8080` while developing.)
