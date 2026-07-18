"""LLM diagnosis via a local Ollama endpoint.

The LLM sees the check failure plus collected evidence and returns a structured
diagnosis. Any failure (endpoint down, timeout, malformed JSON) falls back to
the heuristic diagnosis passed in — the medic must keep working when the LLM
box is the patient.
"""
from __future__ import annotations

import json
import urllib.request

from .checks import CheckResult
from .config import LLMConfig
from .evidence import Evidence
from .heuristics import Diagnosis

_SYSTEM = """You are an SRE diagnosing a failed service on a Docker homelab.
Respond with ONLY a JSON object:
{"cause": "<one-line probable root cause>",
 "confidence": <0.0-1.0>,
 "recommended_action": "restart" | "investigate" | "none",
 "reasoning": "<2-3 sentences citing the evidence>"}
Prefer "investigate" over "restart" when a restart clearly cannot fix the cause
(port conflicts, disk full, crash loops)."""


def diagnose(cfg: LLMConfig, result: CheckResult, ev: Evidence,
             fallback: Diagnosis) -> Diagnosis:
    if not cfg.enabled or not cfg.model:
        return fallback

    prompt = (
        f"Service: {result.service}\n"
        f"Check failed with: {result.detail}\n"
        f"Container: {ev.container or 'n/a'}\n"
        f"State: running={ev.running} exit_code={ev.exit_code} "
        f"oom_killed={ev.oom_killed} restarts={ev.restart_count} "
        f"health={ev.health_status or 'n/a'}\n"
        f"Heuristic guess: {fallback.cause} ({fallback.confidence:.0%})\n"
        f"Last log lines:\n{ev.logs_tail[-3000:] or '(no logs)'}"
    )
    body = json.dumps({
        "model": cfg.model,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }).encode()

    req = urllib.request.Request(
        f"{cfg.url}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_seconds) as resp:
            payload = json.loads(resp.read())
        parsed = json.loads(payload["message"]["content"])
        action = parsed.get("recommended_action", "investigate")
        if action not in ("restart", "investigate", "none"):
            action = "investigate"
        return Diagnosis(
            cause=str(parsed.get("cause", "unknown"))[:200],
            confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
            recommended_action=action,
            reasoning=str(parsed.get("reasoning", ""))[:600],
            source="llm",
        )
    except Exception:
        return fallback
