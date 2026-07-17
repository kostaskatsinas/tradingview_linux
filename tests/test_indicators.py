import numpy as np
import pandas as pd
import pytest

from tvcharts import indicators as ind


@pytest.fixture()
def df():
    idx = pd.date_range("2026-01-01", periods=100, freq="1D", tz="UTC")
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, 100))
    open_ = np.concatenate(([100], close[:-1]))
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = np.full(100, 1000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_sma_matches_manual(df):
    result = ind.sma(df, period=5)
    assert np.isnan(result.iloc[3])
    expected = df["close"].iloc[0:5].mean()
    assert result.iloc[4] == pytest.approx(expected)
    assert result.iloc[-1] == pytest.approx(df["close"].iloc[-5:].mean())


def test_ema_recurrence(df):
    period = 10
    result = ind.ema(df, period=period)
    alpha = 2.0 / (period + 1)
    # EMA obeys ema[t] = alpha*close[t] + (1-alpha)*ema[t-1]
    expected = alpha * df["close"].iloc[-1] + (1 - alpha) * result.iloc[-2]
    assert result.iloc[-1] == pytest.approx(expected)


def test_wma_weights(df):
    result = ind.wma(df, period=3)
    x = df["close"].iloc[-3:].values
    expected = (1 * x[0] + 2 * x[1] + 3 * x[2]) / 6
    assert result.iloc[-1] == pytest.approx(expected)


def test_bollinger_band_geometry(df):
    bands = ind.bollinger_bands(df, period=20, stddev=2.0)
    valid = bands["basis"].dropna().index
    assert len(valid) == len(df) - 19
    assert (bands["upper"][valid] >= bands["basis"][valid]).all()
    assert (bands["lower"][valid] <= bands["basis"][valid]).all()
    # upper - basis == basis - lower (symmetric around basis)
    np.testing.assert_allclose(
        (bands["upper"] - bands["basis"]).dropna(),
        (bands["basis"] - bands["lower"]).dropna(),
    )


def test_rsi_bounds_and_extremes(df):
    result = ind.rsi(df, period=14)
    valid = result.dropna()
    assert not valid.empty
    assert ((valid >= 0) & (valid <= 100)).all()

    # Monotonically rising prices -> RSI == 100
    up = df.copy()
    up["close"] = np.arange(1.0, 101.0)
    assert ind.rsi(up, period=14).iloc[-1] == pytest.approx(100.0)

    # Monotonically falling prices -> RSI == 0
    down = df.copy()
    down["close"] = np.arange(200.0, 100.0, -1.0)
    assert ind.rsi(down, period=14).iloc[-1] == pytest.approx(0.0)


def test_macd_is_ema_difference(df):
    result = ind.macd(df, fast=12, slow=26, signal=9)
    expected = ind.ema(df, 12) - ind.ema(df, 26)
    pd.testing.assert_series_equal(result["macd"], expected)
    hist = result["macd"] - result["signal"]
    pd.testing.assert_series_equal(result["histogram"], hist)


def test_stochastic_bounds(df):
    result = ind.stochastic(df)
    for series in result.values():
        valid = series.dropna()
        assert ((valid >= 0) & (valid <= 100)).all()


def test_atr_positive(df):
    result = ind.atr(df, period=14)
    assert (result.dropna() > 0).all()


def test_vwap_between_low_and_high_first_bar(df):
    result = ind.vwap(df)
    # First bar of each day: VWAP equals that bar's typical price
    typical = (df["high"] + df["low"] + df["close"]) / 3
    assert result.iloc[0] == pytest.approx(typical.iloc[0])


def test_registry_entries_run(df):
    for key, spec in ind.INDICATOR_REGISTRY.items():
        defaults = {name: default for name, (_, default) in spec["params"].items()}
        result = spec["func"](df, **defaults)
        series_list = result.values() if isinstance(result, dict) else [result]
        for series in series_list:
            assert isinstance(series, pd.Series), key
            assert len(series) == len(df), key
