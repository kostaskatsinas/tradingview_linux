"""Free OHLCV data providers.

All providers return a pandas DataFrame with a UTC DatetimeIndex and
columns ``open, high, low, close, volume`` sorted oldest → newest.

Included providers (no API key required):

* BinanceProvider — crypto pairs (BTCUSDT, ETHUSDT, ...) from the public
  Binance REST API. Generous limits (~1200 request-weight/min).
* YahooProvider — stocks / ETFs / forex / indices (AAPL, SPY, EURUSD=X, ^GSPC)
  from Yahoo Finance's public chart endpoint. Unofficial but widely used.
* SampleProvider — deterministic synthetic data; works offline, used as a
  fallback and in tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# interval token used across the app -> seconds
INTERVALS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
    "1M": 2592000,
}


class ProviderError(RuntimeError):
    """Raised when a data source cannot deliver candles."""


@dataclass
class _CacheEntry:
    expires: float
    frame: pd.DataFrame


class BaseProvider:
    """Common plumbing: HTTP session and a small in-memory TTL cache."""

    name = "base"
    cache_ttl = 30.0  # seconds
    STATIC_SYMBOLS: list[str] = []

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "tvcharts/0.1 (open-source charting app)"
        self._cache: dict[tuple, _CacheEntry] = {}
        self._symbols: list[str] | None = None
        self._symbols_expiry = 0.0

    def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int = 300,
        start: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        key = (symbol.upper(), interval, limit, None if start is None else str(start))
        hit = self._cache.get(key)
        now = time.time()
        if hit is not None and hit.expires > now:
            return hit.frame
        frame = self._fetch(symbol.upper(), interval, limit)
        if start is not None:
            frame = frame[frame.index >= start]
        self._cache[key] = _CacheEntry(now + self.cache_ttl, frame)
        return frame

    def list_symbols(self, limit: int = 100) -> list[str]:
        """Suggested symbols for the UI dropdown (static per provider)."""
        return list(self.STATIC_SYMBOLS)[:limit]

    def _fetch(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        raise NotImplementedError


class BinanceProvider(BaseProvider):
    """Crypto OHLCV from the public Binance API (no key needed)."""

    name = "binance"
    BASE_URL = "https://api.binance.com/api/v3/klines"
    TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

    # Fallback when the exchange can't be reached (offline development)
    STATIC_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "TRXUSDT", "MATICUSDT",
        "LTCUSDT", "SHIBUSDT", "UNIUSDT", "ATOMUSDT", "XLMUSDT", "NEARUSDT",
        "APTUSDT", "ARBUSDT", "OPUSDT", "FILUSDT", "INJUSDT", "SUIUSDT",
        "PEPEUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT", "ALGOUSDT", "FTMUSDT",
        "SANDUSDT", "MANAUSDT", "AXSUSDT", "THETAUSDT", "EGLDUSDT", "EOSUSDT",
        "XTZUSDT", "CHZUSDT", "CRVUSDT", "SNXUSDT", "COMPUSDT", "ENJUSDT",
        "KSMUSDT", "DASHUSDT", "ZECUSDT", "XMRUSDT", "BCHUSDT", "ETCUSDT",
        "VETUSDT", "ICPUSDT", "HBARUSDT", "QNTUSDT", "LDOUSDT", "STXUSDT",
        "IMXUSDT", "FLOWUSDT", "GALAUSDT", "MINAUSDT", "RUNEUSDT", "KAVAUSDT",
        "BTCEUR", "ETHEUR", "BNBEUR", "XRPEUR", "SOLEUR", "ADAEUR", "DOGEEUR",
        "BTCUSDC", "ETHUSDC", "BTCGBP", "ETHGBP",
    ]

    def list_symbols(self, limit: int = 100) -> list[str]:
        """Top pairs by 24h quote volume, refreshed hourly; static fallback."""
        now = time.time()
        if self._symbols is not None and self._symbols_expiry > now:
            return self._symbols
        try:
            resp = self._session.get(self.TICKER_URL, timeout=15)
            resp.raise_for_status()
            tickers = resp.json()
            tickers.sort(key=lambda t: float(t.get("quoteVolume", 0.0)), reverse=True)
            symbols = [t["symbol"] for t in tickers[:limit]]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            symbols = list(self.STATIC_SYMBOLS)[:limit]
        self._symbols = symbols
        self._symbols_expiry = now + 3600
        return symbols

    def _fetch(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if interval not in INTERVALS:
            raise ProviderError(f"Unsupported interval: {interval}")
        # Binance caps a single request at 1000 klines; page backwards with
        # endTime when more history is requested (strategies use up to 2800).
        remaining = min(limit, 5000)
        batches: list[list] = []
        end_time: int | None = None
        while remaining > 0:
            params = {"symbol": symbol, "interval": interval,
                      "limit": min(remaining, 1000)}
            if end_time is not None:
                params["endTime"] = end_time
            try:
                resp = self._session.get(self.BASE_URL, params=params, timeout=15)
                resp.raise_for_status()
                raw = resp.json()
            except requests.RequestException as exc:
                if batches:
                    break  # keep what we already have
                raise ProviderError(
                    f"Binance request failed for {symbol}: {exc}") from exc
            if isinstance(raw, dict):  # Binance error payload, e.g. bad symbol
                raise ProviderError(
                    f"Binance error for {symbol}: {raw.get('msg', raw)}")
            if not raw:
                break
            batches.append(raw)
            remaining -= len(raw)
            if len(raw) < params["limit"]:
                break  # start of the symbol's history reached
            end_time = raw[0][0] - 1  # continue before the earliest open time
        if not batches:
            raise ProviderError(f"No data returned for {symbol}")
        rows = [row[:6] for batch in batches for row in batch]
        frame = pd.DataFrame(rows, columns=["time", *OHLCV_COLUMNS])
        frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
        frame = frame.set_index("time").astype(float).sort_index()
        return frame[~frame.index.duplicated(keep="last")].tail(limit)


class YahooProvider(BaseProvider):
    """Stocks/ETFs/forex/indices from Yahoo Finance's public chart endpoint."""

    name = "yahoo"
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    STATIC_SYMBOLS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "JPM", "V", "UNH", "XOM", "LLY", "JNJ", "WMT", "PG", "MA", "HD",
        "CVX", "MRK", "ABBV", "KO", "PEP", "AVGO", "COST", "ORCL", "BAC",
        "CRM", "AMD", "NFLX", "ADBE", "DIS", "CSCO", "INTC", "TMO", "ABT",
        "NKE", "IBM", "QCOM", "TXN", "PYPL", "UBER", "SHOP", "PLTR",
        "SPY", "QQQ", "DIA", "IWM", "VTI", "^GSPC", "^IXIC", "^DJI",
        "EURUSD=X", "GBPUSD=X", "JPY=X", "CHF=X", "AUDUSD=X",
        "GC=F", "SI=F", "CL=F", "NG=F", "BTC-USD", "ETH-USD",
    ]

    # Yahoo only serves intraday data for short ranges; pick a sane range per interval.
    _RANGE_FOR_INTERVAL = {
        "1m": "5d",
        "5m": "1mo",
        "15m": "1mo",
        "30m": "1mo",
        "1h": "3mo",
        "4h": "3mo",  # requested as 1h and resampled
        "1d": "10y",
        "1w": "10y",
        "1M": "max",
    }
    _YAHOO_INTERVAL = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "60m",
        "1d": "1d",
        "1w": "1wk",
        "1M": "1mo",
    }

    def _fetch(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if interval not in self._YAHOO_INTERVAL:
            raise ProviderError(f"Unsupported interval: {interval}")
        try:
            resp = self._session.get(
                self.BASE_URL.format(symbol=symbol),
                params={
                    "interval": self._YAHOO_INTERVAL[interval],
                    "range": self._RANGE_FOR_INTERVAL[interval],
                },
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise ProviderError(f"Yahoo request failed for {symbol}: {exc}") from exc

        result = (payload.get("chart") or {}).get("result")
        if not result:
            err = ((payload.get("chart") or {}).get("error") or {}).get("description")
            raise ProviderError(f"Yahoo error for {symbol}: {err or 'no data'}")
        result = result[0]
        quote = result["indicators"]["quote"][0]
        frame = pd.DataFrame(
            {
                "time": pd.to_datetime(result["timestamp"], unit="s", utc=True),
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            }
        ).dropna(subset=["open", "high", "low", "close"])
        frame = frame.set_index("time").astype(float).sort_index()
        if interval == "4h":
            frame = (
                frame.resample("4h")
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                )
                .dropna(subset=["open"])
            )
        return frame.tail(limit)


class SampleProvider(BaseProvider):
    """Deterministic synthetic OHLCV — works with no network at all.

    Prices follow a seeded geometric random walk, so a given symbol always
    produces the same chart. Useful for demos, development and tests.
    """

    name = "sample"
    cache_ttl = 3600.0
    STATIC_SYMBOLS = ["DEMO", "BTCUSDT", "ETHUSDT", "ACME", "TEST"]

    def _fetch(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if interval not in INTERVALS:
            raise ProviderError(f"Unsupported interval: {interval}")
        seed = abs(hash(symbol)) % (2**32)
        rng = np.random.default_rng(seed)
        n = max(limit, 10)

        base_price = 50.0 + (seed % 1000) * 60.0  # per-symbol price scale
        returns = rng.normal(loc=0.0002, scale=0.02, size=n)
        close = base_price * np.exp(np.cumsum(returns))
        open_ = np.concatenate(([base_price], close[:-1]))
        spread = np.abs(rng.normal(0.0, 0.008, size=n)) * close
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        volume = rng.integers(1_000, 100_000, size=n).astype(float)

        step = INTERVALS[interval]
        end = pd.Timestamp.now(tz="UTC").floor(f"{step}s")
        index = pd.date_range(end=end, periods=n, freq=pd.Timedelta(seconds=step))
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=pd.DatetimeIndex(index, name="time"),
        )


PROVIDERS: dict[str, BaseProvider] = {}


def get_provider(name: str) -> BaseProvider:
    """Return a shared provider instance by name ('binance', 'yahoo', 'sample')."""
    if name not in PROVIDERS:
        registry = {
            "binance": BinanceProvider,
            "yahoo": YahooProvider,
            "sample": SampleProvider,
        }
        if name not in registry:
            raise ProviderError(f"Unknown provider: {name}")
        PROVIDERS[name] = registry[name]()
    return PROVIDERS[name]
