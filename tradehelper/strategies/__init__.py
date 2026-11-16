"""Registre des stratégies disponibles."""

from __future__ import annotations

from typing import Any

from .adaptive import AdaptiveRegime
from .base import Strategy
from .ml import MLSignal
from .strategies import DonchianBreakout, MACDMomentum, MovingAverageCross, RSIMeanReversion

STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageCross.name: MovingAverageCross,
    RSIMeanReversion.name: RSIMeanReversion,
    DonchianBreakout.name: DonchianBreakout,
    MACDMomentum.name: MACDMomentum,
    AdaptiveRegime.name: AdaptiveRegime,
    MLSignal.name: MLSignal,
}


def list_strategies() -> list[dict]:
    return [
        {"key": cls.name, "label": cls.label, "description": cls.description, "params": dict(cls.default_params)}
        for cls in STRATEGIES.values()
    ]


def get_strategy(key: str, **params: Any) -> Strategy:
    if key not in STRATEGIES:
        raise KeyError(f"Stratégie inconnue: {key} (disponibles: {sorted(STRATEGIES)})")
    return STRATEGIES[key](**params)


def strategy_version(key: str, params: dict | None = None) -> str:
    """Version figée d'une stratégie (SPEC AUD-04 / GOU-01) : tout changement de
    paramètre produit une NOUVELLE version (hash du jeu de paramètres gelé)."""
    import hashlib
    import json

    p = dict(params) if params is not None else dict(get_strategy(key).params)
    digest = hashlib.sha1(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()[:8]
    return f"{key}@{digest}"
