# Eval set: how the cases were made and labeled

`cases.jsonl` holds **50 labeled incidents**: a 20-case dev split and a 30-case test split.

## Where the cases come from

- **3 are real** (`origin: "real-anonymized"`): the three incidents medic recorded on my homelab
  (d01, d02, t01). The log tails are trimmed, and the service names are generic.
- **47 are synthetic** (`origin: "synthetic"`). I wrote them to cover common Docker failure modes
  (OOM kills, crash loops, port conflicts, full disks, permissions, bad config, dependency races,
  wedged processes, operator stops, probe and proxy problems). Each is modeled on the log format of the
  named software (Postgres, Redis, nginx, Node, Go, Python, Java, Mongo, MinIO, gunicorn, ...). Several are
  **adversarial on purpose**: old error lines in the log tail of a cleanly stopped container, the word
  "fatal" in a config error, "connection refused" in a container that later recovered, and an OOM kill
  that the OOMKilled flag missed.

Because most cases are synthetic, the results measure how well medic applies the rubric below to
realistic evidence. They are **not** a failure-rate estimate for any real fleet.

## Labels

Each case has an **action** (what the medic should do) and a **category** (root cause, one of the 13
in `medic/heuristics.py: CATEGORIES`).

**action = `restart`** when a restart is expected to restore service *and* will not hide a persistent fault:
- the container was stopped by an operator or the host (exit 0, 143 SIGTERM, 137 from a stop timeout)
- a one-off crash or OOM kill (fewer than 3 restarts)
- it exited once at startup because a dependency was not up yet, and the dependency is up now
- a wedged process (healthcheck failing) with no persistent cause in the logs

**action = `investigate`** when a restart cannot fix the cause or would loop:
- port conflict, disk full, permission or ownership errors
- bad or missing config, env var, credentials, pending migration, expired certificate
- crash loop (3 or more restarts), *including repeated OOM kills*
- the container is healthy but the check fails (probe, reverse proxy or network path)
- the container is still running and retrying a dependency or DNS lookup: the dependency is the patient
- evidence collection failed

**action = `none`** is reserved for "nothing is wrong". No case in this set uses it.

**Category precedence:** the root cause wins (e.g. `oom` for t03 even though it is looping). A repeating
crash with no clearer cause is `crash_loop`.

## Splits and process

1. I wrote and labeled all 50 cases before running any model.
2. I ran prompt v1 on the **dev** split only and read its errors.
3. I finalized prompt v2 against the dev errors and froze it.
4. Only then did I run v1, v2, and the LLM-only ablation on the **test** split, once each, with 3 seeds.

The README reports **test-split** numbers. Dev-split numbers are in `results/` for transparency.

**Known bias:** I wrote the cases, the labels, and both the heuristics and the prompts. v2's rules restate
this rubric. That makes v2 better at following *my* policy, which is the point, but a second labeler and
more real incidents would make the numbers more trustworthy (see README, limitations).
