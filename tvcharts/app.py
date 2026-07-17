"""Dash web application — a TradingView-style chart with configurable indicators.

Run with:  python run.py   (then open http://127.0.0.1:8050)
"""

from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html
from plotly.subplots import make_subplots

from .indicators import INDICATOR_REGISTRY
from .providers import BinanceProvider, INTERVALS, ProviderError, get_provider

APP_NAME = "TradingView Local"

# --- TradingView-ish dark palette -----------------------------------------
BG = "#131722"
PANEL = "#1e222d"
GRID = "#2a2e39"
TEXT = "#d1d4dc"
WHITE = "#ffffff"
UP = "#26a69a"
DOWN = "#ef5350"
ACCENT = "#2962ff"
OVERLAY_COLORS = ["#2962ff", "#ff9800", "#e040fb", "#00bcd4", "#cddc39", "#ff5252"]

PROVIDER_OPTIONS = [
    {"label": "Binance (crypto)", "value": "binance"},
    {"label": "Yahoo Finance (stocks/forex)", "value": "yahoo"},
    {"label": "Sample data (offline)", "value": "sample"},
]

DEFAULT_SYMBOL = {"binance": "BTCUSDT", "yahoo": "AAPL", "sample": "DEMO"}
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
WATCHLIST_MAX = 30

# Custom CSS: dark dropdowns/date picker + white bold sidebar text
INDEX_STRING = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            /* Dark theme for Dash 4 dropdowns (Radix-based, dash-dropdown-*) */
            .dash-dropdown {
                background: #131722 !important;
                border: 1px solid #2a2e39 !important;
                border-radius: 4px;
                color: #fff !important;
            }
            .dash-dropdown-value, .dash-dropdown-value-item,
            .dash-dropdown-search {
                color: #fff !important; font-weight: 600;
            }
            .dash-dropdown-content {
                background: #1e222d !important;
                border: 1px solid #2a2e39 !important;
                color: #d1d4dc !important;
            }
            .dash-dropdown-search-container {
                background: #1e222d !important;
                border-bottom: 1px solid #2a2e39;
            }
            .dash-dropdown-search { background: transparent !important; }
            .dash-dropdown-option, .dash-options-list-option {
                background: #1e222d; color: #d1d4dc;
            }
            .dash-dropdown-option:hover, .dash-options-list-option:hover,
            .dash-options-list-option[data-highlighted],
            .dash-options-list-option.selected {
                background: #2a2e39 !important; color: #fff !important;
            }
            /* Dark theme for the date picker */
            .dash-datepicker, .dash-datepicker-input-container {
                background: #131722 !important;
                border: 1px solid #2a2e39 !important;
                border-radius: 4px;
            }
            .dash-datepicker-input {
                background: #131722 !important;
                color: #fff !important;
                font-weight: 600;
            }
            .dash-datepicker-calendar, .dash-datepicker-content {
                background: #1e222d !important; color: #d1d4dc !important;
                border: 1px solid #2a2e39 !important;
            }
            .wl-row:hover { background: #2a2e39; }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>{%config%}{%scripts%}{%renderer%}</footer>
    </body>
</html>"""


def _control_label(text: str) -> html.Label:
    return html.Label(text, style={"fontSize": "12px", "color": WHITE,
                                   "fontWeight": "700", "textTransform": "uppercase",
                                   "letterSpacing": "0.5px"})


def _param_inputs() -> list:
    """Numeric inputs for every registered indicator parameter."""
    rows = []
    for ind_key, spec in INDICATOR_REGISTRY.items():
        for param, (label, default) in spec["params"].items():
            rows.append(
                html.Div(
                    [
                        html.Span(f"{spec['label']} · {label}",
                                  style={"fontSize": "12px", "flex": "1",
                                         "color": WHITE, "fontWeight": "600"}),
                        dcc.Input(
                            id={"type": "param", "indicator": ind_key, "param": param},
                            type="number", value=default, min=1, step=1,
                            style={"width": "70px", "background": BG, "color": WHITE,
                                   "border": f"1px solid {GRID}", "borderRadius": "4px",
                                   "padding": "2px 6px", "fontWeight": "600"},
                        ),
                    ],
                    style={"display": "flex", "alignItems": "center",
                           "gap": "8px", "marginBottom": "6px"},
                )
            )
    return rows


_WL_GRID = {"display": "grid",
            "gridTemplateColumns": "1.3fr 1fr 0.9fr 0.9fr 16px",
            "gap": "4px", "alignItems": "center", "padding": "4px 6px"}


def build_layout() -> html.Div:
    sidebar = html.Div(
        [
            html.H2(APP_NAME, style={"color": ACCENT, "margin": "0 0 16px",
                                     "fontSize": "19px"}),
            _control_label("Data source"),
            dcc.Dropdown(id="provider", options=PROVIDER_OPTIONS, value="binance",
                         clearable=False, className="dark-dropdown"),
            html.Div(style={"height": "10px"}),
            _control_label("Symbol"),
            # Seed options at mount: the dropdown clears any value that is not
            # in its options, so they must never start empty.
            dcc.Dropdown(id="symbol",
                         options=[{"label": s, "value": s}
                                  for s in BinanceProvider.STATIC_SYMBOLS],
                         value="BTCUSDT", clearable=False,
                         searchable=True, className="dark-dropdown",
                         placeholder="Type a symbol…"),
            html.Div(style={"height": "10px"}),
            _control_label("Interval"),
            dcc.Dropdown(id="interval",
                         options=[{"label": k, "value": k} for k in INTERVALS],
                         value="1d", clearable=False, className="dark-dropdown"),
            html.Div(style={"height": "10px"}),
            _control_label("Start date"),
            html.Div(
                dcc.DatePickerSingle(id="start-date", date=None, clearable=True,
                                     display_format="YYYY-MM-DD",
                                     placeholder="All history"),
                style={"marginTop": "4px"},
            ),
            html.Div(style={"height": "10px"}),
            _control_label("Bars"),
            dcc.Slider(id="limit", min=50, max=1000, step=50, value=300,
                       marks={50: "50", 500: "500", 1000: "1000"},
                       tooltip={"placement": "bottom"}),
            html.Div(style={"height": "16px"}),
            _control_label("Indicators"),
            dcc.Checklist(
                id="indicators",
                options=[{"label": spec["label"], "value": key}
                         for key, spec in INDICATOR_REGISTRY.items()],
                value=["bb", "ema", "rsi"],
                style={"display": "flex", "flexDirection": "column", "gap": "6px",
                       "marginTop": "6px"},
                inputStyle={"marginRight": "8px"},
                labelStyle={"color": WHITE, "fontWeight": "600", "fontSize": "13px"},
            ),
            html.Details(
                [html.Summary("Indicator settings",
                              style={"cursor": "pointer", "margin": "12px 0 8px",
                                     "color": WHITE, "fontWeight": "600",
                                     "fontSize": "12px"}),
                 *(_param_inputs())],
            ),
            dcc.Interval(id="refresh", interval=30_000, n_intervals=0),
        ],
        style={"width": "260px", "minWidth": "260px", "background": PANEL,
               "padding": "16px", "overflowY": "auto",
               "borderRight": f"1px solid {GRID}"},
    )

    chart = html.Div(
        [
            html.Div(id="status", style={"color": DOWN, "padding": "4px 12px",
                                         "fontSize": "13px", "minHeight": "22px"}),
            dcc.Loading(
                dcc.Graph(id="chart", style={"height": "calc(100vh - 40px)"},
                          config={"scrollZoom": True, "displaylogo": False}),
                type="dot", color=ACCENT,
            ),
        ],
        style={"flex": "1", "minWidth": "0"},
    )

    hdr = {"fontSize": "11px", "color": "#787b86", "fontWeight": "700",
           "textTransform": "uppercase"}
    watchlist = html.Div(
        [
            html.Div(
                [
                    html.Span("Watchlist", style={"color": WHITE, "fontWeight": "700",
                                                  "fontSize": "15px"}),
                    html.Button("＋ pin current", id="watch-add", n_clicks=0,
                                title="Pin the charted symbol to the watchlist",
                                style={"background": ACCENT, "color": WHITE,
                                       "border": "none", "borderRadius": "4px",
                                       "padding": "4px 8px", "cursor": "pointer",
                                       "fontSize": "12px", "fontWeight": "600"}),
                ],
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "marginBottom": "10px"},
            ),
            html.Div(
                [html.Span("Symbol", style=hdr),
                 html.Span("Last", style={**hdr, "textAlign": "right"}),
                 html.Span("Chg", style={**hdr, "textAlign": "right"}),
                 html.Span("Chg%", style={**hdr, "textAlign": "right"}),
                 html.Span("")],
                style={**_WL_GRID, "borderBottom": f"1px solid {GRID}"},
            ),
            html.Div(id="watchlist-body"),
            dcc.Store(id="watchlist", data=DEFAULT_WATCHLIST, storage_type="local"),
        ],
        style={"width": "290px", "minWidth": "290px", "background": PANEL,
               "borderLeft": f"1px solid {GRID}", "padding": "12px",
               "overflowY": "auto"},
    )

    return html.Div([sidebar, chart, watchlist],
                    style={"display": "flex", "height": "100vh", "margin": "0",
                           "background": BG, "color": TEXT,
                           "fontFamily": "'Trebuchet MS', Roboto, sans-serif"})


def _collect_params(param_ids, param_values) -> dict[str, dict]:
    """Map pattern-matched inputs back to {indicator: {param: value}}."""
    out: dict[str, dict] = {}
    for ident, value in zip(param_ids, param_values):
        ind, param = ident["indicator"], ident["param"]
        _, default = INDICATOR_REGISTRY[ind]["params"][param]
        out.setdefault(ind, {})[param] = value if value else default
    return out


def _empty_fig() -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
                      xaxis_visible=False, yaxis_visible=False,
                      margin=dict(l=8, r=8, t=8, b=8))
    return fig


def _fmt_price(v: float) -> str:
    if v >= 1000:
        return f"{v:,.1f}"
    if v >= 1:
        return f"{v:,.2f}"
    return f"{v:.4f}"


def _watchlist_rows(symbols: list[str], provider_name: str) -> list:
    rows = []
    provider = get_provider(provider_name)
    for sym in symbols:
        last = chg = pct = None
        try:
            df = provider.get_ohlcv(sym, "1d", 2)
        except ProviderError:
            try:
                df = get_provider("sample").get_ohlcv(sym, "1d", 2)
            except ProviderError:
                df = None
        if df is not None and len(df) >= 2:
            last = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2])
            chg = last - prev
            pct = 100.0 * chg / prev if prev else 0.0
        color = TEXT if chg is None else (UP if chg >= 0 else DOWN)
        num = {"textAlign": "right", "fontSize": "12px", "color": color,
               "fontWeight": "600", "whiteSpace": "nowrap"}
        rows.append(
            html.Div(
                [
                    html.Span(sym, id={"type": "wl-sym", "symbol": sym},
                              n_clicks=0,
                              title="Load on chart",
                              style={"color": WHITE, "fontWeight": "700",
                                     "fontSize": "12px", "cursor": "pointer",
                                     "overflow": "hidden",
                                     "textOverflow": "ellipsis"}),
                    html.Span("—" if last is None else _fmt_price(last), style=num),
                    html.Span("—" if chg is None else f"{chg:+,.2f}", style=num),
                    html.Span("—" if pct is None else f"{pct:+.2f}%", style=num),
                    html.Span("×", id={"type": "wl-del", "symbol": sym},
                              n_clicks=0, title="Remove",
                              style={"color": "#787b86", "cursor": "pointer",
                                     "textAlign": "center", "fontWeight": "700"}),
                ],
                className="wl-row",
                style={**_WL_GRID, "borderBottom": f"1px solid {GRID}"},
            )
        )
    if not rows:
        rows.append(html.Div("No pinned pairs yet — press “＋ pin current”.",
                             style={"color": "#787b86", "fontSize": "12px",
                                    "padding": "10px 4px"}))
    return rows


def build_figure(df, symbol: str, active: list[str], params: dict) -> go.Figure:
    pane_inds = [k for k in active if INDICATOR_REGISTRY[k]["kind"] == "pane"]
    overlay_inds = [k for k in active if INDICATOR_REGISTRY[k]["kind"] == "overlay"]

    rows = 2 + len(pane_inds)  # price + volume + one row per oscillator
    price_h = 0.55 if pane_inds else 0.8
    pane_h = (1.0 - price_h - 0.12) / max(len(pane_inds), 1)
    heights = [price_h, 0.12] + [pane_h] * len(pane_inds)

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.02, row_heights=heights)

    # -- price ---------------------------------------------------------------
    fig.add_trace(
        go.Candlestick(x=df.index, open=df["open"], high=df["high"],
                       low=df["low"], close=df["close"], name=symbol,
                       increasing_line_color=UP, increasing_fillcolor=UP,
                       decreasing_line_color=DOWN, decreasing_fillcolor=DOWN),
        row=1, col=1,
    )

    color_i = 0

    def next_color() -> str:
        nonlocal color_i
        c = OVERLAY_COLORS[color_i % len(OVERLAY_COLORS)]
        color_i += 1
        return c

    for key in overlay_inds:
        spec = INDICATOR_REGISTRY[key]
        result = spec["func"](df, **params.get(key, {}))
        if key == "bb":
            fig.add_trace(go.Scatter(x=df.index, y=result["upper"], name="BB upper",
                                     line=dict(color="rgba(41,98,255,0.6)", width=1)),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["lower"], name="BB lower",
                                     line=dict(color="rgba(41,98,255,0.6)", width=1),
                                     fill="tonexty",
                                     fillcolor="rgba(41,98,255,0.06)"),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["basis"], name="BB basis",
                                     line=dict(color="#ff9800", width=1)),
                          row=1, col=1)
        elif isinstance(result, dict):
            for sub, series in result.items():
                fig.add_trace(go.Scatter(x=df.index, y=series,
                                         name=f"{spec['label']} {sub}",
                                         line=dict(color=next_color(), width=1.2)),
                              row=1, col=1)
        else:
            label = spec["label"]
            period = params.get(key, {}).get("period")
            if period:
                label = f"{key.upper()} {int(period)}"
            fig.add_trace(go.Scatter(x=df.index, y=result, name=label,
                                     line=dict(color=next_color(), width=1.4)),
                          row=1, col=1)

    # -- volume --------------------------------------------------------------
    vol_colors = [UP if c >= o else DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="Volume",
                         marker_color=vol_colors, opacity=0.5),
                  row=2, col=1)

    # -- oscillator panes ----------------------------------------------------
    for i, key in enumerate(pane_inds):
        row = 3 + i
        spec = INDICATOR_REGISTRY[key]
        result = spec["func"](df, **params.get(key, {}))
        if key == "macd":
            hist_colors = ["rgba(38,166,154,0.7)" if v >= 0 else "rgba(239,83,80,0.7)"
                           for v in result["histogram"].fillna(0)]
            fig.add_trace(go.Bar(x=df.index, y=result["histogram"], name="MACD hist",
                                 marker_color=hist_colors), row=row, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["macd"], name="MACD",
                                     line=dict(color=ACCENT, width=1.2)), row=row, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["signal"], name="Signal",
                                     line=dict(color="#ff9800", width=1.2)), row=row, col=1)
        elif isinstance(result, dict):
            for sub, series in result.items():
                fig.add_trace(go.Scatter(x=df.index, y=series,
                                         name=f"{spec['label']} {sub}",
                                         line=dict(width=1.2)), row=row, col=1)
        else:
            fig.add_trace(go.Scatter(x=df.index, y=result, name=spec["label"],
                                     line=dict(color="#b39ddb", width=1.4)),
                          row=row, col=1)
        if key == "rsi":
            for level, dash_style in ((70, "dash"), (30, "dash"), (50, "dot")):
                fig.add_hline(y=level, line_color="#787b86", line_width=1,
                              line_dash=dash_style, row=row, col=1)
            fig.update_yaxes(range=[0, 100], row=row, col=1)
        if key == "stoch":
            fig.add_hline(y=80, line_color="#787b86", line_width=1, line_dash="dash",
                          row=row, col=1)
            fig.add_hline(y=20, line_color="#787b86", line_width=1, line_dash="dash",
                          row=row, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, size=12),
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=True,
        legend=dict(orientation="h", y=1.0, x=0, bgcolor="rgba(0,0,0,0)"),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        dragmode="pan",
    )
    fig.update_xaxes(
        gridcolor=GRID, showspikes=True, spikemode="across",
        spikecolor="#787b86", spikethickness=1,
        # Zoom-adaptive labels: years when zoomed out, then month+year,
        # then week/day, down to intraday times.
        tickformatstops=[
            dict(dtickrange=[None, 60_000], value="%H:%M:%S"),
            dict(dtickrange=[60_000, 3_600_000], value="%H:%M"),
            dict(dtickrange=[3_600_000, 86_400_000], value="%d %b %H:%M"),
            dict(dtickrange=[86_400_000, 604_800_000], value="%d %b"),
            dict(dtickrange=[604_800_000, "M1"], value="%d %b '%y"),
            dict(dtickrange=["M1", "M12"], value="%b '%y"),
            dict(dtickrange=["M12", None], value="%Y"),
        ],
    )
    fig.update_yaxes(gridcolor=GRID, side="right")
    return fig


def create_app() -> dash.Dash:
    app = dash.Dash(__name__, title=APP_NAME)
    app.index_string = INDEX_STRING
    app.layout = build_layout()

    @app.callback(Output("symbol", "options"),
                  Input("provider", "value"),
                  Input("symbol", "search_value"),
                  State("symbol", "value"))
    def symbol_options(provider_name, search, current):
        """Top pairs for the active provider, plus whatever the user types."""
        suggested = get_provider(provider_name).list_symbols(100)
        options = [{"label": s, "value": s} for s in suggested]
        extras = {(search or "").strip().upper(), (current or "").strip().upper()}
        for extra in sorted(extras - set(suggested)):
            if extra:
                options.insert(0, {"label": extra, "value": extra})
        return options

    @app.callback(Output("symbol", "value"), Input("provider", "value"),
                  State("symbol", "value"))
    def default_symbol(provider, current):
        # Switch to a sensible default when the current symbol clearly belongs
        # to the other data source.
        if current in DEFAULT_SYMBOL.values() or not current:
            return DEFAULT_SYMBOL[provider]
        return current

    @app.callback(Output("symbol", "value", allow_duplicate=True),
                  Input({"type": "wl-sym", "symbol": ALL}, "n_clicks"),
                  prevent_initial_call=True)
    def load_from_watchlist(_clicks):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            raise dash.exceptions.PreventUpdate
        return ctx.triggered_id["symbol"]

    @app.callback(Output("watchlist", "data"),
                  Input("watch-add", "n_clicks"),
                  Input({"type": "wl-del", "symbol": ALL}, "n_clicks"),
                  State("watchlist", "data"),
                  State("symbol", "value"),
                  prevent_initial_call=True)
    def edit_watchlist(_add, _dels, data, symbol):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            raise dash.exceptions.PreventUpdate
        data = list(data or [])
        trigger = ctx.triggered_id
        if trigger == "watch-add":
            symbol = (symbol or "").strip().upper()
            if symbol and symbol not in data and len(data) < WATCHLIST_MAX:
                data.append(symbol)
        elif isinstance(trigger, dict) and trigger.get("type") == "wl-del":
            data = [s for s in data if s != trigger["symbol"]]
        return data

    @app.callback(Output("watchlist-body", "children"),
                  Input("watchlist", "data"),
                  Input("provider", "value"),
                  Input("refresh", "n_intervals"))
    def render_watchlist(symbols, provider_name, _tick):
        return _watchlist_rows(list(symbols or []), provider_name)

    @app.callback(
        Output("chart", "figure"),
        Output("status", "children"),
        Input("provider", "value"),
        Input("symbol", "value"),
        Input("interval", "value"),
        Input("limit", "value"),
        Input("start-date", "date"),
        Input("indicators", "value"),
        Input({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "value"),
        Input("refresh", "n_intervals"),
        State({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "id"),
    )
    def update_chart(provider_name, symbol, interval, limit, start_date, active,
                     param_values, _tick, param_ids):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return _empty_fig(), "Enter a symbol."
        params = _collect_params(param_ids, param_values)
        start = None
        fetch_limit = int(limit)
        if start_date:
            start = pd.Timestamp(start_date, tz="UTC")
            fetch_limit = 1000  # grab max depth, then slice from the start date
        status = ""
        try:
            df = get_provider(provider_name).get_ohlcv(symbol, interval,
                                                       fetch_limit, start=start)
        except ProviderError as exc:
            try:  # graceful offline fallback so the UI never goes blank
                df = get_provider("sample").get_ohlcv(symbol, interval,
                                                      fetch_limit, start=start)
                status = f"⚠ {exc} — showing offline sample data instead."
            except ProviderError:
                return _empty_fig(), str(exc)
        if df.empty:
            return _empty_fig(), (
                f"No bars for {symbol} after "
                f"{start.date() if start is not None else 'start'} — try an "
                "earlier start date or a larger interval."
            )
        fig = build_figure(df, symbol, active or [], params)
        return fig, status

    return app


def main(host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
    create_app().run(host=host, port=port, debug=debug)
