"""Couche d'exécution « broker » avec garde-fous.

- `PaperBroker` (défaut) : exécute contre le portefeuille fictif (`paper.py`).
- `IBKRBroker` : connexion réelle IBKR — **verrouillée par défaut** : exige
  `TRADEHELPER_ALLOW_LIVE_TRADING=1` + la config IBKR (host/port/client_id) +
  `ib_insync` installé, une confirmation explicite par ordre (`confirm=True`)
  et un plafond de notionnel par ordre (`max_order_notional`). Sans tout cela,
  elle refuse de placer quoi que ce soit. Aucun ordre réel n'est jamais envoyé
  sans validation explicite (règle projet).
"""

from __future__ import annotations

import os

from .paper import PaperPortfolio, PaperError


class BrokerError(Exception):
    pass


class Broker:
    name = "broker"

    def place_order(self, symbol: str, side: str, qty: float, source: str = "stock",
                    limit_price: float | None = None, confirm: bool = False,
                    dry_run: bool = True, **kw) -> dict:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


class PaperBroker(Broker):
    """Exécute sur le portefeuille fictif persistant (aucun ordre réel)."""

    name = "paper"

    def __init__(self, pf: PaperPortfolio):
        self.pf = pf

    def place_order(self, symbol: str, side: str, qty: float, source: str = "stock",
                    limit_price: float | None = None, confirm: bool = False,
                    dry_run: bool = True, reason: str = "", **kw) -> dict:
        if not dry_run:
            raise BrokerError("PaperBroker n'envoie jamais d'ordres réels (dry_run requis).")
        price = limit_price or kw.get("price")
        if price is None:
            raise BrokerError("prix requis (limit_price ou price)")
        if side.lower() in ("buy", "long"):
            pos = self.pf.open_position(symbol, source, float(price), side="long",
                                        qty=qty, reason=reason or "ordre manuel")
            return {"broker": self.name, "status": "filled", "symbol": symbol, "side": "long",
                    "qty": qty, "price": float(price), "position_id": pos["id"], "dry_run": True}
        if side.lower() in ("sell", "close", "short"):
            try:
                pos = self.pf.close_symbol(symbol, float(price), reason=reason or "clôture manuelle")
            except PaperError as exc:
                raise BrokerError(str(exc)) from exc
            return {"broker": self.name, "status": "filled", "symbol": symbol, "side": "closed",
                    "qty": pos["qty"], "price": float(price), "pnl": pos["pnl"], "dry_run": True}
        raise BrokerError(f"side inconnu: {side}")

    def describe(self) -> str:
        return "Paper (portefeuille fictif persistant)"


class IBKRBroker(Broker):
    """IBKR réel — verrouillé. Refuse toute exécution sans :
    TRADEHELPER_ALLOW_LIVE_TRADING=1, config IBKR (host/port/client_id),
    ib_insync installé, confirm=True ET notionnel <= max_order_notional."""

    name = "ibkr"

    def __init__(self, cfg: dict | None = None, max_order_notional: float = 1_000.0):
        from .config import load_config
        cfg = cfg or load_config()
        self.cfg = cfg
        self.max_order_notional = float(cfg.get("max_order_notional", max_order_notional))
        self.ibkr = cfg.get("ibkr", {})
        if os.environ.get("TRADEHELPER_ALLOW_LIVE_TRADING", "").lower() not in ("1", "true", "oui"):
            raise BrokerError("Trading live IBKR désactivé : définir TRADEHELPER_ALLOW_LIVE_TRADING=1 "
                              "(et configurer ibkr host/port/client_id) — mode paper par défaut.")
        if not (self.ibkr.get("host") and self.ibkr.get("port") and self.ibkr.get("client_id")):
            raise BrokerError("Config IBKR incomplète (ibkr.host/port/client_id dans la config).")
        try:
            import ib_insync  # noqa: F401
        except ImportError as exc:
            raise BrokerError("ib_insync non installé — IBKR indisponible, utiliser le paper.") from exc
        self._ib = None  # connexion établie paresseusement dans connect()

    def connect(self):
        if self._ib is None:
            from ib_insync import IB
            self._ib = IB()
            self._ib.connect(self.ibkr["host"], int(self.ibkr["port"]), clientId=int(self.ibkr["client_id"]))
        return self._ib

    def place_order(self, symbol: str, side: str, qty: float, source: str = "stock",
                    limit_price: float | None = None, confirm: bool = False,
                    dry_run: bool = True, **kw) -> dict:
        notional = abs(qty) * (limit_price or kw.get("price") or 0.0)
        if not confirm:
            raise BrokerError("Ordre live refusé : confirmation explicite requise (confirm=True).")
        if notional > self.max_order_notional:
            raise BrokerError(f"Ordre refusé : notionnel {notional:.2f} > plafond {self.max_order_notional:.2f}.")
        if dry_run:
            return {"broker": self.name, "status": "dry_run", "symbol": symbol, "side": side,
                    "qty": qty, "limit_price": limit_price, "notional": notional}
        # exécution réelle : jamais atteinte sans toutes les conditions ci-dessus
        ib = self.connect()
        from ib_insync import Stock, MarketOrder
        contract = Stock(symbol, "SMART", "USD")
        order = MarketOrder("BUY" if side.lower() in ("buy", "long") else "SELL", qty)
        trade = ib.placeOrder(contract, order)
        return {"broker": self.name, "status": "submitted", "symbol": symbol, "side": side,
                "qty": qty, "trade": str(trade)}

    def describe(self) -> str:
        return f"IBKR live (plafond {self.max_order_notional:g} €/ordre, confirmation requise)"


def get_broker(name: str, pf: PaperPortfolio | None = None, cfg: dict | None = None) -> Broker:
    """Fabrique un broker. 'paper' par défaut ; 'ibkr' lève BrokerError si verrouillé."""
    name = (name or "paper").lower()
    if name == "paper":
        return PaperBroker(pf or PaperPortfolio())
    if name == "ibkr":
        return IBKRBroker(cfg=cfg)
    raise BrokerError(f"broker inconnu: {name} (dispo: paper, ibkr)")
