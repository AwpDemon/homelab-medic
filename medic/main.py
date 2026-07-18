"""CLI entry point: scan once, watch forever, or print recent incidents."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import checks, evidence, heal, heuristics, llm, notify, policy, postmortem
from .config import Config, Service, load_config
from .store import Store

log = logging.getLogger("medic")

KILL_FILE = Path("KILL")


def handle_service(cfg: Config, store: Store, service: Service) -> str:
    """Check one service; on failure run the full incident pipeline.

    Returns a one-word outcome for the scan summary:
    up | recovered | healed | heal-failed | open | new-incident
    """
    result = checks.check(service, timeout=cfg.timeout_seconds)

    if result.status == checks.UP:
        if store.resolve_open_incidents(service.name):
            log.info("%s: back up, incident resolved", service.name)
            notify.send(cfg.discord_webhook, "healed", service.name,
                        [f"Service is back up ({result.detail})."])
            return "recovered"
        return "up"

    log.warning("%s: DOWN — %s", service.name, result.detail)

    if store.has_open_incident(service.name):
        # Already diagnosed and (maybe) healed this outage; don't spam.
        log.info("%s: incident already open, skipping re-diagnosis", service.name)
        return "open"

    ev = evidence.collect(service.container)
    diag = heuristics.diagnose(ev)
    # Only consult the LLM when the evidence is ambiguous: a high-confidence
    # heuristic (OOM, clean stop, port conflict) doesn't need a model's
    # opinion, and log-tail noise can talk an LLM out of an obvious call.
    if diag.confidence < 0.85:
        diag = llm.diagnose(cfg.llm, result, ev, fallback=diag)
    log.info("%s: diagnosis (%s, %.0f%%): %s",
             service.name, diag.source, diag.confidence * 100, diag.cause)

    incident_id = store.open_incident(
        service.name, result.detail, diag.cause, diag.confidence,
        diag.recommended_action, diag.reasoning, diag.source)

    decision = policy.decide(service, diag, store.recent_heal_times(service.name))
    heal_result = None
    outcome = "new-incident"
    if decision.allowed:
        log.info("%s: healing (%s)", service.name, decision.reason)
        heal_result = heal.restart_and_verify(
            service, grace_seconds=cfg.heal_grace_seconds,
            check_timeout=cfg.timeout_seconds)
        store.record_heal(incident_id, service.name, heal_result.success,
                          heal_result.detail)
        outcome = "healed" if heal_result.success else "heal-failed"
    else:
        log.info("%s: not healing — %s", service.name, decision.reason)

    pm = postmortem.write(cfg.postmortem_dir, incident_id, result, ev, diag,
                          decision.reason, heal_result)
    log.info("%s: postmortem written to %s", service.name, pm)

    kind = "healed" if (heal_result and heal_result.success) else "down"
    notify.send(cfg.discord_webhook, kind, service.name, [
        f"**Check:** {result.detail}",
        f"**Diagnosis** ({diag.source}, {diag.confidence:.0%}): {diag.cause}",
        f"**Policy:** {decision.reason}",
        f"**Outcome:** {heal_result.detail if heal_result else 'no action taken'}",
    ])
    return outcome


def scan(cfg: Config, store: Store) -> dict[str, str]:
    outcomes = {}
    for service in cfg.services:
        try:
            outcomes[service.name] = handle_service(cfg, store, service)
        except Exception:
            log.exception("%s: unhandled error in pipeline", service.name)
            outcomes[service.name] = "error"
    return outcomes


def watch(cfg: Config, store: Store) -> None:
    log.info("watching %d services every %ds (touch KILL to stop)",
             len(cfg.services), cfg.interval_seconds)
    while True:
        if KILL_FILE.exists():
            log.info("KILL file found, exiting cleanly")
            return
        outcomes = scan(cfg, store)
        down = {k: v for k, v in outcomes.items() if v not in ("up", "recovered")}
        log.info("scan done: %d up, issues: %s",
                 sum(1 for v in outcomes.values() if v == "up"), down or "none")
        time.sleep(cfg.interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medic", description=__doc__)
    parser.add_argument("command", choices=["scan", "watch", "incidents"])
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    try:
        if args.command == "scan":
            outcomes = scan(cfg, store)
            width = max(len(n) for n in outcomes)
            for name, outcome in outcomes.items():
                print(f"{name:<{width}}  {outcome}")
            return 0 if all(v in ("up", "recovered", "healed") for v in outcomes.values()) else 1
        if args.command == "watch":
            watch(cfg, store)
            return 0
        for inc in store.recent_incidents():
            print(f"#{inc['id']:>3} {inc['opened_at'][:19]} {inc['service']:<20} "
                  f"{inc['cause']} → {'resolved' if inc['resolved_at'] else 'OPEN'}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
