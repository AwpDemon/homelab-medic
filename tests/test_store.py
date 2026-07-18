import pytest

from medic.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "medic.db")
    yield s
    s.close()


def _open(store, service="web"):
    return store.open_incident(service, "HTTP 502", "wedged", 0.8,
                               "restart", "because", "llm")


class TestStore:
    def test_open_and_fetch(self, store):
        iid = _open(store)
        inc = store.incident(iid)
        assert inc["service"] == "web"
        assert inc["cause"] == "wedged"
        assert inc["resolved_at"] is None

    def test_has_open_incident(self, store):
        assert not store.has_open_incident("web")
        _open(store)
        assert store.has_open_incident("web")
        assert not store.has_open_incident("other")

    def test_resolve_open(self, store):
        _open(store)
        assert store.resolve_open_incidents("web") == 1
        assert not store.has_open_incident("web")
        assert store.resolve_open_incidents("web") == 0  # idempotent

    def test_successful_heal_resolves(self, store):
        iid = _open(store)
        store.record_heal(iid, "web", True, "restarted ok")
        inc = store.incident(iid)
        assert inc["heal_attempted"] == 1
        assert inc["heal_success"] == 1
        assert inc["resolved_at"] is not None

    def test_failed_heal_stays_open(self, store):
        iid = _open(store)
        store.record_heal(iid, "web", False, "still down")
        inc = store.incident(iid)
        assert inc["heal_success"] == 0
        assert inc["resolved_at"] is None
        assert store.has_open_incident("web")

    def test_recent_heal_times(self, store):
        iid = _open(store)
        store.record_heal(iid, "web", True, "ok")
        times = store.recent_heal_times("web")
        assert len(times) == 1
        assert store.recent_heal_times("other") == []

    def test_recent_incidents_order(self, store):
        first = _open(store, "a")
        second = _open(store, "b")
        recents = store.recent_incidents()
        assert recents[0]["id"] == second
        assert recents[1]["id"] == first

    def test_missing_incident(self, store):
        assert store.incident(999) == {}
