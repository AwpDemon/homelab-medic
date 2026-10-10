"""LLM diagnosis via a chat-completions endpoint.

Two transports, both stdlib-only:
- ``api = "ollama"``  -> POST {url}/api/chat (Ollama native, JSON mode)
- ``api = "openai"``  -> POST {url}/v1/chat/completions (any OpenAI-compatible
  server: Ollama's /v1, llama.cpp, vLLM, LM Studio, or a hosted API with a key
  read from the env var named in ``api_key_env``)

The LLM sees the check failure plus collected evidence and returns a structured
diagnosis. Any failure (endpoint down, timeout, malformed JSON) falls back to
the heuristic diagnosis passed in: the medic must keep working when the LLM
box is the patient.
"""
from __future__ import annotations

import json
import os
import urllib.request

from .checks import CheckResult
from .config import LLMConfig
from .evidence import Evidence
from .heuristics import CATEGORIES, Diagnosis

_CATEGORY_LIST = ", ".join(f'"{c}"' for c in CATEGORIES)

# v1: the original production prompt, plus the category field so the eval can
# score root causes. Kept for the before/after comparison in PROMPTS.md.
PROMPT_V1 = f"""You are an SRE diagnosing a failed service on a Docker homelab.
Respond with ONLY a JSON object:
{{"cause": "<one-line probable root cause>",
 "category": one of [{_CATEGORY_LIST}],
 "confidence": <0.0-1.0>,
 "recommended_action": "restart" | "investigate" | "none",
 "reasoning": "<2-3 sentences citing the evidence>"}}
Prefer "investigate" over "restart" when a restart clearly cannot fix the cause
(port conflicts, disk full, crash loops)."""

# v2: decision rules from the labeling rubric (evals/LABELING.md), checked against
# v1's errors on the dev split and frozen before any test-split run (PROMPTS.md).
# Adds the decision rules that v1 got wrong: weight container state over old
# log lines, and treat config/credential/migration/cert problems and
# still-running-but-retrying containers as "investigate".
PROMPT_V2 = f"""You are an SRE deciding whether an automatic container restart is safe.
Respond with ONLY a JSON object:
{{"cause": "<one-line probable root cause>",
 "category": one of [{_CATEGORY_LIST}],
 "confidence": <0.0-1.0>,
 "recommended_action": "restart" | "investigate" | "none",
 "reasoning": "<2-3 sentences citing the evidence>"}}

Decide in this order:
1. Container state beats log text. Logs are a tail that can include old lines from
   earlier runs. If the container stopped with exit code 0, 143 (SIGTERM) or 137
   without OOM, and restarts < 3, it was stopped by an operator or the host:
   category "external_stop", action "restart", even if older log lines show errors.
2. restarts >= 3 means "crash_loop" (or the underlying cause if obvious) and
   action "investigate": restarting again will not help.
3. Choose "investigate" when a restart cannot fix the cause: port conflict, disk
   full, permission or ownership errors, bad or missing config, env vars,
   credentials, database migrations, expired certificates ("config_error"), or a
   container that is running and healthy while the check fails: the probe, proxy
   or network path is at fault ("network_path").
4. If the container is still running and keeps failing to reach a dependency or
   resolve a host, the dependency is the problem: action "investigate".
   If it exited once at startup because a dependency was not up yet, "restart".
5. Choose "restart" for one-off crashes, OOM kills (restarts < 3), and wedged
   processes whose healthcheck fails with no persistent cause in the logs.
6. Use "none" only if nothing is wrong. A stopped service is never "none"."""

PROMPTS = {"v1": PROMPT_V1, "v2": PROMPT_V2}
DEFAULT_PROMPT = "v2"


def build_user_prompt(result: CheckResult, ev: Evidence, fallback: Diagnosis) -> str:
    return (
        f"Service: {result.service}\n"
        f"Check failed with: {result.detail}\n"
        f"Container: {ev.container or 'n/a'}\n"
        f"State: running={ev.running} exit_code={ev.exit_code} "
        f"oom_killed={ev.oom_killed} restarts={ev.restart_count} "
        f"health={ev.health_status or 'n/a'}\n"
        f"Heuristic guess: {fallback.cause} ({fallback.confidence:.0%})\n"
        f"Last log lines:\n{ev.logs_tail[-3000:] or '(no logs)'}"
    )


def _request(cfg: LLMConfig, system: str, user: str, seed: int | None) -> str:
    """Send one chat request and return the assistant message content."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    headers = {"Content-Type": "application/json"}

    if cfg.api == "openai":
        body = {"model": cfg.model, "messages": messages,
                "temperature": cfg.temperature,
                "response_format": {"type": "json_object"}}
        if seed is not None:
            body["seed"] = seed
        key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{cfg.url}/v1/chat/completions"
    else:
        options = {"temperature": cfg.temperature}
        if seed is not None:
            options["seed"] = seed
        body = {"model": cfg.model, "messages": messages, "stream": False,
                "format": "json", "options": options}
        url = f"{cfg.url}/api/chat"

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=cfg.timeout_seconds) as resp:
        payload = json.loads(resp.read())
    if cfg.api == "openai":
        return payload["choices"][0]["message"]["content"]
    return payload["message"]["content"]


def parse_diagnosis(content: str) -> Diagnosis:
    """Validate the model's JSON into a Diagnosis. Raises on malformed output."""
    parsed = json.loads(content)
    action = parsed.get("recommended_action", "investigate")
    if action not in ("restart", "investigate", "none"):
        action = "investigate"
    category = str(parsed.get("category", "unknown"))
    if category not in CATEGORIES:
        category = "unknown"
    return Diagnosis(
        cause=str(parsed.get("cause", "unknown"))[:200],
        confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
        recommended_action=action,
        reasoning=str(parsed.get("reasoning", ""))[:600],
        source="llm",
        category=category,
    )


def diagnose(cfg: LLMConfig, result: CheckResult, ev: Evidence,
             fallback: Diagnosis, prompt: str | None = None,
             seed: int | None = None) -> Diagnosis:
    if not cfg.enabled or not cfg.model:
        return fallback
    system = PROMPTS[prompt or cfg.prompt or DEFAULT_PROMPT]
    try:
        content = _request(cfg, system, build_user_prompt(result, ev, fallback), seed)
        return parse_diagnosis(content)
    except Exception:
        return fallback
