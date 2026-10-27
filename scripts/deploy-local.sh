#!/usr/bin/env bash
#
# deploy-local.sh - One-shot deploy of the DevOps + AIOps platform to minikube.
#
# Prereqs: minikube, docker, kubectl, python3
#
set -euo pipefail

GITHUB_USER="${GITHUB_USER:-your-github-username}"
IMAGE="ghcr.io/${GITHUB_USER}/aiops-app"
SHA="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo local)"

echo "=== AI Ops Platform - local deploy ==="
echo "GitHub user: $GITHUB_USER"
echo "Image tag:   $IMAGE:$SHA"

# --- 1. Minikube + docker env ---
if ! minikube status >/dev/null 2>&1; then
  echo "Starting minikube..."
  minikube start --driver=docker --cpus=4 --memory=8192
fi
eval "$(minikube docker-env)"
minikube addons enable ingress 2>/dev/null || true

# --- 2. Build images locally inside minikube's docker (one image per service) ---
echo "Building images..."
API_IMAGE="${IMAGE}-api"
FRONTEND_IMAGE="${IMAGE}-frontend"
ASSISTANT_IMAGE="${IMAGE}-assistant"
docker build -t "$API_IMAGE:$SHA" -t "$API_IMAGE:latest" ./apps/api
docker build -t "$FRONTEND_IMAGE:$SHA" -t "$FRONTEND_IMAGE:latest" ./apps/frontend
docker build -t "$ASSISTANT_IMAGE:$SHA" -t "$ASSISTANT_IMAGE:latest" ./aiops

# --- 2.5. Rewrite image references + pull policy for local minikube demo ---
sed -i '' "s|ghcr.io/your-github-username/aiops-api:latest|${API_IMAGE}:latest|g" apps/api/k8s/deployment.yaml
sed -i '' "s|ghcr.io/your-github-username/aiops-frontend:latest|${FRONTEND_IMAGE}:latest|g" apps/frontend/k8s/deployment.yaml
sed -i '' "s|ghcr.io/your-github-username/aiops-assistant:latest|${ASSISTANT_IMAGE}:latest|g" aiops/k8s/deployment.yaml
sed -i '' 's|imagePullPolicy: Always|imagePullPolicy: IfNotPresent|g' \
  apps/api/k8s/deployment.yaml apps/frontend/k8s/deployment.yaml aiops/k8s/deployment.yaml

# --- 2.7. AIOps secrets (create at deploy time; values not stored in git) ---
# kubectl create secret generic aiops-secrets -n aiops \
#   --from-literal=github-token=<your-github-pat>
# kubectl create secret generic db-secret -n aiops \
#   --from-literal=password=<your-db-password>


# --- 3. Namespaces ---
kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: aiops
---
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: Namespace
metadata:
  name: argocd
EOF

# --- 4. Database secret ---
kubectl -n aiops apply -f apps/database/k8s/secret.yaml

# --- 5. Monitoring stack ---
echo "Deploying monitoring (Prometheus, Loki, Grafana, Alertmanager)..."
kubectl -n monitoring apply -f monitoring/prometheus/k8s/serviceaccount.yaml
kubectl -n monitoring apply -f monitoring/prometheus/k8s/configmap.yaml
kubectl -n monitoring apply -f monitoring/prometheus/k8s/deployment.yaml
kubectl -n monitoring apply -f monitoring/prometheus/k8s/service.yaml

kubectl -n monitoring apply -f monitoring/loki/k8s/configmap.yaml
kubectl -n monitoring apply -f monitoring/loki/k8s/deployment.yaml
kubectl -n monitoring apply -f monitoring/loki/k8s/service.yaml

kubectl -n monitoring apply -f monitoring/grafana/k8s/configmap.yaml
kubectl -n monitoring apply -f monitoring/grafana/k8s/deployment.yaml
kubectl -n monitoring apply -f monitoring/grafana/k8s/service.yaml

kubectl -n monitoring apply -f monitoring/alertmanager/k8s/configmap.yaml
kubectl -n monitoring apply -f monitoring/alertmanager/k8s/deployment.yaml
kubectl -n monitoring apply -f monitoring/alertmanager/k8s/service.yaml

# --- 6. Apps ---
echo "Deploying apps (database, api, frontend)..."
kubectl -n aiops apply -f apps/database/k8s/statefulset.yaml
kubectl -n aiops apply -f apps/database/k8s/service.yaml
kubectl -n aiops apply -f apps/api/k8s/deployment.yaml
kubectl -n aiops apply -f apps/api/k8s/service.yaml
kubectl -n aiops apply -f apps/frontend/k8s/deployment.yaml
kubectl -n aiops apply -f apps/frontend/k8s/service.yaml

# --- 7. AIOps assistant ---
echo "Deploying AIOps assistant..."
kubectl -n aiops apply -f aiops/k8s/serviceaccount.yaml
kubectl -n aiops apply -f aiops/k8s/deployment.yaml
kubectl -n aiops apply -f aiops/k8s/service.yaml

# --- 8. Argo CD ---
echo "Installing Argo CD..."
kubectl apply -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd wait --for=condition=Available deployment/argocd-server --timeout=300s || true

# Patch the Argo CD Application with the real repo URL
sed -e "s|<YOUR_GITHUB_USERNAME>|$GITHUB_USER|g" argocd/app.yaml | kubectl apply -f -

# --- 9. Wait for key pods ---
echo "Waiting for workloads to be ready..."
kubectl -n aiops wait --for=condition=Available deployment/api --timeout=300s || true
kubectl -n aiops wait --for=condition=Available deployment/frontend --timeout=300s || true
kubectl -n aiops wait --for=condition=Available deployment/aiops-assistant --timeout=300s || true
kubectl -n monitoring wait --for=condition=Available deployment/prometheus --timeout=300s || true

MINIKUBE_IP="$(minikube ip)"

echo ""
echo "=== Deploy complete ==="
echo "Minikube IP:   $MINIKUBE_IP"
echo ""
echo "Access points (NodePort):"
echo "  Frontend: http://$MINIKUBE_IP:30080"
echo "  API:      http://$MINIKUBE_IP:30001"
echo "  Prometheus: http://$MINIKUBE_IP:30900"
echo "  Grafana:  http://$MINIKUBE_IP:30300  (admin / admin)"
echo ""
echo "Argo CD:"
echo "  minikube service argocd-server -n argocd"
echo "  admin password:"
echo "    kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "Simulate an incident:"
echo "  curl http://$MINIKUBE_IP:30001/stress?mb=200"
echo "  kubectl logs -n aiops deploy/aiops-assistant -f"
echo ""
echo "GitHub repo URL to put in Argo CD:"
echo "  https://github.com/$GITHUB_USER/aiops-project.git"
