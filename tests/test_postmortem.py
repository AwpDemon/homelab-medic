from medic.checks import CheckResult
from medic.evidence import Evidence
from medic.heal import HealResult
from medic.heuristics import Diagnosis
from medic.postmortem import write

RESULT = CheckResult("web", "down", 42.0, "HTTP 502")
EV = Evidence(container="web", logs_tail="line1\nline2", running=False,
              exit_code=1, restart_count=0)
DIAG = Diagnosis(cause="wedged worker", confidence=0.8,
                 recommended_action="restart", reasoning="logs show timeouts",
                 source="llm")


class TestPostmortem:
    def test_healed(self, tmp_path):
        p = write(tmp_path, 7, RESULT, EV, DIAG, "within budget",
                  HealResult(True, True, "restarted; verify: HTTP 200"))
        text = p.read_text()
        assert p.name == "incident-0007-web.md"
        assert "wedged worker" in text
        assert "✅ Auto-healed" in text
        assert "line1" in text
        assert "80%" in text

    def test_heal_failed(self, tmp_path):
        p = write(tmp_path, 8, RESULT, EV, DIAG, "within budget",
                  HealResult(True, False, "still down"))
        assert "❌ Heal failed" in p.read_text()

    def test_no_heal(self, tmp_path):
        p = write(tmp_path, 9, RESULT, EV, DIAG, "cooldown active", None)
        text = p.read_text()
        assert "No heal attempted" in text
        assert "cooldown active" in text

    def test_empty_logs(self, tmp_path):
        ev = Evidence(container="web", logs_tail="")
        p = write(tmp_path, 10, RESULT, ev, DIAG, "x", None)
        assert "(no logs captured)" in p.read_text()

    def test_creates_directory(self, tmp_path):
        p = write(tmp_path / "nested" / "dir", 11, RESULT, EV, DIAG, "x", None)
        assert p.exists()
