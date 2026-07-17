# TradingView Local (tvcharts) — a TradingView-style charting app in Python

An open-source, self-hosted charting application inspired by TradingView.
Candlestick charts with configurable technical indicators (Bollinger Bands,
EMA, SMA, RSI, MACD and more), powered by **free, keyless market-data APIs**.

Runs on Linux, macOS and Windows — anywhere Python runs.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py
# open http://127.0.0.1:8050 in your browser
```

No API keys, no accounts, no configuration needed.

### Run with Docker (works the same on any OS)

```bash
docker compose up --build
# open http://127.0.0.1:8050 in your browser
```

Or without compose:

```bash
docker build -t tvcharts .
docker run -p 8050:8050 tvcharts
```

The container runs as an unprivileged user, includes a healthcheck, and
respects `TVCHARTS_HOST` / `TVCHARTS_PORT` environment variables (inside the
container it binds `0.0.0.0:8050` by default; change the published port with
`-p <host-port>:8050`).

> Building behind a corporate TLS-intercepting proxy? Pass your proxy's CA to
> pip, e.g. add `COPY ca.crt /ca.crt` and `ENV PIP_CERT=/ca.crt` after the
> `WORKDIR` line, or build with `--network host` and proxy build args.

---

## Features

- **Candlestick chart** with pan, scroll-zoom, crosshair and unified hover — dark TradingView-style theme
- **Zoom-adaptive time axis**: labels shift from years → month+year → week/day → intraday times as you zoom in
- **Symbol dropdown** with the top 100 pairs (live by 24h volume on Binance, curated lists elsewhere) — free typing still works for any symbol
- **Watchlist panel** on the right: pin any pair, see Last / Chg / Chg% at a glance (auto-refreshing), click a row to load it on the chart; persists across restarts in the browser
- **Start date picker** to anchor the chart's history
- **Overlay indicators** on the price pane:
  - Simple Moving Average (SMA)
  - Exponential Moving Average (EMA)
  - Weighted Moving Average (WMA)
  - Bollinger Bands (with band fill)
  - VWAP
- **Oscillators**, each in its own pane:
  - RSI (with 30/50/70 guide lines)
  - MACD (line, signal, histogram)
  - Stochastic (%K/%D)
  - ATR
- **Editable indicator parameters** (lengths, standard deviations, MACD fast/slow/signal)
- **Volume pane** colored by candle direction
- **Multiple data sources**: crypto (Binance), stocks/ETFs/forex/indices (Yahoo Finance), plus an offline sample generator
- **Timeframes**: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M
- **Auto-refresh** every 30 seconds
- Graceful fallback: if a data source is unreachable, the app shows offline sample data with a warning instead of a blank screen

---

## Free market-data APIs — comparison

The app ships with the first two; the table shows the wider landscape if you
want to add more providers.

| API | Assets | API key | Free limits | Notes |
|---|---|---|---|---|
| **Binance** (built in) | Crypto | ❌ none | ~1200 req-weight/min | Best free option for crypto OHLCV; 1m–1w klines, up to 1000 bars/request |
| **Yahoo Finance** (built in) | Stocks, ETFs, forex, indices | ❌ none | Unofficial, be gentle | The endpoint `yfinance` uses under the hood; great coverage, no guarantees |
| CoinGecko | Crypto | ❌ none (demo key optional) | ~30 calls/min | OHLC endpoint limited to fixed day-ranges |
| Alpha Vantage | Stocks, forex, crypto | ✅ free key | 25 req/**day** | Very tight free tier; fine for end-of-day |
| Twelve Data | Stocks, forex, crypto | ✅ free key | 800 req/day, 8/min | Good general-purpose fallback |
| Finnhub | Stocks, forex, crypto | ✅ free key | 60 req/min | Candles restricted on free tier for some exchanges |

**Recommendation:** Binance for crypto and Yahoo Finance for everything else —
both are keyless, which keeps setup friction at zero.

---

## Architecture & framework choice

```
┌─────────────────────────────────────────────────────┐
│  Browser  (Plotly.js chart, rendered by Dash)       │
└───────────────▲─────────────────────────────────────┘
                │ HTTP (Dash callbacks)
┌───────────────┴─────────────────────────────────────┐
│  tvcharts/app.py        Dash UI + figure builder    │
│  tvcharts/indicators.py Indicator math (pandas)     │
│  tvcharts/providers.py  Data sources + TTL cache    │
└───────────────▲─────────────────────────────────────┘
                │ HTTPS (REST, JSON)
        Binance API · Yahoo Finance · (offline sample)
```

### Why Dash + Plotly?

| Option | Verdict |
|---|---|
| **Dash + Plotly** ✅ (chosen) | Pure Python, first-class financial charts (candlestick, subplots, crosshair, scroll-zoom) with zero JavaScript. Reactive callbacks map perfectly to "change a dropdown → redraw the chart". Runs in any browser, trivially deployable. |
| PyQt6/PySide6 + pyqtgraph | True native desktop app and the fastest rendering, but you hand-roll candlesticks, crosshairs and axis linking yourself — 3–4× the code for the same result. Good future path if you outgrow the browser. |
| `lightweight-charts-python` | Wraps TradingView's own open-source Lightweight Charts library — pixel-identical look. Downside: the chart logic lives in JS, the Python API is a thinner wrapper, and multi-pane indicator layouts are less flexible. |
| Streamlit | Fastest to prototype, but every interaction reruns the whole script and fine-grained chart interactivity is limited. Better for dashboards than for a trading terminal. |
| Matplotlib/mplfinance | Static images only — no pan/zoom/crosshair. Fine for reports, wrong tool for an interactive chart. |

### Design decisions

- **Indicator registry** (`INDICATOR_REGISTRY` in `indicators.py`): each indicator
  declares its label, kind (`overlay` vs `pane`) and parameters. The sidebar
  checklist, the settings inputs and the figure builder are all generated from
  it — adding an indicator touches exactly one file.
- **Provider abstraction** (`BaseProvider`): every source returns the same
  normalized DataFrame (`open/high/low/close/volume`, UTC index), so
  indicators and UI are source-agnostic. A small in-memory TTL cache keeps
  auto-refresh polite toward the free APIs.
- **Wilder's smoothing** for RSI/ATR and `adjust=False` EMA so values match
  what TradingView displays.
- **Offline sample provider**: deterministic synthetic OHLCV, used as an
  automatic fallback and in the test suite — the app and tests work with no
  network at all.

---

## Adding your own indicator

1. Write a function in `tvcharts/indicators.py` that takes the OHLCV DataFrame
   and returns a `pd.Series` (or a dict of named Series):

   ```python
   def hull_ma(df, period: int = 20):
       half = wma(df, period // 2)
       full = wma(df, period)
       raw = 2 * half - full
       tmp = df.assign(close=raw)
       return wma(tmp, int(period ** 0.5))
   ```

2. Register it:

   ```python
   INDICATOR_REGISTRY["hma"] = {
       "label": "Hull MA",
       "kind": "overlay",          # or "pane" for its own subplot
       "func": hull_ma,
       "params": {"period": ("Length", 20)},
   }
   ```

That's it — it appears in the sidebar automatically.

---

## Project structure

```
run.py                  # entry point: python run.py [--host] [--port] [--debug]
requirements.txt
Dockerfile              # container image (python:3.12-slim, non-root, healthcheck)
docker-compose.yml      # one-command run: docker compose up --build
tvcharts/
  app.py                # Dash layout, callbacks, figure builder
  indicators.py         # indicator math + registry
  providers.py          # Binance / Yahoo / Sample providers, TTL cache
tests/
  test_indicators.py    # math verified against hand-computed values
  test_providers.py     # provider contract + cache tests (offline)
```

## Running the tests

```bash
pip install pytest
python -m pytest tests/ -q
```

All tests run offline (they use the sample provider).

---

## Roadmap ideas

- WebSocket streaming (Binance offers free kline streams) for tick-level updates
- Drawing tools (trend lines, horizontal levels) via Plotly shape editing
- Watchlist sidebar with live last-price updates
- Price alerts (threshold crossings → desktop/telegram notification)
- Strategy backtesting on top of the indicator layer

## Disclaimer

This project is for educational purposes and is not investment advice. It is
not affiliated with TradingView, Binance or Yahoo. Respect each data
provider's terms of service.

## License

MIT
