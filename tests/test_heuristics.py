from medic.evidence import Evidence
from medic.heuristics import diagnose


def _ev(**kw):
    base = dict(container="c1", running=False, exit_code=1)
    base.update(kw)
    return Evidence(**base)


class TestDiagnose:
    def test_oom_killed_wins(self):
        d = diagnose(_ev(oom_killed=True, logs_tail="address already in use"))
        assert "OOM" in d.cause
        assert d.recommended_action == "restart"
        assert d.confidence >= 0.9

    def test_repeated_oom_is_not_restarted(self):
        d = diagnose(_ev(oom_killed=True, restart_count=4))
        assert d.recommended_action == "investigate"
        assert d.category == "oom"
        assert d.confidence >= 0.85  # deterministic: no LLM consultation

    def test_crash_loop_blocks_restart(self):
        d = diagnose(_ev(restart_count=5))
        assert d.cause == "crash loop"
        assert d.recommended_action == "investigate"

    def test_port_conflict(self):
        d = diagnose(_ev(logs_tail="Error: listen tcp :8080: address already in use"))
        assert "port conflict" in d.cause
        assert d.recommended_action == "investigate"

    def test_disk_full(self):
        d = diagnose(_ev(logs_tail="write /data: no space left on device"))
        assert d.cause == "disk full"
        assert d.recommended_action == "investigate"

    def test_dependency_down(self):
        d = diagnose(_ev(logs_tail="dial tcp: connection refused"))
        assert d.recommended_action == "restart"

    def test_clean_stop_high_confidence(self):
        d = diagnose(_ev(exit_code=0, logs_tail="shutting down"))
        assert "cleanly" in d.cause
        assert d.recommended_action == "restart"
        assert d.confidence >= 0.85  # must bypass LLM consultation

    def test_plain_exit(self):
        d = diagnose(_ev(exit_code=137, logs_tail="bye"))
        assert "exited" in d.cause
        assert "137" in d.cause
        assert d.recommended_action == "restart"

    def test_unhealthy_but_running(self):
        d = diagnose(_ev(running=True, exit_code=0, health_status="unhealthy"))
        assert "healthcheck" in d.cause
        assert d.recommended_action == "restart"

    def test_no_signature(self):
        d = diagnose(_ev(running=True, exit_code=0, logs_tail="all quiet"))
        assert d.recommended_action == "investigate"
        assert d.confidence <= 0.5

    def test_evidence_error(self):
        d = diagnose(Evidence(container="", error="no container associated"))
        assert d.recommended_action == "investigate"
        assert d.confidence <= 0.3

    def test_case_insensitive_patterns(self):
        d = diagnose(_ev(logs_tail="PANIC: runtime error"))
        assert "panic" in d.cause.lower()

    def test_source_is_heuristic(self):
        assert diagnose(_ev()).source == "heuristic"


class TestCategories:
    def test_every_branch_returns_a_known_category(self):
        from medic.heuristics import CATEGORIES
        samples = [
            Evidence(container="c", error="inspect failed"),
            _ev(oom_killed=True),
            _ev(restart_count=4),
            _ev(logs_tail="address already in use"),
            _ev(logs_tail="no space left on device"),
            _ev(logs_tail="permission denied"),
            _ev(logs_tail="connection refused"),
            _ev(logs_tail="no such host"),
            _ev(logs_tail="panic: boom"),
            _ev(exit_code=0),
            _ev(exit_code=1),
            _ev(running=True, exit_code=0, health_status="unhealthy"),
            _ev(running=True, exit_code=0),
        ]
        for ev in samples:
            assert diagnose(ev).category in CATEGORIES

    def test_clean_stop_is_external_stop(self):
        assert diagnose(_ev(exit_code=0)).category == "external_stop"

    def test_oom_category(self):
        assert diagnose(_ev(oom_killed=True)).category == "oom"
