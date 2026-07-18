import pandas as pd
import pytest

from tvcharts import stream as st
from tvcharts.app import _merge_live_bar
from tvcharts.providers import SampleProvider


def test_build_stream_names():
    names = st.build_stream_names(("BTCUSDT", "1d"),
                                  ["ETHUSDT", "BTCUSDT"])
    assert names[0] == "btcusdt@kline_1d"
    assert "ethusdt@miniTicker" in names
    assert "btcusdt@miniticker" not in names  # case: miniTicker preserved
    # de-duped
    assert len(names) == len(set(names))


def test_build_stream_names_empty():
    assert st.build_stream_names(None, []) == []
    # unknown interval yields no kline stream
    assert st.build_stream_names(("BTCUSDT", "7m"), []) == []


def test_parse_kline():
    frame = {"data": {"e": "kline", "s": "BTCUSDT",
                      "k": {"i": "1d", "t": 1_700_000_000_000,
                            "o": "1", "h": "3", "l": "0.5", "c": "2",
                            "v": "100", "x": False}}}
    kind, data = st.parse_message(frame)
    assert kind == "kline"
    assert data["symbol"] == "BTCUSDT"
    assert data["close"] == 2.0 and data["high"] == 3.0
    assert data["closed"] is False


def test_parse_ticker():
    frame = {"data": {"e": "24hrMiniTicker", "s": "ETHUSDT",
                      "c": "1850.5", "o": "1800"}}
    kind, data = st.parse_message(frame)
    assert kind == "ticker"
    assert data["last"] == 1850.5 and data["open"] == 1800.0


def test_parse_message_ignores_other():
    assert st.parse_message({"data": {"e": "depthUpdate"}}) is None


def test_merge_live_bar_replaces_last():
    df = SampleProvider().get_ohlcv("BTCUSDT", "1d", 50)
    last_ts = df.index[-1]
    st.MANAGER._bars[("BTCUSDT", "1d")] = {
        "symbol": "BTCUSDT", "interval": "1d",
        "start": int(last_ts.timestamp() * 1000),
        "open": 1.0, "high": 9.0, "low": 0.5, "close": 7.0,
        "volume": 42.0, "closed": False}
    merged = _merge_live_bar(df, "BTCUSDT", "1d")
    assert len(merged) == len(df)  # replaced, not appended
    assert merged["close"].iloc[-1] == 7.0
    assert merged["high"].iloc[-1] == 9.0
    st.MANAGER._bars.clear()


def test_merge_live_bar_appends_new():
    df = SampleProvider().get_ohlcv("BTCUSDT", "1d", 50)
    new_ts = df.index[-1] + pd.Timedelta(days=1)
    st.MANAGER._bars[("BTCUSDT", "1d")] = {
        "symbol": "BTCUSDT", "interval": "1d",
        "start": int(new_ts.timestamp() * 1000),
        "open": 5.0, "high": 6.0, "low": 4.0, "close": 5.5,
        "volume": 10.0, "closed": False}
    merged = _merge_live_bar(df, "BTCUSDT", "1d")
    assert len(merged) == len(df) + 1
    assert merged.index[-1] == new_ts
    assert merged["close"].iloc[-1] == 5.5
    st.MANAGER._bars.clear()


def test_merge_live_bar_no_stream_is_noop():
    df = SampleProvider().get_ohlcv("BTCUSDT", "1d", 20)
    st.MANAGER._bars.clear()
    merged = _merge_live_bar(df, "BTCUSDT", "1d")
    pd.testing.assert_frame_equal(merged, df)
