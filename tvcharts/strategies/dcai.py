"""DCAi — Machine Learning based DCA strategy (Python port).

This module is a Python port of the DCAi Pine Script indicator:
    https://github.com/Cerebrux/DCAi   (branch: stable, v26.06.15)
    © 2026 Salih Emin / Cerebrux

This source code is subject to the terms of the GNU Affero General Public
License v3.0 (AGPL-3.0), the license of the original work. Full license text:
https://www.gnu.org/licenses/agpl-3.0.html — a copy is vendored at
third_party/DCAi/LICENSE together with the original Pine source.

IMPORTANT: Under the AGPL-3.0, if you modify this code or use it in a web
service (SaaS, trading platform), you MUST provide its source code to your
users. The rest of this repository is MIT; distributing the app with this
module included is subject to the AGPL's terms.

Port overview (mirrors the sections of dcai.pine):
  * 8 percentile-ranked features (MFI, ROC, ATR, RSI, %B, MA200 distance,
    volume ratio, return stdev) feed a KNN classifier with Lorentzian
    distance and inverse-distance weighted voting.
  * Walk-forward validation adapts the probability threshold to the model's
    rolling accuracy.
  * Decision engine: three buy tiers — PULLBACK (healthy uptrend dip),
    OVERSOLD (MFI oversold) and FEAR (MFI panic) — one signal of each kind
    per month, funded by the monthly budget plus a Savings Pot that
    accumulates skipped months.
  * A blind monthly DCA benchmark runs in parallel for comparison
    (Sortino, max drawdown, ROI, average entry, profit/risk).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

UP = "#26a69a"
DOWN = "#ef5350"
ORANGE = "#ff9800"
GOLD = "#FFD700"
GRAY = "#787b86"
PINK = "#E91E63"
PURPLE = "#ab47bc"
LIME = "#00e676"

ASSET_CLASSES = {
    "crypto": {"label": "Crypto (BTC, ETH, Crypto ETFs)",
               "mfi_min": 0, "mfi_max": 35, "rho": 1.7},
    "stocks": {"label": "Stocks (Tech)",
               "mfi_min": 0, "mfi_max": 55, "rho": 2.0},
    "indices": {"label": "Indices (S&P500, World ETFs)",
                "mfi_min": 30, "mfi_max": 48, "rho": 2.5},
    "commodities": {"label": "Commodities (Gold, Metals)",
                    "mfi_min": 35, "mfi_max": 50, "rho": 2.5},
}

# Adjustable inputs surfaced in the sidebar (mirrors the Pine inputs)
PARAMS = {
    "asset": {"label": "Asset class", "kind": "select", "default": "crypto",
              "options": [{"label": v["label"], "value": k}
                          for k, v in ASSET_CLASSES.items()]},
    "budget": {"label": "Monthly budget (€/$)", "kind": "number",
               "default": 100.0, "min": 1, "step": 1},
    "auto_rho": {"label": "Auto-optimize Rho", "kind": "bool", "default": True},
    "manual_rho": {"label": "Manual sensitivity (Rho)", "kind": "number",
                   "default": 2.0, "min": 0.5, "max": 10, "step": 0.5},
    "smart_cap": {"label": "Max multiplier cap (x)", "kind": "number",
                  "default": 3.0, "min": 1, "step": 0.5},
    "strong_boost": {"label": "Strong buy boost (x)", "kind": "number",
                     "default": 1.5, "min": 1, "step": 0.1},
    "max_boost": {"label": "MAX buy boost (x)", "kind": "number",
                  "default": 2.0, "min": 1, "step": 0.1},
    "pot_reserve": {"label": "Pot reserve (0-0.5)", "kind": "number",
                    "default": 0.1, "min": 0, "max": 0.5, "step": 0.05},
    "knn_neighbors": {"label": "KNN neighbors", "kind": "number",
                      "default": 5, "min": 1, "max": 100, "step": 1},
    "knn_history": {"label": "KNN history lookback", "kind": "number",
                    "default": 1000, "min": 10, "max": 2800, "step": 10},
    "prob_thresh": {"label": "Probability threshold %", "kind": "number",
                    "default": 70.0, "min": 50, "max": 100, "step": 1},
    "ml_boost_sens": {"label": "ML confidence sensitivity", "kind": "number",
                      "default": 2.0, "min": 0.1, "step": 0.1},
    "dca_ml_thresh": {"label": "DCA entry ML %", "kind": "number",
                      "default": 50.0, "min": 0, "max": 100, "step": 1},
    "sb_cooldown": {"label": "Strong buy cooldown (bars)", "kind": "number",
                    "default": 10, "min": 0, "step": 1},
    "kernel_filter": {"label": "Use kernel filter", "kind": "bool",
                      "default": True},
    "kijun_len": {"label": "Kijun length", "kind": "number",
                  "default": 26, "min": 1, "step": 1},
    "mfi_len": {"label": "MFI length", "kind": "number",
                "default": 14, "min": 1, "step": 1},
}

PREDICTION_WINDOW = 21  # bars ahead the model tries to predict


# --------------------------------------------------------------------------- #
# Feature engineering (vectorized)
# --------------------------------------------------------------------------- #

def _percentrank(values: np.ndarray, length: int = 100) -> np.ndarray:
    """Pine ta.percentrank: % of the previous `length` values <= current."""
    n = len(values)
    out = np.full(n, np.nan)
    if n <= length:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    # windows[i] = values[i : i+length]; current value = values[i+length]
    current = values[length:]
    with np.errstate(invalid="ignore"):
        out[length:] = 100.0 * np.mean(windows[:-1] <= current[:, None], axis=1)
    return out


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _mfi(df: pd.DataFrame, length: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    delta = tp.diff()
    flow = tp * df["volume"]
    pos = flow.where(delta > 0, 0.0).rolling(length).sum()
    neg = flow.where(delta < 0, 0.0).rolling(length).sum()
    return 100.0 - 100.0 / (1.0 + pos / neg)


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0.0), length)
    loss = _wilder(-delta.clip(upper=0.0), length)
    return 100.0 - 100.0 / (1.0 + gain / loss)


def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return _wilder(tr, length)


def _kernel_regression(close: np.ndarray, lookback: int, rw: float) -> np.ndarray:
    """Nadaraya-Watson rational quadratic kernel (trailing window)."""
    i = np.arange(lookback)
    w = (1.0 + (i ** 2) / ((lookback * lookback) * 2.0 * rw * rw)) ** (-rw)
    out = np.full(len(close), np.nan)
    for t in range(lookback - 1, len(close)):
        # close[t - i] for i = 0..lookback-1
        out[t] = np.dot(close[t - lookback + 1:t + 1][::-1], w) / w.sum()
    return out


def _donchian_mid(df: pd.DataFrame, length: int) -> pd.Series:
    return (df["low"].rolling(length).min() + df["high"].rolling(length).max()) / 2.0


# --------------------------------------------------------------------------- #
# The simulation
# --------------------------------------------------------------------------- #

def run(df: pd.DataFrame, **p) -> dict:
    """Run the full DCAi simulation over daily OHLCV; return final-bar state."""
    asset = ASSET_CLASSES.get(p.get("asset", "crypto"), ASSET_CLASSES["crypto"])
    mfi_target_min, mfi_target_max = asset["mfi_min"], asset["mfi_max"]
    rho = asset["rho"] if p.get("auto_rho", True) else float(p.get("manual_rho", 2.0))
    budget = float(p.get("budget", 100.0))
    smart_cap = float(p.get("smart_cap", 3.0))
    strong_boost = float(p.get("strong_boost", 1.5))
    max_boost = float(p.get("max_boost", 2.0))
    pot_reserve = float(p.get("pot_reserve", 0.1))
    knn_k = int(p.get("knn_neighbors", 5))
    knn_history = int(p.get("knn_history", 1000))
    prob_thresh = float(p.get("prob_thresh", 70.0))
    ml_boost_sens = float(p.get("ml_boost_sens", 2.0))
    dca_ml_thresh = float(p.get("dca_ml_thresh", 50.0))
    sb_cooldown = int(p.get("sb_cooldown", 10))
    use_kernel = bool(p.get("kernel_filter", True))
    kijun_len = int(p.get("kijun_len", 26))
    mfi_len = int(p.get("mfi_len", 14))

    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)

    # --- indicators / features -------------------------------------------- #
    mf = _mfi(df, mfi_len).to_numpy()
    ma200 = df["close"].rolling(200).mean().to_numpy()
    roc = (100.0 * (df["close"] / df["close"].shift(14) - 1.0)).to_numpy()
    atr = _atr(df, 14).to_numpy()
    rsi = _rsi(df["close"], 14).to_numpy()
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std(ddof=0)
    bb_pct_b = ((df["close"] - sma20) / (2.0 * std20)).to_numpy()
    ma200_dist = (close - ma200) / ma200
    vol_sma50 = df["volume"].rolling(50).mean().to_numpy()
    vol_ratio = volume / vol_sma50
    ret_std5 = df["close"].pct_change().rolling(5).std().to_numpy()

    features = np.column_stack([
        _percentrank(mf), _percentrank(roc), _percentrank(atr),
        _percentrank(rsi), _percentrank(bb_pct_b), _percentrank(ma200_dist),
        _percentrank(vol_ratio), _percentrank(ret_std5),
    ])

    # --- CVD --------------------------------------------------------------- #
    rng = np.maximum(high - low, 1e-5)
    raw_delta = ((close - open_) / rng) * volume
    cvd = np.cumsum(raw_delta)
    low_s, cvd_s = pd.Series(low), pd.Series(cvd)
    price_ll = (low_s.rolling(10).min() <
                low_s.shift(10).rolling(10).min()).to_numpy()
    cvd_hl = (cvd_s.rolling(10).min() >
              cvd_s.shift(10).rolling(10).min()).to_numpy()
    is_cvd_div_bull = price_ll & cvd_hl
    delta_ema = pd.Series(raw_delta).ewm(span=5, adjust=False).mean().to_numpy()
    is_cvd_bullish = is_cvd_div_bull | (raw_delta > delta_ema)

    # --- kernel regression -------------------------------------------------- #
    kernel = _kernel_regression(close, 8, 8.5)
    kernel_prev, close_prev = np.roll(kernel, 1), np.roll(close, 1)
    kernel_bull = (kernel > close) & (kernel_prev <= close_prev)
    kernel_bull[0] = False

    # --- Ichimoku ----------------------------------------------------------- #
    displace = 26
    tenkan = _donchian_mid(df, 9)
    kijun = _donchian_mid(df, kijun_len).to_numpy()
    span_a = ((tenkan + _donchian_mid(df, kijun_len)) / 2.0)
    span_b = _donchian_mid(df, 52)
    lead_a = span_a.shift(displace).to_numpy()
    lead_b = span_b.shift(displace).to_numpy()

    # --- training labels: close[t] > close[t-21] * 0.985 -------------------- #
    labels = np.full(n, np.nan)
    labels[PREDICTION_WINDOW:] = (
        close[PREDICTION_WINDOW:] > close[:-PREDICTION_WINDOW] * 0.985
    ).astype(float)

    # --- bar-by-bar state --------------------------------------------------- #
    months = df.index.year * 12 + df.index.month
    days = df.index.day

    savings_pot = 0.0
    smart_invested = smart_units = 0.0
    monthly_invested = monthly_units = 0.0
    smart_first_bar = monthly_first_bar = None
    smart_returns: list[float] = []
    monthly_returns: list[float] = []
    smart_prev_eq = monthly_prev_eq = 0.0
    last_std_m = last_strong_m = last_max_m = -10**9
    last_strong_buy_bar = 0
    last_distance = 0.0
    pred_log: list[tuple[int, float]] = []   # (bar, prob)
    pred_hits: list[float] = []
    prev_ml_signal = False
    last_flip_bar = -100
    smart_peak = monthly_peak = smart_max_dd = monthly_max_dd = 0.0

    # final-bar snapshot values
    prob = 0.0
    rolling_acc = 0.5
    eff_thresh = prob_thresh
    is_ml_bottom = False
    trigger_std = trigger_strong = trigger_max = False
    recommended = 0.0
    base_strong_condition = False
    min_bull_dist = 100.0

    first_train = PREDICTION_WINDOW + 1  # bar index where training rows start

    for t in range(n):
        month_id = int(months[t])
        is_new_month = t > 0 and months[t] != months[t - 1]

        # -- savings pot accrual + passive benchmark on month start -------- #
        if is_new_month:
            prev_id = month_id - 1
            bought_last_month = prev_id in (last_std_m, last_strong_m, last_max_m)
            if t - 1 >= 0 and not bought_last_month:
                savings_pot += budget
            monthly_invested += budget
            monthly_units += budget / close[t]
            if monthly_first_bar is None:
                monthly_first_bar = t

        # -- KNN prediction ------------------------------------------------- #
        prob = 0.0
        min_bull_dist = 100.0
        if t >= first_train:
            j_end = t                       # training rows appended up to bar t
            j_start = max(first_train, j_end - knn_history + 1)
            train_idx = np.arange(j_start, j_end + 1)
            if len(train_idx) >= knn_k:
                feats = features[train_idx - PREDICTION_WINDOW]
                labs = labels[train_idx]
                cur = features[t]
                with np.errstate(invalid="ignore"):
                    d = np.sum(np.log1p(np.abs(cur[None, :] - feats)), axis=1)
                # Chronological spacing: every 4th entry reuses the previous
                # computed distance (matches the Pine ANN optimization).
                adj = d.copy()
                for i in range(len(adj)):
                    if i % 4 == 0:
                        adj[i] = adj[i - 1] if i > 0 else (
                            last_distance if last_distance > 0 else np.nan)
                    elif not np.isnan(adj[i]):
                        last_distance = adj[i]
                valid = ~np.isnan(adj) & (adj > 0) & ~np.isnan(labs)
                if valid.sum() >= knn_k:
                    v_d, v_l = adj[valid], labs[valid]
                    nearest = np.argpartition(v_d, knn_k - 1)[:knn_k]
                    nd, nl = v_d[nearest], v_l[nearest]
                    w = 1.0 / (1.0 + nd)
                    if w.sum() > 0:
                        prob = 100.0 * w[nl > 0.5].sum() / w.sum()
                    bull = nd[nl > 0.5]
                    if len(bull):
                        min_bull_dist = float(bull.min())

        # -- walk-forward validation ---------------------------------------- #
        kk = 0
        while kk < len(pred_log):
            bar_logged, logged_prob = pred_log[kk]
            if t - bar_logged >= PREDICTION_WINDOW:
                was_bull = logged_prob >= 50
                actual = close[t] > close[t - PREDICTION_WINDOW] * 0.985
                pred_hits.append(1.0 if was_bull == actual else 0.0)
                pred_log.pop(kk)
            else:
                kk += 1
        while len(pred_hits) > 30:
            pred_hits.pop(0)
        rolling_acc = (sum(pred_hits) / len(pred_hits)
                       if len(pred_hits) >= 10 else 0.5)
        eff_thresh = min(85.0, max(prob_thresh, 50 + (rolling_acc - 0.5) * 70))
        is_ml_bottom = prob >= eff_thresh

        # -- signal-flip detection ------------------------------------------ #
        signal_flipped = (is_ml_bottom != prev_ml_signal) and \
            (t - last_flip_bar <= 4)
        if is_ml_bottom != prev_ml_signal:
            last_flip_bar = t
            prev_ml_signal = is_ml_bottom

        # -- decision engine ------------------------------------------------- #
        vr = vol_ratio[t] if not np.isnan(vol_ratio[t]) else 0.0
        vol_boost = 5 if vr > 1.2 else 0
        if is_cvd_div_bull[t]:
            eff_mfi_strong, eff_mfi_max = 35 + 10 + vol_boost, 20 + 5 + vol_boost
        elif vr > 1.2:
            eff_mfi_strong, eff_mfi_max = 35 + vol_boost, 20 + vol_boost
        else:
            eff_mfi_strong, eff_mfi_max = 35, 20

        mft = mf[t] if not np.isnan(mf[t]) else 50.0
        is_strong_zone = mft < eff_mfi_strong
        is_max_zone = mft < eff_mfi_max

        cloud_green = (not np.isnan(lead_a[t]) and not np.isnan(lead_b[t])
                       and lead_a[t] > lead_b[t])
        is_pullback_zone = (cloud_green and not np.isnan(kijun[t])
                            and close[t] <= kijun[t] and close[t] > lead_b[t])
        kernel_ok = (not use_kernel) or kernel_bull[t] or \
            (not np.isnan(ma200[t]) and close[t] > ma200[t])

        trigger_std = trigger_strong = trigger_max = False
        already_strong = month_id in (last_strong_m, last_max_m)
        already_pullback = last_std_m == month_id
        strong_allowed = True
        if already_pullback and (savings_pot - budget) < budget:
            strong_allowed = False

        base_strong_condition = (is_ml_bottom and is_strong_zone
                                 and is_cvd_bullish[t] and not signal_flipped
                                 and (t - last_strong_buy_bar > sb_cooldown))

        if (base_strong_condition and kernel_ok and not already_strong
                and strong_allowed and not np.isnan(kijun[t])
                and close[t] <= kijun[t]):
            if is_max_zone:
                trigger_max = True
                last_max_m = month_id
                last_strong_buy_bar = t
            else:
                trigger_strong = True
                last_strong_m = month_id
                last_strong_buy_bar = t

        if (not already_pullback and is_pullback_zone and prob >= dca_ml_thresh
                and kernel_ok and not trigger_max and not trigger_strong):
            trigger_std = True
            last_std_m = month_id

        # -- amount calculation ---------------------------------------------- #
        ml_conf_mult = 1.0
        if is_ml_bottom and min_bull_dist < 100.0:
            ml_conf_mult = 1.0 + ml_boost_sens / (1.0 + min_bull_dist)
        available_pot = savings_pot * (1.0 - pot_reserve)
        ml_pot_factor = (1.0 / (1.0 + math.exp(-0.08 * (prob - 65)))
                         if prob > 50 else 0.0)

        any_buy_this_month = already_pullback or already_strong
        monthly_salary = 0.0 if any_buy_this_month else budget
        available_capital = monthly_salary + available_pot

        recommended = 0.0
        if trigger_max or trigger_strong or trigger_std:
            # inverse-price weighted smart amount over recent price history
            hist = close[max(0, t - knn_history + 1):t]  # prices before now
            raw_smart = budget
            if len(hist) and close[t] > 0:
                inv = np.power(1.0 / hist[hist > 0], rho)
                if len(inv):
                    mult = min((1.0 / close[t]) ** rho / inv.mean(), smart_cap)
                    raw_smart = budget * mult
            if trigger_max:
                desired = max(raw_smart * max_boost * ml_conf_mult, budget)
                bonus = min(desired * 0.5, available_pot * ml_pot_factor)
            elif trigger_strong:
                desired = max(raw_smart * strong_boost * ml_conf_mult, budget)
                bonus = min(desired * 0.35, available_pot * ml_pot_factor * 0.85)
            else:
                desired = max(raw_smart * ml_conf_mult, budget)
                bonus = min(desired * 0.25, available_pot * ml_pot_factor * 0.6)
            recommended = min(desired + bonus, available_capital)
            if recommended > 0:
                from_salary = min(recommended, monthly_salary)
                savings_pot -= (recommended - from_salary)
                smart_invested += recommended
                smart_units += recommended / close[t]
                if smart_first_bar is None:
                    smart_first_bar = t
                pred_log.append((t, prob))

        # -- returns tracking (time-weighted) -------------------------------- #
        s_eq = smart_units * close[t]
        if smart_prev_eq > 0:
            smart_returns.append((s_eq - recommended - smart_prev_eq)
                                 / smart_prev_eq)
        smart_prev_eq = s_eq
        m_eq = monthly_units * close[t]
        if monthly_prev_eq > 0:
            cash_flow = budget if is_new_month else 0.0
            monthly_returns.append((m_eq - cash_flow - monthly_prev_eq)
                                   / monthly_prev_eq)
        monthly_prev_eq = m_eq

        # -- drawdown tracking (equity incl. pot) ----------------------------- #
        cur_smart_eq = smart_units * close[t] + savings_pot
        cur_monthly_eq = monthly_units * close[t]
        smart_peak = max(smart_peak, cur_smart_eq)
        if smart_peak > 0:
            smart_max_dd = max(smart_max_dd,
                               (smart_peak - cur_smart_eq) / smart_peak)
        monthly_peak = max(monthly_peak, cur_monthly_eq)
        if monthly_peak > 0:
            monthly_max_dd = max(monthly_max_dd,
                                 (monthly_peak - cur_monthly_eq) / monthly_peak)

    # ---- final metrics ------------------------------------------------------ #
    def sortino(returns: list[float]) -> float:
        if len(returns) < 2:
            return 0.0
        arr = np.asarray(returns)
        # Pine divides the sum of squared negative returns by the FULL sample:
        downside = math.sqrt(float(np.sum(np.square(arr[arr < 0]))) / len(arr))
        return float(arr.mean() / downside * math.sqrt(252)) if downside > 0 else 0.0

    t = n - 1
    cur_smart_eq = smart_units * close[t] + savings_pot
    cur_monthly_eq = monthly_units * close[t]
    smart_roi = ((cur_smart_eq - smart_invested) / smart_invested
                 if smart_invested > 0 else 0.0)
    monthly_roi = ((cur_monthly_eq - monthly_invested) / monthly_invested
                   if monthly_invested > 0 else 0.0)
    smart_ann = monthly_ann = None
    if smart_first_bar is not None and smart_invested > 0:
        held = t - smart_first_bar
        if held >= 180:
            smart_ann = (cur_smart_eq / smart_invested) ** (365.0 / held) - 1.0
    if monthly_first_bar is not None and monthly_invested > 0:
        held = t - monthly_first_bar
        if held >= 180:
            monthly_ann = (cur_monthly_eq / monthly_invested) ** (365.0 / held) - 1.0

    return {
        "prob": prob,
        "rolling_acc": rolling_acc,
        "n_verified": len(pred_hits),
        "eff_thresh": eff_thresh,
        "is_ml_bottom": is_ml_bottom,
        "dca_ml_thresh": dca_ml_thresh,
        "close": close[t],
        "kijun": kijun[t],
        "mf": mf[t],
        "mfi_target_min": mfi_target_min,
        "mfi_target_max": mfi_target_max,
        "kernel_bull": bool(kernel_bull[t]),
        "bars_since_flip": t - last_flip_bar,
        "day": int(days[t]), "month": int(months[t] % 12 or 12),
        "budget_used": int(months[t]) in (last_std_m, last_strong_m, last_max_m),
        "savings_pot": savings_pot,
        "smart_invested": smart_invested,
        "monthly_invested": monthly_invested,
        "smart_equity": cur_smart_eq,
        "monthly_equity": cur_monthly_eq,
        "smart_roi": smart_roi, "monthly_roi": monthly_roi,
        "smart_ann": smart_ann, "monthly_ann": monthly_ann,
        "smart_avg_entry": smart_invested / smart_units if smart_units else 0.0,
        "monthly_avg_entry": (monthly_invested / monthly_units
                              if monthly_units else 0.0),
        "smart_sortino": sortino(smart_returns),
        "monthly_sortino": sortino(monthly_returns),
        "smart_max_dd": smart_max_dd, "monthly_max_dd": monthly_max_dd,
        "smart_profit_risk": smart_roi / smart_max_dd if smart_max_dd > 0 else 0.0,
        "monthly_profit_risk": (monthly_roi / monthly_max_dd
                                if monthly_max_dd > 0 else 0.0),
        "trigger_std": trigger_std, "trigger_strong": trigger_strong,
        "trigger_max": trigger_max, "recommended": recommended,
        "wait": base_strong_condition and not np.isnan(kijun[t])
                and close[t] > kijun[t],
    }


# --------------------------------------------------------------------------- #
# Panel rows (mirrors the Pine dashboard)
# --------------------------------------------------------------------------- #

def get_stats(symbol: str | None = None, df: pd.DataFrame | None = None,
              **params) -> list[dict]:
    if df is None or len(df) < 60:
        return [{"label": "DCAi", "value": "need ≥60 daily bars",
                 "status": "WAIT", "status_color": ORANGE}]
    r = run(df, **params)

    ml_status, ml_col = "IDLE", ORANGE
    if r["is_ml_bottom"]:
        ml_status, ml_col = "ACTIVE", LIME
    elif r["prob"] >= r["dca_ml_thresh"]:
        ml_status, ml_col = "STANDBY", UP

    nv = r["n_verified"]
    acc_pct = r["rolling_acc"] * 100
    track_status = ("Learned ✓" if nv >= 30 else
                    f"{'BOOT' if nv < 10 else 'Learning' if nv < 20 else 'Improving'} ({nv})")
    track_col = (LIME if nv >= 30 else GRAY if nv < 10 else
                 LIME if acc_pct >= 70 else ORANGE if acc_pct >= 55 else DOWN)

    kj = r["kijun"]
    dist_kijun = 100.0 * (r["close"] - kj) / kj if kj and not np.isnan(kj) else 0.0
    kijun_status = "Discount" if r["close"] <= kj else "Premium"

    mfv = r["mf"] if not np.isnan(r["mf"]) else 0.0
    mfi_ok = r["mfi_target_min"] <= mfv <= r["mfi_target_max"]
    mfi_status = "✓ OK" if mfi_ok else ("⚡ FEAR" if mfv < r["mfi_target_min"]
                                        else "✖ WAIT")
    mfi_col = UP if mfi_ok else (PINK if mfv < r["mfi_target_min"] else ORANGE)

    flips = r["bars_since_flip"]
    pot = r["savings_pot"]

    if r["trigger_max"]:
        decision, dec_col = "Buy FEAR", PINK
    elif r["trigger_strong"]:
        decision, dec_col = "Buy Oversold", PURPLE
    elif r["trigger_std"]:
        decision, dec_col = "BUY", UP
    elif r["wait"]:
        decision, dec_col = "Wait", ORANGE
    else:
        decision, dec_col = "HODL", GRAY
    amount = f"€{r['recommended']:,.0f}" if r["recommended"] >= 1 else ""

    def money(v):
        return f"€{v:,.0f}"

    def pct(v):
        return f"{v * 100:+.2f}%"

    def cmp_col(a, b, invert=False):
        better = a < b if invert else a > b
        return UP if better else "#d1d4dc"

    ann_s = pct(r["smart_ann"]) if r["smart_ann"] is not None else "N/A (<6m)"
    ann_m = pct(r["monthly_ann"]) if r["monthly_ann"] is not None else "N/A (<6m)"

    return [
        {"label": "Signal Prob", "value": f"{r['prob']:.1f}%",
         "status": ml_status, "status_color": ml_col},
        {"label": "Track Record", "value": f"{acc_pct:.1f}%",
         "status": track_status, "status_color": track_col},
        {"label": "Price vs Kijun", "value": f"{dist_kijun:+.2f}%",
         "status": kijun_status,
         "status_color": UP if kijun_status == "Discount" else ORANGE},
        {"label": "MFI", "value": f"{mfv:.1f}",
         "status": mfi_status, "status_color": mfi_col},
        {"label": "Signal Stability", "value": f"{flips} bars",
         "status": "STABLE" if flips > 4 else f"FLIP ({flips})",
         "status_color": LIME if flips > 4 else ORANGE},
        {"label": "Monthly Status", "value": f"{r['day']}/{r['month']}",
         "status": "✓ Used" if r["budget_used"] else "○ OPEN",
         "status_color": UP if r["budget_used"] else "#ffffff"},
        {"label": "Savings Pot", "value": money(pot),
         "status": "ACCUM" if pot > 0 else "EMPTY",
         "status_color": GOLD if pot > 0 else GRAY},
        {"label": "Benchmark", "value": "DCAi", "status": "Blind DCA",
         "status_color": GRAY},
        {"label": "Invested", "value": money(r["smart_invested"]),
         "status": money(r["monthly_invested"]), "status_color": GRAY},
        {"label": "Total Value", "value": money(r["smart_equity"]),
         "status": money(r["monthly_equity"]), "status_color": GRAY},
        {"label": "Total ROI", "value": pct(r["smart_roi"]),
         "status": pct(r["monthly_roi"]),
         "status_color": UP if r["monthly_roi"] >= 0 else DOWN},
        {"label": "Ann. Return", "value": ann_s, "status": ann_m,
         "status_color": GRAY},
        {"label": "Avg Entr.", "value": f"{r['smart_avg_entry']:,.2f}",
         "status": f"{r['monthly_avg_entry']:,.2f}", "status_color": GRAY},
        {"label": "Sortino", "value": f"{r['smart_sortino']:.2f}",
         "status": f"{r['monthly_sortino']:.2f}", "status_color": GRAY},
        {"label": "Max Pain", "value": f"{r['smart_max_dd'] * 100:.1f}%",
         "status": f"{r['monthly_max_dd'] * 100:.1f}%",
         "status_color": cmp_col(r["smart_max_dd"], r["monthly_max_dd"],
                                 invert=True)},
        {"label": "Profit/Risk", "value": f"{r['smart_profit_risk']:.2f}",
         "status": f"{r['monthly_profit_risk']:.2f}", "status_color": GRAY},
        {"label": "Decision", "value": decision or "",
         "status": amount or decision, "status_color": dec_col},
    ]
