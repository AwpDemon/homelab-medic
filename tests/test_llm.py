import io
import json

import pytest

from medic import llm
from medic.checks import CheckResult
from medic.config import LLMConfig
from medic.evidence import Evidence
from medic.heuristics import Diagnosis

FALLBACK = Diagnosis(cause="heuristic guess", confidence=0.5,
                     recommended_action="restart", reasoning="", source="heuristic")
RESULT = CheckResult("web", "down", 12.0, "HTTP 502")
EV = Evidence(container="web", logs_tail="some logs", running=True)


def _cfg(**kw):
    base = dict(enabled=True, url="http://llm", model="test:1b", timeout_seconds=5)
    base.update(kw)
    return LLMConfig(**base)


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_response(monkeypatch, content: str):
    payload = json.dumps({"message": {"content": content}}).encode()

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(payload)
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)


class TestLLMDiagnose:
    def test_disabled_returns_fallback(self):
        d = llm.diagnose(_cfg(enabled=False), RESULT, EV, FALLBACK)
        assert d is FALLBACK

    def test_no_model_returns_fallback(self):
        d = llm.diagnose(_cfg(model=""), RESULT, EV, FALLBACK)
        assert d is FALLBACK

    def test_valid_response(self, monkeypatch):
        _patch_response(monkeypatch, json.dumps({
            "cause": "upstream 502 from wedged worker",
            "confidence": 0.75,
            "recommended_action": "restart",
            "reasoning": "logs show timeouts",
        }))
        d = llm.diagnose(_cfg(), RESULT, EV, FALLBACK)
        assert d.source == "llm"
        assert d.cause == "upstream 502 from wedged worker"
        assert d.confidence == 0.75

    def test_malformed_json_falls_back(self, monkeypatch):
        _patch_response(monkeypatch, "sorry, as an AI I cannot")
        d = llm.diagnose(_cfg(), RESULT, EV, FALLBACK)
        assert d is FALLBACK

    def test_invalid_action_coerced(self, monkeypatch):
        _patch_response(monkeypatch, json.dumps({
            "cause": "x", "confidence": 0.9,
            "recommended_action": "reboot the datacenter", "reasoning": "",
        }))
        d = llm.diagnose(_cfg(), RESULT, EV, FALLBACK)
        assert d.recommended_action == "investigate"

    def test_confidence_clamped(self, monkeypatch):
        _patch_response(monkeypatch, json.dumps({
            "cause": "x", "confidence": 7,
            "recommended_action": "restart", "reasoning": "",
        }))
        d = llm.diagnose(_cfg(), RESULT, EV, FALLBACK)
        assert d.confidence == 1.0

    def test_network_error_falls_back(self, monkeypatch):
        def boom(req, timeout=None):
            raise OSError("connection refused")
        monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
        d = llm.diagnose(_cfg(), RESULT, EV, FALLBACK)
        assert d is FALLBACK
