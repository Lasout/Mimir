"""Moteur de backtest, sans lookahead.

Modèle d'exécution :
  - le signal calculé sur la barre i est exécuté à l'open de la barre i+1
  - frais via un FeeModel (par exécution : fixe + % + min/max), slippage
  - funding périodique (perps crypto) sur le notionnel des positions ouvertes
  - stop-loss / take-profit déclenchés en intrabarre (High/Low de la barre)
  - positions signées : +1 long, -1 short (si autorisé), 0 flat
  - risk management optionnel : vol targeting (réduit la taille si vol > cible)
    et stop de drawdown (aplatit le portefeuille tant que le DD dépasse le seuil)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..fees import FeeModel
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
    commission: float = 0.001,          # legacy : % du notionnel si `fee` est None
    fee: FeeModel | None = None,        # modèle de frais par exécution (ex. Trade Republic FR)
    slippage: float = 0.0005,
    allow_short: bool = False,
    sizing: str = "all_in",             # "all_in" | "risk" | "kelly"
    risk_per_trade: float = 0.01,
    kelly_fraction: float = 0.5,        # fraction de Kelly appliquée (ex. 0.5 = half-Kelly)
    periods_per_year: float = 252,
    funding_per_bar: float = 0.0,       # ex. 0.0001 = 1 pb/bar sur le notionnel (perps crypto)
    vol_target: float | None = None,    # vol annualisée cible (réduit la taille au-delà)
    max_drawdown_stop: float | None = None,  # ex. 0.20 -> aplatit si DD <= -20 %
) -> BacktestResult:
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    sig = signals.reindex(df.index).fillna({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""})
    n = len(df)
    if n < 2:
        raise ValueError("Pas assez de barres pour backtester.")

    fee = fee if fee is not None else FeeModel(name="commission %", pct=commission)

    open_ = df["Open"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    close = df["Close"].to_numpy()
    target_pos = sig["position"].to_numpy(dtype=float)
    stop_arr = sig["stop"].to_numpy(dtype=float)
    take_arr = sig["target"].to_numpy(dtype=float)
    reason_arr = sig["reason"].fillna("").to_numpy()

    # vol annualisée rolling (pour le vol targeting)
    if vol_target is not None:
        rets = pd.Series(close).pct_change()
        vol_ann = rets.rolling(20, min_periods=20).std() * np.sqrt(periods_per_year)
        vol_arr = vol_ann.to_numpy()
    else:
        vol_arr = None

    cash = float(initial_capital)
    shares = 0.0
    entry_price = np.nan
    entry_fee = 0.0
    entry_time = None
    entry_reason = ""
    entry_bar = 0
    pending_target = 0.0
    trades: list[dict] = []
    equity_curve = np.empty(n)
    pos_curve = np.zeros(n)
    total_fees = 0.0
    peak_equity = float(initial_capital)
    halted = False  # stop de drawdown actif

    def exec_price(side: str, price: float) -> float:
        return price * (1.0 + slippage) if side == "buy" else price * (1.0 - slippage)

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal cash, shares, entry_price, entry_fee, entry_time, entry_reason, entry_bar, total_fees
        if shares == 0:
            return
        qty = abs(shares)
        side = "sell" if shares > 0 else "buy"
        px = exec_price(side, price)
        exit_fee = fee.cost(qty * px)
        pnl_gross = (px - entry_price) * qty * (1 if shares > 0 else -1)
        cash += qty * px * (1 if shares > 0 else -1) - exit_fee
        total_fees += exit_fee
        trades.append({
            "entry_time": entry_time, "entry_price": entry_price,
            "exit_time": df.index[i], "exit_price": px,
            "side": "long" if shares > 0 else "short",
            "pnl": pnl_gross - exit_fee - entry_fee,
            "ret": (px / entry_price - 1.0) * (1 if shares > 0 else -1),
            "bars": i - entry_bar, "reason": reason,
        })
        shares = 0.0
        entry_price = np.nan
        entry_fee = 0.0

    def open_or_adjust(i: int, target: float) -> None:
        nonlocal cash, shares, entry_price, entry_fee, entry_time, entry_reason, entry_bar, total_fees
        px_raw = open_[i]
        if target < 0 and not allow_short:
            target = 0.0
        if target == 0:
            if shares != 0:
                close_position(i, px_raw, "Signal de sortie")
            return
        if shares != 0 and target == np.sign(shares):
            return
        direction = 1 if target > 0 else -1
        if shares != 0 and np.sign(shares) != direction:
            close_position(i, px_raw, "Inversion de position")
        equity = cash + shares * px_raw
        if sizing == "risk" and not np.isnan(stop_arr[i]):
            risk_per_unit = abs(px_raw - stop_arr[i])
            desired = (equity * risk_per_trade) / risk_per_unit if risk_per_unit > 0 else 0.0
        elif sizing == "kelly" and len(trades) >= 5:
            # Kelly fractionnaire : f = W - (1-W)/R sur les trades passés, plafonné
            rets = [t["ret"] for t in trades]
            wins = [r for r in rets if r > 0]
            losses = [-r for r in rets if r <= 0]
            W = len(wins) / len(rets)
            R = (np.mean(wins) / np.mean(losses)) if wins and losses else 1.0
            f = W - (1.0 - W) / R if R > 0 else 0.0
            f = float(np.clip(f, 0.0, 1.0))
            desired = equity * min(0.95, f * kelly_fraction) / px_raw
        else:
            desired = equity * 0.95 / px_raw
        if vol_arr is not None and not np.isnan(vol_arr[i]) and vol_arr[i] > 0:
            desired *= min(1.0, vol_target / vol_arr[i])  # vol targeting : on réduit si trop volatile
        delta = direction * desired - shares
        if abs(delta) < 1e-12:
            return
        side = "buy" if delta > 0 else "sell"
        px = exec_price(side, px_raw)
        cost = fee.cost(abs(delta) * px)
        cash -= delta * px + cost
        total_fees += cost
        if shares == 0:
            entry_price = px
            entry_fee = cost
            entry_time = df.index[i]
            entry_reason = reason_arr[i]
            entry_bar = i
        shares += delta

    for i in range(n):
        if i > 0:
            open_or_adjust(i, pending_target)
        # stop / take-profit en intrabarre
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
        # funding périodique sur le notionnel de la position ouverte (perps crypto)
        if shares != 0 and funding_per_bar:
            f = funding_per_bar * abs(shares) * close[i]
            cash -= f
            total_fees += f
        # nouvelle cible (exécutée à l'open de i+1) — ignorée si stop de drawdown actif
        pending_target = 0.0 if halted else (float(np.clip(target_pos[i], -1, 1)) if allow_short else max(0.0, float(target_pos[i])))
        pos_curve[i] = shares
        equity_curve[i] = cash + shares * close[i]
        # stop de drawdown avec hystérésis
        if max_drawdown_stop:
            peak_equity = max(peak_equity, equity_curve[i])
            dd = (equity_curve[i] - peak_equity) / peak_equity if peak_equity > 0 else 0.0
            if not halted and dd <= -max_drawdown_stop:
                halted = True
                if shares != 0:
                    close_position(i, close[i], "Stop de drawdown global")
                pending_target = 0.0
            elif halted and dd >= -max_drawdown_stop / 2.0:
                halted = False

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
    metrics["total_fees"] = total_fees
    return BacktestResult(
        equity=equity, trades=trades_df, positions=positions, signals=sig,
        metrics=metrics,
        params={"initial_capital": initial_capital, "fee": fee.describe(), "slippage": slippage,
                "allow_short": allow_short, "sizing": sizing, "risk_per_trade": risk_per_trade,
                "kelly_fraction": kelly_fraction,
                "funding_per_bar": funding_per_bar, "vol_target": vol_target,
                "max_drawdown_stop": max_drawdown_stop},
    )
