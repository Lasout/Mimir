"""Moteur paper trading conforme — SPEC.md §3.5 (PAP-01…PAP-08).

Cycle quotidien (exécution idempotente) :
1. Après la clôture de J, `record_signal` enregistre le conseil avec les données de
   J seulement (entrée planifiée, stop, cible, risque %). trade_id =
   hash(strategy_version, asset, signal_date) -> rejouer le même jour ne crée pas
   de doublon (PAP-05). Horodatage à l'horloge réelle (PAP-05).
2. `fill_pending` remplit au **open de J+1** + slippage, jamais au prix du signal
   (PAP-01).
3. `on_bar` suit la position : stop et cible vérifiés sur la barre du jour ; si les
   deux sont touchés le même jour, **stop prioritaire** (PAP-03).
4. À la clôture : R-multiple **net de frais et slippage** écrit dans le journal
   (PAP-04) : r_realized = pnl_net / risque_initial_au_stop.

Modèle de coûts non négociable (PAP-06) : frais aller-retour >= 0,1 % du notionnel
(plancher), slippage 0,05 % par côté (fixe) ou basé sur la volatilité, appliqué
**contre** la position à l'entrée et à la sortie. Aucune métrique de référence
sans coûts.

Tableau de bord (PAP-08, `dashboard`) : espérance en R, win rate + payoff, max DD
en R et en %, benchmark buy-and-hold des mêmes actifs sur la même fenêtre, n trades
vs win rate d'équilibre = 1/(1+payoff), IC de l'espérance (± 2 sigma / sqrt(n)).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import pandas as pd

from .data.store import Store
from .timeutil import utcnow_naive

MIN_FEE_RT_PCT = 0.001  # PAP-06 : plancher non négociable, 0,1 % aller-retour
DEFAULT_SLIPPAGE_PCT = 0.0005  # 0,05 % par côté


class PaperEngineError(Exception):
    pass


@dataclass
class CostModel:
    """Modèle de coûts PAP-06. `fee_pct_rt` = frais aller-retour (plancher 0,1 %).
    `slippage_pct` = slippage fixe par côté ; `slippage_mode` = "fixed" | "vol"
    (dans ce cas slippage = vol_mult * atr_pct de la barre)."""

    fee_pct_rt: float = MIN_FEE_RT_PCT
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    slippage_mode: str = "fixed"  # "fixed" | "vol"
    vol_mult: float = 0.25  # mode "vol" : slippage = vol_mult * ATR%

    def __post_init__(self) -> None:
        self.fee_pct_rt = max(float(self.fee_pct_rt), MIN_FEE_RT_PCT)  # plancher
        if self.slippage_mode not in ("fixed", "vol"):
            raise PaperEngineError(f"slippage_mode inconnu: {self.slippage_mode}")

    def fee_side(self, notional: float) -> float:
        """Frais d'un côté (entrée ou sortie) : la moitié du aller-retour."""
        return abs(notional) * self.fee_pct_rt / 2.0

    def slippage_rate(self, atr_pct: float | None = None) -> float:
        if self.slippage_mode == "vol" and atr_pct:
            return self.vol_mult * float(atr_pct)
        return self.slippage_pct

    def apply_entry(self, open_price: float, direction: str, atr_pct: float | None = None) -> float:
        """Prix de remplissage à l'entrée, slippage appliqué CONTRE la position."""
        s = self.slippage_rate(atr_pct)
        return open_price * (1 + s) if direction == "long" else open_price * (1 - s)

    def apply_exit(self, price: float, direction: str, atr_pct: float | None = None) -> float:
        """Prix de sortie, slippage appliqué CONTRE la position."""
        s = self.slippage_rate(atr_pct)
        return price * (1 - s) if direction == "long" else price * (1 + s)


def make_trade_id(strategy_version: str, asset: str, signal_date: str) -> str:
    """trade_id unique = hash(strategy_version, asset, signal_date) — idempotence PAP-05."""
    raw = f"{strategy_version}|{asset.upper()}|{signal_date}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class Bar:
    """Une barre OHLC de suivi (date, open, high, low, close, atr_pct optionnel)."""

    date: str
    open: float
    high: float
    low: float
    close: float
    atr_pct: float | None = None

    @classmethod
    def from_row(cls, ts, row) -> Bar:
        return cls(
            date=str(pd.Timestamp(ts).date()),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            atr_pct=float(row["ATR_pct"]) if "ATR_pct" in row.index and pd.notna(row.get("ATR_pct")) else None,
        )


class PaperEngine:
    """Moteur d'exécution simulée conforme (SPEC §3.5). Persistance SQLite via Store."""

    def __init__(
        self,
        store: Store,
        initial_capital: float = 100_000.0,
        risk_pct: float = 0.01,
        cost: CostModel | None = None,
        max_positions: int = 5,
        clock=None,
    ):
        self.store = store
        self.initial_capital = float(initial_capital)
        self.risk_pct = float(risk_pct)
        self.cost = cost or CostModel()
        self.max_positions = int(max_positions)
        self.clock = clock or utcnow_naive  # PAP-05 : horloge réelle (injectable pour tests)

    # ------------------------------------------------------------- trésorerie
    @property
    def cash(self) -> float:
        v = self.store.meta_get("paper_engine.cash")
        return float(v) if v is not None else self.initial_capital

    @cash.setter
    def cash(self, v: float) -> None:
        self.store.meta_set("paper_engine.cash", float(v))

    def equity(self) -> float:
        """Capital + positions ouvertes valorisées au prix d'entrée (mark simple)."""
        return self.cash + sum(
            (t["qty"] or 0) * (t["fill_price"] or t["planned_entry"]) for t in self.store.ptrade_list(status="open")
        )

    def reset(self) -> None:
        self.store.ptrade_clear()
        self.cash = self.initial_capital

    # ------------------------------------------------------------- 1) signal (jour J)
    def record_signal(
        self,
        *,
        signal_date: str,
        asset: str,
        source: str,
        direction: str,
        planned_entry: float,
        stop: float,
        target: float,
        strategy_version: str,
        risk_pct: float | None = None,
    ) -> dict:
        """Enregistre le conseil du jour J (données de J uniquement — PAP-02).

        Idempotent (PAP-05) : même (strategy_version, asset, signal_date) -> même
        trade_id, aucune duplication. `signal_date` au format YYYY-MM-DD."""
        asset = asset.upper()
        direction = direction.lower()
        if direction not in ("long", "short"):
            raise PaperEngineError(f"direction invalide: {direction}")
        if planned_entry <= 0 or stop <= 0 or target <= 0:
            raise PaperEngineError("prix invalides")
        risk = float(stop - planned_entry if direction == "short" else planned_entry - stop)
        if risk <= 0:
            raise PaperEngineError("stop incohérent avec la direction (risque <= 0)")
        tid = make_trade_id(strategy_version, asset, signal_date)
        existing = self.store.ptrade_get(tid)
        if existing is not None:
            return existing  # PAP-05 : idempotent
        if (
            len(self.store.ptrade_list(status="open")) + len(self.store.ptrade_list(status="pending"))
            >= self.max_positions
        ):
            raise PaperEngineError(f"nombre max de positions atteint ({self.max_positions})")
        rp = risk_pct if risk_pct is not None else self.risk_pct
        r_planned = (target - planned_entry) / risk if direction == "long" else (planned_entry - target) / risk
        trade = {
            "trade_id": tid,
            "strategy_version": strategy_version,
            "signal_date": signal_date,
            "asset": asset,
            "source": source,
            "direction": direction,
            "planned_entry": float(planned_entry),
            "stop": float(stop),
            "target": float(target),
            "risk_pct": rp,
            "r_planned": float(r_planned),
            "status": "pending",
            "created_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),  # PAP-05 : horloge réelle
        }
        self.store.ptrade_add(trade)
        return self.store.ptrade_get(tid)

    # ------------------------------------------------------------- 2) remplissage (open de J+1)
    def fill_pending(self, asset: str, source: str, bar: Bar) -> dict | None:
        """Remplit les signaux en attente sur `asset` au **open de la barre** (J+1),
        slippage contre la position (PAP-01). Ne remplit jamais au prix du signal.
        Idempotent : un trade déjà rempli est retourné tel quel."""
        asset = asset.upper()
        filled = None
        for t in self.store.ptrade_list(status="pending"):
            if t["asset"] != asset or t["source"] != source:
                continue
            if bar.date <= t["signal_date"]:
                raise PaperEngineError(
                    f"fill interdit le jour du signal (PAP-01) : signal {t['signal_date']}, barre {bar.date}"
                )
            # sizing : risk_pct * equity / |planned_entry - stop|
            risk_money = self.equity() * t["risk_pct"]
            per_unit = abs(t["planned_entry"] - t["stop"])
            qty = risk_money / per_unit if per_unit > 0 else 0.0
            entry_price = self.cost.apply_entry(bar.open, t["direction"], bar.atr_pct)
            notional = qty * entry_price
            fee_in = self.cost.fee_side(notional)
            slip_in = abs(qty * (entry_price - bar.open))
            if notional + fee_in > self.cash + 1e-9:
                raise PaperEngineError(
                    f"trésorerie insuffisante pour {asset} ({notional + fee_in:.2f} > {self.cash:.2f})"
                )
            self.cash = self.cash - notional - fee_in
            self.store.ptrade_update(
                t["trade_id"],
                status="open",
                qty=qty,
                fill_price=entry_price,
                fill_date=bar.date,
                fees=fee_in,
                slippage_cost=slip_in,
            )
            filled = self.store.ptrade_get(t["trade_id"])
        return filled

    # ------------------------------------------------------------- 3) suivi quotidien
    def on_bar(self, asset: str, source: str, bar: Bar) -> list[dict]:
        """Vérifie stop/cible des trades ouverts sur `asset` pour la barre du jour.
        Si stop ET cible sont touchés le même jour : **stop prioritaire** (PAP-03).
        Retourne la liste des trades clôturés (avec r_realized)."""
        asset = asset.upper()
        closed = []
        for t in self.store.ptrade_list(status="open"):
            if t["asset"] != asset or t["source"] != source:
                continue
            if bar.date <= (t["fill_date"] or ""):
                continue  # pas de suivi le jour du fill
            d = t["direction"]
            stop_hit = (bar.low <= t["stop"]) if d == "long" else (bar.high >= t["stop"])
            target_hit = (bar.high >= t["target"]) if d == "long" else (bar.low <= t["target"])
            if stop_hit and target_hit:
                reason, raw = "stop", t["stop"]  # PAP-03 : stop prioritaire
            elif stop_hit:
                reason, raw = "stop", t["stop"]
            elif target_hit:
                reason, raw = "target", t["target"]
            else:
                continue
            # gap : si l'open est déjà au-delà du stop, on sort à l'open
            if d == "long" and bar.open < t["stop"]:
                raw = bar.open
            if d == "short" and bar.open > t["stop"]:
                raw = bar.open
            exit_price = self.cost.apply_exit(raw, d, bar.atr_pct)
            slip_out = (t["qty"] or 0.0) * abs(exit_price - raw)
            self._close(t, bar.date, exit_price, slip_out, reason)
            closed.append(self.store.ptrade_get(t["trade_id"]))
        return closed

    def close_trade(self, trade_id: str, bar: Bar, reason: str = "règle de sortie") -> dict:
        """Clôture manuelle/explicite à la barre donnée (règle de sortie)."""
        t = self.store.ptrade_get(trade_id)
        if t is None or t["status"] != "open":
            raise PaperEngineError(f"trade {trade_id} non ouvert")
        exit_price = self.cost.apply_exit(bar.close, t["direction"], bar.atr_pct)
        slip_out = (t["qty"] or 0.0) * abs(exit_price - bar.close)
        self._close(t, bar.date, exit_price, slip_out, reason)
        return self.store.ptrade_get(trade_id)

    def close_all(self, bar_by_asset: dict[tuple[str, str], Bar], reason: str = "fin de période") -> list[dict]:
        """Clôture toutes les positions ouvertes (fin de période de test)."""
        out = []
        for t in self.store.ptrade_list(status="open"):
            bar = bar_by_asset.get((t["asset"], t["source"]))
            if bar is None:
                continue
            exit_price = self.cost.apply_exit(bar.close, t["direction"], bar.atr_pct)
            slip_out = (t["qty"] or 0.0) * abs(exit_price - bar.close)
            self._close(t, bar.date, exit_price, slip_out, reason)
            out.append(self.store.ptrade_get(t["trade_id"]))
        return out

    def _close(self, t: dict, exit_date: str, exit_price: float, slip_out: float, reason: str) -> None:
        """Clôture : frais + slippage de sortie, R-multiple net (PAP-04/06).

        `exit_price` inclut déjà le slippage (prix réellement obtenu) ; `slip_out`
        est le coût de slippage en monnaie. pnl = net de frais ET de slippage."""
        d = t["direction"]
        qty = t["qty"] or 0.0
        fee_in = t["fees"] or 0.0
        fee_out = self.cost.fee_side(qty * exit_price)
        # pnl net : écart des prix d'exécution (slippage inclus) moins les frais
        gross = (exit_price - t["fill_price"]) * qty * (1 if d == "long" else -1)
        pnl = gross - fee_in - fee_out
        # mise à jour de la trésorerie (l'entrée avait déjà été décomptée au fill)
        if d == "long":
            self.cash = self.cash + qty * exit_price - fee_out
        else:
            self.cash = self.cash - qty * exit_price - fee_out
        # risque initial au stop, en monnaie (PAP-04 : R = pnl / risque initial)
        risk_money = abs(t["planned_entry"] - t["stop"]) * qty
        r_realized = pnl / risk_money if risk_money > 0 else None
        self.store.ptrade_update(
            t["trade_id"],
            status="closed",
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=reason,
            fees=fee_in + fee_out,
            slippage_cost=(t["slippage_cost"] or 0.0) + slip_out,
            pnl=pnl,
            r_realized=r_realized,
        )

    # ------------------------------------------------------------- 4) tableau de bord (PAP-08)
    def dashboard(self) -> dict:
        """Métriques de pilotage, recalculées UNIQUEMENT sur les trades clôturés (PAP-08).
        Toutes nettes de frais et slippage (PAP-06)."""
        closed = [t for t in self.store.ptrade_list(status="closed") if t["r_realized"] is not None]
        rs = [t["r_realized"] for t in closed]
        n = len(rs)
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        expectancy = sum(rs) / n if n else None
        ic = None
        if n >= 2:
            sd = (sum((r - expectancy) ** 2 for r in rs) / (n - 1)) ** 0.5
            ic = 2.0 * sd / math.sqrt(n)
        win_rate = len(wins) / n if n else None
        payoff = (sum(wins) / len(wins)) / (sum(-r for r in losses) / len(losses)) if wins and losses else None
        breakeven_wr = 1.0 / (1.0 + payoff) if payoff else None
        # drawdown : courbe en R (cumul des r_realized dans l'ordre de sortie)
        cum, peak, maxdd_r = 0.0, 0.0, 0.0
        rs_by_exit = [t["r_realized"] for t in sorted(closed, key=lambda t: (t["exit_date"] or "", t["trade_id"]))]
        for r in rs_by_exit:
            cum += r
            peak = max(peak, cum)
            maxdd_r = min(maxdd_r, cum - peak)
        # drawdown en % du capital : courbe d'equity reconstruite depuis les pnl nets
        pnls = [
            t["pnl"]
            for t in sorted(closed, key=lambda t: (t["exit_date"] or "", t["trade_id"]))
            if t["pnl"] is not None
        ]
        eq, eq_peak, maxdd_pct = self.initial_capital, self.initial_capital, 0.0
        for p in pnls:
            eq += p
            eq_peak = max(eq_peak, eq)
            if eq_peak > 0:
                maxdd_pct = min(maxdd_pct, (eq - eq_peak) / eq_peak)
        return {
            "n_closed": n,
            "n_open": len(self.store.ptrade_list(status="open")),
            "n_pending": len(self.store.ptrade_list(status="pending")),
            "expectancy_r": expectancy,  # cible +0,15 R .. +0,30 R
            "expectancy_ic95": ic,  # ± 2 sigma / sqrt(n)
            "win_rate": win_rate,
            "payoff": payoff,  # avg win / avg loss (en R)
            "breakeven_win_rate": breakeven_wr,  # 1 / (1 + payoff)
            "max_drawdown_r": maxdd_r if n else None,
            "max_drawdown_pct": maxdd_pct if n else None,
            "cash": self.cash,
            "equity": self.equity(),
            "total_return": (self.equity() / self.initial_capital - 1.0) if self.initial_capital else None,
        }

    def benchmark_buy_hold(self, interval: str = "1d") -> dict | None:
        """Benchmark PAP-08 : buy-and-hold des mêmes actifs sur la même fenêtre
        [premier fill, dernière sortie], lu dans le store. Rendement moyen égal-pondéré."""
        closed = self.store.ptrade_list(status="closed")
        if not closed:
            return None
        first = min(t["fill_date"] for t in closed if t["fill_date"])
        last = max(t["exit_date"] for t in closed if t["exit_date"])
        assets = sorted({(t["asset"], t["source"]) for t in closed})
        rets = []
        for asset, source in assets:
            df = self.store.read(source, asset, interval=interval, start=pd.Timestamp(first), end=pd.Timestamp(last))
            if df is None or len(df) < 2:
                continue
            rets.append(float(df["Close"].iloc[-1] / df["Open"].iloc[0] - 1.0))
        if not rets:
            return None
        return {"window": [first, last], "n_assets": len(rets), "mean_return": sum(rets) / len(rets), "per_asset": rets}
