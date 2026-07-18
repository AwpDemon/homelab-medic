"""Service health checks: HTTP, TCP, and Docker container state."""
from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Service

UP = "up"
DOWN = "down"


@dataclass(frozen=True)
class CheckResult:
    service: str
    status: str          # up | down
    latency_ms: float
    detail: str


def check(service: Service, timeout: int = 5) -> CheckResult:
    start = time.monotonic()
    if service.kind == "http":
        status, detail = _http(service, timeout)
    elif service.kind == "tcp":
        status, detail = _tcp(service, timeout)
    else:
        status, detail = _container(service, timeout)
    latency = (time.monotonic() - start) * 1000
    return CheckResult(service.name, status, round(latency, 1), detail)


def _http(service: Service, timeout: int) -> tuple[str, str]:
    req = urllib.request.Request(service.target, headers={"User-Agent": "homelab-medic"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        return DOWN, f"{type(e).__name__}: {e}"

    if service.expect_status:
        ok = code == service.expect_status
    else:
        ok = code < 500
    return (UP, f"HTTP {code}") if ok else (DOWN, f"HTTP {code}")


def _tcp(service: Service, timeout: int) -> tuple[str, str]:
    host, _, port = service.target.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return UP, "tcp connect ok"
    except Exception as e:
        return DOWN, f"{type(e).__name__}: {e}"


def _container(service: Service, timeout: int) -> tuple[str, str]:
    name = service.container or service.target
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", name],
            capture_output=True, text=True, timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        return DOWN, "docker inspect timed out"
    if out.returncode != 0:
        return DOWN, f"container not found: {out.stderr.strip()[:120]}"

    state = json.loads(out.stdout)
    running = state.get("Running", False)
    health = (state.get("Health") or {}).get("Status", "")
    if running and health in ("", "healthy"):
        return UP, f"running{f', {health}' if health else ''}"
    return DOWN, f"running={running} health={health or 'n/a'} exit={state.get('ExitCode')}"
