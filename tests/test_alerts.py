import pytest

from tvcharts.alerts import AlertEngine, AlertStore
from tvcharts.providers import get_provider


@pytest.fixture()
def store(tmp_path):
    return AlertStore(path=tmp_path / "alerts.json")


def test_store_round_trip(store):
    a = store.add("BTCUSDT", "price_above", 100.0)
    b = store.add("ETHUSDT", "dcai_signal")
    loaded = store.load()
    assert {x["id"] for x in loaded} == {a["id"], b["id"]}
    assert loaded[0]["symbol"] == "BTCUSDT"
    assert loaded[1]["level"] is None
    store.remove(a["id"])
    assert [x["id"] for x in store.load()] == [b["id"]]


def test_add_rejects_unknown_condition(store):
    with pytest.raises(ValueError):
        store.add("BTCUSDT", "nonsense", 1.0)


def _last_close(symbol):
    df = get_provider("sample").get_ohlcv(symbol, "1d", 2)
    return float(df["close"].iloc[-1])


def test_price_alert_fires_and_is_one_shot(store):
    engine = AlertEngine(store)
    last = _last_close("DEMO")
    alert = store.add("DEMO", "price_above", last - 1, provider="sample")
    fired = engine.run_once()
    assert any(n["symbol"] == "DEMO" for n in fired)
    assert engine.notifications  # captured in the deque
    # one-shot: marked fired, so a second round produces nothing
    assert store.load()[0]["last_fired"] is not None
    assert engine.run_once() == []


def test_price_below_not_triggered_when_above(store):
    engine = AlertEngine(store)
    last = _last_close("DEMO")
    store.add("DEMO", "price_below", last - 1000, provider="sample")
    assert engine.run_once() == []


def test_dcai_signal_alert(store, monkeypatch):
    import pandas as pd

    from tvcharts.strategies import dcai

    df = get_provider("sample").get_ohlcv("DEMO", "1d", 300)
    # Force a signal on the final bar so the alert must fire.
    monkeypatch.setattr(dcai, "get_signals",
                        lambda *a, **k: [{"time": df.index[-1],
                                          "text": "FEAR €100"}])
    monkeypatch.setattr("tvcharts.alerts.get_provider",
                        lambda name: type("P", (), {
                            "get_ohlcv": staticmethod(lambda *a, **k: df)})())
    engine = AlertEngine(store)
    store.add("DEMO", "dcai_signal", provider="sample")
    fired = engine.run_once()
    assert fired and "DCAi" in fired[0]["message"]
    # deduped for the same bar
    assert engine.run_once() == []


def test_evaluate_handles_missing_data(store):
    engine = AlertEngine(store)
    # A symbol the sample provider still serves, but a price_below far away:
    store.add("DEMO", "price_below", -1.0, provider="sample")
    assert engine.run_once() == []
