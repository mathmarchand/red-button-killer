# Chaos Button

A small chaos-engineering app: register "resources" (MAAS machines or
Kubernetes pods), then press the **Big Red Button** to destroy one of
them, chosen at random.

- **MAAS machine** resource → the button calls the MAAS API to power off
  the machine (`system_id`).
- **Kubernetes pod** resource → the button connects to the target
  cluster using the stored kubeconfig and deletes the pod.

Every kill (what was picked, and the outcome) is logged to stdout on the
backend pod, so `kubectl logs` on that pod is your audit trail.

## Architecture

```
┌─────────────┐        /api/*        ┌─────────────┐
│  frontend    │ ───────────────────▶ │  backend     │
│ (static UI,  │                      │ (FastAPI)    │
│  big red btn)│                      │              │
└─────────────┘                      └──────┬───────┘
                                             │
                     reads/writes            │  Secrets in its own
                     resource catalog        ▼  namespace (RBAC-scoped)
                                      Kubernetes Secrets
                                             │
                      ┌──────────────────────┴───────────────────────┐
                      │                                              │
               MAAS resource secret                         k8s_pod resource secret
         (url, oauth_key, system_id)              (kubeconfig, namespace, pod_name)
                      │                                              │
                      ▼                                              ▼
              MAAS API: power_off                     Target cluster API: delete pod
              (via requests + OAuth1)                  (via kubeconfig in the secret)
```

Both the frontend and backend are separate Deployments/Services so they
scale and update independently. An optional Ingress exposes the frontend
at `/` and the backend at `/api` on the same host (so the frontend's
relative `fetch('/api/...')` calls just work).

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

### Press the button

```bash
curl -X POST http://<host>/api/kill
# {"killed":"payments-worker","type":"k8s_pod","message":"Pod 'payments-worker-7f8d9-abcde' in namespace 'payments' deleted"}
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
  --set image.frontend.tag=0.1.0 \
  --set ingress.enabled=true \
  --set ingress.host=chaos.example.com
```

Without an Ingress, use port-forwarding (see the post-install `NOTES.txt`
printed by Helm):

```bash
kubectl -n chaos-button port-forward svc/chaos-button-frontend 8080:80
kubectl -n chaos-button port-forward svc/chaos-button-backend 8081:8080
```

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
| `service.type`               | `ClusterIP`                   | Service type for both frontend/backend             |
| `ingress.enabled`            | `false`                        | Create an Ingress covering `/` and `/api`          |
| `ingress.host`               | `chaos-button.local`           | Ingress hostname                                   |
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

Serve the frontend separately and point your browser/Ingress/proxy such
that `/api` reaches the backend (e.g. `cd frontend && python3 -m http.server 8081`,
plus any simple reverse proxy, or just use `curl` against the API
directly while developing).
