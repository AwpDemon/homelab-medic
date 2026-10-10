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

## Evaluation: does the diagnosis make the right call?

Measured on a labeled set of **50 incidents** ([evals/](evals/)): 3 real incidents from my homelab,
anonymized, and 47 synthetic ones modeled on real software logs, several of them adversarial. Labeling rules
are in [evals/LABELING.md](evals/LABELING.md). The prompt was tuned on a 20-case **dev** split only. The
numbers below are from the held-out 30-case **test** split, using `qwen2.5:7b-instruct` on Ollama,
temperature 0.2, mean of 3 seeds (range in brackets).

| Diagnosis mode | Action accuracy | Root-cause accuracy | False-heal rate | Missed-heal rate | LLM calls | p50 latency |
|---|---|---|---|---|---|---|
| Heuristics only | 73.3% | 73.3% | **44.4%** (8 of 18) | 0.0% | 0% | <1 ms |
| Hybrid, prompt v1 (original) | 70.0% | 80.0% [76.7-83.3] | 5.6% | 66.7% | 70% | 5.3 s |
| **Hybrid, prompt v2 (production)** | **80.0%** | **83.3%** | **5.6%** | 41.7% | 70% | 5.3 s |
| LLM on every incident, v2 (ablation) | 81.1% [76.7-86.7] | 74.4% [73.3-76.7] | 0.0% | 47.2% [33.3-58.3] | 100% | 6.0 s |

- **False heal** = recommends a restart when the label says a restart cannot fix it (it masks a disk-full,
  a bad config or a crash loop). **Missed heal** = withholds a restart that would have fixed the outage.
  Healing still has to pass the policy gate (opt-in, cooldown, daily budget), so a false heal is bounded,
  but it is the more dangerous error.
- **What the table says:** heuristics alone never miss a heal but restart into 44% of the problems a restart
  cannot fix. Adding the LLM for the ambiguous 70% of cases cuts false heals to about 6%. The trade-off is
  that the 7B model stays too cautious and withholds about 42% of the restarts that would have worked. Prompt v2
  (rules about container state vs stale log lines) moved action accuracy from 70% to 80% on the held-out split.
  The before/after and the remaining failure modes are in [PROMPTS.md](PROMPTS.md).
- **Limits:** 47 of 50 cases are synthetic and written by me (as were the labels and the prompts); 30 test cases
  give wide intervals. A second labeler and more real incidents are the next step.

Reproduce (the LLM rows need an Ollama or OpenAI-compatible endpoint):

```bash
python evals/run_eval.py --mode heuristics --split test
python evals/run_eval.py --mode hybrid --prompt v2 --split test --seeds 1 2 3     --url http://127.0.0.1:11434 --model qwen2.5:7b-instruct
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

The LLM can be any Ollama model (`api = "ollama"`) or any OpenAI-compatible endpoint (`api = "openai"`,
with an API key read from the env var named in `api_key_env`).

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

85 pytest tests: real local HTTP/TCP servers for the checks, mocked docker and
LLM endpoints for the rest, plus checks on the eval set and metric definitions.
No Docker or LLM needed to run the suite. CI also runs the deterministic
(heuristics-only) eval over all 50 cases.

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

## Roadmap

- Re-evaluate open incidents on a backoff schedule (currently: one diagnosis per outage)
- `compose up -d` as a heal action for multi-container services
- Weekly digest: uptime per service, heal counts, top causes
