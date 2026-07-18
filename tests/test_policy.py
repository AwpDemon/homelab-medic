from datetime import datetime, timedelta, timezone

from medic.config import Service
from medic.heuristics import Diagnosis
from medic.policy import decide

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _svc(**kw):
    base = dict(name="s", kind="http", target="http://x", container="c1",
                heal="restart", cooldown_seconds=300, max_heals_per_day=3)
    base.update(kw)
    return Service(**base)


def _diag(action="restart"):
    return Diagnosis(cause="x", confidence=0.8, recommended_action=action,
                     reasoning="", source="heuristic")


class TestDecide:
    def test_allowed_no_history(self):
        d = decide(_svc(), _diag(), [], now=NOW)
        assert d.allowed

    def test_not_opted_in(self):
        d = decide(_svc(heal="none"), _diag(), [], now=NOW)
        assert not d.allowed
        assert "opted in" in d.reason

    def test_diagnosis_says_investigate(self):
        d = decide(_svc(), _diag("investigate"), [], now=NOW)
        assert not d.allowed
        assert "investigate" in d.reason

    def test_cooldown_blocks(self):
        recent = [NOW - timedelta(seconds=60)]
        d = decide(_svc(cooldown_seconds=300), _diag(), recent, now=NOW)
        assert not d.allowed
        assert "cooldown" in d.reason

    def test_cooldown_expired(self):
        recent = [NOW - timedelta(seconds=301)]
        d = decide(_svc(cooldown_seconds=300), _diag(), recent, now=NOW)
        assert d.allowed

    def test_budget_exhausted(self):
        recent = [NOW - timedelta(hours=h) for h in (2, 5, 9)]
        d = decide(_svc(max_heals_per_day=3), _diag(), recent, now=NOW)
        assert not d.allowed
        assert "budget" in d.reason

    def test_budget_window_slides(self):
        # 3 heals but two are older than 24h — only 1 counts
        recent = [NOW - timedelta(hours=30), NOW - timedelta(hours=26),
                  NOW - timedelta(hours=2)]
        d = decide(_svc(max_heals_per_day=3, cooldown_seconds=300), _diag(),
                   recent, now=NOW)
        assert d.allowed

    def test_unordered_history(self):
        recent = [NOW - timedelta(seconds=10_000), NOW - timedelta(seconds=30)]
        d = decide(_svc(cooldown_seconds=300), _diag(), recent, now=NOW)
        assert not d.allowed  # most recent heal 30s ago triggers cooldown
