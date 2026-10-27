# 🚀 End-to-End DevOps + AIOps Platform

A self-healing microservices platform on Kubernetes.  
**Flow:** `Developer → GitHub → GitHub Actions (build & push image) → Argo CD (GitOps) → Kubernetes cluster (Frontend / API / DB)`  
**Observability:** Prometheus + Grafana + Loki + Alertmanager  
**AIOps:** Alerts + logs streamed to an LLM → incident summary + likely root cause → automated response (restart pod / scale up deployment) → human-in-the-loop rollback for risky changes.

> Built as a reference implementation of the "End-to-End DevOps + AIOps Platform" project. Adapted to be fully runnable locally with minikube (and portable to AWS EKS via the Terraform folder). No portfolio attribution — this is your project.

---

## Architecture

```
Developer
   │ push code
   ▼
GitHub ──────────┐
   │             │ watch
   ▼             ▼
GitHub Actions  Argo CD ─────┬──► Frontend
   │  (build & push image)    ├──► API
   ▼                          └──► Database
GHCR (images)
   │
   ▼
Kubernetes Cluster
   │
   ├── Prometheus (metrics + alert rules)
   ├── Grafana (dashboards)
   ├── Loki (logs)
   └── Alertmanager ─────┐
                         │ webhook
                         ▼
                    AIOps Assistant (LLM agent)
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   (automatic)   (automatic)   (human in the loop)
   restart pod   scale up     rollback deploy
   (OOMKilled)   (memory)     (on-call approve/reject)
```

---

## Prerequisites

- Docker
- `kubectl`
- minikube (local) **or** access to a Kubernetes cluster (EKS via `terraform/`)
- Python 3.10+ (for the AIOps assistant)
- A GitHub repo + a GitHub Personal Access Token (packages: `write`, contents: `write`)
- An LLM endpoint (Ollama, LM Studio, OpenAI-compatible, or AWS Bedrock). A **mock mode** is included so the AIOps flow runs end-to-end even without an LLM key.

---

## Quick Start (local with minikube)

```bash
# 1. Start minikube (already running or start it)
minikube start --driver=docker --cpus=4 --memory=8192
eval $(minikube docker-env)

# 2. Enable ingress (optional, for local hostnames)
minikube addons enable ingress

# 3. Export your GitHub user & token
export GITHUB_USER=<your-github-username>
export GITHUB_TOKEN=<your-pat-with-package-and-content-write>

# 4. Deploy the platform (apps + monitoring + argocd + aiops)
./scripts/deploy-local.sh

# 5. Open Argo CD
minikube service argocd-server -n argocd
# initial admin password:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d

# 6. Trigger the full flow: push a code change → GitHub Actions builds & pushes
#    → Argo CD syncs → app is live on Kubernetes
```

### Simulate an incident (demo the AIOps loop)

```bash
# Hit the /stress endpoint to pressure the API pod (may OOMKilled)
curl http://<minikube-ip>:<api-nodeport>/stress

# Watch the AIOps assistant logs
kubectl logs -n aiops deploy/aiops-assistant -f

# Grafana dashboards
minikube service grafana -n monitoring
```

---

## Repository Layout

```
aiops-project/
├── README.md
├── .github/workflows/ci-cd.yml          # CI: build & push images; GitOps commit
├── argocd/                              # Argo CD Application (GitOps target)
│   └── app.yaml
├── apps/
│   ├── frontend/                        # static frontend + tiny server
│   │   ├── Dockerfile, server.py, index.html, requirements.txt
│   │   └── k8s/{deployment.yaml,service.yaml}
│   ├── api/                             # FastAPI microservice
│   │   ├── Dockerfile, app.py, requirements.txt
│   │   └── k8s/{deployment.yaml,service.yaml}
│   └── database/
│       └── k8s/{statefulset.yaml,service.yaml}
├── monitoring/
│   ├── prometheus/{prometheus.yml,rules/alert-rules.yml,k8s/*.yaml}
│   ├── loki/k8s/{deployment.yaml,service.yaml}
│   ├── grafana/k8s/{deployment.yaml,service.yaml,dashboards/*.json}
│   └── alertmanager/{alertmanager.yml,k8s/*.yaml}
├── aiops/                               # LLM-driven incident response agent
│   ├── assistant.py, automation.py, requirements.txt, Dockerfile
│   └── k8s/{deployment.yaml,service.yaml}
├── scripts/
│   └── deploy-local.sh                  # one-shot local deploy
├── terraform/                           # optional AWS EKS provisioning
│   └── main.tf
└── docs/
    └── ARCHITECTURE.md
```

---

## How the AIOps loop works

1. **Detect** — Prometheus fires an alert (e.g. `PodOOMKilled`, `HighMemoryUsage`, `DeploymentDown`).
2. **Notify** — Alertmanager sends a webhook to the AIOps assistant.
3. **Enrich** — The assistant pulls recent logs from **Loki** for the offending pod and queries Kubernetes for pod status/events.
4. **Reason** — An LLM produces:
   - **Incident summary**
   - **Likely root cause** (e.g. "Memory limit set too low → OOMKilled")
   - **Suggested action**
5. **Act**:
   - **Known safe pattern (e.g. OOMKilled memory too low)** → automatically **restart the pod**; if memory pressure persists → **scale up the deployment**.
   - **Uncertain / risky** → creates a **GitHub issue** for the on-call engineer with **Approve / Reject** to roll back the deployment.
6. **Loop** — Human reviews; on-call is paged via Alertmanager if needed.

---

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|----------|---------|
| `GITHUB_USER` / `GITHUB_TOKEN` | GitHub auth for CI/CD & issue creation |
| `LLM_PROVIDER` | `ollama` \| `lmstudio` \| `openai` \| `bedrock` \| `mock` |
| `LLM_BASE_URL` | e.g. `http://localhost:11434` (Ollama) or `http://localhost:1234` (LM Studio) |
| `LLM_MODEL` | model name (e.g. `llama3.2`) |
| `AWS_*` | only needed for `bedrock` provider |

See `aiops/requirements.txt` for Python deps.

---

## CI/CD → GitOps flow (in `.github/workflows/ci-cd.yml`)

On every push to `main`:

1. Build `frontend` and `api` Docker images.
2. Push to `ghcr.io/<GITHUB_USER>/...` tagged with the commit SHA **and** `latest`.
3. Update the image tag in `apps/*/k8s/deployment.yaml` and commit back to the repo (GitOps).
4. Argo CD (watching this repo) detects the manifest change and syncs the new image to the cluster.

No manual `kubectl apply` — the cluster is always converged to Git.

---

## License

MIT. Build it, break it, fix it — that's the point of the exercise.
