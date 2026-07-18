"""Alerts: price levels and DCAi buy signals, with notifications.

Alerts are stored server-side (``~/.tvcharts/alerts.json``) so they survive
restarts and fire regardless of which browser is open. A daemon thread
evaluates them every ``CHECK_INTERVAL`` seconds.

Notification channels:
  * in-app  — recent notifications are kept in a deque the UI renders,
  * browser — the UI mirrors new notifications to the desktop via the
              Notification API (see the script in app.INDEX_STRING),
  * Telegram — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment
              variables and every notification is also sent there.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

from .providers import ProviderError, get_provider

CHECK_INTERVAL = 60.0  # seconds between evaluation rounds

CONDITIONS = {
    "price_above": "Price above",
    "price_below": "Price below",
    "dcai_signal": "DCAi buy signal",
}


def _store_path() -> Path:
    return Path(os.environ.get("TVCHARTS_HOME",
                               Path.home() / ".tvcharts")) / "alerts.json"


class AlertStore:
    """JSON-file backed list of alert dicts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _store_path()
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def save(self, alerts: list[dict]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(alerts, indent=2))

    def add(self, symbol: str, condition: str, level: float | None = None,
            provider: str = "binance") -> dict:
        if condition not in CONDITIONS:
            raise ValueError(f"Unknown condition: {condition}")
        alert = {
            "id": uuid.uuid4().hex[:8],
            "symbol": symbol.upper(),
            "condition": condition,
            "level": level,
            "provider": provider,
            "created": datetime.now(timezone.utc).isoformat(),
            "last_fired": None,
        }
        alerts = self.load()
        alerts.append(alert)
        self.save(alerts)
        return alert

    def remove(self, alert_id: str) -> None:
        self.save([a for a in self.load() if a["id"] != alert_id])

    def mark_fired(self, alert_id: str, stamp: str) -> None:
        alerts = self.load()
        for alert in alerts:
            if alert["id"] == alert_id:
                alert["last_fired"] = stamp
        self.save(alerts)


def send_telegram(text: str) -> bool:
    """Send `text` via Telegram if the env vars are configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=10)
        return resp.ok
    except requests.RequestException:
        return False


class AlertEngine:
    """Evaluates alerts and fans notifications out to the channels."""

    def __init__(self, store: AlertStore | None = None) -> None:
        self.store = store or AlertStore()
        self.notifications: deque[dict] = deque(maxlen=50)
        self._thread: threading.Thread | None = None

    # -- evaluation ------------------------------------------------------- #

    def _quote(self, alert: dict):
        try:
            return get_provider(alert.get("provider", "binance")).get_ohlcv(
                alert["symbol"], "1d", 2)
        except ProviderError:
            return None

    def evaluate_alert(self, alert: dict) -> tuple[str, str] | None:
        """Return ``(message, dedup_token)`` if the alert fires, else None.

        The dedup token is stored back on the alert as ``last_fired`` so the
        same event does not notify twice: for one-shot price alerts it is a
        wall-clock timestamp (any non-empty value stops re-firing); for DCAi
        alerts it is the signal bar's timestamp (re-fires on a *new* bar).
        """
        condition = alert["condition"]
        if condition in ("price_above", "price_below"):
            if alert.get("last_fired"):
                return None  # price alerts are one-shot
            df = self._quote(alert)
            if df is None or df.empty:
                return None
            last = float(df["close"].iloc[-1])
            level = float(alert["level"])
            token = datetime.now(timezone.utc).isoformat()
            if condition == "price_above" and last > level:
                return (f"🔔 {alert['symbol']} is above {level:,.6g} "
                        f"(last {last:,.6g})", token)
            if condition == "price_below" and last < level:
                return (f"🔔 {alert['symbol']} is below {level:,.6g} "
                        f"(last {last:,.6g})", token)
            return None
        if condition == "dcai_signal":
            from .strategies import dcai  # late import: heavy module
            try:
                df = get_provider(alert.get("provider", "binance")).get_ohlcv(
                    alert["symbol"], "1d", 2800)
            except ProviderError:
                return None
            signals = dcai.get_signals(alert["symbol"], df)
            if not signals:
                return None
            last_sig = signals[-1]
            if last_sig["time"] != df.index[-1]:
                return None  # signal is not on the latest bar
            stamp = str(last_sig["time"])
            if alert.get("last_fired") == stamp:
                return None  # already notified for this bar
            text = last_sig["text"].replace("<br>", " ")
            return f"🤖 DCAi {alert['symbol']}: {text}", stamp
        return None

    def run_once(self) -> list[dict]:
        """One evaluation round; returns the notifications produced."""
        fired = []
        for alert in self.store.load():
            try:
                result = self.evaluate_alert(alert)
            except Exception:
                continue  # a broken alert must not kill the engine
            if not result:
                continue
            message, token = result
            self.store.mark_fired(alert["id"], token)
            note = {"time": datetime.now(timezone.utc).isoformat(),
                    "message": message, "symbol": alert["symbol"]}
            self.notifications.append(note)
            fired.append(note)
            send_telegram(message)
        return fired

    # -- background thread ------------------------------------------------ #

    def start(self) -> None:
        if self._thread is not None:
            return
        def loop() -> None:
            while True:
                time.sleep(CHECK_INTERVAL)
                self.run_once()
        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="tvcharts-alerts")
        self._thread.start()


# Shared engine instance used by the app
ENGINE = AlertEngine()
