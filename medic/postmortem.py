"""Markdown postmortem generation — one file per incident."""
from __future__ import annotations

from pathlib import Path

from .checks import CheckResult
from .evidence import Evidence
from .heal import HealResult
from .heuristics import Diagnosis


def write(directory: str | Path, incident_id: int, result: CheckResult,
          ev: Evidence, diagnosis: Diagnosis, policy_reason: str,
          heal: HealResult | None) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"incident-{incident_id:04d}-{result.service}.md"

    if heal is None:
        outcome = "No heal attempted."
    elif heal.success:
        outcome = f"✅ Auto-healed: {heal.detail}"
    else:
        outcome = f"❌ Heal failed: {heal.detail}"

    log_excerpt = ev.logs_tail[-1500:].strip() or "(no logs captured)"

    path.write_text(f"""# Incident #{incident_id} — {result.service}

## Detection
- Check result: **{result.status.upper()}** — {result.detail}
- Latency: {result.latency_ms} ms

## Evidence
- Container: `{ev.container or 'n/a'}`
- Running: {ev.running} · Exit code: {ev.exit_code} · OOM-killed: {ev.oom_killed}
- Restart count: {ev.restart_count} · Health: {ev.health_status or 'n/a'}

## Diagnosis ({diagnosis.source}, confidence {diagnosis.confidence:.0%})
**{diagnosis.cause}**

{diagnosis.reasoning}

Recommended action: `{diagnosis.recommended_action}`

## Policy
{policy_reason}

## Outcome
{outcome}

## Log tail
```
{log_excerpt}
```
""", encoding="utf-8")
    return path
