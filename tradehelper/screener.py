"""Screener multi-stratégies.

Pour chaque actif : on exécute toutes les stratégies en backtest, on choisit
la meilleure (Sharpe, avec repli sur le rendement si peu de trades), puis on
recommande une prise de position avec horizon (durée moyenne des trades),
prix d'entrée, stop-loss et objectif de cours.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

from .advice import advise
from .backtest import periods_per_year, run_backtest
from .compute import add_all_dispatch, detect_backend
from .data.sources import load_data
from .data.universe import asset_type
from .fees import get_fee_model
from .strategies import get_strategy, list_strategies

BAR_HOURS = {"1d": 24.0, "1wk": 168.0, "1mo": 720.0, "12h": 12.0, "8h": 8.0,
             "4h": 4.0, "2h": 2.0, "1h": 1.0, "30m": 0.5, "15m": 0.25, "5m": 5.0 / 60.0}


def bar_hours(interval: str) -> float:
    return BAR_HOURS.get(interval, 24.0)


def format_horizon(avg_bars: float, interval: str) -> str:
    """Convertit une durée moyenne de trade (en barres) en horizon lisible."""
    hours = max(avg_bars, 1.0) * bar_hours(interval)
    if hours < 48:
        return f"≈ {max(1, round(hours))} h"
    days = hours / 24.0
    if days < 60:
        return f"≈ {max(1, round(days))} j"
    return f"≈ {max(1, round(days / 30.0))} mois"


def recommend(df: pd.DataFrame, signals: pd.DataFrame, metrics: dict,
              interval: str, stop_mult: float = 2.0, reward_risk: float = 2.5) -> dict:
    """Recommandation à partir du dernier signal de la stratégie choisie.

    - position : ACHAT si la stratégie est en position longue à la dernière barre
    - entrée ≈ dernier cours (exécution au prochain open)
    - stop = entrée - stop_mult * ATR14 ; objectif = entrée + reward_risk * risque
    - horizon = durée moyenne des trades du backtest
    """
    pos = int(signals["position"].iloc[-1])
    entry = float(df["Close"].iloc[-1])
    atr = float(df["ATR14"].iloc[-1]) if "ATR14" in df.columns and pd.notna(df["ATR14"].iloc[-1]) else entry * 0.02
    if not atr > 0:
        atr = entry * 0.02
    stop = entry - stop_mult * atr
    risk = entry - stop
    target = entry + reward_risk * risk
    horizon = format_horizon(metrics.get("avg_bars") or 5.0, interval)
    if pos > 0:
        position = "ACHAT"
    elif pos < 0:
        position = "SHORT"
    else:
        position = "ATTENTE"
    return {"position": position, "entry": entry, "stop": stop, "target": target,
            "horizon": horizon, "atr": atr}


def _pick_best(results: list[tuple]) -> tuple:
    """results : (key, label, BacktestResult, signals)."""
    valid = [r for r in results if r[2].metrics["n_trades"] >= 2]
    pool = valid or results
    return max(pool, key=lambda r: (r[2].metrics["sharpe"], r[2].metrics["total_return"]))


def top_pick(rows: list[dict]) -> dict | None:
    """Conseil du jour : meilleur candidat acheteur parmi les résultats du screener.

    Parmi les lignes sans erreur et en position ACHAT, on classe par
    0,6 x score d'avis + 0,4 x Sharpe normalisé de la meilleure stratégie.
    Retourne la ligne choisie (avec une clé "why" explicative), ou None.
    """
    cands = [r for r in rows
             if not r.get("error") and r.get("position") == "ACHAT"
             and isinstance(r.get("score"), (int, float))]
    if not cands:
        return None

    def rank(r):
        sharpe = r.get("sharpe")
        sharpe_n = max(-1.0, min(1.0, sharpe / 2.0)) if isinstance(sharpe, (int, float)) else 0.0
        return 0.6 * r["score"] + 0.4 * sharpe_n

    best = max(cands, key=rank)
    best = dict(best)
    best["why"] = (f"Avis {best['label']} (score {best['score']:+.2f}), meilleure stratégie "
                   f"{best['strategy_label']} (Sharpe {best['sharpe']}, {best['n_trades']} trades, "
                   f"win rate {best['win_rate'] * 100:.0f} %)")
    return best


def _screen_one(args: tuple) -> dict:
    """Analyse un symbole (fonction module-level, picklable pour le pool de processus).

    Le 10e élément de `args` est une file de progression (queue.Queue en séquentiel,
    multiprocessing.Queue en pool) ou None : après chaque stratégie, on y dépose
    ("step", symbole, stratégies faites, nb total de stratégies).
    """
    source, sym, period, interval, capital, fee, slippage, keys, backend, prog = args
    ppy = periods_per_year(interval, source)
    try:
        if prog is not None:
            prog.put(("phase", sym, "chargement des données"))
        df = add_all_dispatch(load_data(source, sym, period=period, interval=interval),
                              backend=backend, periods_per_year=ppy)
        if prog is not None:
            prog.put(("phase", sym, "indicateurs + avis"))
        adv = advise(df, sym, source=source, interval=interval)
        results = []
        for i, key in enumerate(keys):
            params = {"device": backend} if key == "ml_signal" else {}
            strat = get_strategy(key, **params)
            sig = strat.generate(df)
            res = run_backtest(df, sig, initial_capital=capital, fee=fee,
                               slippage=slippage, periods_per_year=ppy)
            results.append((key, strat.label, res, sig))
            if prog is not None:
                prog.put(("step", sym, i + 1, len(keys), key))
        bkey, blabel, bres, bsig = _pick_best(results)
        rec = recommend(df, bsig, bres.metrics, interval)
        m = bres.metrics
        return {
            "symbol": sym,
            "type": asset_type(source, sym),
            "last_close": round(adv["last_close"], 4),
            "score": adv["score"],
            "label": adv["label"],
            "rsi": adv["rsi"],
            "adx": adv["adx"],
            "atr_pct": adv["atr_pct"],
            "risk": adv["risk"],
            "best_strategy": bkey,
            "strategy_label": blabel,
            "sharpe": round(m["sharpe"], 2),
            "total_return": round(m["total_return"], 4),
            "n_trades": m["n_trades"],
            "win_rate": round(m["win_rate"], 3),
            "position": rec["position"],
            "entry": round(rec["entry"], 4),
            "stop": round(rec["stop"], 4),
            "target": round(rec["target"], 4),
            "horizon": rec["horizon"],
            "error": None,
        }
    except Exception as exc:
        return {"symbol": sym, "type": asset_type(source, sym), "error": str(exc),
                "score": "", "label": f"Erreur: {exc}"[:40],
                "position": "", "entry": "", "stop": "", "target": "", "horizon": "",
                "best_strategy": "", "sharpe": "", "total_return": "", "n_trades": "",
                "win_rate": "", "last_close": "", "rsi": "", "adx": "", "atr_pct": "",
                "risk": "", "strategy_label": ""}


def _progress_reader(prog_q, on_progress, total_units, state, stop):
    """Lit la file de progression et appelle on_progress(n, total, sym, phase).

    Deux types de messages : ("step", sym, faits, total, stratégie) — compté dans la
    barre ; ("phase", sym, texte) — simple info (ex. "chargement des données") sans
    faire avancer le compteur, pour rendre visible ce qui se passe entre deux backtests.
    `state` = {"n": compteur global partagé, "sym": {symbole: steps faits}} — le thread
    principal complète (padding) les étapes manquantes d'un symbole en erreur.
    """
    while not stop["flag"]:
        try:
            msg = prog_q.get(timeout=0.25)
        except queue.Empty:
            continue
        if msg[0] == "step":
            _, sym, done_s, total_s, key = msg
            state["sym"][sym] = done_s
            state["n"] += 1
            on_progress(state["n"], total_units, sym, f"backtest {key} ({done_s}/{total_s})")
        elif msg[0] == "phase":
            _, sym, text = msg
            state["sym"].setdefault(sym, 0)
            on_progress(state["n"], total_units, sym, text)


def screen_assets(source: str, symbols: list[str], period: str = "1y", interval: str = "1d",
                  capital: float = 10_000.0, fee=None, slippage: float = 0.0005,
                  strategies: list[str] | None = None, on_progress=None,
                  backend: str = "auto", workers: int = 0) -> list[dict]:
    """Analyse chaque symbole avec toutes les stratégies. Retourne une liste de dicts.

    `fee` : modèle de frais (défaut : Trade Republic FR auto selon la source).
    `backend` : "auto" | "cpu" | "cuda" — en "cuda", les indicateurs passent sur GPU (cupy)
      et le ML sur XGBoost CUDA. En "cpu", les symboles sont traités en parallèle
      (ProcessPoolExecutor, `workers` processus ; 0 = auto).
    `on_progress(i, n, symbole, phase)` : progression **par stratégie** — n = len(symboles) ×
      nb de stratégies (la barre avance continuellement, même en mode pool). `phase`
      décrit l'étape en cours : "chargement des données", "indicateurs + avis",
      "backtest <stratégie> (k/N)", "terminé" — utile pour voir que le scan avance
      pendant les étapes longues (téléchargement, avis fondamental).
    """
    keys = strategies or [s["key"] for s in list_strategies()]
    if fee is None:
        fee = get_fee_model("trade_republic_fr_auto", source)
    if backend == "auto":
        backend = detect_backend()
    total_units = len(symbols) * len(keys)
    use_pool = backend == "cpu" and len(symbols) > 1 and workers != 1
    mgr = None
    if on_progress is not None:
        if use_pool:
            # mp.Queue n'est plus picklable via les args d'un executor (Python >= 3.14) :
            # on passe par un Manager (proxy picklable entre processus)
            mgr = mp.Manager()
            prog_q = mgr.Queue()
        else:
            prog_q = queue.Queue()
    else:
        prog_q = None
    args = [(source, s, period, interval, capital, fee, slippage, keys, backend, prog_q)
            for s in symbols]

    state = {"n": 0, "sym": {}}
    stop = {"flag": False}
    reader = None
    if on_progress is not None:
        reader = threading.Thread(target=_progress_reader,
                                  args=(prog_q, on_progress, total_units, state, stop),
                                  daemon=True)
        reader.start()

    rows: list[dict] = []
    try:
        if use_pool:
            w = workers or min(8, os.cpu_count() or 1, len(symbols))
            with ProcessPoolExecutor(max_workers=w) as ex:
                futures = {ex.submit(_screen_one, a): a[1] for a in args}
                for fut in as_completed(futures):
                    rows.append(fut.result())
        else:
            # mode séquentiel (cuda : calcul GPU ; ou workers=1)
            for a in args:
                rows.append(_screen_one(a))
    finally:
        # fin de la progression : on draine les derniers steps puis on force 100 % si
        # un symbole a levé avant d'avoir tout compté (la barre atteint toujours le total)
        stop["flag"] = True
        if reader is not None:
            reader.join(timeout=2.0)
        if prog_q is not None:
            while True:
                try:
                    msg = prog_q.get_nowait()
                except queue.Empty:
                    break
                if msg[0] == "step" and on_progress is not None:
                    state["n"] += 1
                    on_progress(state["n"], total_units, msg[1], f"backtest {msg[4]} ({msg[2]}/{msg[3]})")
        if on_progress is not None and state["n"] < total_units:
            on_progress(total_units, total_units, symbols[-1] if symbols else "", "terminé")
        if mgr is not None:
            mgr.shutdown()
    order = {s: i for i, s in enumerate(symbols)}
    rows.sort(key=lambda r: order.get(r["symbol"], 0))
    return rows
