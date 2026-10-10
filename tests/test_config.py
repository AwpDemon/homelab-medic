import pytest

from medic.config import ConfigError, load_config

VALID = """
[llm]
enabled = true
model = "test:1b"

[[service]]
name = "web"
kind = "http"
target = "http://localhost:8080"
container = "web"
heal = "restart"

[[service]]
name = "dns"
kind = "tcp"
target = "localhost:53"
"""


def _write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


class TestLoadConfig:
    def test_valid(self, tmp_path):
        cfg = load_config(_write(tmp_path, VALID))
        assert len(cfg.services) == 2
        assert cfg.services[0].heal == "restart"
        assert cfg.services[1].heal == "none"          # default
        assert cfg.llm.enabled and cfg.llm.model == "test:1b"

    def test_defaults(self, tmp_path):
        cfg = load_config(_write(tmp_path, VALID))
        assert cfg.interval_seconds == 120
        assert cfg.services[0].cooldown_seconds == 300
        assert cfg.services[0].max_heals_per_day == 5
        assert cfg.discord_webhook == ""

    def test_no_services(self, tmp_path):
        with pytest.raises(ConfigError, match="no .*service"):
            load_config(_write(tmp_path, "[defaults]\n"))

    def test_missing_name(self, tmp_path):
        bad = '[[service]]\nkind = "http"\ntarget = "x"\n'
        with pytest.raises(ConfigError, match="missing 'name'"):
            load_config(_write(tmp_path, bad))

    def test_duplicate_name(self, tmp_path):
        bad = VALID + '\n[[service]]\nname = "web"\nkind = "tcp"\ntarget = "x:1"\n'
        with pytest.raises(ConfigError, match="duplicate"):
            load_config(_write(tmp_path, bad))

    def test_bad_kind(self, tmp_path):
        bad = '[[service]]\nname = "a"\nkind = "icmp"\ntarget = "x"\n'
        with pytest.raises(ConfigError, match="kind"):
            load_config(_write(tmp_path, bad))

    def test_bad_heal(self, tmp_path):
        bad = '[[service]]\nname = "a"\nkind = "tcp"\ntarget = "x:1"\nheal = "reboot"\n'
        with pytest.raises(ConfigError, match="heal"):
            load_config(_write(tmp_path, bad))

    def test_restart_requires_container(self, tmp_path):
        bad = '[[service]]\nname = "a"\nkind = "http"\ntarget = "http://x"\nheal = "restart"\n'
        with pytest.raises(ConfigError, match="requires 'container'"):
            load_config(_write(tmp_path, bad))

    def test_missing_target(self, tmp_path):
        bad = '[[service]]\nname = "a"\nkind = "tcp"\n'
        with pytest.raises(ConfigError, match="target"):
            load_config(_write(tmp_path, bad))


class TestLLMConfig:
    def test_llm_defaults(self, tmp_path):
        cfg = load_config(_write(tmp_path, VALID))
        assert cfg.llm.api == "ollama"
        assert cfg.llm.prompt == "v2"
        assert cfg.llm.temperature == 0.2

    def test_openai_api_accepted(self, tmp_path):
        text = VALID.replace('model = "test:1b"', 'model = "m"\napi = "openai"\napi_key_env = "KEY"')
        cfg = load_config(_write(tmp_path, text))
        assert cfg.llm.api == "openai" and cfg.llm.api_key_env == "KEY"

    def test_bad_api_rejected(self, tmp_path):
        text = VALID.replace('model = "test:1b"', 'model = "m"\napi = "grpc"')
        with pytest.raises(ConfigError, match="llm.api"):
            load_config(_write(tmp_path, text))

    def test_bad_prompt_rejected(self, tmp_path):
        text = VALID.replace('model = "test:1b"', 'model = "m"\nprompt = "v9"')
        with pytest.raises(ConfigError, match="llm.prompt"):
            load_config(_write(tmp_path, text))

    def test_example_config_loads(self):
        from pathlib import Path
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
        assert cfg.llm.model == "qwen2.5:7b-instruct"
