"""Technical indicators implemented with pandas.

Every function takes a DataFrame with columns
``open, high, low, close, volume`` (lowercase) indexed by timestamp,
and returns one or more Series aligned to that index.

Adding a new indicator: write a function here, add an entry to
``INDICATOR_REGISTRY`` at the bottom, and the UI picks it up automatically.
"""

from __future__ import annotations

import numpy as np
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


def donchian_mid(df: pd.DataFrame, period: int) -> pd.Series:
    """Midpoint of the highest high / lowest low channel (Ichimoku building block)."""
    return (df["low"].rolling(period).min()
            + df["high"].rolling(period).max()) / 2.0


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26,
             span_b: int = 52, displacement: int = 26) -> dict[str, pd.Series]:
    """Ichimoku Cloud: Tenkan, Kijun and the two displaced leading spans."""
    tenkan_line = donchian_mid(df, tenkan)
    kijun_line = donchian_mid(df, kijun)
    span_a = ((tenkan_line + kijun_line) / 2.0).shift(displacement)
    span_b_line = donchian_mid(df, span_b).shift(displacement)
    return {"tenkan": tenkan_line, "kijun": kijun_line,
            "span_a": span_a, "span_b": span_b_line}


def supertrend(df: pd.DataFrame, period: int = 10,
               multiplier: float = 3.0) -> pd.Series:
    """Supertrend line: trails below price in uptrends, above in downtrends."""
    hl2 = (df["high"] + df["low"]) / 2.0
    band = multiplier * atr(df, period)
    upper = (hl2 + band).to_numpy()
    lower = (hl2 - band).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    n = len(df)
    line = np.full(n, np.nan)
    trend = 1  # 1 = up (line below price), -1 = down
    fu, fl = upper[0], lower[0]  # final bands carry over
    for t in range(n):
        if np.isnan(upper[t]):
            continue
        if np.isnan(fu) or np.isnan(fl):
            fu, fl = upper[t], lower[t]
        # bands ratchet: only tighten in the direction of the trend
        fl = max(lower[t], fl) if close[t - 1] > fl else lower[t]
        fu = min(upper[t], fu) if close[t - 1] < fu else upper[t]
        if trend == 1 and close[t] < fl:
            trend = -1
        elif trend == -1 and close[t] > fu:
            trend = 1
        line[t] = fl if trend == 1 else fu
    return pd.Series(line, index=df.index)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin Ashi transform of an OHLCV frame (volume passes through)."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = np.empty(len(df))
    o, c = df["open"].to_numpy(dtype=float), ha_close.to_numpy()
    ha_open[0] = (o[0] + df["close"].iloc[0]) / 2.0
    for t in range(1, len(df)):
        ha_open[t] = (ha_open[t - 1] + c[t - 1]) / 2.0
    ha_open = pd.Series(ha_open, index=df.index)
    return pd.DataFrame({
        "open": ha_open,
        "high": pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1),
        "low": pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1),
        "close": ha_close,
        "volume": df["volume"],
    })


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


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (trend strength, Wilder's smoothing)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_w = atr(df, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False,
                                  min_periods=period).mean() / atr_w
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False,
                                    min_periods=period).mean() / atr_w
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume: cumulative volume signed by close direction."""
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


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
    "ichimoku": {
        "label": "Ichimoku Cloud",
        "kind": "overlay",
        "func": ichimoku,
        "params": {"tenkan": ("Tenkan", 9), "kijun": ("Kijun", 26),
                   "span_b": ("Span B", 52)},
    },
    "supertrend": {
        "label": "Supertrend",
        "kind": "overlay",
        "func": supertrend,
        "params": {"period": ("ATR len", 10), "multiplier": ("Mult", 3.0)},
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
    "adx": {
        "label": "ADX",
        "kind": "pane",
        "func": adx,
        "params": {"period": ("Length", 14)},
    },
    "obv": {
        "label": "OBV",
        "kind": "pane",
        "func": obv,
        "params": {},
    },
}
