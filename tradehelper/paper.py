"""Paper trading : portefeuille fictif persistant (SQLite) qui suit les recommandations
du screener. Aucun ordre réel — voir `broker.py` pour la couche d'exécution guardée.

Règles : positions longues/courtes, poids égaux (1/max_positions de la trésorerie),
frais par exécution (Trade Republic FR par défaut), stop/objectif issus du screener.
"""

from __future__ import annotations

from .data.store import Store, default_store
from .fees import get_fee_model
from .timeutil import utcnow_naive


class PaperError(Exception):
    pass


def _now_str() -> str:
    return utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")


class PaperPortfolio:
    """Portefeuille fictif persistant. `cash` est stocké dans meta 'paper.cash'."""

    def __init__(self, store: Store | None = None, initial_capital: float = 10_000.0, max_positions: int = 5):
        self.store = store or default_store()
        self.initial_capital = float(initial_capital)
        self.max_positions = max(1, int(max_positions))
        cash = self.store.meta_get("paper.cash")
        self.cash = float(cash) if cash is not None else float(initial_capital)

    # ------------------------------------------------------------- helpers
    def open_positions(self) -> list[dict]:
        return self.store.paper_list(status="open")

    def closed_trades(self, limit: int = 100) -> list[dict]:
        return [p for p in self.store.paper_list(status="closed")][-limit:]

    def _persist_cash(self):
        self.store.meta_set("paper.cash", self.cash)

    def reset(self):
        """Tout solder au prix d'entrée (ou dernier connu) et remettre le capital initial."""
        self.store.paper_clear()
        self.cash = self.initial_capital
        self._persist_cash()

    # ------------------------------------------------------------- ordres
    def open_position(
        self,
        symbol: str,
        source: str,
        price: float,
        side: str = "long",
        amount: float | None = None,
        qty: float | None = None,
        stop: float | None = None,
        target: float | None = None,
        strategy: str = "",
        reason: str = "",
        fee=None,
        when: str | None = None,
    ) -> dict:
        """Ouvre une position. `amount` = montant investi (défaut : part égale de la
        trésorerie) ; `qty` = quantité explicite (prioritaire sur amount)."""
        symbol = symbol.upper()
        if price <= 0:
            raise PaperError("prix invalide")
        if any(p["symbol"] == symbol and p["status"] == "open" for p in self.open_positions()):
            raise PaperError(f"position déjà ouverte sur {symbol}")
        if len(self.open_positions()) >= self.max_positions:
            raise PaperError(f"nombre max de positions atteint ({self.max_positions})")
        fee = fee or get_fee_model("trade_republic_fr_auto", source)
        if qty is None:
            amount = self.cash / self.max_positions if amount is None else min(amount, self.cash)
            # ajuste le montant pour que montant + frais <= trésorerie (itération fixe)
            for _ in range(3):
                f = fee.cost(amount)
                if amount + f <= self.cash + 1e-9:
                    break
                amount = self.cash - f
            if amount <= 0:
                raise PaperError("trésorerie insuffisante après frais")
            qty = amount / price
        else:
            qty = float(qty)
            cost = qty * price + fee.cost(qty * price)
            if cost > self.cash:
                raise PaperError("trésorerie insuffisante")
        self.cash -= qty * price + fee.cost(qty * price)
        pos = {
            "symbol": symbol,
            "source": source,
            "side": side,
            "qty": qty,
            "entry_price": float(price),
            "entry_time": when or _now_str(),
            "stop": stop,
            "target": target,
            "strategy": strategy,
            "reason": reason,
            "status": "open",
        }
        pos["id"] = self.store.paper_add(pos)
        self._persist_cash()
        return pos

    def close_position(self, pid: int, price: float, reason: str = "", fee=None, when: str | None = None) -> dict:
        """Clôture une position ouverte (vente à `price`), crédite le cash, calcule le P&L."""
        pos = next((p for p in self.open_positions() if p["id"] == pid), None)
        if pos is None:
            raise PaperError(f"position {pid} introuvable (ouverte)")
        fee = fee or get_fee_model("trade_republic_fr_auto", pos["source"])
        proceeds = pos["qty"] * price
        self.cash += proceeds - fee.cost(proceeds)
        if pos["side"] == "long":
            pnl = (
                (price - pos["entry_price"]) * pos["qty"]
                - fee.cost(pos["qty"] * pos["entry_price"])
                - fee.cost(proceeds)
            )
        else:  # short
            pnl = (
                (pos["entry_price"] - price) * pos["qty"]
                - fee.cost(pos["qty"] * pos["entry_price"])
                - fee.cost(proceeds)
            )
        self.store.paper_update(
            pid,
            status="closed",
            exit_price=float(price),
            exit_time=when or _now_str(),
            pnl=float(pnl),
            exit_reason=reason,
        )
        self._persist_cash()
        pos.update(
            status="closed", exit_price=float(price), exit_time=when or _now_str(), pnl=float(pnl), exit_reason=reason
        )
        return pos

    def close_symbol(self, symbol: str, price: float, reason: str = "", **kw) -> dict:
        pos = next((p for p in self.open_positions() if p["symbol"] == symbol.upper()), None)
        if pos is None:
            raise PaperError(f"pas de position ouverte sur {symbol}")
        return self.close_position(pos["id"], price, reason=reason, **kw)

    # ------------------------------------------------------------- valorisation
    def mark(self, prices: dict[str, float]) -> dict:
        """Valorisation latente : equity = cash + Σ qty × prix courant."""
        equity = self.cash
        latent = 0.0
        for p in self.open_positions():
            px = prices.get(p["symbol"])
            if px is None:
                px = p["entry_price"]
            v = p["qty"] * px
            if p["side"] == "long":
                latent += (px - p["entry_price"]) * p["qty"]
            else:
                latent += (p["entry_price"] - px) * p["qty"]
            equity += v
        return {
            "cash": self.cash,
            "equity": equity,
            "latent_pnl": latent,
            "total_return": (equity / self.initial_capital - 1.0) if self.initial_capital else 0.0,
        }

    def stats(self, prices: dict[str, float] | None = None) -> dict:
        m = self.mark(prices or {})
        closed = self.closed_trades(limit=10_000)
        wins = [t for t in closed if (t["pnl"] or 0) > 0]
        m["n_open"] = len(self.open_positions())
        m["n_closed"] = len(closed)
        m["win_rate"] = len(wins) / len(closed) if closed else None
        m["realized_pnl"] = sum(t["pnl"] or 0 for t in closed)
        return m

    # ------------------------------------------------------------- synchro screener
    def apply_screener(self, rows: list[dict], source: str = "stock") -> dict:
        """Applique les recommandations du screener : ouvre sur les ACHAT, clôture si le
        signal repasse non-ACHAT, ou si stop/objectif touché au dernier cours.

        Retourne {"opened": [...], "closed": [...], "skipped": [...], "n_signals": int}.
        `n_signals` = nombre de lignes de scan traitées (AUD-03 : chaque signal émis est
        compté, aucune exclusion rétrospective)."""
        opened, closed, skipped = [], [], []
        by_sym = {r["symbol"]: r for r in rows if not r.get("error")}
        # 1) clôtures : stop / objectif / signal de sortie
        for p in self.open_positions():
            row = by_sym.get(p["symbol"])
            px = (
                float(row["last_close"])
                if row and isinstance(row.get("last_close"), (int, float))
                else p["entry_price"]
            )
            reason = None
            if row is None:
                reason = "hors du scan"
            elif row.get("position") != "ACHAT":
                reason = f"signal {row.get('position')}"
            elif p["stop"] and p["side"] == "long" and px <= p["stop"]:
                reason = "stop touché"
            elif p["target"] and p["side"] == "long" and px >= p["target"]:
                reason = "objectif atteint"
            if reason:
                closed.append(self.close_position(p["id"], px, reason=reason))
            else:
                skipped.append(p["symbol"])
        # 2) ouvertures sur les ACHAT restants ; toute ligne non actionnée est comptée
        #    dans "skipped" (AUD-03 : aucun signal émis n'est perdu silencieusement)
        for r in rows:
            if r.get("error"):
                skipped.append(f"{r['symbol']} (erreur: {r['error']})")
                continue
            if r.get("position") != "ACHAT":
                skipped.append(f"{r['symbol']} (signal {r.get('position')})")
                continue
            sym = r["symbol"]
            if any(p["symbol"] == sym for p in self.open_positions()):
                continue
            if len(self.open_positions()) >= self.max_positions:
                skipped.append(sym + " (max positions)")
                continue
            entry = r["entry"] if isinstance(r.get("entry"), (int, float)) else r["last_close"]
            stop = r["stop"] if isinstance(r.get("stop"), (int, float)) else None
            target = r["target"] if isinstance(r.get("target"), (int, float)) else None
            try:
                pos = self.open_position(
                    sym,
                    source,
                    float(entry),
                    side="long",
                    stop=stop,
                    target=target,
                    strategy=r.get("best_strategy") or "",
                    reason=f"screener: {r.get('label', '')}",
                )
                opened.append(pos)
            except PaperError as exc:
                skipped.append(f"{sym} ({exc})")
        return {"opened": opened, "closed": closed, "skipped": skipped, "n_signals": len(rows)}
