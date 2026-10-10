# Prompt log

The diagnosis prompts live in `medic/llm.py` (`PROMPT_V1`, `PROMPT_V2`). The production default is `v2`.
Model for all runs: `qwen2.5:7b-instruct` via Ollama, temperature 0.2, seeds 1-3. Process and labeling
rules: [evals/LABELING.md](evals/LABELING.md).

## v1: the original prompt (+ a category field)

The prompt medic shipped with: return JSON with cause, confidence, action and reasoning; "prefer
investigate when a restart clearly cannot fix the cause (port conflicts, disk full, crash loops)". The only
change for the eval was a `category` field, so root causes can be scored.

**Dev split, hybrid mode (3 seeds):** action accuracy 61.7% (60-65%), category 81.7%, false heals 0%,
**missed heals 76.7%**.

What went wrong (read from the dev errors only):
- **Too cautious.** It answered "investigate" for things a restart fixes: a one-off Go panic (d13), a
  startup race with the database (d08), DNS failure before redis existed (d14), wedged workers (d09, d20).
- **Trusted old log lines over container state.** For a container that stopped cleanly with exit 0
  (d10), a two-week-old `level=fatal` plugin line made it say "config_error / investigate".
- **Used "none" for a stopped service** (d11, SIGTERM).
- Called a healthy container behind a failing proxy "config_error" instead of "network_path" (d15).

## v2: decision rules

v2 keeps the JSON schema and adds six ordered rules, which restate the labeling rubric:
1. Container state beats log text (exit 0/143, or 137 without OOM, and < 3 restarts → `external_stop`, restart).
2. 3+ restarts → crash loop → investigate.
3. Restarts cannot fix config, credential, migration, certificate, permission, disk or port problems, or a
   healthy container behind a failing probe (`network_path`).
4. A container still running and retrying a dependency → investigate; one that exited once at startup → restart.
5. One-off crashes, single OOM kills, and wedged processes → restart.
6. "none" only if nothing is wrong.

**Dev split, hybrid mode:** action accuracy 71.7% (70-75%), category 88.3%, false heals 3.3%, missed heals 53.3%.

v2 was then frozen. v1 and v2 were each run **once** on the 30-case test split (results in the README).

## What v2 still gets wrong (test split)

The 7B model is still more cautious than the rubric. It withholds restarts for a single Node heap OOM (t07),
a one-off `KeyError` crash (t14), a deadlocked scanner (t18), and a startup race with redis (t06). It also
picks plausible but wrong categories: "permission" for a failed password (t22), "dependency_unreachable"
for a container that recovered on its own (t09). Rules in the prompt help, but they do not fully override
the model's prior that errors in logs mean "a human should look". A larger model, or few-shot examples,
are the next things to try. Both should be judged on the test split only after being frozen on dev.

## Post-hoc heuristic fix

The only false heal in hybrid mode (t03, an ML container OOM-killed 4 times in a row) never reached the
LLM. The heuristic checked `OOMKilled` before `RestartCount >= 3` and answered "restart" with 0.95
confidence. That contradicts the rubric, which was written before any run ("repeated OOM kills →
investigate"), so I fixed the rule order. Because the error was found on the test split, the post-fix
numbers are reported separately and labeled post-hoc in the README.
