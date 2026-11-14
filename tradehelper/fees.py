"""Modèles de frais de courtage.

Un frais s'applique **par exécution** (chaque ordre) : `flat` (montant fixe, ex. 1 €)
+ `pct` (fraction du notionnel), encadré par `min_fee` / `max_fee`.

Préréglage par défaut : **Trade Republic France** — 1 € par ordre sur actions/ETF,
1 % (min 1 €, max 50 €) sur crypto. Trade Republic ne propose pas le forex :
un modèle générique 0,1 % est utilisé pour le forex.
(Vérifier les tarifs à jour sur le site de Trade Republic avant usage réel.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class FeeModel:
    name: str
    flat: float = 0.0          # € par exécution
    pct: float = 0.0           # fraction du notionnel (0.001 = 0,1 %)
    min_fee: float = 0.0
    max_fee: float = math.inf

    def cost(self, notional: float) -> float:
        """Frais pour un notionnel donné."""
        c = self.flat + self.pct * abs(notional)
        return min(max(c, self.min_fee), self.max_fee)

    def describe(self) -> str:
        parts = []
        if self.flat:
            parts.append(f"{self.flat:g} €/ordre")
        if self.pct:
            parts.append(f"{self.pct * 100:g} %")
        if self.min_fee:
            parts.append(f"min {self.min_fee:g} €")
        if math.isfinite(self.max_fee):
            parts.append(f"max {self.max_fee:g} €")
        return f"{self.name} ({', '.join(parts) if parts else 'gratuit'})"


PRESETS: dict[str, FeeModel] = {
    "trade_republic_fr_auto": FeeModel("Trade Republic FR (auto selon la source)"),
    "trade_republic_fr_actions": FeeModel("Trade Republic FR — actions/ETF", flat=1.0),
    "trade_republic_fr_crypto": FeeModel("Trade Republic FR — crypto", pct=0.01, min_fee=1.0, max_fee=50.0),
    "binance_spot": FeeModel("Binance spot (taker)", pct=0.001),
    "zero": FeeModel("Sans frais", 0.0),
    "courtier_0_1pct": FeeModel("Courtier générique 0,1 %", pct=0.001),
    "forex_0_1pct": FeeModel("Forex générique 0,1 %", pct=0.001),
}

# auto Trade Republic FR : selon la classe d'actifs
_TR_AUTO = {
    "stock": PRESETS["trade_republic_fr_actions"],
    "crypto": PRESETS["trade_republic_fr_crypto"],
    "forex": PRESETS["forex_0_1pct"],  # TR ne fait pas de forex
}


def get_fee_model(preset: str, source: str = "stock") -> FeeModel:
    """Retourne le modèle de frais pour un préréglage (défaut : Trade Republic FR auto)."""
    if preset == "trade_republic_fr_auto":
        return _TR_AUTO.get(source.lower(), PRESETS["trade_republic_fr_actions"])
    if preset == "custom":
        return FeeModel("Personnalisé")
    if preset not in PRESETS:
        raise KeyError(f"Préréglage inconnu: {preset} (dispo: {sorted(PRESETS)})")
    return PRESETS[preset]


def list_presets() -> list[str]:
    return list(PRESETS.keys())
