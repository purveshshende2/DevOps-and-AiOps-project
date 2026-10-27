# Architecture - AI Ops Platform

## Data flow

1. **Developer** pushes code to GitHub (`main`).
2. **GitHub Actions** builds Docker images for `api` and `frontend`, pushes them to
   `ghcr.io/<user>/aiops-app:<sha>` and `:latest`.
3. The workflow commits the new image tag back into `apps/*/k8s/deployment.yaml`
   (GitOps). **Argo CD** watches the `apps/` directory and syncs the new image
   into the `aiops` namespace on the Kubernetes cluster.
4. The cluster runs **Frontend / API / Database**. Prometheus scrapes metrics;
   Loki ingests stdout logs from all pods; Grafana exposes dashboards.
5. **Alertmanager** evaluates Prometheus alert rules (`AppDown`, `PodOOMKilled`,
   `HighMemoryUsage`, `CrashLoop`) and sends webhooks to the **AIOps assistant**.
6. The AIOps assistant:
   - pulls recent logs from **Loki** for the offending pod,
   - asks the **LLM** for an incident summary + likely root cause,
   - for **known safe patterns** (e.g. OOMKilled / memory too low) automatically
     **restarts the pod** and **scales up the deployment**,
   - for anything else opens a **GitHub issue** for the on-call engineer
     (human-in-the-loop: Approve / Reject rollback).

## Namespaces

| Namespace | Purpose |
|-----------|---------|
| `aiops`   | application workloads + AIOps assistant |
| `monitoring` | Prometheus, Loki, Grafana, Alertmanager |
| `argocd`  | Argo CD control plane |

## Extension points

- Swap `LLM_PROVIDER=mock` for `ollama`, `lmstudio`, `openai`, or `bedrock` in
  `.env` / the assistant Deployment env to use a real LLM.
- Provision a real cluster with `terraform/` (AWS EKS) and point Argo CD at it.
- Add more known patterns to `KNOWN_PATTERNS` in `aiops/assistant.py` for
  additional auto-remediation (e.g. disk pressure, rate limiting).
