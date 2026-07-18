import http.server
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from medic import checks
from medic.config import Service


@pytest.fixture
def http_server():
    """Local HTTP server whose status code is set per-request via path: /<code>."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = int(self.path.lstrip("/") or 200)
            self.send_response(code)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _svc(**kw):
    base = dict(name="s", kind="http", target="http://x")
    base.update(kw)
    return Service(**base)


class TestHttp:
    def test_200_up(self, http_server):
        r = checks.check(_svc(target=f"{http_server}/200"))
        assert r.status == checks.UP
        assert "200" in r.detail

    def test_404_up_by_default(self, http_server):
        # 4xx = app responded; only 5xx counts as down by default
        r = checks.check(_svc(target=f"{http_server}/404"))
        assert r.status == checks.UP

    def test_503_down(self, http_server):
        r = checks.check(_svc(target=f"{http_server}/503"))
        assert r.status == checks.DOWN

    def test_expect_status(self, http_server):
        r = checks.check(_svc(target=f"{http_server}/404", expect_status=200))
        assert r.status == checks.DOWN

    def test_connection_refused(self):
        r = checks.check(_svc(target="http://127.0.0.1:1"), timeout=2)
        assert r.status == checks.DOWN

    def test_latency_recorded(self, http_server):
        r = checks.check(_svc(target=f"{http_server}/200"))
        assert r.latency_ms >= 0


class TestTcp:
    def test_open_port(self):
        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        r = checks.check(_svc(kind="tcp", target=f"127.0.0.1:{port}"))
        srv.close()
        assert r.status == checks.UP

    def test_closed_port(self):
        r = checks.check(_svc(kind="tcp", target="127.0.0.1:1"), timeout=2)
        assert r.status == checks.DOWN


class TestContainer:
    def _fake_run(self, returncode, state=None, stderr=""):
        def fake(cmd, **kw):
            return SimpleNamespace(
                returncode=returncode,
                stdout=json.dumps(state or {}),
                stderr=stderr,
            )
        return fake

    def test_running_healthy(self, monkeypatch):
        monkeypatch.setattr(checks.subprocess, "run", self._fake_run(
            0, {"Running": True, "Health": {"Status": "healthy"}}))
        r = checks.check(_svc(kind="container", target="c1", container="c1"))
        assert r.status == checks.UP

    def test_running_no_healthcheck(self, monkeypatch):
        monkeypatch.setattr(checks.subprocess, "run", self._fake_run(0, {"Running": True}))
        r = checks.check(_svc(kind="container", target="c1"))
        assert r.status == checks.UP

    def test_unhealthy(self, monkeypatch):
        monkeypatch.setattr(checks.subprocess, "run", self._fake_run(
            0, {"Running": True, "Health": {"Status": "unhealthy"}}))
        r = checks.check(_svc(kind="container", target="c1"))
        assert r.status == checks.DOWN

    def test_exited(self, monkeypatch):
        monkeypatch.setattr(checks.subprocess, "run", self._fake_run(
            0, {"Running": False, "ExitCode": 137}))
        r = checks.check(_svc(kind="container", target="c1"))
        assert r.status == checks.DOWN
        assert "137" in r.detail

    def test_not_found(self, monkeypatch):
        monkeypatch.setattr(checks.subprocess, "run",
                            self._fake_run(1, stderr="No such object: c1"))
        r = checks.check(_svc(kind="container", target="c1"))
        assert r.status == checks.DOWN
