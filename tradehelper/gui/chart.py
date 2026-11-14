"""Rendu matplotlib : chandeliers, indicateurs, courbe d'equity."""

from __future__ import annotations

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

UP = "#26a69a"
DOWN = "#ef5350"


def _candles(ax, df: pd.DataFrame) -> None:
    x = mdates.date2num(df.index.to_pydatetime())
    o, h, l, c = df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    for xi, oi, hi, li, ci in zip(x, o, h, l, c):
        color = UP if ci >= oi else DOWN
        ax.vlines(xi, li, hi, color=color, linewidth=0.8, zorder=1)
        body_low, body_high = min(oi, ci), max(oi, ci)
        height = body_high - body_low if body_high > body_low else 1e-9
        ax.bar(xi, height, bottom=body_low, width=0.6, color=color, zorder=2, edgecolor=color)
    ax.set_xlim(x[0] - 1, x[-1] + 1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.grid(True, alpha=0.25)


def draw_price_chart(fig: Figure, df: pd.DataFrame, signals: pd.DataFrame | None = None,
                     title: str = "", max_bars: int = 160) -> None:
    """Chandeliers + moyennes mobiles + Bollinger + RSI + MACD + volume."""
    fig.clear()
    df = df.dropna(subset=["Close"])
    if len(df) > max_bars:
        df = df.iloc[-max_bars:]
        if signals is not None:
            signals = signals.loc[signals.index >= df.index[0]]
    gs = fig.add_gridspec(4, 1, height_ratios=[3.2, 1, 1, 0.9], hspace=0.12)
    ax_p = fig.add_subplot(gs[0])
    ax_rsi = fig.add_subplot(gs[1], sharex=ax_p)
    ax_macd = fig.add_subplot(gs[2], sharex=ax_p)
    ax_vol = fig.add_subplot(gs[3], sharex=ax_p)

    _candles(ax_p, df)
    for col, color, lw, label in [
        ("SMA20", "#ff9800", 1.0, "SMA20"),
        ("SMA50", "#2196f3", 1.2, "SMA50"),
        ("EMA200", "#9c27b0", 1.2, "EMA200"),
    ]:
        if col in df.columns and df[col].notna().any():
            ax_p.plot(mdates.date2num(df.index.to_pydatetime()), df[col], color=color, lw=lw, label=label)
    if "BB_upper" in df.columns:
        x = mdates.date2num(df.index.to_pydatetime())
        ax_p.fill_between(x, df["BB_lower"], df["BB_upper"], color="#90a4ae", alpha=0.15, label="Bollinger 20")
        ax_p.plot(x, df["BB_upper"], color="#b0bec5", lw=0.7)
        ax_p.plot(x, df["BB_lower"], color="#b0bec5", lw=0.7)
    ax_p.set_title(title or f"{len(df)} dernières barres", fontsize=10)
    ax_p.legend(loc="upper left", fontsize=7)
    ax_p.set_ylabel("Prix")

    # marqueurs d'achat/vente issus des signaux de la stratégie
    if signals is not None and "position" in signals.columns:
        pos = signals["position"].reindex(df.index).fillna(0)
        changes = pos.diff().fillna(0)
        for i, (ts, ch) in enumerate(changes.items()):
            xi = mdates.date2num(ts.to_pydatetime())
            if ch == 1:
                ax_p.scatter(xi, df["Close"].iloc[i] * 0.985, marker="^", color=UP, s=70, zorder=5)
            elif ch == -1:
                ax_p.scatter(xi, df["Close"].iloc[i] * 1.015, marker="v", color=DOWN, s=70, zorder=5)

    if "RSI14" in df.columns:
        ax_rsi.plot(mdates.date2num(df.index.to_pydatetime()), df["RSI14"], color="#3f51b5", lw=1)
        ax_rsi.axhline(70, color=DOWN, lw=0.6, alpha=0.6)
        ax_rsi.axhline(30, color=UP, lw=0.6, alpha=0.6)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_ylabel("RSI", fontsize=8)
        ax_rsi.grid(True, alpha=0.25)

    if "MACD" in df.columns:
        x = mdates.date2num(df.index.to_pydatetime())
        hist = df["MACD_hist"].to_numpy()
        colors = [UP if v >= 0 else DOWN for v in hist]
        ax_macd.bar(x, hist, width=0.6, color=colors, alpha=0.7)
        ax_macd.plot(x, df["MACD"], color="#1565c0", lw=1, label="MACD")
        ax_macd.plot(x, df["MACD_signal"], color="#ff7043", lw=1, label="Signal")
        ax_macd.axhline(0, color="#666", lw=0.6)
        ax_macd.set_ylabel("MACD", fontsize=8)
        ax_macd.legend(loc="upper left", fontsize=6)
        ax_macd.grid(True, alpha=0.25)

    if "Volume" in df.columns:
        vol = df["Volume"].fillna(0).to_numpy()
        x = mdates.date2num(df.index.to_pydatetime())
        ax_vol.bar(x, vol, width=0.6, color="#78909c", alpha=0.7)
        ax_vol.set_ylabel("Vol.", fontsize=8)
        ax_vol.grid(True, alpha=0.25)

    for ax in (ax_p, ax_rsi, ax_macd):
        ax.tick_params(labelbottom=False, labelsize=7)
    ax_vol.tick_params(labelsize=7)
    fig.autofmt_xdate(rotation=0)


def draw_equity_chart(fig: Figure, equity: pd.Series, trades: pd.DataFrame | None = None,
                      title: str = "Courbe d'equity") -> None:
    fig.clear()
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.12)
    ax = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax)
    x = mdates.date2num(equity.index.to_pydatetime())
    ax.plot(x, equity.to_numpy(), color="#1565c0", lw=1.5)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel("Capital (€)")
    ax.grid(True, alpha=0.25)
    if trades is not None and len(trades):
        for _, t in trades.iterrows():
            ts = pd.Timestamp(t["entry_time"])
            y = equity.loc[ts] if ts in equity.index else equity.asof(ts)
            if pd.notna(y):
                color = UP if t["ret"] > 0 else DOWN
                ax.scatter(mdates.date2num(ts.to_pydatetime()), y, marker="o",
                           color=color, s=25, zorder=5, alpha=0.8)
    peak = equity.cummax()
    dd = (equity - peak) / peak
    ax_vol.fill_between(x, dd.to_numpy() * 100, 0, color=DOWN, alpha=0.4)
    ax_vol.set_ylabel("Drawdown %", fontsize=8)
    ax_vol.grid(True, alpha=0.25)
    ax.tick_params(labelbottom=False, labelsize=7)
    ax_vol.tick_params(labelsize=7)
    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%y"))
    fig.autofmt_xdate(rotation=0)
