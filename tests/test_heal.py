from types import SimpleNamespace

from medic import heal
from medic.checks import CheckResult
from medic.config import Service

SVC = Service(name="web", kind="http", target="http://x", container="c1",
              heal="restart")


def _fake_run(returncode, stderr=""):
    def fake(cmd, **kw):
        assert cmd == ["docker", "restart", "c1"]
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return fake


class TestRestartAndVerify:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(heal.subprocess, "run", _fake_run(0))
        monkeypatch.setattr(heal.time, "sleep", lambda s: None)
        monkeypatch.setattr(heal.checks, "check",
                            lambda svc, timeout: CheckResult("web", "up", 5.0, "HTTP 200"))
        r = heal.restart_and_verify(SVC, grace_seconds=0)
        assert r.attempted and r.success
        assert "HTTP 200" in r.detail

    def test_restart_fails(self, monkeypatch):
        monkeypatch.setattr(heal.subprocess, "run", _fake_run(1, "no such container"))
        r = heal.restart_and_verify(SVC, grace_seconds=0)
        assert r.attempted and not r.success
        assert "no such container" in r.detail

    def test_restarted_but_still_down(self, monkeypatch):
        monkeypatch.setattr(heal.subprocess, "run", _fake_run(0))
        monkeypatch.setattr(heal.time, "sleep", lambda s: None)
        monkeypatch.setattr(heal.checks, "check",
                            lambda svc, timeout: CheckResult("web", "down", 5.0, "HTTP 502"))
        r = heal.restart_and_verify(SVC, grace_seconds=0)
        assert r.attempted and not r.success
        assert "still down" in r.detail
