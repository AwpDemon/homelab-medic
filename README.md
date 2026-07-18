# homelab-medic

[![ci](https://github.com/AwpDemon/homelab-medic/actions/workflows/ci.yml/badge.svg)](https://github.com/AwpDemon/homelab-medic/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![runtime deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A self-healing monitor for a Docker homelab. When a service dies, medic collects
evidence (logs, exit codes, OOM flags), diagnoses the failure — deterministic
heuristics first, a **local LLM** for the ambiguous cases — decides whether it's
*allowed* to act, restarts the container, verifies recovery, and writes a markdown
postmortem. Runs my ~25-container homeserver; **zero runtime dependencies** (Python
stdlib only — the thing that fixes your stack shouldn't have its own dependency stack).

```
check (http/tcp/docker) ──DOWN──► evidence ──► heuristics ──confidence ≥ 0.85──► policy ──► heal ──► verify
                                     │              │                              ▲                   │
                                     │              └──ambiguous──► local LLM ─────┘                   │
                                     │                             (Ollama)                            │
                                     └────────────────────────────────────────────► postmortem.md ◄────┘
                                                                                    + Discord alert
```

## A real incident, start to finish

I stopped a container on my live stack. One scan later:

```
13:24:27 [WARNING] searxng: DOWN — URLError: <urlopen error timed out>
13:24:27 [INFO] searxng: diagnosis (heuristic, 90%): container stopped cleanly (exit 0)
13:24:27 [INFO] searxng: healing (within cooldown and budget)
13:24:43 [INFO] searxng: postmortem written to postmortems/incident-0003-searxng.md

homepage     up
open-webui   up
adguard-dns  up
searxng      healed
uptime-kuma  up
immich       up
```

And the generated postmortem:

> **# Incident #3 — searxng**
> **Detection:** DOWN — URLError: timed out (5005.2 ms)
> **Evidence:** Running: False · Exit code: 0 · OOM-killed: False · Restarts: 0
> **Diagnosis** (heuristic, 90%): container stopped cleanly (exit 0) — clean exit
> means manual stop, compose down, or host event; not an application fault.
> **Policy:** within cooldown and budget
> **Outcome:** ✅ Auto-healed: restarted; verify: HTTP 200

Bonus: the very first scan I ever ran flagged my DNS check as down and the LLM
diagnosis pointed at upstream timeouts in the AdGuard logs. The truth was better —
AdGuard only binds port 53 on the LAN interface, so my probe target was wrong.
The medic's first catch was my own config bug.

## Design decisions

**Heuristics gate the LLM, not the other way around.** OOM-killed, port conflicts,
crash loops, and clean stops are unambiguous — pattern matching answers instantly
and no model gets a vote. The LLM (any Ollama endpoint) is consulted only below
0.85 heuristic confidence, where reading 60 lines of logs actually helps. This came
from a live failure: an early version let the LLM see a *cleanly stopped* container,
and it talked itself into "investigate" from stale warnings in the log tail —
blocking a trivially correct restart.

**Healing requires passing a policy, not just a diagnosis.** Auto-restart without
guardrails is how a watchdog amplifies an outage. Every heal must clear:

1. the service opted in (`heal = "restart"` — my DNS is `"none"`; it deserves a human),
2. the diagnosis actually recommends a restart (port conflict → never),
3. a per-service cooldown since the last heal,
4. a daily heal budget — hitting it means the failure is recurring and a restart
   is a painkiller, not a cure.

**The medic must outlive its tools.** Evidence collection never raises; LLM failure
falls back to heuristics — because sometimes the container that died *is* Ollama.

**One incident, one diagnosis.** An open incident isn't re-diagnosed every scan;
recovery is detected and the incident closes itself. No alert storms.

## Quick start

Python 3.11+, Docker, and optionally an [Ollama](https://ollama.com) endpoint.

```bash
git clone https://github.com/AwpDemon/homelab-medic && cd homelab-medic
cp config.example.toml config.toml   # edit for your stack
python -m medic.main scan            # one pass, exit code 0 = all healthy
python -m medic.main watch           # continuous (touch KILL to stop cleanly)
python -m medic.main incidents       # recent incident history
```

Each service is a few lines of TOML:

```toml
[[service]]
name = "dashboard"
kind = "http"                 # http | tcp | container
target = "http://localhost:3001"
container = "homepage"        # docker name for evidence + healing
heal = "restart"              # or "none": observe, diagnose, notify only
cooldown_seconds = 300
max_heals_per_day = 5
```

## Tests

65 pytest tests: real local HTTP/TCP servers for the checks, mocked docker and
Ollama for the rest. No Docker needed to run the suite.

```bash
python -m pytest tests/
```

## Roadmap

- Re-evaluate open incidents on a backoff schedule (currently: one diagnosis per outage)
- `compose up -d` as a heal action for multi-container services
- Weekly digest: uptime per service, heal counts, top causes
