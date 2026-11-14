"""Registre des stratégies disponibles."""

from __future__ import annotations

from .base import Strategy
from .strategies import DonchianBreakout, MACDMomentum, MovingAverageCross, RSIMeanReversion

STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageCross.name: MovingAverageCross,
    RSIMeanReversion.name: RSIMeanReversion,
    DonchianBreakout.name: DonchianBreakout,
    MACDMomentum.name: MACDMomentum,
}


def list_strategies() -> list[dict]:
    return [
        {"key": cls.name, "label": cls.label, "description": cls.description,
         "params": dict(cls.default_params)}
        for cls in STRATEGIES.values()
    ]


def get_strategy(key: str, **params) -> Strategy:
    if key not in STRATEGIES:
        raise KeyError(f"Stratégie inconnue: {key} (disponibles: {sorted(STRATEGIES)})")
    return STRATEGIES[key](**params)
