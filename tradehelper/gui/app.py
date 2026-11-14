"""Interface graphique Tkinter de tradeHelper.

Un seul contexte d'instrument (source / actif / période / intervalle) est partagé
par tous les onglets : le backtest porte toujours sur l'actif sélectionné.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from ..advice import advise
from ..backtest import periods_per_year, run_backtest
from ..data.sources import SOURCE_LABELS, DataError, load_data
from ..data.universe import universe_for
from ..indicators import add_all
from ..screener import screen_assets
from ..strategies import get_strategy, list_strategies
from .chart import draw_equity_chart, draw_price_chart

PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
INTERVALS = {
    "stock": ["1d", "1h", "4h", "30m", "15m", "5m", "1wk", "1mo"],
    "crypto": ["1d", "4h", "1h", "30m", "15m", "5m"],
    "forex": ["1d"],
}
DEFAULT_SYMBOLS = {"stock": "AAPL", "crypto": "BTCUSDT", "forex": "EURUSD"}


class TradeHelperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("tradeHelper — aide au trading (swing / day)")
        root.geometry("1360x860")
        self.df: pd.DataFrame | None = None
        self.adv: dict | None = None
        self.last_signals: pd.DataFrame | None = None
        self.backtest_result = None
        self.screener_rows: dict[str, dict] = {}
        self._sort_state: dict[tuple[int, str], bool] = {}  # (id(tree), colonne) -> desc

        # --- barre d'instrument globale, partagée par tous les onglets ---
        ctx_frame = ttk.LabelFrame(root, text="Instrument (commun à tous les onglets)")
        ctx_frame.pack(fill="x", padx=6, pady=(6, 0))
        self.ctx = self._context_frame(ctx_frame)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.tab_analyse = ttk.Frame(self.notebook)
        self.tab_backtest = ttk.Frame(self.notebook)
        self.tab_screener = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_analyse, text="Analyse & conseils")
        self.notebook.add(self.tab_backtest, text="Backtest")
        self.notebook.add(self.tab_screener, text="Screener multi-stratégies")

        self._build_analyse_tab()
        self._build_backtest_tab()
        self._build_screener_tab()

        self.status = tk.StringVar(value="Prêt. Données ouvertes : Yahoo Finance, Binance, BCE.")
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w").pack(
            fill="x", side="bottom")

    # ------------------------------------------------------------------ util
    def _run_async(self, work, on_done):
        """Exécute `work` dans un thread ; `on_done(result, err)` est appelé
        dans le thread Tk via une file thread-safe + polling."""
        q: queue.Queue = queue.Queue()

        def wrapper():
            try:
                q.put((work(), None))
            except Exception as exc:
                q.put((None, exc))

        def poll():
            try:
                result, err = q.get_nowait()
                on_done(result, err)
            except queue.Empty:
                self.root.after(100, poll)

        threading.Thread(target=wrapper, daemon=True).start()
        self.root.after(100, poll)

    def _context_frame(self, parent) -> dict:
        frame = parent
        ttk.Label(frame, text="Source :").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        source = ttk.Combobox(frame, values=list(SOURCE_LABELS.keys()), state="readonly", width=10)
        source.set("stock")
        source.grid(row=0, column=1, padx=4)
        ttk.Label(frame, text="Actif :").grid(row=0, column=2, sticky="w", padx=4)
        symbol = ttk.Combobox(frame, values=universe_for("stock"), width=14)
        symbol.set(DEFAULT_SYMBOLS["stock"])
        symbol.grid(row=0, column=3, padx=4)
        ttk.Label(frame, text="Période :").grid(row=0, column=4, sticky="w", padx=4)
        period = ttk.Combobox(frame, values=PERIODS, state="readonly", width=7)
        period.set("1y")
        period.grid(row=0, column=5, padx=4)
        ttk.Label(frame, text="Intervalle :").grid(row=0, column=6, sticky="w", padx=4)
        interval = ttk.Combobox(frame, values=INTERVALS["stock"], state="readonly", width=7)
        interval.set("1d")
        interval.grid(row=0, column=7, padx=4)

        def on_source_change(_=None):
            src = source.get()
            interval["values"] = INTERVALS[src]
            interval.set(INTERVALS[src][0])
            symbol["values"] = universe_for(src)
            if not symbol.get().strip() or symbol.get() in DEFAULT_SYMBOLS.values():
                symbol.set(DEFAULT_SYMBOLS[src])
        source.bind("<<ComboboxSelected>>", on_source_change)
        return {"source": source, "symbol": symbol, "period": period, "interval": interval}

    def _make_sortable(self, tree: ttk.Treeview) -> None:
        """Rend les colonnes d'un Treeview triables (clic sur l'en-tête, asc/desc)."""
        for c in tree["columns"]:
            tree.heading(c, command=lambda col=c: self._sort_tree(tree, col))

    def _sort_tree(self, tree: ttk.Treeview, col: str) -> None:
        items = [(tree.set(i, col), i) for i in tree.get_children("")]

        def key(item):
            v = item[0]
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, str(v))

        reverse = self._sort_state.get((id(tree), col), False)
        items.sort(key=key, reverse=reverse)
        for idx, (_, iid) in enumerate(items):
            tree.move(iid, "", idx)
        self._sort_state[(id(tree), col)] = not reverse
        arrow = " ▼" if reverse else " ▲"
        for c in tree["columns"]:
            base = tree.heading(c)["text"].replace(" ▼", "").replace(" ▲", "")
            tree.heading(c, text=base + (arrow if c == col else ""))

    def _make_chart(self, parent) -> tuple[Figure, FigureCanvasTkAgg]:
        fig = Figure(figsize=(8, 6), dpi=100)
        canvas = FigureCanvasTkAgg(fig, master=parent)
        toolbar = NavigationToolbar2Tk(canvas, parent)
        toolbar.pack(fill="x")
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return fig, canvas

    @staticmethod
    def _ctx_values(ctx: dict) -> dict:
        """Lit les widgets Tk (thread principal uniquement)."""
        return {
            "source": ctx["source"].get(),
            "symbol": ctx["symbol"].get().strip(),
            "period": ctx["period"].get(),
            "interval": ctx["interval"].get(),
        }

    def _load_df(self, vals: dict) -> pd.DataFrame:
        if not vals["symbol"]:
            raise DataError("Symbole vide.")
        df = load_data(vals["source"], vals["symbol"], period=vals["period"], interval=vals["interval"])
        return add_all(df)

    # ------------------------------------------------------------- onglet 1
    def _build_analyse_tab(self):
        top = ttk.Frame(self.tab_analyse)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Label(top, text="Stratégie affichée sur le graphique :").pack(side="left", padx=(0, 4))
        self.strat_var = tk.StringVar(value=list_strategies()[0]["key"])
        self.strat_box = ttk.Combobox(top, values=[s["key"] for s in list_strategies()],
                                      textvariable=self.strat_var, state="readonly", width=18)
        self.strat_box.pack(side="left")
        self.btn_analyse = ttk.Button(top, text="Charger & analyser l'actif", command=self.on_analyse)
        self.btn_analyse.pack(side="left", padx=8)

        paned = ttk.PanedWindow(self.tab_analyse, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=4)
        left = ttk.Frame(paned)
        self.fig_a, self.canvas_a = self._make_chart(left)
        paned.add(left, weight=3)
        right = ttk.Frame(paned, width=400)
        paned.add(right, weight=1)
        ttk.Label(right, text="Avis de l'analyse (technique + fondamentale)",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=6, pady=(6, 2))
        self.adv_text = scrolledtext.ScrolledText(right, wrap="word", width=50, height=32)
        self.adv_text.pack(fill="both", expand=True, padx=6, pady=4)

    def on_analyse(self):
        vals = self._ctx_values(self.ctx)
        strat_key = self.strat_var.get()
        self.btn_analyse.config(state="disabled")
        self.status.set(f"Chargement de {vals['symbol']}...")

        def work():
            df = self._load_df(vals)
            signals = get_strategy(strat_key).generate(df)
            advice = advise(df, vals["symbol"], source=vals["source"], interval=vals["interval"])
            return df, signals, advice

        def done(result, err):
            self.btn_analyse.config(state="normal")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur", str(err))
                return
            self.df, self.last_signals, self.adv = result
            draw_price_chart(self.fig_a, self.df, self.last_signals,
                             title=f"{vals['symbol']} — {vals['interval']} — stratégie {strat_key}")
            self.canvas_a.draw_idle()
            self._fill_advice()
            self.status.set(f"{vals['symbol']} : {len(self.df)} barres. Avis : {self.adv['label']} (score {self.adv['score']:+.2f}).")

        self._run_async(work, done)

    def _fill_advice(self):
        adv = self.adv
        if not adv:
            return
        t = self.adv_text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", f"{adv['symbol']} — Avis : {adv['label']}\n")
        t.insert("end", f"Score global : {adv['score']:+.2f} / 1   (technique {adv['technical_score']:+.2f}")
        if adv["fundamental_score"] is not None:
            t.insert("end", f", fondamental {adv['fundamental_score']:+.2f}")
        t.insert("end", ")\n\n")
        t.insert("end", f"Dernier cours : {adv['last_close']:.2f}   RSI : {adv['rsi']}   ADX : {adv['adx']}\n")
        t.insert("end", f"ATR : {adv['atr_pct'] * 100:.2f} %   Risque : {adv['risk']}   Horizon : {adv['horizon']}\n")
        if adv["stop_suggestion"]:
            t.insert("end", f"Stop suggéré (2x ATR) : {adv['stop_suggestion']:.2f}\n")
        t.insert("end", "\nDétail :\n")
        for r in adv["reasons"]:
            t.insert("end", f"• {r}\n")
        t.config(state="disabled")

    # ------------------------------------------------------------- onglet 2
    def _build_backtest_tab(self):
        mid = ttk.LabelFrame(self.tab_backtest, text="Stratégie & paramètres de backtest")
        mid.pack(fill="x", padx=6, pady=4)
        ttk.Label(mid, text="Stratégie :").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.bt_strat_var = tk.StringVar(value=list_strategies()[0]["key"])
        self.bt_strat_box = ttk.Combobox(mid, values=[s["key"] for s in list_strategies()],
                                         textvariable=self.bt_strat_var, state="readonly", width=18)
        self.bt_strat_box.grid(row=0, column=1, padx=4)
        self.bt_strat_box.bind("<<ComboboxSelected>>", lambda _=None: self._rebuild_params(mid))
        self._params_parent = mid
        self.params_frame = ttk.Frame(mid)
        self.params_frame.grid(row=1, column=0, columnspan=6, sticky="w", padx=4)
        self.param_entries: dict[str, ttk.Entry] = {}
        self._rebuild_params(mid)

        ttk.Label(mid, text="Capital initial :").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.bt_capital = ttk.Entry(mid, width=10)
        self.bt_capital.insert(0, "10000")
        self.bt_capital.grid(row=2, column=1, sticky="w")
        ttk.Label(mid, text="Commission (%) :").grid(row=2, column=2, sticky="w", padx=4)
        self.bt_commission = ttk.Entry(mid, width=7)
        self.bt_commission.insert(0, "0.10")
        self.bt_commission.grid(row=2, column=3, sticky="w")
        ttk.Label(mid, text="Slippage (%) :").grid(row=2, column=4, sticky="w", padx=4)
        self.bt_slippage = ttk.Entry(mid, width=7)
        self.bt_slippage.insert(0, "0.05")
        self.bt_slippage.grid(row=2, column=5, sticky="w")
        ttk.Label(mid, text="Sizing :").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        self.bt_sizing = ttk.Combobox(mid, values=["all_in", "risk"], state="readonly", width=10)
        self.bt_sizing.set("all_in")
        self.bt_sizing.grid(row=3, column=1, sticky="w")
        ttk.Label(mid, text="Risque/trade (%) :").grid(row=3, column=2, sticky="w", padx=4)
        self.bt_risk = ttk.Entry(mid, width=7)
        self.bt_risk.insert(0, "1.0")
        self.bt_risk.grid(row=3, column=3, sticky="w")
        self.bt_short = tk.BooleanVar(value=False)
        ttk.Checkbutton(mid, text="Autoriser le short", variable=self.bt_short).grid(
            row=3, column=4, sticky="w", padx=8)
        self.btn_bt = ttk.Button(mid, text="Lancer le backtest sur l'actif sélectionné",
                                 command=self.on_backtest)
        self.btn_bt.grid(row=3, column=5, padx=8)

        paned = ttk.PanedWindow(self.tab_backtest, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=4)
        left = ttk.Frame(paned)
        self.fig_bt, self.canvas_bt = self._make_chart(left)
        paned.add(left, weight=3)
        right = ttk.Frame(paned, width=460)
        paned.add(right, weight=1)
        ttk.Label(right, text="Résultats du backtest").pack(anchor="w", padx=6)
        self.bt_metrics = scrolledtext.ScrolledText(right, wrap="word", width=56, height=12)
        self.bt_metrics.pack(fill="x", padx=6, pady=4)
        ttk.Label(right, text="Trades").pack(anchor="w", padx=6)
        cols = ("entrée", "sortie", "sens", "pnl", "ret%", "barres", "raison")
        self.bt_tree = ttk.Treeview(right, columns=cols, show="headings", height=14)
        for c in cols:
            self.bt_tree.heading(c, text=c)
            self.bt_tree.column(c, width=70 if c != "raison" else 150)
        self.bt_tree.pack(fill="both", expand=True, padx=6, pady=4)
        self._make_sortable(self.bt_tree)

    def _rebuild_params(self, parent):
        for w in self.params_frame.winfo_children():
            w.destroy()
        self.param_entries = {}
        meta = next(s for s in list_strategies() if s["key"] == self.bt_strat_var.get())
        for j, (k, v) in enumerate(meta["params"].items()):
            ttk.Label(self.params_frame, text=f"{k} :").grid(row=0, column=2 * j, sticky="w", padx=(8, 2))
            e = ttk.Entry(self.params_frame, width=8)
            e.insert(0, str(v))
            e.grid(row=0, column=2 * j + 1)
            self.param_entries[k] = e
        ttk.Label(self.params_frame, text=meta["description"], foreground="#555").grid(
            row=1, column=0, columnspan=12, sticky="w", padx=8, pady=(2, 4))

    def _strategy_params(self) -> dict:
        meta = next(s for s in list_strategies() if s["key"] == self.bt_strat_var.get())
        out = {}
        for k, entry in self.param_entries.items():
            default = meta["params"].get(k)
            raw = entry.get().strip()
            if default is None:
                continue
            try:
                out[k] = type(default)(raw) if not isinstance(default, bool) else raw.lower() in ("1", "true", "oui")
            except ValueError:
                out[k] = default
        return out

    def on_backtest(self):
        vals = self._ctx_values(self.ctx)  # même actif que l'onglet Analyse
        strat_key = self.bt_strat_var.get()
        self._rebuild_params(self._params_parent)  # synchro champs <-> stratégie
        strat_params = self._strategy_params()
        bt_opts = {
            "initial_capital": float(self.bt_capital.get() or 10000),
            "commission": float(self.bt_commission.get() or 0) / 100.0,
            "slippage": float(self.bt_slippage.get() or 0) / 100.0,
            "allow_short": bool(self.bt_short.get()),
            "sizing": self.bt_sizing.get(),
            "risk_per_trade": float(self.bt_risk.get() or 1) / 100.0,
        }
        self.btn_bt.config(state="disabled")
        self.status.set(f"Backtest de {strat_key} sur {vals['symbol']}...")

        def work():
            df = self._load_df(vals)
            strat = get_strategy(strat_key, **strat_params)
            signals = strat.generate(df)
            ppy = periods_per_year(vals["interval"], vals["source"])
            result = run_backtest(df, signals, periods_per_year=ppy, **bt_opts)
            return df, signals, result, strat

        def done(result, err):
            self.btn_bt.config(state="normal")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur de backtest", str(err))
                return
            df, signals, res, strat = result
            self.backtest_result = res
            draw_equity_chart(self.fig_bt, res.equity, res.trades,
                              title=f"Equity — {strat.label} sur {vals['symbol']} ({vals['interval']})")
            self.canvas_bt.draw_idle()
            m = res.metrics
            lines = [
                f"Actif : {vals['symbol']} ({vals['source']}, {vals['interval']}, {vals['period']})   Stratégie : {strat.label}",
                f"Rendement total : {m['total_return'] * 100:+.2f} %   CAGR : {m['cagr'] * 100:+.2f} %",
                f"Sharpe : {m['sharpe']:.2f}   Sortino : {m['sortino']:.2f}   Vol. annualisée : {m['ann_vol'] * 100:.1f} %",
                f"Drawdown max : {m['max_drawdown'] * 100:.2f} %   Calmar : {m['calmar']:.2f}",
                f"Trades : {m['n_trades']}   Win rate : {m['win_rate'] * 100:.1f} %   Profit factor : {m['profit_factor']:.2f}",
                f"Expectancy : {m['expectancy']:+.2f}   Trade moyen : {m['avg_trade_ret'] * 100:+.2f} %",
                f"Meilleur trade : {m['best_trade'] * 100:+.2f} %   Pire : {m['worst_trade'] * 100:+.2f} %",
                f"Exposition au marché : {m['exposure'] * 100:.1f} %   Durée moy. d'un trade : {m['avg_bars']:.1f} barres",
            ]
            self.bt_metrics.config(state="normal")
            self.bt_metrics.delete("1.0", "end")
            self.bt_metrics.insert("end", "\n".join(lines))
            self.bt_metrics.config(state="disabled")
            for i in self.bt_tree.get_children():
                self.bt_tree.delete(i)
            for _, t in res.trades.iterrows():
                self.bt_tree.insert("", "end", values=(
                    pd.Timestamp(t["entry_time"]).strftime("%Y-%m-%d %H:%M"),
                    pd.Timestamp(t["exit_time"]).strftime("%Y-%m-%d %H:%M"),
                    t["side"], f"{t['pnl']:+.2f}", f"{t['ret'] * 100:+.2f}",
                    int(t["bars"]), str(t["reason"])[:40]))
            self.status.set(f"Backtest {vals['symbol']} terminé : {m['n_trades']} trades, rendement {m['total_return'] * 100:+.2f} %.")

        self._run_async(work, done)

    # ------------------------------------------------------------- onglet 3
    def _build_screener_tab(self):
        top = ttk.Frame(self.tab_screener)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Button(top, text="Remplir la liste par défaut (source active)",
                   command=self._fill_symbols).pack(side="left", padx=4)
        self.btn_scr = ttk.Button(top, text="Scanner (toutes les stratégies)", command=self.on_screener)
        self.btn_scr.pack(side="left", padx=8)
        ttk.Button(top, text="Ouvrir la sélection dans l'onglet Analyse",
                   command=self._open_screener_selection).pack(side="left", padx=4)
        self.scr_progress = ttk.Label(top, text="")
        self.scr_progress.pack(side="left", padx=8)
        ttk.Label(self.tab_screener,
                  text="Symboles à scanner (un par ligne) — source, période et intervalle repris de la barre du haut :").pack(anchor="w", padx=6)
        self.scr_text = scrolledtext.ScrolledText(self.tab_screener, height=5, width=60)
        self.scr_text.pack(anchor="w", padx=6, pady=2)
        self._fill_symbols()
        cols = ("symbole", "cours", "avis", "stratégie", "sharpe", "ret%", "trades",
                "position", "horizon", "entrée", "stop", "objectif")
        self.scr_tree = ttk.Treeview(self.tab_screener, columns=cols, show="headings", height=18)
        widths = {"symbole": 90, "cours": 80, "avis": 150, "stratégie": 170, "sharpe": 60,
                  "ret%": 70, "trades": 55, "position": 90, "horizon": 70, "entrée": 80,
                  "stop": 80, "objectif": 80}
        for c in cols:
            self.scr_tree.heading(c, text=c)
            self.scr_tree.column(c, width=widths[c])
        self.scr_tree.pack(fill="both", expand=True, padx=6, pady=4)
        self._make_sortable(self.scr_tree)
        self.scr_tree.bind("<Double-1>", lambda _=None: self._open_screener_selection())

    def _fill_symbols(self):
        src = self.ctx["source"].get()
        self.scr_text.delete("1.0", "end")
        self.scr_text.insert("1.0", "\n".join(universe_for(src)[:10]))

    def on_screener(self):
        vals = self._ctx_values(self.ctx)
        symbols = [s.strip().upper() for s in self.scr_text.get("1.0", "end").splitlines() if s.strip()]
        if not symbols:
            messagebox.showinfo("Screener", "Entrez au moins un symbole.")
            return
        self.btn_scr.config(state="disabled")
        prog_q: queue.Queue = queue.Queue()
        finished = {"done": False}

        def poll_progress():
            try:
                while True:
                    self.scr_progress.config(text=prog_q.get_nowait())
            except queue.Empty:
                pass
            if not finished["done"]:
                self.root.after(100, poll_progress)

        def work():
            return screen_assets(
                vals["source"], symbols, period=vals["period"], interval=vals["interval"],
                on_progress=lambda i, n, s: prog_q.put(f"{i + 1}/{n} — {s}"),
            )

        def done(result, err):
            finished["done"] = True
            self.btn_scr.config(state="normal")
            self.scr_progress.config(text="")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur screener", str(err))
                return
            self.screener_rows = {r["symbol"]: r for r in result}
            for i in self.scr_tree.get_children():
                self.scr_tree.delete(i)

            def sort_key(r):
                s = r.get("score")
                return (isinstance(s, str), -(s if not isinstance(s, str) else 0))

            for r in sorted(result, key=sort_key):
                self.scr_tree.insert("", "end", values=(
                    r["symbol"], r["last_close"], r["label"], r["strategy_label"],
                    r["sharpe"], f"{r['total_return'] * 100:+.1f}" if isinstance(r["total_return"], float) else r["total_return"],
                    r["n_trades"], r["position"], r["horizon"], r["entry"], r["stop"], r["target"]))
            n_ok = sum(1 for r in result if not r["error"])
            self.status.set(f"Screener terminé : {n_ok}/{len(result)} actifs analysés "
                            f"({vals['source']}, {vals['interval']}). Double-clic pour ouvrir un actif.")

        self.root.after(100, poll_progress)
        self._run_async(work, done)

    def _open_screener_selection(self):
        """Charge l'actif du screener dans le contexte global + stratégie recommandée."""
        sel = self.scr_tree.selection()
        if not sel:
            self.status.set("Screener : sélectionnez d'abord une ligne.")
            return
        symbol = str(self.scr_tree.item(sel[0])["values"][0]).strip()
        row = self.screener_rows.get(symbol)
        if not row or row.get("error"):
            return
        src = self.ctx["source"].get()
        self.ctx["interval"]["values"] = INTERVALS[src]
        self.ctx["symbol"]["values"] = universe_for(src)
        self.ctx["symbol"].set(symbol)
        # pré-sélectionne la stratégie recommandée par le screener
        if row.get("best_strategy"):
            self.strat_var.set(row["best_strategy"])
            self.bt_strat_var.set(row["best_strategy"])
            self._rebuild_params(self._params_parent)
        self.notebook.select(self.tab_analyse)
        self.on_analyse()
        self.status.set(f"{symbol} chargé (stratégie recommandée : {row.get('best_strategy')}). "
                        f"Position : {row.get('position')}, horizon {row.get('horizon')}, "
                        f"entrée {row.get('entry')}, stop {row.get('stop')}, objectif {row.get('target')}.")


def main():
    root = tk.Tk()
    TradeHelperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
