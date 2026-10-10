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


class TestCategoryAndPrompts:
    def test_category_parsed(self, monkeypatch):
        _patch_response(monkeypatch, json.dumps({
            "cause": "x", "category": "disk_full", "confidence": 0.9,
            "recommended_action": "investigate", "reasoning": "",
        }))
        assert llm.diagnose(_cfg(), RESULT, EV, FALLBACK).category == "disk_full"

    def test_unknown_category_coerced(self, monkeypatch):
        _patch_response(monkeypatch, json.dumps({
            "cause": "x", "category": "cosmic rays", "confidence": 0.9,
            "recommended_action": "restart", "reasoning": "",
        }))
        assert llm.diagnose(_cfg(), RESULT, EV, FALLBACK).category == "unknown"

    def test_prompt_versions_selectable(self, monkeypatch):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(json.loads(req.data)["messages"][0]["content"])
            return _FakeResponse(json.dumps({"message": {"content": "{}"}}).encode())
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        llm.diagnose(_cfg(), RESULT, EV, FALLBACK, prompt="v1")
        llm.diagnose(_cfg(), RESULT, EV, FALLBACK, prompt="v2")
        assert seen == [llm.PROMPT_V1, llm.PROMPT_V2]

    def test_both_prompts_list_every_category(self):
        from medic.heuristics import CATEGORIES
        for prompt in (llm.PROMPT_V1, llm.PROMPT_V2):
            for c in CATEGORIES:
                assert f'"{c}"' in prompt


class TestOpenAICompatibleTransport:
    def test_request_shape_and_auth(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data)
            content = json.dumps({"cause": "c", "category": "oom", "confidence": 0.8,
                                  "recommended_action": "restart", "reasoning": "r"})
            return _FakeResponse(json.dumps(
                {"choices": [{"message": {"content": content}}]}).encode())
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setenv("MEDIC_TEST_KEY", "sk-test")

        d = llm.diagnose(_cfg(api="openai", api_key_env="MEDIC_TEST_KEY"), RESULT, EV,
                         FALLBACK, seed=7)
        assert captured["url"] == "http://llm/v1/chat/completions"
        assert captured["auth"] == "Bearer sk-test"
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["seed"] == 7
        assert d.source == "llm" and d.category == "oom"

    def test_no_key_sends_no_auth_header(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.get_header("Authorization")
            return _FakeResponse(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        llm.diagnose(_cfg(api="openai"), RESULT, EV, FALLBACK)
        assert captured["auth"] is None

    def test_ollama_native_shape(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _FakeResponse(json.dumps({"message": {"content": "{}"}}).encode())
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        llm.diagnose(_cfg(), RESULT, EV, FALLBACK, seed=3)
        assert captured["url"] == "http://llm/api/chat"
        assert captured["body"]["format"] == "json"
        assert captured["body"]["options"]["seed"] == 3
