"""Discord webhook notifications. No webhook configured = silent no-op."""
from __future__ import annotations

import json
import urllib.request

_COLORS = {"down": 0xE74C3C, "healed": 0x2ECC71, "manual": 0xF39C12}


def send(webhook_url: str, kind: str, service: str, lines: list[str]) -> bool:
    if not webhook_url:
        return False
    body = json.dumps({
        "embeds": [{
            "title": f"[{kind.upper()}] {service}",
            "description": "\n".join(lines)[:3900],
            "color": _COLORS.get(kind, 0x95A5A6),
        }]
    }).encode()
    req = urllib.request.Request(
        webhook_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
