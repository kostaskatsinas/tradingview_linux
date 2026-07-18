"""Live updates from Binance's public WebSocket streams.

A single background :class:`StreamManager` holds one combined-stream
connection and exposes thread-safe snapshots:

  * the live (still-forming) candle for the charted symbol+interval, and
  * mini-ticker last/open prices for every watchlist symbol.

The REST providers remain the source of truth for history; the stream only
freshens the right-most candle and the watchlist quotes. Everything degrades
gracefully: if ``websocket-client`` is missing or the connection drops, the
app just keeps polling. No API key is required.

Binance uses lowercase symbols in stream names and a 250 ms kline cadence.
"""

from __future__ import annotations

import json
import threading
import time

try:
    import websocket  # websocket-client
    _HAVE_WS = True
except ImportError:  # optional dependency
    websocket = None
    _HAVE_WS = False

WS_BASE = "wss://stream.binance.com:9443/stream?streams="

# Binance kline stream interval tokens (subset we expose in the UI)
_WS_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w", "1M": "1M",
}


def build_stream_names(chart: tuple[str, str] | None,
                       watch: list[str]) -> list[str]:
    """Combined-stream path components for the given subscriptions."""
    names: list[str] = []
    if chart is not None:
        symbol, interval = chart
        token = _WS_INTERVAL.get(interval)
        if token:
            names.append(f"{symbol.lower()}@kline_{token}")
    for sym in watch:
        names.append(f"{sym.lower()}@miniTicker")
    # de-dupe while preserving order
    seen: set[str] = set()
    ordered = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def parse_message(payload: dict) -> tuple[str, dict] | None:
    """Parse a combined-stream frame into ``(kind, data)``.

    Returns ``("kline", {...})`` or ``("ticker", {...})`` or None.
    """
    data = payload.get("data") or payload  # combined vs raw
    etype = data.get("e")
    if etype == "kline":
        k = data["k"]
        return "kline", {
            "symbol": data["s"],
            "interval": k["i"],
            "start": int(k["t"]),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "closed": bool(k["x"]),
        }
    if etype == "24hrMiniTicker":
        return "ticker", {
            "symbol": data["s"],
            "last": float(data["c"]),
            "open": float(data["o"]),
        }
    return None


class StreamManager:
    """Owns one Binance combined-stream socket and the latest snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: dict[tuple[str, str], dict] = {}   # (SYM, interval) -> bar
        self._tickers: dict[str, dict] = {}            # SYM -> {last, open}
        self._names: list[str] = []
        self._ws = None
        self._thread: threading.Thread | None = None
        self._want_names: list[str] = []
        self._debounce: threading.Timer | None = None
        self.connected = False

    # -- public snapshots ------------------------------------------------- #

    def get_bar(self, symbol: str, interval: str) -> dict | None:
        with self._lock:
            bar = self._bars.get((symbol.upper(), interval))
            return dict(bar) if bar else None

    def get_ticker(self, symbol: str) -> dict | None:
        with self._lock:
            t = self._tickers.get(symbol.upper())
            return dict(t) if t else None

    # -- subscription management ------------------------------------------ #

    def set_subscriptions(self, chart: tuple[str, str] | None,
                          watch: list[str]) -> None:
        """Request a new set of streams (debounced 1.5s, reconnects once)."""
        if not _HAVE_WS:
            return
        names = build_stream_names(chart, watch)
        with self._lock:
            if names == self._want_names:
                return
            self._want_names = names
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = threading.Timer(1.5, self._reconnect)
        self._debounce.daemon = True
        self._debounce.start()

    def _reconnect(self) -> None:
        with self._lock:
            names = list(self._want_names)
            self._names = names
            # stale bars/tickers for dropped subscriptions are harmless; the
            # app only reads the ones it currently displays.
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if not names:
            return
        self._thread = threading.Thread(target=self._run, args=(names,),
                                        daemon=True, name="tvcharts-stream")
        self._thread.start()

    # -- socket loop ------------------------------------------------------ #

    def _run(self, names: list[str]) -> None:
        url = WS_BASE + "/".join(names)

        def on_message(_ws, message):
            try:
                parsed = parse_message(json.loads(message))
            except (ValueError, KeyError):
                return
            if parsed is None:
                return
            kind, data = parsed
            with self._lock:
                # ignore if our desired subscription set has moved on
                if self._names is not names:
                    return
                if kind == "kline":
                    self._bars[(data["symbol"], data["interval"])] = data
                else:
                    self._tickers[data["symbol"]] = data

        def on_open(_ws):
            self.connected = True

        def on_close(_ws, *_a):
            self.connected = False

        def on_error(_ws, *_a):
            self.connected = False

        backoff = 1.0
        while True:
            with self._lock:
                if self._names is not names:
                    return  # superseded by a newer subscription
            try:
                self._ws = websocket.WebSocketApp(
                    url, on_message=on_message, on_open=on_open,
                    on_close=on_close, on_error=on_error)
                self._ws.run_forever(ping_interval=180, ping_timeout=10)
            except Exception:
                pass
            self.connected = False
            with self._lock:
                if self._names is not names:
                    return
            time.sleep(min(backoff, 30.0))  # reconnect with backoff
            backoff = min(backoff * 2, 30.0)


# Shared instance used by the app
MANAGER = StreamManager()
