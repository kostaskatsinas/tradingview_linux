"""Dash web application — a TradingView-style chart with configurable indicators.

Run with:  python run.py   (then open http://127.0.0.1:8050)
"""

from __future__ import annotations

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html
from plotly.subplots import make_subplots

from .indicators import INDICATOR_REGISTRY
from .providers import INTERVALS, ProviderError, get_provider

# --- TradingView-ish dark palette -----------------------------------------
BG = "#131722"
PANEL = "#1e222d"
GRID = "#2a2e39"
TEXT = "#d1d4dc"
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


def _control_label(text: str) -> html.Label:
    return html.Label(text, style={"fontSize": "11px", "color": "#787b86",
                                   "textTransform": "uppercase", "letterSpacing": "0.5px"})


def _param_inputs() -> list:
    """Numeric inputs for every registered indicator parameter."""
    rows = []
    for ind_key, spec in INDICATOR_REGISTRY.items():
        for param, (label, default) in spec["params"].items():
            rows.append(
                html.Div(
                    [
                        html.Span(f"{spec['label']} · {label}",
                                  style={"fontSize": "12px", "flex": "1"}),
                        dcc.Input(
                            id={"type": "param", "indicator": ind_key, "param": param},
                            type="number", value=default, min=1, step=1,
                            style={"width": "70px", "background": BG, "color": TEXT,
                                   "border": f"1px solid {GRID}", "borderRadius": "4px",
                                   "padding": "2px 6px"},
                        ),
                    ],
                    style={"display": "flex", "alignItems": "center",
                           "gap": "8px", "marginBottom": "6px"},
                )
            )
    return rows


def build_layout() -> html.Div:
    sidebar = html.Div(
        [
            html.H2("tvcharts", style={"color": ACCENT, "margin": "0 0 16px",
                                       "fontSize": "20px"}),
            _control_label("Data source"),
            dcc.Dropdown(id="provider", options=PROVIDER_OPTIONS, value="binance",
                         clearable=False, className="dark-dropdown"),
            html.Div(style={"height": "10px"}),
            _control_label("Symbol"),
            dcc.Input(id="symbol", type="text", value="BTCUSDT", debounce=True,
                      style={"width": "100%", "background": BG, "color": TEXT,
                             "border": f"1px solid {GRID}", "borderRadius": "4px",
                             "padding": "6px 8px", "boxSizing": "border-box"}),
            html.Div(style={"height": "10px"}),
            _control_label("Interval"),
            dcc.Dropdown(id="interval",
                         options=[{"label": k, "value": k} for k in INTERVALS],
                         value="1d", clearable=False, className="dark-dropdown"),
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
            ),
            html.Details(
                [html.Summary("Indicator settings",
                              style={"cursor": "pointer", "margin": "12px 0 8px",
                                     "color": "#787b86", "fontSize": "12px"}),
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

    return html.Div([sidebar, chart],
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
    fig.update_xaxes(gridcolor=GRID, showspikes=True, spikemode="across",
                     spikecolor="#787b86", spikethickness=1)
    fig.update_yaxes(gridcolor=GRID, side="right")
    return fig


def create_app() -> dash.Dash:
    app = dash.Dash(__name__, title="tvcharts")
    app.layout = build_layout()

    @app.callback(Output("symbol", "value"), Input("provider", "value"),
                  State("symbol", "value"))
    def default_symbol(provider, current):
        # Switch to a sensible default when the current symbol clearly belongs
        # to the other data source.
        if current in DEFAULT_SYMBOL.values() or not current:
            return DEFAULT_SYMBOL[provider]
        return current

    @app.callback(
        Output("chart", "figure"),
        Output("status", "children"),
        Input("provider", "value"),
        Input("symbol", "value"),
        Input("interval", "value"),
        Input("limit", "value"),
        Input("indicators", "value"),
        Input({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "value"),
        Input("refresh", "n_intervals"),
        State({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "id"),
    )
    def update_chart(provider_name, symbol, interval, limit, active, param_values,
                     _tick, param_ids):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return go.Figure(), "Enter a symbol."
        params = _collect_params(param_ids, param_values)
        status = ""
        try:
            df = get_provider(provider_name).get_ohlcv(symbol, interval, int(limit))
        except ProviderError as exc:
            try:  # graceful offline fallback so the UI never goes blank
                df = get_provider("sample").get_ohlcv(symbol, interval, int(limit))
                status = f"⚠ {exc} — showing offline sample data instead."
            except ProviderError:
                return go.Figure(), str(exc)
        fig = build_figure(df, symbol, active or [], params)
        return fig, status

    return app


def main(host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
    create_app().run(host=host, port=port, debug=debug)
