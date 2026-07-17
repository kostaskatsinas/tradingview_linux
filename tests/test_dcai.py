import numpy as np
import pytest

from tvcharts.providers import SampleProvider
from tvcharts.strategies import STRATEGIES, dcai, get_strategy_params


@pytest.fixture(scope="module")
def df():
    return SampleProvider().get_ohlcv("BTCUSDT", "1d", 600)


def test_registry_contains_dcai():
    assert "dcai" in STRATEGIES
    assert STRATEGIES["dcai"]["module"] is dcai
    assert "asset" in get_strategy_params("dcai")
    assert get_strategy_params("none") == {}


def test_get_stats_shape(df):
    rows = dcai.get_stats("BTCUSDT", df)
    labels = [r["label"] for r in rows]
    for expected in ("Signal Prob", "MFI", "Savings Pot", "Invested",
                     "Sortino", "Max Pain", "Decision"):
        assert expected in labels
    for r in rows:
        assert set(r) <= {"label", "value", "status", "status_color"}


def test_run_metrics_sane(df):
    r = dcai.run(df)
    assert 0.0 <= r["prob"] <= 100.0
    assert r["savings_pot"] >= 0.0
    assert r["monthly_invested"] > 0  # benchmark buys every month
    assert 0.0 <= r["smart_max_dd"] <= 1.0
    assert 0.0 <= r["monthly_max_dd"] <= 1.0
    # Smart strategy never spends more than the benchmark's committed budget
    assert r["smart_invested"] <= r["monthly_invested"] + 1e-6


def test_asset_class_changes_behavior(df):
    crypto = dcai.run(df, asset="crypto")
    indices = dcai.run(df, asset="indices")
    assert crypto["mfi_target_max"] == 35
    assert indices["mfi_target_min"] == 30


def test_short_history_handled():
    short = SampleProvider().get_ohlcv("DEMO", "1d", 30)
    rows = dcai.get_stats("DEMO", short)
    assert rows[0]["status"] == "WAIT"


def test_start_end_date_gate_the_period(df):
    full = dcai.run(df)
    mid = str(df.index[len(df) // 2].date())
    late_start = dcai.run(df, start_date=mid)
    early_end = dcai.run(df, end_date=mid)
    # benchmark invests one budget per month, so a shorter period = less
    assert late_start["monthly_invested"] < full["monthly_invested"]
    assert early_end["monthly_invested"] < full["monthly_invested"]
    # no buy signal may fall outside the active period
    start_ts = df.index[len(df) // 2]
    assert all(s["time"] >= start_ts for s in late_start["signals"])
    assert all(s["time"] <= start_ts for s in early_end["signals"])


def test_default_params_match_pine_dialog():
    p = dcai.PARAMS
    assert p["budget"]["default"] == 300.0
    assert p["start_date"]["default"] == "2026-02-01"
    assert p["end_date"]["default"] == "2099-12-31"
    assert p["auto_rho"]["default"] is True
    assert p["smart_cap"]["default"] == 3.0
    assert p["strong_boost"]["default"] == 1.5
    assert p["max_boost"]["default"] == 2.0
    assert p["pot_reserve"]["default"] == 0.1
    assert p["show_pot_debug"]["default"] is False


def test_percentrank_matches_definition():
    values = np.array([1.0, 2, 3, 4, 5, 3])
    out = dcai._percentrank(values, length=5)
    assert np.isnan(out[:5]).all()
    # last value 3: previous 5 values [1,2,3,4,5], <=3 are [1,2,3] -> 60%
    assert out[5] == pytest.approx(60.0)
