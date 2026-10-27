"""AIOps assistant: receives Alertmanager webhooks, queries Loki, reasons with an LLM,
and triggers automated remediation or human-in-the-loop rollback."""
import os
import json
import logging
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from automation import restart_pod, scale_deployment, get_pod, list_pods_for_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s aiops %(message)s",
)
log = logging.getLogger("aiops")

app = FastAPI(title="AIOps Assistant")

LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc:3100")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # owner/name
NAMESPACE = os.getenv("POD_NAMESPACE", "aiops")

# Known safe automation patterns: (keywords) -> (action, replicas)
KNOWN_PATTERNS = {
    "oom": ("restart_pod", 2),
    "oomkilled": ("restart_pod", 2),
    "memory": ("restart_pod", 2),
    "out of memory": ("restart_pod", 2),
}


# ---------------------------------------------------------------------------
# LLM client (pluggable providers + robust mock fallback)
# ---------------------------------------------------------------------------
class LLMResult(BaseModel):
    summary: str
    likely_root_cause: str
    suggested_action: str
    confidence: str


def llm_analyze(alert: Dict[str, Any], logs: str) -> LLMResult:
    """Return a structured incident analysis. Falls back to mock if no provider."""
    provider = LLM_PROVIDER
    prompt = build_prompt(alert, logs)

    if provider == "mock":
        return mock_analyze(alert, logs)

    if provider in ("ollama", "lmstudio", "openai"):
        return call_openai_compatible(prompt)

    if provider == "bedrock":
        return call_bedrock(prompt)

    # Unknown provider -> safe mock
    log.warning("Unknown LLM_PROVIDER=%s; using mock", provider)
    return mock_analyze(alert, logs)


def build_prompt(alert: Dict[str, Any], logs: str) -> str:
    return f"""You are an AIOps incident-response engineer. Analyze this alert and produce JSON only.

ALERT:
- alertname: {alert.get('alertname')}
- severity: {alert.get('severity')}
- pod: {alert.get('pod')}
- namespace: {alert.get('namespace')}
- instance: {alert.get('instance')}
- description: {alert.get('description')}

RECENT LOGS (Loki):
{logs[:3000]}

Respond with strict JSON:
{{
  "summary": "one-line incident summary",
  "likely_root_cause": "probable root cause (e.g. OOMKilled - memory limit too low)",
  "suggested_action": "restart pod / scale up deployment / human rollback / investigate",
  "confidence": "high/medium/low"
}}"""


def call_openai_compatible(prompt: str) -> LLMResult:
    url = LLM_BASE_URL.rstrip("/")
    if LLM_PROVIDER == "ollama":
        url = url + "/api/generate"
        payload = {
            "model": LLM_MODEL,
            "prompt": prompt,
            "format": "json",
            "stream": False,
        }
        try:
            r = requests.post(url, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            text = data.get("response", "{}")
            return _parse_llm_json(text)
        except Exception as e:
            log.error("Ollama request failed: %s", e)
            return mock_analyze({}, "")
    else:
        # LM Studio / OpenAI chat completions
        url = url + "/v1/chat/completions"
        payload = {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if LLM_PROVIDER == "openai" and os.getenv("OPENAI_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY')}"
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            return _parse_llm_json(text)
        except Exception as e:
            log.error("OpenAI-compatible request failed: %s", e)
            return mock_analyze({}, "")


def call_bedrock(prompt: str) -> LLMResult:
    import boto3  # type: ignore

    try:
        brt = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        body = json.dumps({
            "prompt": f"\n\nHuman: {prompt}\n\nAssistant:",
            "max_tokens_to_sample": 512,
            "temperature": 0.2,
        })
        resp = brt.invoke_model(
            modelId=os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-v2"),
            body=body,
        )
        text = json.loads(resp["body"].read()).get("completion", "{}")
        return _parse_llm_json(text)
    except Exception as e:
        log.error("Bedrock request failed: %s", e)
        return mock_analyze({}, "")


def _parse_llm_json(text: str) -> LLMResult:
    try:
        # Strip markdown fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        return LLMResult(
            summary=str(data.get("summary", "")),
            likely_root_cause=str(data.get("likely_root_cause", "")),
            suggested_action=str(data.get("suggested_action", "investigate")),
            confidence=str(data.get("confidence", "low")),
        )
    except Exception as e:
        log.error("Failed to parse LLM JSON: %s | text=%s", e, text[:200])
        return LLMResult(
            summary="LLM parse failed; manual review needed",
            likely_root_cause="unable to determine automatically",
            suggested_action="human rollback / investigate",
            confidence="low",
        )


def mock_analyze(alert: Dict[str, Any], logs: str) -> LLMResult:
    """Deterministic analysis so the full loop runs end-to-end without an LLM key."""
    alertname = (alert.get("alertname") or "").lower()
    description = (alert.get("description") or "").lower()
    combined = f"{alertname} {description} {logs}".lower()

    if any(k in combined for k in ("oom", "oomkilled", "out of memory", "memory")):
        return LLMResult(
            summary=f"Pod {alert.get('pod')} is restarting after a memory-related failure.",
            likely_root_cause="OOMKilled - memory limit set too low for the workload.",
            suggested_action="restart pod, then scale up deployment if memory pressure persists.",
            confidence="high",
        )
    if "crash" in combined or "crashloop" in combined:
        return LLMResult(
            summary=f"Pod {alert.get('pod')} is crash looping.",
            likely_root_cause="Application crash on startup (bad config/image/deploy).",
            suggested_action="human rollback of the deployment.",
            confidence="medium",
        )
    if alert.get("severity") == "critical" or "down" in combined:
        return LLMResult(
            summary=f"Service {alert.get('pod')} is down.",
            likely_root_cause="Pod not ready / container failed to start.",
            suggested_action="human rollback / investigate.",
            confidence="medium",
        )
    return LLMResult(
        summary=f"Alert {alert.get('alertname')} on {alert.get('pod')} requires review.",
        likely_root_cause="unclassified - needs manual triage",
        suggested_action="human rollback / investigate",
        confidence="low",
    )


# ---------------------------------------------------------------------------
# Loki log fetch
# ---------------------------------------------------------------------------
def fetch_loki_pod_logs(namespace: str, pod: str, minutes: int = 10) -> str:
    """Query Loki for recent container logs of a pod."""
    query = f'{{namespace="{namespace}",pod="{pod}"}} |={minutes}m'
    try:
        r = requests.get(
            f"{LOKI_URL}/loki/api/v1/query",
            params={"query": query, "limit": 50},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        lines = []
        for stream in data.get("data", {}).get("result", []):
            for _, line in stream.get("values", []):
                lines.append(line)
        return "\n".join(lines[-40:]) if lines else "(no logs found)"
    except Exception as e:
        log.warning("Loki query failed (%s); falling back to k8s logs", e)
        return _k8s_pod_logs(namespace, pod)


def _k8s_pod_logs(namespace: str, pod: str) -> str:
    from kubernetes import client

    try:
        v1 = client.CoreV1Api()
        return v1.read_namespaced_pod_log(name=pod, namespace=namespace, tail_lines=40) or "(no logs)"
    except Exception as e:
        return f"(unable to fetch logs: {e})"


# ---------------------------------------------------------------------------
# GitHub issue (human-in-the-loop rollback request)
# ---------------------------------------------------------------------------
def create_github_issue(alert: Dict[str, Any], analysis: LLMResult) -> Optional[str]:
    if not (GITHUB_TOKEN and GITHUB_REPO):
        log.info("GitHub token/repo not configured; skipping issue creation")
        return None
    title = f"[AIOps] Rollback requested: {alert.get('alertname')} on {alert.get('pod')}"
    body = f"""## AIOps Incident - Human Review Required

**Alert:** {alert.get('alertname')} ({alert.get('severity')})
**Pod:** {alert.get('namespace')}/{alert.get('pod')}
**Instance:** {alert.get('instance')}

**Incident Summary:** {analysis.summary}
**Likely Root Cause:** {analysis.likely_root_cause}
**Suggested Action:** {analysis.suggested_action}
**Confidence:** {analysis.confidence}

This was NOT auto-remediated because the pattern is not a known safe automation.
Please review and **Approve** or **Reject** a rollback of the deployment.

cc @on-call
"""
    try:
        r = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body, "labels": ["aiops", "rollback", "on-call"]},
            timeout=20,
        )
        r.raise_for_status()
        issue = r.json()
        log.info("Created GitHub issue #%s", issue.get("number"))
        return str(issue.get("number"))
    except Exception as e:
        log.error("Failed to create GitHub issue: %s", e)
        return None


# ---------------------------------------------------------------------------
# Webhook entrypoint (Alertmanager -> AIOps)
# ---------------------------------------------------------------------------
class Alert(BaseModel):
    alertname: str
    severity: str = "warning"
    pod: str = ""
    namespace: str = ""
    instance: str = ""
    description: str = ""


@app.get("/health")
def health():
    return {"status": "healthy", "service": "aiops-assistant", "llm_provider": LLM_PROVIDER}


@app.post("/alertwebhook")
async def alertwebhook(request: Request):
    payload = await request.json()
    log.info("Received Alertmanager webhook: %s", json.dumps(payload)[:500])

    results = []

    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        status = alert.get("status", "")

        a = Alert(
            alertname=labels.get("alertname", "unknown"),
            severity=labels.get("severity", "warning"),
            pod=labels.get("pod", ""),
            namespace=labels.get("namespace", NAMESPACE),
            instance=labels.get("instance", ""),
            description=annotations.get("description", ""),
        )

        # Skip resolved alerts (they are informational)
        if status == "resolved":
            results.append({"alert": a.alertname, "action": "ignored (resolved)"})
            continue

        # Enrich with Loki logs
        logs = fetch_loki_pod_logs(a.namespace, a.pod) if a.pod else "(no pod label)"

        # LLM reasoning
        analysis = llm_analyze(a.model_dump(), logs)

        # Decide action
        action_taken = ""
        combined_text = f"{a.alertname} {a.description} {logs}".lower()

        if any(k in combined_text for k in ("oom", "oomkilled", "out of memory", "memory")):
            # Known safe pattern -> automatic remediation
            if a.pod:
                action_taken += restart_pod(a.namespace, a.pod) + "; "
            # If memory pressure, scale up the api deployment
            app_name = (labels.get("app") or "api")
            if list_pods_for_app(a.namespace, app_name):
                action_taken += scale_deployment(a.namespace, app_name, replicas=2) + "; "
            results.append({
                "alert": a.alertname,
                "analysis": analysis.model_dump(),
                "action": "automatic: restart pod + scale deployment",
                "detail": action_taken,
            })
        else:
            # Not a known safe pattern -> human in the loop
            issue_no = create_github_issue(a.model_dump(), analysis)
            results.append({
                "alert": a.alertname,
                "analysis": analysis.model_dump(),
                "action": "human-in-the-loop: rollback requested",
                "github_issue": issue_no,
            })

    return JSONResponse(status_code=200, content={"received": len(results), "results": results})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
