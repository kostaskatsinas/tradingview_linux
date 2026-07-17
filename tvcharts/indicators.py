"""Technical indicators implemented with pandas.

Every function takes a DataFrame with columns
``open, high, low, close, volume`` (lowercase) indexed by timestamp,
and returns one or more Series aligned to that index.

Adding a new indicator: write a function here, add an entry to
``INDICATOR_REGISTRY`` at the bottom, and the UI picks it up automatically.
"""

from __future__ import annotations

import pandas as pd


# --------------------------------------------------------------------------- #
# Overlays (drawn on the price pane)
# --------------------------------------------------------------------------- #

def sma(df: pd.DataFrame, period: int = 20, source: str = "close") -> pd.Series:
    """Simple Moving Average."""
    return df[source].rolling(window=period, min_periods=period).mean()


def ema(df: pd.DataFrame, period: int = 20, source: str = "close") -> pd.Series:
    """Exponential Moving Average (TradingView-compatible: adjust=False)."""
    return df[source].ewm(span=period, adjust=False, min_periods=period).mean()


def wma(df: pd.DataFrame, period: int = 20, source: str = "close") -> pd.Series:
    """Weighted Moving Average (linear weights, most recent bar heaviest)."""
    weights = pd.Series(range(1, period + 1), dtype=float)
    return df[source].rolling(period).apply(
        lambda x: (x * weights.values).sum() / weights.sum(), raw=True
    )


def bollinger_bands(
    df: pd.DataFrame, period: int = 20, stddev: float = 2.0, source: str = "close"
) -> dict[str, pd.Series]:
    """Bollinger Bands: SMA basis with +/- ``stddev`` standard deviations."""
    basis = sma(df, period, source)
    dev = df[source].rolling(window=period, min_periods=period).std(ddof=0) * stddev
    return {"basis": basis, "upper": basis + dev, "lower": basis - dev}


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price, anchored per calendar day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.normalize()
    cum_pv = (typical * df["volume"]).groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_v


# --------------------------------------------------------------------------- #
# Oscillators (drawn in their own pane)
# --------------------------------------------------------------------------- #

def rsi(df: pd.DataFrame, period: int = 14, source: str = "close") -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (matches TradingView)."""
    delta = df[source].diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # When avg_loss is zero RSI is 100 by definition (rs -> inf gives 100 anyway,
    # but 0/0 gives NaN): fix the all-gain case explicitly.
    out = out.where(avg_loss != 0.0, 100.0)
    out[avg_gain.isna() | avg_loss.isna()] = float("nan")
    return out


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    source: str = "close",
) -> dict[str, pd.Series]:
    """MACD line, signal line and histogram."""
    macd_line = ema(df, fast, source) - ema(df, slow, source)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": macd_line - signal_line,
    }


def stochastic(
    df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3
) -> dict[str, pd.Series]:
    """Stochastic oscillator (%K smoothed, %D)."""
    lowest = df["low"].rolling(k_period).min()
    highest = df["high"].rolling(k_period).max()
    raw_k = 100.0 * (df["close"] - lowest) / (highest - lowest)
    k = raw_k.rolling(smooth).mean()
    d = k.rolling(d_period).mean()
    return {"k": k, "d": d}


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range with Wilder's smoothing."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# --------------------------------------------------------------------------- #
# Registry consumed by the UI
# --------------------------------------------------------------------------- #
# kind: "overlay" indicators render on the price pane, "pane" get their own row.
# params: name -> (label, default) — exposed as numeric inputs in the UI.

INDICATOR_REGISTRY: dict[str, dict] = {
    "sma": {
        "label": "Simple Moving Average",
        "kind": "overlay",
        "func": sma,
        "params": {"period": ("Length", 20)},
    },
    "ema": {
        "label": "Exponential Moving Average",
        "kind": "overlay",
        "func": ema,
        "params": {"period": ("Length", 20)},
    },
    "wma": {
        "label": "Weighted Moving Average",
        "kind": "overlay",
        "func": wma,
        "params": {"period": ("Length", 20)},
    },
    "bb": {
        "label": "Bollinger Bands",
        "kind": "overlay",
        "func": bollinger_bands,
        "params": {"period": ("Length", 20), "stddev": ("StdDev", 2.0)},
    },
    "vwap": {
        "label": "VWAP",
        "kind": "overlay",
        "func": vwap,
        "params": {},
    },
    "rsi": {
        "label": "RSI",
        "kind": "pane",
        "func": rsi,
        "params": {"period": ("Length", 14)},
    },
    "macd": {
        "label": "MACD",
        "kind": "pane",
        "func": macd,
        "params": {"fast": ("Fast", 12), "slow": ("Slow", 26), "signal": ("Signal", 9)},
    },
    "stoch": {
        "label": "Stochastic",
        "kind": "pane",
        "func": stochastic,
        "params": {"k_period": ("%K", 14), "d_period": ("%D", 3), "smooth": ("Smooth", 3)},
    },
    "atr": {
        "label": "ATR",
        "kind": "pane",
        "func": atr,
        "params": {"period": ("Length", 14)},
    },
}
