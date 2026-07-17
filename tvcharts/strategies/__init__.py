"""Strategy registry for the bottom-right panel.

Each entry maps a key to a module exposing:

    get_stats(symbol, df, **params) -> list[dict]   # rows for the panel
    PARAMS (optional) -> dict of adjustable parameters (rendered in the sidebar)

Param spec format::

    PARAMS = {
        "name": {"label": "...", "kind": "number|select|bool",
                 "default": ..., "options": [...],   # select only
                 "min": ..., "max": ..., "step": ...}  # number only
    }
"""

from __future__ import annotations

from .. import strategy as custom_strategy
from . import dcai

STRATEGIES: dict[str, dict] = {
    "none": {"label": "None", "module": None},
    "dcai": {"label": "DCAi (ML DCA)", "module": dcai},
    "custom": {"label": "Custom (strategy.py)", "module": custom_strategy},
}


def get_strategy_params(key: str) -> dict:
    module = STRATEGIES.get(key, {}).get("module")
    return getattr(module, "PARAMS", {}) if module else {}
