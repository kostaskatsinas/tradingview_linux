import numpy as np
import pandas as pd
import pytest

from tvcharts import indicators as ind
from tvcharts.app import _merge_drawings
from tvcharts.providers import SampleProvider


@pytest.fixture(scope="module")
def df():
    return SampleProvider().get_ohlcv("BTCUSDT", "1d", 300)


def test_heikin_ashi_invariants(df):
    ha = ind.heikin_ashi(df)
    expected_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    pd.testing.assert_series_equal(ha["close"], expected_close,
                                   check_names=False)
    # ha_open recursion: mean of previous ha_open and ha_close
    t = 50
    assert ha["open"].iloc[t] == pytest.approx(
        (ha["open"].iloc[t - 1] + ha["close"].iloc[t - 1]) / 2)
    assert (ha["high"] >= ha[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (ha["low"] <= ha[["open", "close"]].min(axis=1) + 1e-9).all()
    pd.testing.assert_series_equal(ha["volume"], df["volume"])


def test_ichimoku_matches_donchian(df):
    result = ind.ichimoku(df)
    t = 100
    expected_tenkan = (df["low"].iloc[t - 8:t + 1].min()
                       + df["high"].iloc[t - 8:t + 1].max()) / 2
    assert result["tenkan"].iloc[t] == pytest.approx(expected_tenkan)
    # displaced spans: value at t comes from bar t-26
    tenkan_past = (df["low"].iloc[t - 26 - 8:t - 26 + 1].min()
                   + df["high"].iloc[t - 26 - 8:t - 26 + 1].max()) / 2
    kijun_past = (df["low"].iloc[t - 26 - 25:t - 26 + 1].min()
                  + df["high"].iloc[t - 26 - 25:t - 26 + 1].max()) / 2
    assert result["span_a"].iloc[t] == pytest.approx(
        (tenkan_past + kijun_past) / 2)


def test_supertrend_tracks_trend():
    idx = pd.date_range("2026-01-01", periods=120, freq="1D", tz="UTC")
    up = np.linspace(100, 220, 120)
    df = pd.DataFrame({"open": up, "high": up + 1, "low": up - 1,
                       "close": up, "volume": np.full(120, 1.0)}, index=idx)
    st = ind.supertrend(df)
    valid = st.dropna()
    # steady uptrend: line stays below price
    assert (valid < df["close"].loc[valid.index]).all()


def test_adx_bounds_and_obv(df):
    a = ind.adx(df)
    valid = a.dropna()
    assert ((valid >= 0) & (valid <= 100)).all()

    up = df.copy()
    up["close"] = np.arange(1.0, len(df) + 1)
    o = ind.obv(up)
    assert (o.diff().dropna() >= 0).all()  # all-up closes: OBV never falls


def test_merge_drawings_full_and_partial():
    shape = {"type": "line", "x0": 1, "x1": 2, "y0": 10, "y1": 20,
             "editable": True}
    indicator_shape = {"type": "line", "y0": 70, "y1": 70}  # not editable
    # full list (draw/erase): only editable shapes are kept
    out = _merge_drawings({"shapes": [indicator_shape, shape]}, {}, "BTC", None)
    assert out == {"BTC": [shape]}
    # partial edit: applied onto the figure's shape list by index
    figure = {"layout": {"shapes": [indicator_shape, shape]}}
    out = _merge_drawings({"shapes[1].y0": 15}, {"BTC": [shape]}, "BTC", figure)
    assert out["BTC"][0]["y0"] == 15
    # unrelated relayout (zoom): no update
    assert _merge_drawings({"xaxis.range[0]": 5}, {}, "BTC", figure) is None
    assert _merge_drawings({}, {}, "BTC", None) is None
