"""Dash web application — a TradingView-style chart with configurable indicators.

Run with:  python run.py   (then open http://127.0.0.1:8050)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html
from plotly.subplots import make_subplots

from .indicators import INDICATOR_REGISTRY
from .indicators import heikin_ashi as ind_heikin_ashi
from .providers import BinanceProvider, INTERVALS, ProviderError, get_provider
from .strategies import STRATEGIES, get_strategy_params

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
# USD pairs from other exchanges map to their Binance USDT equivalents.
DEFAULT_WATCHLIST = [
    "BTCUSDT", "BTCEUR", "ETHUSDT", "ETHEUR", "SOLEUR", "BNBEUR",
    "RUNEEUR", "ATOMUSDT", "DOTEUR", "HNTUSDT", "APTEUR", "PYTHUSDT",
    "THETAUSDT", "SUIUSDT", "LINKEUR", "ASTERUSDT", "ETHBTC", "TRXEUR",
]
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
            /* Dark theme for the date picker — white bold text everywhere */
            .dash-datepicker, .dash-datepicker-input-container {
                background: #131722 !important;
                border: 1px solid #2a2e39 !important;
                border-radius: 4px;
            }
            .dash-datepicker-input {
                background: #131722 !important;
                color: #fff !important;
                font-weight: 700;
            }
            .dash-datepicker-input::placeholder {
                color: #fff !important; font-weight: 700; opacity: 1;
            }
            .dash-datepicker-calendar, .dash-datepicker-content {
                background: #1e222d !important;
                border: 1px solid #2a2e39 !important;
            }
            .dash-datepicker-calendar, .dash-datepicker-calendar *,
            .dash-datepicker-content, .dash-datepicker-content * {
                color: #fff !important; font-weight: 700;
            }
            /* year/month inputs inside the calendar popup */
            .dash-datepicker-calendar input, .dash-datepicker-content input,
            .dash-datepicker-calendar select, .dash-datepicker-content select {
                background: #131722 !important;
                border: 1px solid #2a2e39 !important;
            }
            .wl-row:hover { background: #2a2e39; }
            /* Drag handles between panels */
            .splitter-v {
                width: 5px; cursor: col-resize; background: #2a2e39;
                flex: 0 0 5px;
            }
            .splitter-h {
                height: 5px; cursor: row-resize; background: #2a2e39;
                flex: 0 0 5px;
            }
            .splitter-v:hover, .splitter-h:hover { background: #2962ff; }
            /* Drag strips between chart panes (price / volume / oscillators) */
            .pane-divider {
                position: absolute; cursor: row-resize; z-index: 20;
            }
            .pane-divider:hover, .pane-divider.dragging {
                background: rgba(41, 98, 255, 0.35);
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>{%config%}{%scripts%}{%renderer%}</footer>
        <script>
        /* Drag-to-resize panels: sidebar width, right column width, and the
           watchlist/strategy split. Panels are plain flex children, so
           changing width/height reflows everything; a window resize event is
           dispatched so the Plotly chart re-measures itself. */
        (function () {
            var lastResize = 0;
            function pokeChart() {
                var now = Date.now();
                if (now - lastResize > 100) {
                    lastResize = now;
                    window.dispatchEvent(new Event("resize"));
                }
            }
            function drag(handle, onMove) {
                handle.addEventListener("mousedown", function (e) {
                    e.preventDefault();
                    function mm(ev) { onMove(ev); pokeChart(); }
                    function mu() {
                        document.removeEventListener("mousemove", mm);
                        document.removeEventListener("mouseup", mu);
                        window.dispatchEvent(new Event("resize"));
                    }
                    document.addEventListener("mousemove", mm);
                    document.addEventListener("mouseup", mu);
                });
            }
            function init() {
                var sb = document.getElementById("sidebar"),
                    rc = document.getElementById("right-column"),
                    wl = document.getElementById("watchlist-panel"),
                    sl = document.getElementById("split-left"),
                    sr = document.getElementById("split-right"),
                    ss = document.getElementById("split-strategy");
                if (!sb || !rc || !wl || !sl || !sr || !ss) {
                    setTimeout(init, 500);
                    return;
                }
                drag(sl, function (e) {
                    var w = Math.min(Math.max(e.clientX, 170), 600);
                    sb.style.width = w + "px";
                    sb.style.minWidth = w + "px";
                });
                drag(sr, function (e) {
                    var w = Math.min(Math.max(window.innerWidth - e.clientX, 180), 800);
                    rc.style.width = w + "px";
                    rc.style.minWidth = w + "px";
                });
                drag(ss, function (e) {
                    var r = rc.getBoundingClientRect();
                    var h = Math.min(Math.max(e.clientY - r.top, 80), r.height - 80);
                    wl.style.flex = "none";
                    wl.style.height = h + "px";
                });
            }
            if (document.readyState !== "loading") init();
            else document.addEventListener("DOMContentLoaded", init);
        })();
        </script>
        <script>
        /* Adjustable dividers between the chart's panes (price, volume,
           oscillators). Overlay strips sit on the gaps between subplots and
           drag the y-axis domains via Plotly.relayout — the figure itself is
           untouched, so data, zoom and indicators survive. The chosen layout
           is stored per pane-count in localStorage and re-applied after every
           server-side figure update. */
        (function () {
            var GAP = 0.01;         // half of the visual gap between panes
            var MIN_PANE = 0.05;    // no pane may shrink below 5% height
            var applying = false;

            function graphDiv() {
                var wrap = document.getElementById("chart");
                if (!wrap) { return null; }
                var gd = wrap.classList.contains("js-plotly-plot")
                    ? wrap : wrap.querySelector(".js-plotly-plot");
                return gd && gd._fullLayout ? gd : null;
            }
            function paneAxes(gd) {
                return Object.keys(gd._fullLayout)
                    .filter(function (k) { return /^yaxis\d*$/.test(k); })
                    .filter(function (k) {
                        var ax = gd._fullLayout[k];
                        return ax && ax.domain && ax.domain.length === 2;
                    })
                    .sort(function (a, b) {   // top pane first
                        return gd._fullLayout[b].domain[1] -
                               gd._fullLayout[a].domain[1];
                    });
            }
            function storeKey(n) { return "tvcharts-panes-" + n; }

            function saveDomains(gd, keys) {
                var domains = keys.map(function (k) {
                    return gd._fullLayout[k].domain.slice();
                });
                try {
                    localStorage.setItem(storeKey(keys.length),
                                         JSON.stringify(domains));
                } catch (err) { /* private mode etc. */ }
            }
            function applySaved(gd) {
                var keys = paneAxes(gd);
                var raw = null;
                try { raw = localStorage.getItem(storeKey(keys.length)); }
                catch (err) { return; }
                if (!raw) { return; }
                var domains;
                try { domains = JSON.parse(raw); } catch (err) { return; }
                if (!domains || domains.length !== keys.length) { return; }
                var update = {}, changed = false;
                keys.forEach(function (k, i) {
                    var cur = gd._fullLayout[k].domain;
                    if (Math.abs(cur[0] - domains[i][0]) > 1e-6 ||
                        Math.abs(cur[1] - domains[i][1]) > 1e-6) {
                        changed = true;
                    }
                    update[k + ".domain"] = domains[i];
                });
                if (changed && window.Plotly) {
                    applying = true;
                    Plotly.relayout(gd, update).then(function () {
                        applying = false;
                    }, function () { applying = false; });
                }
            }

            function buildDividers(gd) {
                gd.querySelectorAll(".pane-divider").forEach(function (el) {
                    el.remove();
                });
                var keys = paneAxes(gd);
                if (keys.length < 2) { return; }
                var size = gd._fullLayout._size;   // plot area in px
                if (!size) { return; }
                if (getComputedStyle(gd).position === "static") {
                    gd.style.position = "relative";
                }
                for (var i = 0; i < keys.length - 1; i++) {
                    (function (i) {
                        var upper = keys[i], lower = keys[i + 1];
                        var mid = (gd._fullLayout[upper].domain[0] +
                                   gd._fullLayout[lower].domain[1]) / 2;
                        var strip = document.createElement("div");
                        strip.className = "pane-divider";
                        strip.style.left = size.l + "px";
                        strip.style.width = size.w + "px";
                        strip.style.height = "9px";
                        strip.style.top =
                            (size.t + (1 - mid) * size.h - 4.5) + "px";
                        strip.title = "Drag to resize panes";
                        strip.addEventListener("mousedown", function (e) {
                            e.preventDefault();
                            e.stopPropagation();
                            strip.classList.add("dragging");
                            var rect = gd.getBoundingClientRect();
                            var pending = null;
                            function mm(ev) {
                                var y = 1 - (ev.clientY - rect.top - size.t)
                                            / size.h;
                                var lo = gd._fullLayout[lower].domain[0]
                                         + MIN_PANE;
                                var hi = gd._fullLayout[upper].domain[1]
                                         - MIN_PANE;
                                y = Math.min(Math.max(y, lo), hi);
                                var update = {};
                                update[upper + ".domain"] =
                                    [y + GAP, gd._fullLayout[upper].domain[1]];
                                update[lower + ".domain"] =
                                    [gd._fullLayout[lower].domain[0], y - GAP];
                                if (!pending && window.Plotly) {
                                    pending = requestAnimationFrame(function () {
                                        pending = null;
                                        applying = true;
                                        Plotly.relayout(gd, update)
                                            .then(function () {
                                                applying = false;
                                            }, function () {
                                                applying = false;
                                            });
                                    });
                                }
                            }
                            function mu() {
                                document.removeEventListener("mousemove", mm);
                                document.removeEventListener("mouseup", mu);
                                strip.classList.remove("dragging");
                                saveDomains(gd, paneAxes(gd));
                                buildDividers(gd);
                            }
                            document.addEventListener("mousemove", mm);
                            document.addEventListener("mouseup", mu);
                        });
                        gd.appendChild(strip);
                    })(i);
                }
            }

            function hook() {
                var gd = graphDiv();
                if (!gd || !gd.on) { setTimeout(hook, 600); return; }
                if (gd.__paneDividersHooked) { return; }
                gd.__paneDividersHooked = true;
                gd.on("plotly_afterplot", function () {
                    if (applying) { return; }
                    applySaved(gd);
                    buildDividers(gd);
                });
                gd.on("plotly_relayout", function () {
                    if (!applying) { buildDividers(gd); }
                });
                applySaved(gd);
                buildDividers(gd);
                window.addEventListener("resize", function () {
                    setTimeout(function () { buildDividers(gd); }, 150);
                });
            }
            if (document.readyState !== "loading") hook();
            else document.addEventListener("DOMContentLoaded", hook);
        })();
        </script>
        <script>
        /* Fixed hover readout: fills #ohlc-bar with the hovered bar's OHLC and
           indicator values, TradingView-style, so no floating box covers the
           chart around the cursor. Traces carry hoverinfo:"none", which hides
           Plotly's labels but still fires hover events. */
        (function () {
            function fmt(v) {
                if (v === null || v === undefined || isNaN(v)) { return "—"; }
                var a = Math.abs(v);
                var d = a >= 1000 ? 1 : a >= 1 ? 2 : 5;
                return v.toLocaleString(undefined,
                                        {maximumFractionDigits: d});
            }
            var SEP = '<span style="color:#2a2e39"> | </span>';
            function hook() {
                var bar = document.getElementById("ohlc-bar");
                var wrap = document.getElementById("chart");
                var gd = wrap && (wrap.classList.contains("js-plotly-plot")
                                  ? wrap
                                  : wrap.querySelector(".js-plotly-plot"));
                if (!bar || !gd || !gd.on) { setTimeout(hook, 600); return; }
                if (gd.__ohlcHooked) { return; }
                gd.__ohlcHooked = true;
                gd.on("plotly_hover", function (ev) {
                    if (!ev || !ev.points || !ev.points.length) { return; }
                    var parts = [];
                    var x = String(ev.points[0].x);
                    parts.push('<span style="color:#787b86">'
                               + x.slice(0, 16) + "</span>");
                    ev.points.forEach(function (pt) {
                        var tr = pt.data;
                        if (tr.type === "candlestick" || tr.type === "ohlc") {
                            var i = pt.pointNumber !== undefined
                                ? pt.pointNumber : pt.pointIndex;
                            var o = pt.open !== undefined ? pt.open : tr.open[i],
                                h = pt.high !== undefined ? pt.high : tr.high[i],
                                l = pt.low !== undefined ? pt.low : tr.low[i],
                                c = pt.close !== undefined ? pt.close
                                                           : tr.close[i];
                            var col = c >= o ? "#26a69a" : "#ef5350";
                            parts.push('<span style="color:' + col + '">O '
                                + fmt(o) + "&nbsp; H " + fmt(h) + "&nbsp; L "
                                + fmt(l) + "&nbsp; C " + fmt(c) + "</span>");
                        } else if (tr.type === "bar") {
                            parts.push('<span style="color:#787b86">'
                                + (tr.name || "") + " " + fmt(pt.y)
                                + "</span>");
                        } else {
                            var lc = (tr.line && tr.line.color) || "#d1d4dc";
                            parts.push('<span style="color:' + lc + '">'
                                + (tr.name || "") + " " + fmt(pt.y)
                                + "</span>");
                        }
                    });
                    bar.innerHTML = parts.join(SEP);
                });
            }
            if (document.readyState !== "loading") hook();
            else document.addEventListener("DOMContentLoaded", hook);
        })();
        </script>
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
            _control_label("Chart type"),
            dcc.Dropdown(id="chart-type",
                         options=[{"label": "Candles", "value": "candles"},
                                  {"label": "Heikin Ashi", "value": "heikin"},
                                  {"label": "Line", "value": "line"},
                                  {"label": "OHLC bars", "value": "ohlc"}],
                         value="candles", clearable=False,
                         className="dark-dropdown"),
            html.Button("＋ Horizontal line", id="add-hline", n_clicks=0,
                        title="Add a draggable horizontal level at the last price",
                        style={"marginTop": "8px", "width": "100%",
                               "background": BG, "color": WHITE,
                               "border": f"1px solid {GRID}",
                               "borderRadius": "4px", "padding": "5px",
                               "cursor": "pointer", "fontWeight": "600",
                               "fontSize": "12px"}),
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
                       marks={v: {"label": str(v),
                                  "style": {"color": WHITE, "fontWeight": "700"}}
                              for v in (50, 500, 1000)},
                       tooltip={"placement": "bottom"}),
            html.Div(style={"height": "16px"}),
            _control_label("Panes"),
            dcc.Checklist(
                id="panes",
                options=[{"label": "Volume", "value": "volume"}],
                value=["volume"],
                style={"marginTop": "6px"},
                inputStyle={"marginRight": "8px"},
                labelStyle={"color": WHITE, "fontWeight": "600", "fontSize": "13px"},
            ),
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
            html.Div(style={"height": "16px"}),
            _control_label("Strategy"),
            dcc.Dropdown(id="strategy-select",
                         options=[{"label": v["label"], "value": k}
                                  for k, v in STRATEGIES.items()],
                         value="dcai", clearable=False,
                         className="dark-dropdown"),
            dcc.Checklist(
                id="strategy-display",
                options=[{"label": "Equity curve pane", "value": "equity"},
                         {"label": "Avg entry line", "value": "avg"}],
                value=[],
                style={"display": "flex", "flexDirection": "column",
                       "gap": "6px", "marginTop": "8px"},
                inputStyle={"marginRight": "8px"},
                labelStyle={"color": WHITE, "fontWeight": "600",
                            "fontSize": "13px"},
            ),
            html.Details(
                [html.Summary("Strategy settings",
                              style={"cursor": "pointer", "margin": "12px 0 8px",
                                     "color": WHITE, "fontWeight": "600",
                                     "fontSize": "12px"}),
                 html.Div(id="strategy-params")],
                open=False,
            ),
            dcc.Interval(id="refresh", interval=30_000, n_intervals=0),
        ],
        id="sidebar",
        style={"width": "260px", "minWidth": "260px", "background": PANEL,
               "padding": "16px", "overflowY": "auto",
               "borderRight": f"1px solid {GRID}"},
    )

    chart = html.Div(
        [
            html.Div(id="status", style={"color": DOWN, "padding": "4px 12px",
                                         "fontSize": "13px", "minHeight": "22px"}),
            # Fixed hover readout (TradingView-style): OHLC + indicator values
            # of the hovered bar render here instead of a floating box that
            # would cover the chart.
            html.Div(id="ohlc-bar",
                     style={"padding": "0 12px 4px", "fontSize": "13px",
                            "minHeight": "20px", "fontWeight": "600",
                            "whiteSpace": "nowrap", "overflow": "hidden",
                            "textOverflow": "ellipsis"}),
            dcc.Loading(
                dcc.Graph(id="chart", style={"height": "calc(100vh - 62px)"},
                          config={"scrollZoom": True, "displaylogo": False,
                                  "modeBarButtonsToAdd": ["drawline",
                                                          "drawrect",
                                                          "eraseshape"]}),
                type="dot", color=ACCENT,
            ),
            dcc.Store(id="drawings", data={}, storage_type="local"),
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
            # v2 key: bumping the id resets browsers that stored the old default list
            dcc.Store(id="watchlist-v2", data=DEFAULT_WATCHLIST, storage_type="local"),
        ],
        id="watchlist-panel",
        # Top three quarters of the right column, scrolls independently
        style={"flex": "3", "minHeight": "0", "overflowY": "auto",
               "padding": "12px"},
    )

    strategy_box = html.Div(
        [
            html.Div(
                [
                    html.Span("Strategy", style={"color": WHITE,
                                                 "fontWeight": "700",
                                                 "fontSize": "15px"}),
                    html.Button("⤓ trades", id="trades-export", n_clicks=0,
                                title="Download the executed buys as CSV",
                                style={"background": "transparent",
                                       "color": TEXT,
                                       "border": f"1px solid {GRID}",
                                       "borderRadius": "4px",
                                       "padding": "2px 8px",
                                       "cursor": "pointer",
                                       "fontSize": "11px",
                                       "fontWeight": "600"}),
                ],
                style={"display": "flex", "justifyContent": "space-between",
                       "alignItems": "center", "marginBottom": "8px"},
            ),
            dcc.Download(id="trades-download"),
            dcc.Loading(html.Div(id="strategy-body"), type="dot", color=ACCENT),
        ],
        id="strategy-box",
        # Reserved bottom quarter: populated from tvcharts/strategies
        style={"flex": "1", "minHeight": "0", "overflowY": "auto",
               "padding": "12px"},
    )

    right_column = html.Div(
        [watchlist,
         html.Div(id="split-strategy", className="splitter-h"),
         strategy_box],
        id="right-column",
        style={"width": "290px", "minWidth": "290px", "background": PANEL,
               "borderLeft": f"1px solid {GRID}", "display": "flex",
               "flexDirection": "column", "height": "100vh"},
    )

    return html.Div([sidebar,
                     html.Div(id="split-left", className="splitter-v"),
                     chart,
                     html.Div(id="split-right", className="splitter-v"),
                     right_column],
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


def _strategy_param_inputs(strategy_key: str) -> list:
    """Sidebar inputs generated from the selected strategy's PARAMS spec."""
    spec = get_strategy_params(strategy_key)
    if not spec:
        return [html.Div("This strategy has no settings.",
                         style={"color": "#787b86", "fontSize": "12px"})]
    rows = []
    for name, cfg in spec.items():
        pid = {"type": "sparam", "param": name}
        kind = cfg.get("kind", "number")
        if kind == "select":
            control = dcc.Dropdown(id=pid, options=cfg["options"],
                                   value=cfg["default"], clearable=False,
                                   className="dark-dropdown",
                                   style={"width": "100%"})
            rows.append(html.Div(
                [html.Span(cfg["label"], style={"fontSize": "12px",
                                                "color": WHITE,
                                                "fontWeight": "600"}),
                 control],
                style={"marginBottom": "8px"}))
        elif kind == "bool":
            rows.append(dcc.Checklist(
                id=pid, options=[{"label": cfg["label"], "value": "on"}],
                value=["on"] if cfg["default"] else [],
                inputStyle={"marginRight": "8px"},
                labelStyle={"color": WHITE, "fontWeight": "600",
                            "fontSize": "12px"},
                style={"marginBottom": "6px"}))
        elif kind == "date":
            # date pickers expose a `date` prop, so they use their own
            # pattern id type ("sdate") and are collected separately
            rows.append(html.Div(
                [html.Span(cfg["label"], style={"fontSize": "12px",
                                                "color": WHITE,
                                                "fontWeight": "600"}),
                 html.Div(dcc.DatePickerSingle(
                     id={"type": "sdate", "param": name},
                     date=cfg["default"], clearable=True,
                     display_format="YYYY-MM-DD"),
                     style={"marginTop": "2px"})],
                style={"marginBottom": "8px"}))
        else:
            rows.append(html.Div(
                [html.Span(cfg["label"],
                           style={"fontSize": "12px", "flex": "1",
                                  "color": WHITE, "fontWeight": "600"}),
                 dcc.Input(id=pid, type="number", value=cfg["default"],
                           min=cfg.get("min"), max=cfg.get("max"),
                           step=cfg.get("step", 1),
                           style={"width": "80px", "background": BG,
                                  "color": WHITE,
                                  "border": f"1px solid {GRID}",
                                  "borderRadius": "4px",
                                  "padding": "2px 6px",
                                  "fontWeight": "600"})],
                style={"display": "flex", "alignItems": "center",
                       "gap": "8px", "marginBottom": "6px"}))
    return rows


def _collect_strategy_params(strategy_key: str, param_ids, param_values,
                             date_ids=None, date_values=None) -> dict:
    spec = get_strategy_params(strategy_key)
    out = {}
    for ident, value in zip(param_ids or [], param_values or []):
        name = ident.get("param")
        if name not in spec:
            continue
        cfg = spec[name]
        if cfg.get("kind") == "bool":
            out[name] = bool(value)  # checklist value: [] or ["on"]
        elif value is None:
            out[name] = cfg["default"]
        else:
            out[name] = value
    for ident, value in zip(date_ids or [], date_values or []):
        name = ident.get("param")
        if name in spec and spec[name].get("kind") == "date":
            out[name] = value  # None = cleared = unbounded
    # fill anything the UI didn't provide (e.g. before first render)
    for name, cfg in spec.items():
        out.setdefault(name, cfg["default"])
    return out


def _merge_drawings(relayout: dict, stored: dict, symbol: str,
                    figure) -> dict | None:
    """Fold a chart relayout event into the per-symbol drawings store.

    Returns the updated store, or None when the event carried nothing
    drawing-related (pane resizes, zooms, ...). Only shapes flagged
    editable are user drawings — indicator hlines are not saved.
    """
    if not relayout:
        return None
    stored = dict(stored or {})
    if "shapes" in relayout:  # draw or erase: full list provided
        stored[symbol] = [s for s in relayout["shapes"] if s.get("editable")]
        return stored
    edits = {k: v for k, v in relayout.items() if k.startswith("shapes[")}
    if not edits or figure is None:
        return None
    shapes = list((figure.get("layout") or {}).get("shapes") or [])
    for key, value in edits.items():  # e.g. "shapes[3].x0": 17.4
        try:
            idx = int(key[key.index("[") + 1:key.index("]")])
            prop = key.split(".", 1)[1]
        except (ValueError, IndexError):
            continue
        if 0 <= idx < len(shapes):
            shapes[idx] = {**shapes[idx], prop: value}
    stored[symbol] = [s for s in shapes if s.get("editable")]
    return stored


def _fetch_quote(provider_name: str, sym: str):
    try:
        return get_provider(provider_name).get_ohlcv(sym, "1d", 2)
    except ProviderError:
        try:
            return get_provider("sample").get_ohlcv(sym, "1d", 2)
        except ProviderError:
            return None


def _watchlist_rows(symbols: list[str], provider_name: str) -> list:
    rows = []
    # Fetch all quotes concurrently — one slow/unreachable symbol must not
    # stall the whole panel.
    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = list(pool.map(lambda s: _fetch_quote(provider_name, s), symbols))
    for sym, df in zip(symbols, frames):
        last = chg = pct = None
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


_STRAT_GRID = {"display": "grid", "gridTemplateColumns": "1.2fr 1fr 0.9fr",
               "gap": "4px", "alignItems": "center", "padding": "3px 6px"}


def _strategy_df(symbol: str, provider_name: str):
    """Daily bars with maximum history — the frame all strategies run on.

    2800 bars matches the Pine indicator's max lookback, so the KNN model
    trains on the same depth of history as TradingView.
    """
    try:
        return get_provider(provider_name).get_ohlcv(symbol, "1d", 2800)
    except ProviderError:
        try:
            return get_provider("sample").get_ohlcv(symbol, "1d", 2800)
        except ProviderError:
            return None


def _strategy_signals(strategy_key: str, symbol: str, provider_name: str,
                      params: dict) -> list[dict]:
    """Buy flags for the price chart, if the strategy provides them."""
    module = STRATEGIES.get(strategy_key, {}).get("module")
    if module is None or not hasattr(module, "get_signals"):
        return []
    df = _strategy_df(symbol, provider_name)
    if df is None:
        return []
    try:
        return module.get_signals(symbol=symbol, df=df, **params)
    except Exception:
        return []


def _strategy_equity(strategy_key: str, symbol: str, provider_name: str,
                     params: dict):
    """Equity curves for the chart pane, if the strategy provides them."""
    module = STRATEGIES.get(strategy_key, {}).get("module")
    if module is None or not hasattr(module, "get_equity"):
        return None
    df = _strategy_df(symbol, provider_name)
    if df is None:
        return None
    try:
        return module.get_equity(symbol=symbol, df=df, **params)
    except Exception:
        return None


def _strategy_rows(strategy_key: str, symbol: str, provider_name: str,
                   params: dict) -> list:
    """Render rows from the selected strategy for the bottom-right box."""
    module = STRATEGIES.get(strategy_key, {}).get("module")
    if module is None:
        return [html.Div("No strategy selected.",
                         style={"color": "#787b86", "fontSize": "12px"})]
    df = _strategy_df(symbol, provider_name)
    try:
        stats = module.get_stats(symbol=symbol, df=df, **params)
    except Exception as exc:  # a broken user strategy must not kill the UI
        return [html.Div(f"strategy error: {exc}",
                         style={"color": DOWN, "fontSize": "12px"})]
    if not stats:
        return [html.Div(
            ["No strategy wired yet — implement ", html.Code("get_stats()"),
             " in ", html.Code("tvcharts/strategy.py"),
             " to fill this box."],
            style={"color": "#787b86", "fontSize": "12px", "padding": "6px 2px",
                   "lineHeight": "1.5"},
        )]
    rows = []
    for stat in stats:
        rows.append(html.Div(
            [
                html.Span(stat.get("label", ""),
                          style={"color": WHITE, "fontWeight": "700",
                                 "fontSize": "12px"}),
                html.Span(str(stat.get("value", "")),
                          style={"color": TEXT, "fontWeight": "600",
                                 "fontSize": "12px", "textAlign": "right"}),
                html.Span(str(stat.get("status", "")),
                          style={"color": stat.get("status_color", TEXT),
                                 "fontWeight": "700", "fontSize": "12px",
                                 "textAlign": "right"}),
            ],
            style={**_STRAT_GRID, "borderBottom": f"1px solid {GRID}"},
        ))
    return rows


def build_figure(df, symbol: str, active: list[str], params: dict,
                 show_volume: bool = True,
                 signals: list[dict] | None = None,
                 equity: dict | None = None,
                 show_equity_pane: bool = False,
                 show_avg_entry: bool = False,
                 chart_type: str = "candles",
                 drawings: list[dict] | None = None) -> go.Figure:
    pane_inds = [k for k in active if INDICATOR_REGISTRY[k]["kind"] == "pane"]
    overlay_inds = [k for k in active if INDICATOR_REGISTRY[k]["kind"] == "overlay"]

    # clip strategy curves to the chart's visible time range
    if equity:
        equity = {k: s.loc[df.index[0]:df.index[-1]] for k, s in equity.items()}
        if equity["equity"].empty:
            equity = None
    equity_pane = equity is not None and show_equity_pane

    vol_h = 0.12 if show_volume else 0.0
    n_panes = len(pane_inds) + (1 if equity_pane else 0)
    rows = 1 + (1 if show_volume else 0) + n_panes
    if n_panes:
        price_h = 0.55
    else:
        price_h = 1.0 - vol_h
    pane_h = (1.0 - price_h - vol_h) / max(n_panes, 1)
    heights = [price_h] + ([vol_h] if show_volume else []) \
        + [pane_h] * n_panes

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.02, row_heights=heights)

    # -- price ---------------------------------------------------------------
    if chart_type == "line":
        fig.add_trace(go.Scatter(x=df.index, y=df["close"], name=symbol,
                                 line=dict(color=ACCENT, width=1.6)),
                      row=1, col=1)
    elif chart_type == "ohlc":
        fig.add_trace(go.Ohlc(x=df.index, open=df["open"], high=df["high"],
                              low=df["low"], close=df["close"], name=symbol,
                              increasing_line_color=UP,
                              decreasing_line_color=DOWN),
                      row=1, col=1)
    else:
        price_df = ind_heikin_ashi(df) if chart_type == "heikin" else df
        fig.add_trace(
            go.Candlestick(x=price_df.index, open=price_df["open"],
                           high=price_df["high"], low=price_df["low"],
                           close=price_df["close"], name=symbol,
                           increasing_line_color=UP, increasing_fillcolor=UP,
                           decreasing_line_color=DOWN,
                           decreasing_fillcolor=DOWN),
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
        elif key == "ichimoku":
            fig.add_trace(go.Scatter(x=df.index, y=result["span_a"],
                                     name="Span A",
                                     line=dict(color="rgba(67,160,71,0.7)",
                                               width=1)),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["span_b"],
                                     name="Span B",
                                     line=dict(color="rgba(244,67,54,0.7)",
                                               width=1),
                                     fill="tonexty",
                                     fillcolor="rgba(67,160,71,0.12)"),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["tenkan"],
                                     name="Tenkan",
                                     line=dict(color="#2962ff", width=1)),
                          row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=result["kijun"],
                                     name="Kijun",
                                     line=dict(color="#b71c1c", width=1.2)),
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
    if show_volume:
        vol_colors = [UP if c >= o else DOWN
                      for o, c in zip(df["open"], df["close"])]
        fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="Volume",
                             marker_color=vol_colors, opacity=0.5),
                      row=2, col=1)

    # -- oscillator panes ----------------------------------------------------
    first_pane_row = 2 + (1 if show_volume else 0)
    for i, key in enumerate(pane_inds):
        row = first_pane_row + i
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

    # -- strategy equity pane (DCAi vs blind DCA vs deployed capital) -------- #
    if equity_pane:
        row = first_pane_row + len(pane_inds)
        fig.add_trace(go.Scatter(x=equity["invested"].index,
                                 y=equity["invested"], name="Invested",
                                 line=dict(color="#787b86", width=1,
                                           dash="dot", shape="hv")),
                      row=row, col=1)
        fig.add_trace(go.Scatter(x=equity["bench"].index, y=equity["bench"],
                                 name="Blind DCA",
                                 line=dict(color="#b2b5be", width=1.3)),
                      row=row, col=1)
        fig.add_trace(go.Scatter(x=equity["equity"].index, y=equity["equity"],
                                 name="DCAi equity",
                                 line=dict(color=ACCENT, width=1.6)),
                      row=row, col=1)

    if show_avg_entry and equity is not None:
        fig.add_trace(go.Scatter(x=equity["avg_entry"].index,
                                 y=equity["avg_entry"], name="Avg entry",
                                 line=dict(color="#b2b5be", width=1.5,
                                           shape="hv", dash="dash")),
                      row=1, col=1)

    fig.update_layout(
        template="plotly_dark",
        # Keep user zoom/pan across auto-refresh; resets when the chart's
        # identity (symbol, pane structure) actually changes.
        uirevision=f"{symbol}|{len(pane_inds)}|{show_volume}",
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, size=12),
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=True,
        legend=dict(orientation="h", y=1.0, x=0, bgcolor="rgba(0,0,0,0)"),
        xaxis_rangeslider_visible=False,
        hovermode="x",
        hoverdistance=-1,
        spikedistance=-1,
        dragmode="pan",
        newshape=dict(line=dict(color=ACCENT, width=1.5),
                      fillcolor="rgba(41,98,255,0.15)"),
    )
    fig.update_xaxes(
        gridcolor=GRID, showspikes=True, spikemode="across",
        spikecolor="#787b86", spikethickness=1, spikedash="dot",
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
    # Hover values are shown in the fixed bar above the chart (see the
    # ohlc-bar script), so suppress the floating labels that would cover
    # the candles around the cursor.
    fig.update_traces(hoverinfo="none", hovertemplate=None)

    if signals:
        first, last = df.index[0], df.index[-1]
        for sig in signals:
            if first <= sig["time"] <= last:
                # Anchor to the chart's own candle so the flag sits on the bar
                # even if the strategy ran on a longer history.
                side = sig.get("side", "below")
                col = "low" if side == "below" else "high"
                try:
                    y_anchor = float(df.loc[sig["time"], col])
                except KeyError:
                    y_anchor = sig.get("y", sig.get("low"))
                fig.add_annotation(
                    x=sig["time"], y=y_anchor, text=sig["text"],
                    showarrow=True, ax=0, ay=42 if side == "below" else -42,
                    arrowhead=2, arrowwidth=1.5,
                    arrowcolor=sig["color"], bgcolor=sig["color"],
                    font=dict(color="#ffffff", size=10), opacity=0.9,
                    borderpad=3, row=1, col=1,
                )

    # User drawings (trend lines, levels, rects) — appended after all
    # indicator hlines so their layout indexes are stable; editable=True is
    # what distinguishes them from indicator shapes when saving edits.
    for shape in drawings or []:
        try:
            fig.add_shape(**{**shape, "editable": True})
        except Exception:
            continue  # never let a corrupt stored shape break the chart
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

    @app.callback(Output("watchlist-v2", "data"),
                  Input("watch-add", "n_clicks"),
                  Input({"type": "wl-del", "symbol": ALL}, "n_clicks"),
                  State("watchlist-v2", "data"),
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

    @app.callback(Output("drawings", "data"),
                  Input("chart", "relayoutData"),
                  State("drawings", "data"),
                  State("symbol", "value"),
                  State("chart", "figure"),
                  prevent_initial_call=True)
    def save_drawings(relayout, stored, symbol, figure):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            raise dash.exceptions.PreventUpdate
        updated = _merge_drawings(relayout, stored, symbol, figure)
        if updated is None:
            raise dash.exceptions.PreventUpdate
        return updated

    @app.callback(Output("drawings", "data", allow_duplicate=True),
                  Input("add-hline", "n_clicks"),
                  State("symbol", "value"),
                  State("drawings", "data"),
                  State("provider", "value"),
                  prevent_initial_call=True)
    def add_horizontal_line(_clicks, symbol, stored, provider_name):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            raise dash.exceptions.PreventUpdate
        quote = _fetch_quote(provider_name, symbol)  # cached, cheap
        if quote is None or quote.empty:
            raise dash.exceptions.PreventUpdate
        level = float(quote["close"].iloc[-1])
        stored = dict(stored or {})
        shapes = list(stored.get(symbol) or [])
        shapes.append({"type": "line", "xref": "paper", "x0": 0, "x1": 1,
                       "yref": "y", "y0": level, "y1": level,
                       "line": {"color": "#ff9800", "width": 1.5},
                       "editable": True})
        stored[symbol] = shapes
        return stored

    @app.callback(Output("watchlist-body", "children"),
                  Input("watchlist-v2", "data"),
                  Input("provider", "value"),
                  Input("refresh", "n_intervals"))
    def render_watchlist(symbols, provider_name, _tick):
        return _watchlist_rows(list(symbols or []), provider_name)

    @app.callback(Output("strategy-params", "children"),
                  Input("strategy-select", "value"))
    def render_strategy_params(strategy_key):
        return _strategy_param_inputs(strategy_key)

    @app.callback(Output("trades-download", "data"),
                  Input("trades-export", "n_clicks"),
                  State("strategy-select", "value"),
                  State("symbol", "value"),
                  State("provider", "value"),
                  State({"type": "sparam", "param": ALL}, "value"),
                  State({"type": "sdate", "param": ALL}, "date"),
                  State({"type": "sparam", "param": ALL}, "id"),
                  State({"type": "sdate", "param": ALL}, "id"),
                  prevent_initial_call=True)
    def export_trades(_clicks, strategy_key, symbol, provider_name,
                      sparam_values, sdate_values, sparam_ids, sdate_ids):
        module = STRATEGIES.get(strategy_key, {}).get("module")
        symbol = (symbol or "").strip().upper()
        if not symbol or module is None or not hasattr(module, "get_trades"):
            raise dash.exceptions.PreventUpdate
        params = _collect_strategy_params(strategy_key, sparam_ids,
                                          sparam_values, sdate_ids,
                                          sdate_values)
        df = _strategy_df(symbol, provider_name)
        trades = module.get_trades(symbol=symbol, df=df, **params)
        if trades.empty:
            raise dash.exceptions.PreventUpdate
        return dcc.send_data_frame(trades.to_csv,
                                   f"dcai_trades_{symbol}.csv", index=False)

    @app.callback(Output("strategy-body", "children"),
                  Input("strategy-select", "value"),
                  Input("symbol", "value"),
                  Input("provider", "value"),
                  Input("refresh", "n_intervals"),
                  Input({"type": "sparam", "param": ALL}, "value"),
                  Input({"type": "sdate", "param": ALL}, "date"),
                  State({"type": "sparam", "param": ALL}, "id"),
                  State({"type": "sdate", "param": ALL}, "id"))
    def render_strategy(strategy_key, symbol, provider_name, _tick,
                        sparam_values, sdate_values, sparam_ids, sdate_ids):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return []
        params = _collect_strategy_params(strategy_key, sparam_ids,
                                          sparam_values, sdate_ids,
                                          sdate_values)
        return _strategy_rows(strategy_key, symbol, provider_name, params)

    @app.callback(
        Output("chart", "figure"),
        Output("status", "children"),
        Input("provider", "value"),
        Input("symbol", "value"),
        Input("interval", "value"),
        Input("limit", "value"),
        Input("start-date", "date"),
        Input("chart-type", "value"),
        Input("drawings", "data"),
        Input("indicators", "value"),
        Input("panes", "value"),
        Input({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "value"),
        Input("refresh", "n_intervals"),
        Input("strategy-select", "value"),
        Input("strategy-display", "value"),
        Input({"type": "sparam", "param": ALL}, "value"),
        Input({"type": "sdate", "param": ALL}, "date"),
        State({"type": "param", "indicator": dash.ALL, "param": dash.ALL}, "id"),
        State({"type": "sparam", "param": ALL}, "id"),
        State({"type": "sdate", "param": ALL}, "id"),
    )
    def update_chart(provider_name, symbol, interval, limit, start_date,
                     chart_type, drawings_data, active,
                     panes, param_values, _tick, strategy_key, strategy_display,
                     sparam_values, sdate_values, param_ids, sparam_ids,
                     sdate_ids):
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
        signals = []
        equity = None
        display = set(strategy_display or [])
        if interval == "1d":  # strategy overlays only make sense on daily bars
            sparams = _collect_strategy_params(strategy_key, sparam_ids,
                                               sparam_values, sdate_ids,
                                               sdate_values)
            signals = _strategy_signals(strategy_key, symbol, provider_name,
                                        sparams)
            if display & {"equity", "avg"}:
                equity = _strategy_equity(strategy_key, symbol, provider_name,
                                          sparams)
        fig = build_figure(df, symbol, active or [], params,
                           show_volume="volume" in (panes or []),
                           signals=signals, equity=equity,
                           show_equity_pane="equity" in display,
                           show_avg_entry="avg" in display,
                           chart_type=chart_type or "candles",
                           drawings=(drawings_data or {}).get(symbol) or [])
        return fig, status

    return app


def main(host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
    create_app().run(host=host, port=port, debug=debug)
