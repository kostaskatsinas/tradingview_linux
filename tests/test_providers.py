import pandas as pd
import pytest

from tvcharts.providers import (
    INTERVALS,
    OHLCV_COLUMNS,
    ProviderError,
    SampleProvider,
    get_provider,
)


def test_sample_provider_shape():
    df = SampleProvider().get_ohlcv("DEMO", "1d", limit=200)
    assert list(df.columns) == OHLCV_COLUMNS
    assert len(df) == 200
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()


def test_sample_provider_deterministic():
    a = SampleProvider()._fetch("BTCUSDT", "1h", 100)
    b = SampleProvider()._fetch("BTCUSDT", "1h", 100)
    pd.testing.assert_frame_equal(a, b)


def test_sample_provider_interval_spacing():
    for interval, seconds in INTERVALS.items():
        df = SampleProvider()._fetch("X", interval, 10)
        deltas = df.index.to_series().diff().dropna().dt.total_seconds()
        assert (deltas == seconds).all(), interval


def test_sample_provider_rejects_bad_interval():
    with pytest.raises(ProviderError):
        SampleProvider().get_ohlcv("DEMO", "7m", 10)


def test_get_provider_registry_and_cache():
    p1 = get_provider("sample")
    p2 = get_provider("sample")
    assert p1 is p2
    with pytest.raises(ProviderError):
        get_provider("nope")


def test_cache_returns_same_frame():
    provider = SampleProvider()
    a = provider.get_ohlcv("DEMO", "1d", 50)
    b = provider.get_ohlcv("DEMO", "1d", 50)
    assert a is b  # served from TTL cache
