"""Heal policy: decides whether the medic is ALLOWED to act.

Auto-healing without guardrails is how a watchdog takes down a stack — a
restart loop can mask a real failure or amplify it. Every heal must pass:
cooldown since the last heal, a daily heal budget, and the service must have
opted in (heal = "restart").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Service
from .heuristics import Diagnosis


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def decide(service: Service, diagnosis: Diagnosis,
           recent_heals: list[datetime], now: datetime | None = None) -> Decision:
    """recent_heals: UTC timestamps of prior heals for this service, any order."""
    now = now or datetime.now(timezone.utc)

    if service.heal != "restart":
        return Decision(False, "service has not opted in to auto-heal")

    if diagnosis.recommended_action != "restart":
        return Decision(False, f"diagnosis recommends {diagnosis.recommended_action!r}, not restart")

    if recent_heals:
        last = max(recent_heals)
        elapsed = (now - last).total_seconds()
        if elapsed < service.cooldown_seconds:
            return Decision(False,
                            f"cooldown: last heal {int(elapsed)}s ago "
                            f"(need {service.cooldown_seconds}s)")

    day_ago = now - timedelta(hours=24)
    todays = sum(1 for t in recent_heals if t >= day_ago)
    if todays >= service.max_heals_per_day:
        return Decision(False,
                        f"heal budget exhausted: {todays}/{service.max_heals_per_day} in 24h "
                        "— repeated failure needs a human")

    return Decision(True, "within cooldown and budget")
