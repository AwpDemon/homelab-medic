"""Sanity checks on the labeled eval set and the eval metrics (no LLM needed)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import run_eval  # noqa: E402
from medic.heuristics import CATEGORIES  # noqa: E402

CASES_PATH = ROOT / "evals" / "cases.jsonl"
CASES = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_case_schema():
    assert len(CASES) == 50
    assert len({c["id"] for c in CASES}) == len(CASES)
    for c in CASES:
        assert c["split"] in ("dev", "test")
        assert c["origin"] in ("real-anonymized", "synthetic")
        assert c["label"]["action"] in ("restart", "investigate", "none")
        assert c["label"]["category"] in CATEGORIES
        assert c["note"]


def test_splits_are_disjoint_and_sized():
    dev = run_eval.load_cases(CASES_PATH, "dev")
    test = run_eval.load_cases(CASES_PATH, "test")
    assert len(dev) == 20 and len(test) == 30
    assert not ({c["id"] for c in dev} & {c["id"] for c in test})


def test_no_private_lan_addresses_in_cases():
    # Only Docker-internal (172.18.x) and loopback addresses may appear.
    text = CASES_PATH.read_text(encoding="utf-8")
    for prefix in ("192.168.", "10.0.", "100.64.", "100.9"):
        assert prefix not in text


def _row(label, pred, lc="oom", pc="oom", llm=False):
    return {"label_action": label, "pred_action": pred, "label_category": lc,
            "pred_category": pc, "llm_called": llm, "llm_fallback": False, "latency_ms": 1.0}


def test_metrics_definitions():
    rows = [
        _row("restart", "restart"),                          # correct heal
        _row("restart", "investigate", llm=True),            # missed heal
        _row("investigate", "restart", pc="app_crash"),      # false heal, wrong category
        _row("investigate", "investigate"),                  # correct hold
    ]
    m = run_eval.metrics(rows)
    assert m["action_accuracy"] == 0.5
    assert m["category_accuracy"] == 0.75
    assert m["false_heal_rate"] == 0.5 and m["false_heals"] == 1
    assert m["missed_heal_rate"] == 0.5
    assert m["llm_invocation_rate"] == 0.25


def test_heuristics_mode_runs_without_llm():
    rows = [run_eval.diagnose_case(c, "heuristics", None, "v2", None, 0.85) for c in CASES]
    assert all(not r["llm_called"] for r in rows)
    assert all(r["pred_category"] in CATEGORIES for r in rows)
