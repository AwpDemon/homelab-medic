"""Heal execution: restart the container, wait, verify the service recovered."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from . import checks
from .config import Service


@dataclass(frozen=True)
class HealResult:
    attempted: bool
    success: bool
    detail: str


def restart_and_verify(service: Service, grace_seconds: int = 20,
                       check_timeout: int = 5) -> HealResult:
    try:
        proc = subprocess.run(
            ["docker", "restart", service.container],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return HealResult(True, False, "docker restart timed out after 120s")

    if proc.returncode != 0:
        return HealResult(True, False, f"docker restart failed: {proc.stderr.strip()[:200]}")

    time.sleep(grace_seconds)
    verify = checks.check(service, timeout=check_timeout)
    if verify.status == checks.UP:
        return HealResult(True, True, f"restarted; verify: {verify.detail}")
    return HealResult(True, False, f"restarted but still down: {verify.detail}")
