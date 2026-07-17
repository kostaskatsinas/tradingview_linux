"""Strategy stats for the bottom-right panel.

Fill in :func:`get_stats` with your own strategy logic. It receives the
currently charted symbol and its daily OHLCV DataFrame (may be ``None`` when
data could not be fetched) and returns the rows to display.

Each row is a dict:

    {
        "label":  "ML Confidence",   # left column (white, bold)
        "value":  "60%",             # middle column
        "status": "Ready",           # right column (optional)
        "status_color": "#26a69a",   # optional CSS color for the status
    }

Example implementation:

    def get_stats(symbol=None, df=None):
        if df is None or df.empty:
            return []
        last = float(df["close"].iloc[-1])
        sma200 = float(df["close"].rolling(200).mean().iloc[-1])
        above = last > sma200
        return [
            {"label": "Last", "value": f"{last:,.2f}"},
            {"label": "vs SMA200", "value": f"{100 * (last / sma200 - 1):+.2f}%",
             "status": "LONG" if above else "WAIT",
             "status_color": "#26a69a" if above else "#ff9800"},
            {"label": "Decision", "value": "", "status": "HODL"},
        ]
"""

from __future__ import annotations

import pandas as pd


def get_stats(symbol: str | None = None,
              df: pd.DataFrame | None = None) -> list[dict]:
    """Return the rows for the strategy panel. Empty list = placeholder shown."""
    return []
