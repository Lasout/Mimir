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
from .gonogo import evaluate_gonogo
from .regime import detect_regime
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
        self._default_capital = float(initial_capital)  # capital de référence pour les nouveaux tests
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

    # ------------------------------------------------------------- gouvernance (GOU, SPEC §3.7)
    def start_test(
        self,
        name: str,
        strategy_version: str,
        risk_pct: float | None = None,
        cost: CostModel | None = None,
        initial_capital: float | None = None,
    ) -> dict:
        """Démarre un NOUVEAU test (GOU-01/02, AUD-04) : la config (version de stratégie,
        capital, risque/trade, modèle de coûts) est figée dans `meta` pour toute la durée
        du test. Tout changement de paramètre = nouveau test (compteur remis à zéro :
        les anciens trades sont effacés et le capital réinitialisé)."""
        test_id = hashlib.sha1(
            f"{name}|{strategy_version}|{self.clock().strftime('%Y-%m-%d %H:%M:%S.%f')}".encode()
        ).hexdigest()[:12]
        cost = cost or self.cost
        cfg = {
            "test_id": test_id,
            "name": name,
            "strategy_version": strategy_version,
            "risk_pct": float(risk_pct if risk_pct is not None else self.risk_pct),
            "initial_capital": float(initial_capital if initial_capital is not None else self._default_capital),
            "cost": {
                "fee_pct_rt": cost.fee_pct_rt,
                "slippage_pct": cost.slippage_pct,
                "slippage_mode": cost.slippage_mode,
                "vol_mult": cost.vol_mult,
            },
            "started_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.store.meta_set("paper_engine.test", cfg)
        self.store.meta_set("paper_engine.last_review_n", 0)
        self.store.ptrade_clear()  # nouveau test = nouvel échantillon (AUD-04)
        self.initial_capital = cfg["initial_capital"]
        self.risk_pct = cfg["risk_pct"]
        self.cost = cost
        self.cash = cfg["initial_capital"]  # capital fixé en début de test (GOU-02)
        return cfg

    def active_test(self) -> dict | None:
        """Config du test en cours (figée dans meta), ou None si aucun test démarré."""
        return self.store.meta_get("paper_engine.test")

    def add_idea(self, text: str, strategy_version: str | None = None) -> int:
        """Carnet d'idées SÉPARÉ (GOU-01) : une idée n'est jamais appliquée à la version
        en cours — journalisée (kind='idée') avec la version visée."""
        ver = strategy_version or (self.active_test() or {}).get("strategy_version") or "?"
        return self.store.journal_add(ver, "idée", text)

    def list_ideas(self) -> list[dict]:
        return [e for e in self.store.journal_list(500) if e["kind"] == "idée"]

    def cancel_signal(self, trade_id: str) -> None:
        """GOU-03 : aucun décrochage manuel — pas d'annulation rétrospective d'un signal.
        Un conseil « absurde » est loggué et tradé virtuellement, jamais annulé."""
        raise PaperEngineError("annulation d'un signal interdite (GOU-03) : on le loggue et on le trade virtuellement")

    def check_review(self) -> bool:
        """GOU-04 : revue à chaque palier de 100 trades clôturés (test en cours).
        Retourne True si un nouveau palier de 100 a été franchi (et loggue la revue)."""
        cfg = self.active_test()
        if not cfg:
            return False
        n = len(
            [
                t
                for t in self.store.ptrade_list(status="closed")
                if t["test_id"] == cfg["test_id"] and t["r_realized"] is not None
            ]
        )
        last = int(self.store.meta_get("paper_engine.last_review_n") or 0)
        if n // 100 > last // 100:
            self.store.meta_set("paper_engine.last_review_n", n)
            self.store.log_run(
                "paper_review",
                cfg["strategy_version"],
                f"REVUE (GOU-04) : palier de 100 trades franchi (n={n})",
                {"n_closed": n},
            )
            return True
        return False

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
        cfg = self.active_test()
        if cfg is None:
            raise PaperEngineError("aucun test démarré : appeler start_test() (GOU-02)")
        if cfg["strategy_version"] != strategy_version:
            raise PaperEngineError(
                f"version {strategy_version} != version du test en cours {cfg['strategy_version']} "
                "(GOU-01/AUD-04) : démarrer un nouveau test avec start_test()"
            )
        tid = make_trade_id(strategy_version, asset, signal_date)
        existing = self.store.ptrade_get(tid)
        if existing is not None:
            return existing  # PAP-05 : idempotent
        if (
            len(self.store.ptrade_list(status="open")) + len(self.store.ptrade_list(status="pending"))
            >= self.max_positions
        ):
            raise PaperEngineError(f"nombre max de positions atteint ({self.max_positions})")
        rp = float(risk_pct if risk_pct is not None else cfg["risk_pct"])
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
            "test_id": cfg["test_id"],
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
    def dashboard(self, test_id: str | None = None) -> dict:
        """Métriques de pilotage, recalculées UNIQUEMENT sur les trades clôturés (PAP-08),
        restreintes au test en cours par défaut (GOU-02 : compteur par version de test).
        Toutes nettes de frais et slippage (PAP-06)."""
        cfg = self.active_test()
        tid = test_id or (cfg or {}).get("test_id")
        all_closed = [t for t in self.store.ptrade_list(status="closed") if t["r_realized"] is not None]
        closed = [t for t in all_closed if tid is None or t["test_id"] == tid]
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
            "test": cfg,
            "n_closed": n,
            "n_open": len(self.store.ptrade_list(status="open")),
            "n_pending": len(self.store.ptrade_list(status="pending")),
            "n_ideas": len(self.list_ideas()),
            "review_due": (n > 0 and n % 100 == 0),
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
            # AUD-06 : significativité statistique — il faut un échantillon suffisant
            # (>= 100 trades clôturés) ET un intervalle de confiance à 95 % qui exclut 0
            "min_trades_ok": n >= 100,
            "significance_ok": bool(n >= 100 and expectancy is not None and ic is not None and abs(expectancy) > ic),
            "conclusion": (
                "Échantillon insuffisant (moins de 100 trades clôturés) : aucune conclusion "
                "statistique fiable, continuer le paper trading."
                if n < 100
                else (
                    "Expectancy significativement différente de 0 (IC 95 % exclut 0)."
                    if expectancy is not None and ic is not None and abs(expectancy) > ic
                    else "Expectancy non significativement différente de 0 : on ne sait rien conclure."
                )
            ),
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

    # ------------------------------------------------------------- 5) grille go/no-go (GNG, SPEC §5)
    def gonogo(self, test_id: str | None = None) -> dict:
        """Grille go/no-go capital réel (P6.4, SPEC §5 GNG-01…06) : les 6 critères évalués
        simultanément sur le test en cours — dashboard (GNG-01…04), benchmark B&H des mêmes
        actifs sur la même fenêtre (GNG-05) et régimes de marché détectés sur chaque actif
        tradé pendant la fenêtre du test (GNG-06, AUD-05). Un seul critère manquant = NO-GO."""
        d = self.dashboard(test_id=test_id)
        bh = self.benchmark_buy_hold()
        cfg = d["test"] or {}
        tid = test_id or cfg.get("test_id")
        closed = [t for t in self.store.ptrade_list(status="closed") if tid is None or t["test_id"] == tid]
        # régimes des actifs tradés sur la fenêtre du test (données du store, aucun futur)
        labels: list[str] = []
        if closed:
            first = min(t["fill_date"] for t in closed if t["fill_date"])
            last = max(t["exit_date"] for t in closed if t["exit_date"])
            for asset, source in sorted({(t["asset"], t["source"]) for t in closed}):
                df = self.store.read(source, asset, interval="1d", start=pd.Timestamp(first), end=pd.Timestamp(last))
                if df is not None and len(df) >= 20:
                    labels.append(detect_regime(df)["label"])
        return evaluate_gonogo(
            n_closed=d["n_closed"],
            expectancy_r=d["expectancy_r"],
            expectancy_ic95=d["expectancy_ic95"],
            win_rate=d["win_rate"],
            payoff=d["payoff"],
            max_drawdown_pct=d["max_drawdown_pct"],
            total_return=d["total_return"],
            benchmark_return=bh["mean_return"] if bh else None,
            regime_labels=labels,
        )
