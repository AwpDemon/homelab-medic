"""Evidence collection for a failed service: docker state + recent logs."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    container: str
    logs_tail: str = ""
    running: bool | None = None
    exit_code: int | None = None
    oom_killed: bool = False
    restart_count: int = 0
    health_status: str = ""
    error: str = ""


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def collect(container: str, log_lines: int = 60) -> Evidence:
    """Gather docker inspect state and log tail. Never raises."""
    if not container:
        return Evidence(container="", error="no container associated with service")
    try:
        inspect = _run(["docker", "inspect", container])
        if inspect.returncode != 0:
            return Evidence(container=container,
                            error=f"inspect failed: {inspect.stderr.strip()[:200]}")
        data = json.loads(inspect.stdout)[0]
        state = data.get("State", {})

        logs = _run(["docker", "logs", "--tail", str(log_lines), container])
        # docker interleaves app output on stderr; keep both
        tail = (logs.stdout + logs.stderr)[-8000:]

        return Evidence(
            container=container,
            logs_tail=tail,
            running=state.get("Running"),
            exit_code=state.get("ExitCode"),
            oom_killed=bool(state.get("OOMKilled", False)),
            restart_count=int(data.get("RestartCount", 0)),
            health_status=(state.get("Health") or {}).get("Status", ""),
        )
    except Exception as e:  # evidence gathering must never take the monitor down
        return Evidence(container=container, error=f"{type(e).__name__}: {e}")
