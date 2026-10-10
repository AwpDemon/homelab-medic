"""Deterministic failure diagnosis from evidence — no LLM required.

Runs before (and as fallback for) the LLM so the medic still gives useful
answers when Ollama itself is the thing that died.
"""
from __future__ import annotations

from dataclasses import dataclass

from .evidence import Evidence


# Root-cause categories shared by the heuristics, the LLM prompt and the eval
# set (evals/). "unknown" means the evidence does not support a category.
CATEGORIES = (
    "oom",                     # killed for memory (kernel OOM or app-level OOM)
    "crash_loop",              # restarted >= 3 times; restarting again will not help
    "port_conflict",           # cannot bind its port
    "disk_full",               # no space left / storage threshold reached
    "permission",              # filesystem/socket permission or ownership error
    "dependency_unreachable",  # a database/cache/API it needs refuses connections
    "dns_failure",             # cannot resolve a hostname
    "app_crash",               # application error/panic/segfault, single occurrence
    "config_error",            # bad/missing config, env var, credentials, migration, cert
    "external_stop",           # stopped by an operator/host (exit 0, SIGTERM, stop timeout)
    "unhealthy",               # process alive but wedged / healthcheck failing
    "network_path",            # container fine; probe, proxy or network path is the problem
    "unknown",
)


@dataclass(frozen=True)
class Diagnosis:
    cause: str
    confidence: float          # 0..1
    recommended_action: str    # restart | investigate | none
    reasoning: str
    source: str                # heuristic | llm
    category: str = "unknown"  # one of CATEGORIES

_LOG_PATTERNS = [
    # (needle in logs, cause, action, confidence, category)
    ("address already in use", "port conflict on startup", "investigate", 0.9, "port_conflict"),
    ("port is already allocated", "port conflict on startup", "investigate", 0.9, "port_conflict"),
    ("no space left on device", "disk full", "investigate", 0.95, "disk_full"),
    ("permission denied", "filesystem permission error", "investigate", 0.7, "permission"),
    ("connection refused", "upstream dependency unreachable", "restart", 0.5, "dependency_unreachable"),
    ("no such host", "DNS resolution failure", "restart", 0.6, "dns_failure"),
    ("panic:", "application crash (panic)", "restart", 0.7, "app_crash"),
    ("fatal", "application fatal error", "restart", 0.5, "app_crash"),
    ("out of memory", "application-level OOM", "restart", 0.8, "oom"),
]


def diagnose(ev: Evidence) -> Diagnosis:
    if ev.error:
        return Diagnosis(
            cause="evidence collection failed",
            confidence=0.2,
            recommended_action="investigate",
            reasoning=ev.error,
            source="heuristic",
            category="unknown",
        )

    if ev.oom_killed:
        return Diagnosis(
            cause="container OOM-killed by kernel",
            confidence=0.95,
            recommended_action="restart",
            reasoning="docker inspect reports OOMKilled=true; consider raising the memory limit",
            source="heuristic",
            category="oom",
        )

    if ev.restart_count >= 3:
        return Diagnosis(
            cause="crash loop",
            confidence=0.85,
            recommended_action="investigate",
            reasoning=f"RestartCount={ev.restart_count}; restarting again is unlikely to help",
            source="heuristic",
            category="crash_loop",
        )

    logs = ev.logs_tail.lower()
    for needle, cause, action, conf, category in _LOG_PATTERNS:
        if needle in logs:
            return Diagnosis(
                cause=cause,
                confidence=conf,
                recommended_action=action,
                reasoning=f"log tail matched pattern {needle!r}",
                source="heuristic",
                category=category,
            )

    if ev.running is False and ev.exit_code == 0:
        return Diagnosis(
            cause="container stopped cleanly (exit 0)",
            confidence=0.9,
            recommended_action="restart",
            reasoning="clean exit means manual stop, compose down, or host event — "
                      "not an application fault; restart is safe",
            source="heuristic",
            category="external_stop",
        )

    if ev.running is False:
        return Diagnosis(
            cause=f"container exited (code {ev.exit_code})",
            confidence=0.6,
            recommended_action="restart",
            reasoning="container is not running and no specific failure pattern matched",
            source="heuristic",
            category="app_crash",
        )

    if ev.health_status == "unhealthy":
        return Diagnosis(
            cause="container running but healthcheck failing",
            confidence=0.6,
            recommended_action="restart",
            reasoning="process alive but not serving; restart usually clears wedged state",
            source="heuristic",
            category="unhealthy",
        )

    return Diagnosis(
        cause="service unreachable, container looks healthy",
        confidence=0.3,
        recommended_action="investigate",
        reasoning="no failure signature in evidence; could be network path or reverse proxy",
        source="heuristic",
        category="network_path",
    )
