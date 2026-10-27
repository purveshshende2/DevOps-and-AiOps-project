"""Kubernetes automation actions: restart a pod, scale a deployment."""
import os
import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

log = logging.getLogger("aiops.automation")

# Load in-cluster config when running inside the cluster, fall back to local kubeconfig
try:
    config.load_incluster_config()
    log.info("Using in-cluster Kubernetes config")
except Exception:
    config.load_kube_config()
    log.info("Using local kubeconfig")

_v1 = client.CoreV1Api()
_apps = client.AppsV1Api()


def restart_pod(namespace: str, pod: str) -> str:
    """Delete a pod so its controller (Deployment/ReplicaSet) recreates it."""
    try:
        _v1.delete_namespaced_pod(name=pod, namespace=namespace)
        log.info("AUTOMATION: restarted pod %s/%s", namespace, pod)
        return f"restarted pod {namespace}/{pod}"
    except ApiException as e:
        log.error("Failed to restart pod %s/%s: %s", namespace, pod, e)
        return f"failed to restart pod {namespace}/{pod}: {e}"


def scale_deployment(namespace: str, deployment: str, replicas: int = 2) -> str:
    """Scale a Deployment up/down."""
    try:
        patch = {"spec": {"replicas": replicas}}
        _apps.patch_namespaced_deployment_scale(
            name=deployment, namespace=namespace, body=patch
        )
        log.info("AUTOMATION: scaled deployment %s/%s to %d replicas", namespace, deployment, replicas)
        return f"scaled deployment {namespace}/{deployment} to {replicas} replicas"
    except ApiException as e:
        log.error("Failed to scale deployment %s/%s: %s", namespace, deployment, e)
        return f"failed to scale deployment {namespace}/{deployment}: {e}"


def get_pod(namespace: str, pod: str) -> Optional[dict]:
    try:
        p = _v1.read_namespaced_pod(name=pod, namespace=namespace)
        return {
            "name": p.metadata.name,
            "namespace": p.metadata.namespace,
            "phase": p.status.phase,
            "restart_count": sum(
                c.restart_count for c in (p.status.container_statuses or [])
            ),
            "image": (p.spec.containers[0].image if p.spec.containers else ""),
        }
    except ApiException as e:
        log.error("Failed to read pod %s/%s: %s", namespace, pod, e)
        return None


def list_pods_for_app(namespace: str, app: str):
    try:
        pods = _v1.list_namespaced_pod(
            namespace=namespace, label_selector=f"app={app}"
        )
        return [p.metadata.name for p in pods.items]
    except ApiException as e:
        log.error("Failed to list pods for app %s: %s", app, e)
        return []
