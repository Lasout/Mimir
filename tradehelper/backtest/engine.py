"""Moteur de backtest vectorisé par boucle, sans lookahead.

Modèle d'exécution :
  - le signal calculé sur la barre i est exécuté à l'open de la barre i+1
  - commissions et slippage appliqués sur chaque exécution
  - stop-loss / take-profit déclenchés en intrabarre (High/Low de la barre)
  - positions signées : +1 long, -1 short (si autorisé), 0 flat
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .metrics import compute_metrics


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    positions: pd.Series
    signals: pd.DataFrame
    metrics: dict
    params: dict = field(default_factory=dict)


def run_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    initial_capital: float = 10_000.0,
    commission: float = 0.001,
    slippage: float = 0.0005,
    allow_short: bool = False,
    sizing: str = "all_in",          # "all_in" | "risk"
    risk_per_trade: float = 0.01,    # fraction du capital risquée par trade (sizing=risk)
    periods_per_year: int = 252,
) -> BacktestResult:
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    sig = signals.reindex(df.index).fillna({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""})
    n = len(df)
    if n < 2:
        raise ValueError("Pas assez de barres pour backtester.")

    open_ = df["Open"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    close = df["Close"].to_numpy()
    target_pos = sig["position"].to_numpy(dtype=float)
    stop_arr = sig["stop"].to_numpy(dtype=float)
    take_arr = sig["target"].to_numpy(dtype=float)
    reason_arr = sig["reason"].fillna("").to_numpy()

    cash = float(initial_capital)
    shares = 0.0           # signé : >0 long, <0 short
    entry_price = np.nan
    entry_time = None
    entry_reason = ""
    entry_bar = 0
    pending_target = 0.0   # position visée, exécutée à l'open de la barre suivante
    trades: list[dict] = []
    equity_curve = np.empty(n)
    pos_curve = np.zeros(n)

    def exec_price(side: str, price: float) -> float:
        """Prix d'exécution avec slippage (side: 'buy' ou 'sell')."""
        return price * (1.0 + slippage) if side == "buy" else price * (1.0 - slippage)

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal cash, shares, entry_price, entry_time, entry_reason, entry_bar
        if shares == 0:
            return
        qty = abs(shares)
        side = "sell" if shares > 0 else "buy"
        px = exec_price(side, price)
        pnl_gross = (px - entry_price) * qty * (1 if shares > 0 else -1)
        comm = qty * px * commission
        cash += qty * px * (1 if shares > 0 else -1) - comm
        trades.append({
            "entry_time": entry_time, "entry_price": entry_price,
            "exit_time": df.index[i], "exit_price": px,
            "side": "long" if shares > 0 else "short",
            "pnl": pnl_gross - comm - qty * entry_price * commission,
            "ret": (px / entry_price - 1.0) * (1 if shares > 0 else -1),
            "bars": i - entry_bar, "reason": reason,
        })
        shares = 0.0
        entry_price = np.nan

    def open_or_adjust(i: int, target: float) -> None:
        """Exécute à l'open de la barre i le passage à la position target."""
        nonlocal cash, shares, entry_price, entry_time, entry_reason, entry_bar
        px_raw = open_[i]
        if target < 0 and not allow_short:
            target = 0.0
        if target == 0:
            if shares != 0:
                close_position(i, px_raw, "Signal de sortie")
            return
        if shares != 0 and target == np.sign(shares):
            return  # déjà dans la bonne direction
        direction = 1 if target > 0 else -1
        if shares != 0 and np.sign(shares) != direction:
            close_position(i, px_raw, "Inversion de position")
        equity = cash + shares * px_raw
        if sizing == "risk" and not np.isnan(stop_arr[i]):
            risk_per_unit = abs(px_raw - stop_arr[i])
            desired = (equity * risk_per_trade) / risk_per_unit if risk_per_unit > 0 else 0.0
        else:
            desired = equity * 0.95 / px_raw
        delta = direction * desired - shares
        if abs(delta) < 1e-12:
            return
        side = "buy" if delta > 0 else "sell"
        px = exec_price(side, px_raw)
        comm = abs(delta) * px * commission
        cash -= delta * px + comm
        if shares == 0:
            entry_price = px
            entry_time = df.index[i]
            entry_reason = reason_arr[i]
            entry_bar = i
        shares += delta

    for i in range(n):
        # 1) exécution à l'open de la barre i de la décision prise à i-1
        if i > 0:
            open_or_adjust(i, pending_target)
        # 2) stop / take-profit en intrabarre
        if shares > 0:
            if not np.isnan(stop_arr[i]) and low[i] <= stop_arr[i]:
                px = stop_arr[i] if open_[i] > stop_arr[i] else open_[i]
                close_position(i, px, "Stop-loss")
                pending_target = 0.0
            elif not np.isnan(take_arr[i]) and high[i] >= take_arr[i]:
                px = take_arr[i] if open_[i] < take_arr[i] else open_[i]
                close_position(i, px, "Take-profit")
                pending_target = 0.0
        elif shares < 0:
            if not np.isnan(stop_arr[i]) and high[i] >= stop_arr[i]:
                px = stop_arr[i] if open_[i] < stop_arr[i] else open_[i]
                close_position(i, px, "Stop-loss")
                pending_target = 0.0
            elif not np.isnan(take_arr[i]) and low[i] <= take_arr[i]:
                px = take_arr[i] if open_[i] > take_arr[i] else open_[i]
                close_position(i, px, "Take-profit")
                pending_target = 0.0
        # 3) nouvelle cible calculée sur la barre i -> exécutée à l'open de i+1
        pending_target = float(np.clip(target_pos[i], -1, 1)) if allow_short else max(0.0, float(target_pos[i]))
        pos_curve[i] = shares
        equity_curve[i] = cash + shares * close[i]

    # liquidation en fin de période
    if shares != 0:
        close_position(n - 1, close[n - 1], "Fin de la période")
        equity_curve[-1] = cash + shares * close[-1]
        pos_curve[-1] = shares

    equity = pd.Series(equity_curve, index=df.index, name="equity")
    positions = pd.Series(pos_curve, index=df.index, name="position")
    trades_df = pd.DataFrame(trades, columns=[
        "entry_time", "entry_price", "exit_time", "exit_price", "side",
        "pnl", "ret", "bars", "reason"])
    metrics = compute_metrics(equity, trades_df, positions, periods_per_year=periods_per_year)
    return BacktestResult(
        equity=equity, trades=trades_df, positions=positions, signals=sig,
        metrics=metrics,
        params={"initial_capital": initial_capital, "commission": commission,
                "slippage": slippage, "allow_short": allow_short, "sizing": sizing,
                "risk_per_trade": risk_per_trade},
    )
