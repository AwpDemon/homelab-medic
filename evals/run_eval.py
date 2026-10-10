"""Evaluate medic's diagnosis component on the labeled incident set.

Modes:
  heuristics  deterministic rules only (no LLM)
  hybrid      production behavior: heuristics, then the LLM only when the
              heuristic confidence is below --threshold (default 0.85)
  llm         ablation: always ask the LLM (heuristic guess still in the prompt)

Examples:
  python evals/run_eval.py --mode heuristics --split all
  python evals/run_eval.py --mode hybrid --prompt v2 --split test --seeds 1 2 3 \
      --url http://127.0.0.1:11434 --model qwen2.5:7b-instruct
  # any OpenAI-compatible server instead of Ollama:
  python evals/run_eval.py --mode llm --api openai --url https://api.example.com \
      --model some-model --api-key-env MY_API_KEY

Writes a JSON report (metrics per seed, mean/min/max, per-case predictions) to
--out (default evals/results/<mode>-<prompt>-<split>.json). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medic import heuristics, llm  # noqa: E402
from medic.checks import CheckResult  # noqa: E402
from medic.config import LLMConfig  # noqa: E402
from medic.evidence import Evidence  # noqa: E402

PRODUCTION_THRESHOLD = 0.85  # medic/main.py consults the LLM below this


def load_cases(path: Path, split: str) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if split == "all" or c["split"] == split]


def to_inputs(case: dict) -> tuple[CheckResult, Evidence]:
    e = case["evidence"]
    ev = Evidence(container=e["container"], logs_tail=e["logs_tail"], running=e["running"],
                  exit_code=e["exit_code"], oom_killed=e["oom_killed"],
                  restart_count=e["restart_count"], health_status=e["health_status"],
                  error=e.get("error", ""))
    result = CheckResult(case["service"], "down", 0.0, case["check_detail"])
    return result, ev


def diagnose_case(case: dict, mode: str, cfg: LLMConfig | None, prompt: str,
                  seed: int | None, threshold: float) -> dict:
    result, ev = to_inputs(case)
    t0 = time.perf_counter()
    diag = heuristics.diagnose(ev)
    llm_called = False
    if mode == "llm" or (mode == "hybrid" and diag.confidence < threshold):
        llm_called = True
        diag = llm.diagnose(cfg, result, ev, fallback=diag, prompt=prompt, seed=seed)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "id": case["id"],
        "label_action": case["label"]["action"],
        "label_category": case["label"]["category"],
        "pred_action": diag.recommended_action,
        "pred_category": diag.category,
        "confidence": round(diag.confidence, 3),
        "source": diag.source,
        "llm_called": llm_called,
        # the LLM was asked but its answer was unusable, so the heuristic stood
        "llm_fallback": llm_called and diag.source != "llm",
        "cause": diag.cause,
        "latency_ms": round(elapsed_ms, 2),
    }


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    inv = [r for r in rows if r["label_action"] == "investigate"]
    rst = [r for r in rows if r["label_action"] == "restart"]
    llm_lat = [r["latency_ms"] for r in rows if r["llm_called"]]
    return {
        "n": n,
        "action_accuracy": sum(r["pred_action"] == r["label_action"] for r in rows) / n,
        "category_accuracy": sum(r["pred_category"] == r["label_category"] for r in rows) / n,
        # a restart recommended where the label says a restart cannot fix it
        "false_heal_rate": sum(r["pred_action"] == "restart" for r in inv) / len(inv) if inv else 0.0,
        "false_heals": sum(r["pred_action"] == "restart" for r in inv),
        # a restart withheld where the label says it would have fixed the outage
        "missed_heal_rate": sum(r["pred_action"] != "restart" for r in rst) / len(rst) if rst else 0.0,
        "llm_invocation_rate": sum(r["llm_called"] for r in rows) / n,
        "llm_fallbacks": sum(r["llm_fallback"] for r in rows),
        "latency_p50_ms": pct([r["latency_ms"] for r in rows], 0.5),
        "latency_p95_ms": pct([r["latency_ms"] for r in rows], 0.95),
        "llm_call_p50_ms": pct(llm_lat, 0.5),
    }


def summarize(per_seed: list[dict]) -> dict:
    out = {}
    for key in per_seed[0]:
        vals = [m[key] for m in per_seed]
        out[key] = {"mean": statistics.fmean(vals), "min": min(vals), "max": max(vals)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    ap.add_argument("--mode", choices=["heuristics", "hybrid", "llm"], required=True)
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--threshold", type=float, default=PRODUCTION_THRESHOLD)
    ap.add_argument("--api", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--api-key-env", default="")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="")
    ap.add_argument("--show-errors", action="store_true")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases), args.split)
    uses_llm = args.mode != "heuristics"
    cfg = LLMConfig(enabled=True, url=args.url.rstrip("/"), model=args.model,
                    timeout_seconds=args.timeout, api=args.api,
                    api_key_env=args.api_key_env, temperature=args.temperature,
                    prompt=args.prompt) if uses_llm else None
    seeds = args.seeds if uses_llm else [0]  # heuristics are deterministic

    per_seed, all_rows = [], {}
    for seed in seeds:
        rows = [diagnose_case(c, args.mode, cfg, args.prompt, seed if uses_llm else None, args.threshold)
                for c in cases]
        per_seed.append(metrics(rows))
        all_rows[str(seed)] = rows
        m = per_seed[-1]
        print(f"seed {seed}: action {m['action_accuracy']:.1%}  category {m['category_accuracy']:.1%}  "
              f"false-heal {m['false_heal_rate']:.1%} ({m['false_heals']})  "
              f"missed-heal {m['missed_heal_rate']:.1%}  llm-calls {m['llm_invocation_rate']:.0%}  "
              f"fallbacks {m['llm_fallbacks']}  p50 {m['latency_p50_ms']:.1f} ms", flush=True)
        if args.show_errors:
            for r in rows:
                if r["pred_action"] != r["label_action"] or r["pred_category"] != r["label_category"]:
                    print(f"   {r['id']}: label {r['label_action']}/{r['label_category']}  "
                          f"pred {r['pred_action']}/{r['pred_category']} ({r['source']}) - {r['cause'][:90]}")

    report = {
        "mode": args.mode,
        "prompt": args.prompt if uses_llm else None,
        "split": args.split,
        "model": args.model if uses_llm else None,
        "api": args.api if uses_llm else None,
        "threshold": args.threshold if args.mode == "hybrid" else None,
        "temperature": args.temperature if uses_llm else None,
        "seeds": seeds,
        "per_seed": per_seed,
        "summary": summarize(per_seed),
        "predictions": all_rows,
    }
    out = Path(args.out) if args.out else (
        ROOT / "evals" / "results" / f"{args.mode}-{args.prompt if uses_llm else 'na'}-{args.split}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
