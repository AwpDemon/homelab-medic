"""TOML config loading and validation."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_KINDS = ("http", "tcp", "container")
VALID_HEALS = ("restart", "none")


@dataclass(frozen=True)
class Service:
    name: str
    kind: str                     # http | tcp | container
    target: str                   # url, host:port, or container name
    container: str = ""           # docker container backing this service ("" = none)
    heal: str = "none"            # restart | none
    cooldown_seconds: int = 300
    max_heals_per_day: int = 5
    expect_status: int = 0        # http only; 0 = any non-5xx


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = False
    url: str = "http://localhost:11434"
    model: str = ""
    timeout_seconds: int = 60


@dataclass(frozen=True)
class Config:
    services: tuple[Service, ...]
    llm: LLMConfig
    interval_seconds: int = 120
    timeout_seconds: int = 5
    heal_grace_seconds: int = 20
    db_path: str = "data/medic.db"
    postmortem_dir: str = "postmortems"
    discord_webhook: str = ""


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    defaults = raw.get("defaults", {})
    llm_raw = raw.get("llm", {})
    storage = raw.get("storage", {})
    notify = raw.get("notify", {}).get("discord", {})

    services = []
    seen: set[str] = set()
    for entry in raw.get("service", []):
        name = entry.get("name", "")
        if not name:
            raise ConfigError("service entry missing 'name'")
        if name in seen:
            raise ConfigError(f"duplicate service name: {name}")
        seen.add(name)

        kind = entry.get("kind", "")
        if kind not in VALID_KINDS:
            raise ConfigError(f"{name}: kind must be one of {VALID_KINDS}, got {kind!r}")
        heal = entry.get("heal", "none")
        if heal not in VALID_HEALS:
            raise ConfigError(f"{name}: heal must be one of {VALID_HEALS}, got {heal!r}")
        target = entry.get("target", "")
        if not target:
            raise ConfigError(f"{name}: missing 'target'")
        if heal == "restart" and not entry.get("container", ""):
            raise ConfigError(f"{name}: heal='restart' requires 'container'")

        services.append(Service(
            name=name,
            kind=kind,
            target=target,
            container=entry.get("container", ""),
            heal=heal,
            cooldown_seconds=int(entry.get("cooldown_seconds", 300)),
            max_heals_per_day=int(entry.get("max_heals_per_day", 5)),
            expect_status=int(entry.get("expect_status", 0)),
        ))

    if not services:
        raise ConfigError("no [[service]] entries defined")

    return Config(
        services=tuple(services),
        llm=LLMConfig(
            enabled=bool(llm_raw.get("enabled", False)),
            url=str(llm_raw.get("url", "http://localhost:11434")).rstrip("/"),
            model=str(llm_raw.get("model", "")),
            timeout_seconds=int(llm_raw.get("timeout_seconds", 60)),
        ),
        interval_seconds=int(defaults.get("interval_seconds", 120)),
        timeout_seconds=int(defaults.get("timeout_seconds", 5)),
        heal_grace_seconds=int(defaults.get("heal_grace_seconds", 20)),
        db_path=str(storage.get("db_path", "data/medic.db")),
        postmortem_dir=str(storage.get("postmortem_dir", "postmortems")),
        discord_webhook=str(notify.get("webhook_url", "")),
    )
