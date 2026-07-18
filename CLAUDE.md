# CLAUDE.md — TradingView Local

Guidance for future Claude sessions working in this repo. Read this before
making changes.

## What this is

A self-hosted, TradingView-style charting web app in Python: candlestick
charts with configurable indicators, a watchlist, drawing tools, alerts, live
Binance updates, and a ported **DCAi** ML-DCA strategy. Framework: **Dash +
Plotly** (pure Python, no separate JS build). Free, keyless data by default.

Entry point: `python run.py` (reads `TVCHARTS_HOST`/`TVCHARTS_PORT`; defaults
`127.0.0.1:8050`). Also dockerized (`Dockerfile`, `docker-compose.yml`).

## Layout

```
run.py                     # CLI entry point
tvcharts/
  app.py                   # Dash layout, callbacks, figure builder, INDEX_STRING JS
  indicators.py            # indicator math + INDICATOR_REGISTRY
  providers.py             # Binance / Yahoo / Sample data sources + TTL cache
  strategy.py              # user's custom-strategy stub (get_stats hook)
  strategies/
    __init__.py            # STRATEGIES registry, get_strategy_params
    dcai.py                # DCAi ML-DCA port (AGPL-3.0 — see Licensing)
  alerts.py                # AlertStore (JSON) + AlertEngine daemon + Telegram
  stream.py                # Binance WebSocket StreamManager (optional dep)
tests/                     # pytest, all offline via SampleProvider (49 tests)
third_party/DCAi/          # original dcai.pine + AGPL-3.0 license text
```

## Core conventions (reuse these — don't reinvent)

- **OHLCV contract**: every provider returns a pandas DataFrame with a UTC
  `DatetimeIndex` and lowercase columns `open, high, low, close, volume`,
  oldest→newest. All indicator/strategy code assumes this shape.
- **Indicator registry** (`indicators.py` `INDICATOR_REGISTRY`): each entry has
  `label`, `kind` (`"overlay"` on price pane / `"pane"` its own row), `func`,
  and `params` (`{name: (label, default)}`). The sidebar checklist, the numeric
  settings inputs, and the figure builder are all **generated from this dict** —
  adding an indicator is one entry + one function. A function returns a
  `pd.Series` or a dict of named Series. Special multi-line rendering (Bollinger,
  Ichimoku, MACD) is special-cased in `build_figure`.
- **Strategy registry** (`strategies/__init__.py` `STRATEGIES`): maps key →
  `{label, module}`. A strategy module exposes `get_stats(symbol, df, **params)`
  (panel rows) and optionally `get_signals` (chart flags), `get_equity`,
  `get_trades`, and a `PARAMS` dict (`kind`: `number|select|bool|date`) that
  auto-generates the sidebar "Strategy settings" inputs.
- **Panel row format** (strategy panel + custom `strategy.py`): list of
  `{label, value, status?, status_color?}`.
- **Provider access**: `get_provider(name)` returns a shared, TTL-cached
  instance (`binance` / `yahoo` / `sample`). `sample` is deterministic (seeded
  per symbol) and is the graceful fallback + the basis for all tests.
- **Palette / theme**: reuse the module-level color constants in `app.py`
  (`BG, PANEL, GRID, TEXT, WHITE, UP, DOWN, ACCENT, OVERLAY_COLORS`). Sidebar
  text is white + bold by house style.

## Dash-specific gotchas (learned the hard way)

- **This is Dash 4** (Radix-based components). Dropdowns are `.dash-dropdown-*`
  and the date picker is `.dash-datepicker-*` — dark-theme CSS for both lives in
  `INDEX_STRING`. The old React-Select `.Select-*` classes do NOT apply.
- **Dropdowns clear a value not in their `options`.** Seed `options` at mount
  (see the `symbol` dropdown) or the value blanks on load.
- **Plotly 6 encodes figure data arrays as base64 typed arrays.** You cannot
  read `figure["data"][i]["close"]` server-side (it's an opaque dict). Get
  prices from the provider cache instead (see `add_horizontal_line`).
- **Custom browser JS** goes in `app.index_string = INDEX_STRING` as `<script>`
  blocks. Existing ones: panel splitters, chart-pane dividers, the fixed OHLC
  hover readout, and desktop notifications. They poll for the Plotly graph div
  and hook `plotly_*` events. `data-*` wildcard props on `html.Div` are used to
  pass values from server callbacks to these scripts (`#notify-feed`).
- **`uirevision`** on the figure (keyed by symbol|panes|volume) preserves user
  pan/zoom across the periodic refresh. Don't drop it.
- Pattern-matching IDs in use: `{"type": "param"|"sparam"|"sdate"|"wl-sym"|
  "wl-del"|"alert-del", ...}`. Collect them with the paired `State(... "id")`.

## Feature specifics

- **Chart panes**: price + optional volume + one row per active oscillator +
  optional strategy-equity pane. Row math is in `build_figure`; pane heights are
  drag-adjustable (JS in `INDEX_STRING`, saved to localStorage per pane count).
- **Drawings**: Plotly draw tools (`modeBarButtonsToAdd`), saved per symbol to a
  `dcc.Store(storage_type="local")` via `_merge_drawings`; only `editable`
  shapes are user drawings (indicator hlines are not saved). Re-injected into
  every rebuilt figure.
- **Refresh**: fixed 1-minute `dcc.Interval#refresh`. The WebSocket keeps the
  server snapshot fresh between repaints. `manage_stream` sets subscriptions.
- **Alerts**: server-side JSON at `~/.tvcharts/alerts.json` (override dir with
  `TVCHARTS_HOME`). `AlertEngine.run_once()` is also called on the UI refresh so
  notifications surface without waiting for the 60s daemon loop. Telegram via
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars.
- **Live stream**: `stream.py` needs `websocket-client` (import-guarded — app
  runs polling-only without it). REST is always the source of history; the live
  bar is merged onto the right edge by `_merge_live_bar` (Binance only).

## DCAi strategy (`strategies/dcai.py`)

Python port of the Pine v6 indicator (`third_party/DCAi/dcai.pine`,
github.com/Cerebrux/DCAi, `stable`). Bar-by-bar sim: 8 percentile-ranked
features → KNN (Lorentzian distance, walk-forward validated) → 3 buy tiers
(pullback/oversold/fear) with monthly budget + savings pot, vs a blind-DCA
benchmark. `run_cached()` memoizes one run shared by the panel, chart flags,
equity pane and CSV export.

Fidelity notes (don't regress these):
- **`_percentrank` must return NaN for warm-up/NaN windows** (matches Pine's
  `ta.percentrank`), so those rows are excluded from KNN training. Turning them
  into 0-percentile poisons the model — this was a real bug.
- Strategies train on **2800 daily bars** (`_strategy_df`); the Binance provider
  pages the REST API to supply that depth. Chart flags are 1d-only.
- Pine and pandas won't match tick-for-tick (warm-up, feed differences). The
  user runs a possibly-different DCAi version on Bitstamp; expect *close*, not
  identical.

## Testing & verification

- `python3 -m pytest tests/ -q` — all offline (SampleProvider), must stay green.
  Set `TVCHARTS_HOME=<tmp>` to avoid touching `~/.tvcharts`.
- For UI changes, boot the app and drive it with Playwright + the pre-installed
  Chromium (`/opt/pw-browsers/chromium-*/chrome-linux/chrome`, `--no-sandbox`),
  screenshot, and read it back. This is the established verification loop.
- **Sandbox can't reach Binance/Telegram** (network policy). Verify parsing/merge
  logic via unit tests; flag live-tick and Telegram delivery as user-side checks.

## Git / workflow

- Develop on `claude/tradingview-python-app-5a5ew8`. The user keeps `main`
  updated: after committing, push to **both** the feature branch and `main`
  (`git push origin claude/...:main`). Don't open PRs unless asked.
- Do not put the model identifier in commits/PRs/code.

## Licensing (important)

Repo is **MIT except** `strategies/dcai.py` and `third_party/DCAi/`, which are
**AGPL-3.0** (the original DCAi license). Keep the AGPL notice on the port. If
the app is ever distributed or hosted as a service with DCAi included, AGPL
requires offering the source. Never strip the attribution to Salih Emin /
Cerebrux.

