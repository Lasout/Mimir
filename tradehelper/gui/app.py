"""Interface graphique Tkinter de tradeHelper.

Un seul contexte d'instrument (source / actif / période / intervalle) est partagé
par tous les onglets : le backtest porte toujours sur l'actif sélectionné.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from ..advice import advise
from ..backtest import (grid_search, monte_carlo_trades, periods_per_year, run_backtest,
                        run_portfolio, walk_forward)
from ..compute import add_all_dispatch, backend_label, detect_backend
from ..data.sources import SOURCE_LABELS, DataError, data_range, load_data
from ..data.store import default_store
from ..data.universe import universe_for, default_symbols
from ..fees import get_fee_model, list_presets
from ..indicators import add_all
from ..screener import screen_assets, top_pick
from ..strategies import get_strategy, list_strategies
from .chart import attach_price_crosshair, draw_equity_chart, draw_price_chart
from .export import export_figure_png, export_screener_csv, export_text_report, export_trades_csv
from .i18n import LANGS, get_lang, t
from .theme import FONT_SCALE_MAX, FONT_SCALE_MIN, apply_mpl_theme, apply_ttk

PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
INTERVALS = {
    "stock": ["1d", "1h", "4h", "30m", "15m", "5m", "1wk", "1mo"],
    "crypto": ["1d", "4h", "1h", "30m", "15m", "5m"],
    "forex": ["1d"],
}
DEFAULT_SYMBOLS = {"stock": "AAPL", "crypto": "BTCUSDT", "forex": "EURUSD"}


class ToolTip:
    """Infobulle au survol d'un widget (accessibilité : description des boutons)."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 24
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=self.text, justify="left", relief="solid", borderwidth=1,
                       padx=6, pady=3, wraplength=320)
        lbl.pack()
        try:
            style = ttk.Style(self.widget)
            lbl.configure(bg=style.lookup(".", "background"),
                          fg=style.lookup(".", "foreground"))
        except tk.TclError:
            pass

    def _hide(self, _=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class TradeHelperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.lang = get_lang()
        self._i18n_reg: list[tuple] = []  # (widget, attr, clé) pour le changement de langue à chaud
        root.title(t("app_title", self.lang))
        root.geometry("1360x860")
        self.df: pd.DataFrame | None = None
        self.adv: dict | None = None
        self.last_signals: pd.DataFrame | None = None
        self.backtest_result = None
        self.screener_rows: dict[str, dict] = {}
        self._sort_state: dict[tuple[int, str], bool] = {}  # (id(tree), colonne) -> desc
        self._xh = None  # réticule de survol du graphique d'analyse
        self._watch_items: list[tuple[str, str]] = []
        try:
            self.store = default_store()
        except Exception:
            self.store = None  # l'app marche sans persistance (watchlist/historique désactivés)
        self.theme_name = "clair"
        self.font_scale = 1.0
        if self.store is not None:
            self.theme_name = self.store.meta_get("ui.theme") or "clair"
            try:
                self.font_scale = float(self.store.meta_get("ui.font_scale") or 1.0)
            except (TypeError, ValueError):
                self.font_scale = 1.0
            self.lang = self.store.meta_get("ui.lang") or self.lang
            if self.lang not in LANGS:
                self.lang = "fr"
            root.title(t("app_title", self.lang))

        # --- barre d'instrument globale, partagée par tous les onglets ---
        ctx_frame = ttk.LabelFrame(root, text=t("ctx_frame", self.lang))
        ctx_frame.pack(fill="x", padx=6, pady=(6, 0))
        self.ctx = self._context_frame(ctx_frame)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.tab_analyse = ttk.Frame(self.notebook)
        self.tab_backtest = ttk.Frame(self.notebook)
        self.tab_screener = ttk.Frame(self.notebook)
        self.tab_portfolio = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_analyse, text=t("tab_analyse", self.lang))
        self.notebook.add(self.tab_backtest, text=t("tab_backtest", self.lang))
        self.notebook.add(self.tab_screener, text=t("tab_screener", self.lang))
        self.notebook.add(self.tab_portfolio, text=t("tab_portfolio", self.lang))

        self._build_analyse_tab()
        self._build_backtest_tab()
        self._build_screener_tab()
        self._build_portfolio_tab()

        self._build_menu()
        self._bind_shortcuts()
        ToolTip(self.btn_analyse, "Charge l'actif de la barre du haut, calcule les indicateurs, "
                                 "la stratégie choisie et l'avis (technique + fondamental). Ctrl+R")
        ToolTip(self.btn_bt, "Backtest walk-forward de la stratégie choisie sur l'actif courant. Ctrl+B")
        ToolTip(self.btn_scr, "Backteste toutes les stratégies sur chaque symbole de la liste "
                               "et recommande position / horizon / niveaux. Ctrl+S")
        ToolTip(self.btn_pf, "Backtest du portefeuille multi-actifs (parts égales, frais par exécution).")

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value=t("status_ready", self.lang))
        ttk.Label(bottom, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True)
        self.backend_var = tk.StringVar(value="")
        self.backend_lbl = ttk.Label(bottom, textvariable=self.backend_var, style="Status.TLabel")
        self.backend_lbl.pack(side="right")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=180)
        self.ctx["backend"].bind("<<ComboboxSelected>>", lambda _=None: self._update_backend_label())
        self._update_backend_label()
        self.apply_ui_theme()

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

    # ------------------------------------------------------------- progression
    def _progress_begin(self, determinate: bool = False, maximum: int = 100):
        """Affiche la barre de progression de la barre de statut (thread Tk uniquement).

        `determinate=True` : barre graduée (ex. screener, maximum = nb de symboles) ;
        sinon mode indéterminé animé (backtest, analyse, portefeuille...)."""
        self.progress.config(mode="determinate" if determinate else "indeterminate",
                             maximum=maximum, value=0)
        self.progress.pack(side="right", padx=6, before=self.backend_lbl)
        if not determinate:
            self.progress.start(60)

    def _progress_set(self, value: int, maximum: int | None = None):
        if maximum:
            self.progress.config(maximum=maximum)
        self.progress.config(value=value)

    def _progress_end(self):
        self.progress.stop()
        self.progress.config(value=0)
        self.progress.pack_forget()

    def _context_frame(self, parent) -> dict:
        frame = parent
        for col, key in ((0, "source"), (2, "symbol"), (4, "period"), (6, "interval"), (8, "backend")):
            lbl = ttk.Label(frame)
            self._set_text(lbl, key)
            lbl.grid(row=0, column=col, sticky="w", padx=4, pady=4)
        source = ttk.Combobox(frame, values=list(SOURCE_LABELS.keys()), state="readonly", width=10)
        source.set("stock")
        source.grid(row=0, column=1, padx=4)
        symbol = ttk.Combobox(frame, values=universe_for("stock"), width=14)
        symbol.set(DEFAULT_SYMBOLS["stock"])
        symbol.grid(row=0, column=3, padx=4)
        period = ttk.Combobox(frame, values=PERIODS, state="readonly", width=7)
        period.set("1y")
        period.grid(row=0, column=5, padx=4)
        interval = ttk.Combobox(frame, values=INTERVALS["stock"], state="readonly", width=7)
        interval.set("1d")
        interval.grid(row=0, column=7, padx=4)
        backend = ttk.Combobox(frame, values=["auto", "cpu", "cuda"], state="readonly", width=7)
        backend.set("auto")
        backend.grid(row=0, column=9, padx=4)

        source.bind("<<ComboboxSelected>>", lambda _=None: self._set_source(source.get()))
        return {"source": source, "symbol": symbol, "period": period, "interval": interval,
                "backend": backend}

    def _set_source(self, src: str):
        """Applique une source à la barre d'instrument globale (combo + intervalles + actif)."""
        ctx = self.ctx
        ctx["source"].set(src)
        ctx["interval"]["values"] = INTERVALS[src]
        ctx["interval"].set(INTERVALS[src][0])
        ctx["symbol"]["values"] = universe_for(src)
        if not ctx["symbol"].get().strip() or ctx["symbol"].get() in DEFAULT_SYMBOLS.values():
            ctx["symbol"].set(DEFAULT_SYMBOLS[src])

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
            "backend": ctx["backend"].get(),
        }

    def _load_df(self, vals: dict) -> pd.DataFrame:
        """Charge + indicateurs, avec mémo LRU (chargement lazy : on ne recalcule pas
        un actif déjà chargé avec les mêmes réglages dans la session)."""
        if not vals["symbol"]:
            raise DataError("Symbole vide.")
        if not hasattr(self, "_df_memo"):
            self._df_memo = {}
        key = (vals["source"], vals["symbol"].upper(), vals["period"], vals["interval"],
               vals.get("backend", "auto"))
        if key in self._df_memo:
            return self._df_memo[key]
        df = load_data(vals["source"], vals["symbol"], period=vals["period"], interval=vals["interval"])
        ppy = periods_per_year(vals["interval"], vals["source"])
        out = add_all_dispatch(df, backend=vals.get("backend", "auto"), periods_per_year=ppy)
        if len(self._df_memo) >= 4:  # LRU simple : 4 actifs max en mémoire
            self._df_memo.pop(next(iter(self._df_memo)))
        self._df_memo[key] = out
        return out

    # ------------------------------------------------------------- thème & UI
    def apply_ui_theme(self):
        """Applique thème clair/sombre + taille de police (persistés), recolore les graphiques."""
        style = ttk.Style(self.root)
        apply_ttk(style, self.theme_name, self.font_scale)
        apply_mpl_theme(self.theme_name)
        if hasattr(self, "theme_var"):
            self.theme_var.set(self.theme_name == "sombre")
        if self.store is not None:
            try:
                self.store.meta_set("ui.theme", self.theme_name)
                self.store.meta_set("ui.font_scale", self.font_scale)
            except Exception:
                pass
        for canvas in (getattr(self, "canvas_a", None), getattr(self, "canvas_bt", None),
                       getattr(self, "canvas_pf", None)):
            if canvas is not None:
                canvas.get_tk_widget().configure(
                    bg=style.lookup("TFrame", "background"))
        self._redraw_charts()

    # ------------------------------------------------------------- i18n
    def _set_text(self, widget, key: str, attr: str = "text"):
        """Pose un libellé traduit et l'enregistre pour le changement de langue à chaud."""
        widget.configure(**{attr: t(key, self.lang)})
        self._i18n_reg.append((widget, attr, key))

    def set_language(self, lang: str):
        if lang not in LANGS:
            return
        self.lang = lang
        if self.store is not None:
            try:
                self.store.meta_set("ui.lang", lang)
            except Exception:
                pass
        for w, attr, key in self._i18n_reg:
            try:
                w.configure(**{attr: t(key, lang)})
            except tk.TclError:
                pass
        for tab, key in ((self.tab_analyse, "tab_analyse"), (self.tab_backtest, "tab_backtest"),
                         (self.tab_screener, "tab_screener"), (self.tab_portfolio, "tab_portfolio")):
            self.notebook.tab(tab, text=t(key, lang))
        self.root.title(t("app_title", lang))
        self.menubar.destroy()
        self._build_menu()

    def _redraw_charts(self):
        """Redessine les graphiques existants avec le thème courant (si des données sont chargées)."""
        if getattr(self, "df", None) is not None and self.last_signals is not None:
            vals = self._ctx_values(self.ctx)
            draw_price_chart(self.fig_a, self.df, self.last_signals,
                             title=f"{vals['symbol']} — {vals['interval']} — stratégie {self.strat_var.get()}")
            self.canvas_a.draw_idle()
            self._attach_crosshair()
        if self.backtest_result is not None and getattr(self, "_bt_draw", None) is not None:
            res, strat, vals, suffix = self._bt_draw
            draw_equity_chart(self.fig_bt, res.equity, res.trades,
                              title=f"Equity — {strat.label} sur {vals['symbol']} ({vals['interval']}) {suffix}")
            self.canvas_bt.draw_idle()

    def toggle_theme(self):
        self.theme_name = "sombre" if self.theme_name == "clair" else "clair"
        self.apply_ui_theme()
        self.status.set(f"Thème {self.theme_name} appliqué (Ctrl+D pour changer).")

    def zoom_font(self, delta: float):
        self.font_scale = min(FONT_SCALE_MAX, max(FONT_SCALE_MIN, round(self.font_scale + delta, 2)))
        self.apply_ui_theme()
        self.status.set(f"Taille de police : {int(self.font_scale * 100)} %")

    def _update_backend_label(self):
        be = self.ctx["backend"].get()
        used = detect_backend() if be == "auto" else be
        self.backend_var.set(f"Calcul : {backend_label(used)}")

    def _attach_crosshair(self):
        if self._xh is not None:
            self._xh.disconnect()
            self._xh = None
        if self.df is not None:
            self._xh = attach_price_crosshair(self.canvas_a, self.fig_a, self.df)

    # ------------------------------------------------------------- menu & raccourcis
    def _build_menu(self):
        lang = self.lang
        menubar = tk.Menu(self.root)
        self.theme_var = tk.BooleanVar(value=self.theme_name == "sombre")
        self.lang_var = tk.StringVar(value=lang)

        m_fichier = tk.Menu(menubar, tearoff=0)
        m_fichier.add_command(label=t("menu_export_png", lang), accelerator="Ctrl+E",
                              command=self._export_png_active)
        m_fichier.add_command(label=t("menu_export_screener", lang), accelerator="Ctrl+Shift+S",
                              command=self._export_screener_csv)
        m_fichier.add_command(label=t("menu_export_trades", lang),
                              command=self._export_trades_csv)
        m_fichier.add_command(label=t("menu_export_report", lang),
                              command=self._export_report_txt)
        m_fichier.add_separator()
        m_fichier.add_command(label=t("menu_history", lang), accelerator="Ctrl+H",
                              command=self._show_history)
        m_fichier.add_separator()
        m_fichier.add_command(label=t("menu_quit", lang), accelerator="Ctrl+Q", command=self.root.destroy)
        menubar.add_cascade(label=t("menu_file", lang), menu=m_fichier)

        m_onglets = tk.Menu(menubar, tearoff=0)
        for i, (tab, key) in enumerate([(self.tab_analyse, "tab_analyse"),
                                        (self.tab_backtest, "tab_backtest"),
                                        (self.tab_screener, "tab_screener"),
                                        (self.tab_portfolio, "tab_portfolio")], start=1):
            m_onglets.add_command(label=t(key, lang), accelerator=f"Ctrl+{i}",
                                  command=lambda tt=tab: self.notebook.select(tt))
        m_onglets.add_separator()
        m_onglets.add_command(label=t("btn_analyse", lang), accelerator="Ctrl+R", command=self.on_analyse)
        m_onglets.add_command(label=t("btn_backtest", lang), accelerator="Ctrl+B", command=self.on_backtest)
        m_onglets.add_command(label=t("btn_screener", lang), accelerator="Ctrl+S", command=self.on_screener)
        menubar.add_cascade(label=t("menu_tabs", lang), menu=m_onglets)

        m_aff = tk.Menu(menubar, tearoff=0)
        m_aff.add_checkbutton(label=t("menu_dark", lang), variable=self.theme_var,
                              accelerator="Ctrl+D", command=self.toggle_theme)
        m_aff.add_command(label=t("menu_font_up", lang), accelerator="Ctrl++",
                          command=lambda: self.zoom_font(+0.1))
        m_aff.add_command(label=t("menu_font_down", lang), accelerator="Ctrl+-",
                          command=lambda: self.zoom_font(-0.1))
        m_aff.add_separator()
        m_aff.add_radiobutton(label=t("lang_fr", lang), variable=self.lang_var, value="fr",
                              command=lambda: self.set_language("fr"))
        m_aff.add_radiobutton(label=t("lang_en", lang), variable=self.lang_var, value="en",
                              command=lambda: self.set_language("en"))
        menubar.add_cascade(label=t("menu_view", lang), menu=m_aff)

        m_aide = tk.Menu(menubar, tearoff=0)
        m_aide.add_command(label=t("menu_shortcuts", lang), accelerator="F1", command=self._show_shortcuts)
        m_aide.add_command(label=t("menu_about", lang), command=self._show_about)
        menubar.add_cascade(label=t("menu_help", lang), menu=m_aide)

        self.menubar = menubar
        self.root.config(menu=menubar)

    def _bind_shortcuts(self):
        r = self.root
        tabs = [self.tab_analyse, self.tab_backtest, self.tab_screener, self.tab_portfolio]
        for i, tab in enumerate(tabs, start=1):
            r.bind_all(f"<Control-Key-{i}>", lambda _=None, t=tab: self.notebook.select(t))
        r.bind_all("<Control-r>", lambda _=None: self.on_analyse())
        r.bind_all("<Control-b>", lambda _=None: self.on_backtest())
        r.bind_all("<Control-s>", lambda _=None: self.on_screener())
        r.bind_all("<Control-Shift-S>", lambda _=None: self._export_screener_csv())
        r.bind_all("<Control-e>", lambda _=None: self._export_png_active())
        r.bind_all("<Control-h>", lambda _=None: self._show_history())
        r.bind_all("<Control-d>", lambda _=None: self.toggle_theme())
        r.bind_all("<Control-equal>", lambda _=None: self.zoom_font(+0.1))
        r.bind_all("<Control-KP_Add>", lambda _=None: self.zoom_font(+0.1))
        r.bind_all("<Control-minus>", lambda _=None: self.zoom_font(-0.1))
        r.bind_all("<Control-KP_Subtract>", lambda _=None: self.zoom_font(-0.1))
        r.bind_all("<F1>", lambda _=None: self._show_shortcuts())
        r.bind_all("<Control-q>", lambda _=None: r.destroy())

    def _show_shortcuts(self):
        dlg = tk.Toplevel(self.root)
        dlg.title(t("shortcuts_title", self.lang))
        txt = scrolledtext.ScrolledText(dlg, width=52, height=20, wrap="word")
        txt.pack(padx=8, pady=8)
        txt.insert("end", "\n".join([
            "Ctrl+1..4 : changer d'onglet (Analyse / Backtest / Screener / Portefeuille)",
            "Ctrl+R : lancer l'analyse de l'actif   Ctrl+B : lancer le backtest",
            "Ctrl+S : lancer le screener   Ctrl+Shift+S : exporter le screener en CSV",
            "Ctrl+E : exporter le graphique actif en PNG",
            "Ctrl+H : historique des analyses   Ctrl+D : thème sombre / clair",
            "Ctrl++ / Ctrl+- : agrandir / réduire le texte",
            "Ctrl+Q : quitter   F1 : cette aide",
            "",
            "Souris : clic sur un en-tête de tableau = tri asc/desc ; double-clic sur une",
            "ligne du screener = ouvrir l'actif dans l'onglet Analyse ; survol du",
            "graphique de prix = réticule + OHLC de la barre.",
        ]))
        txt.config(state="disabled")
        b_close = ttk.Button(dlg, command=dlg.destroy)
        self._set_text(b_close, "close")
        b_close.pack(pady=(0, 8))

    def _show_about(self):
        messagebox.showinfo(t("about_title", self.lang), t("about_text", self.lang))

    # ------------------------------------------------------------- exports
    def _active_fig(self):
        tab = self.notebook.select()
        return {str(self.tab_analyse): self.fig_a, str(self.tab_backtest): self.fig_bt,
                str(self.tab_screener): self.fig_a, str(self.tab_portfolio): self.fig_pf}.get(tab, self.fig_a)

    def _ask_save(self, defaultextension: str, filetypes: list) -> str | None:
        return filedialog.asksaveasfilename(
            parent=self.root, defaultextension=defaultextension, filetypes=filetypes,
            title="Exporter sous...")

    def _export_png_active(self):
        path = self._ask_save(".png", [("Image PNG", "*.png")])
        if not path:
            return
        try:
            export_figure_png(self._active_fig(), path)
            self.status.set(f"Graphique exporté : {path}")
        except Exception as exc:
            messagebox.showerror("Export PNG", str(exc))

    def _export_screener_csv(self):
        rows = list(self.screener_rows.values())
        if not rows:
            messagebox.showinfo("Export CSV", "Lancez d'abord le screener.")
            return
        path = self._ask_save(".csv", [("CSV", "*.csv")])
        if not path:
            return
        try:
            export_screener_csv(rows, path)
            self.status.set(f"Screener exporté : {path} ({len(rows)} lignes)")
        except Exception as exc:
            messagebox.showerror("Export CSV", str(exc))

    def _export_trades_csv(self):
        if self.backtest_result is None:
            messagebox.showinfo("Export CSV", "Lancez d'abord un backtest simple.")
            return
        path = self._ask_save(".csv", [("CSV", "*.csv")])
        if not path:
            return
        try:
            export_trades_csv(self.backtest_result.trades, path)
            self.status.set(f"Trades exportés : {path}")
        except Exception as exc:
            messagebox.showerror("Export CSV", str(exc))

    def _export_report_txt(self):
        tab = self.notebook.select()
        if tab == str(self.tab_backtest):
            text = self.bt_metrics.get("1.0", "end")
        elif tab == str(self.tab_portfolio):
            text = self.pf_metrics.get("1.0", "end")
        else:
            text = self.adv_text.get("1.0", "end")
        path = self._ask_save(".txt", [("Texte", "*.txt")])
        if not path:
            return
        try:
            export_text_report(text, path)
            self.status.set(f"Rapport exporté : {path}")
        except Exception as exc:
            messagebox.showerror("Export TXT", str(exc))

    # ------------------------------------------------------------- watchlist & historique
    def _watch_refresh(self):
        self.watch_listbox.delete(0, "end")
        self._watch_items = []
        if self.store is None:
            return
        try:
            items = self.store.watch_list()
        except Exception:
            return
        for src, sym, _added in items:
            self._watch_items.append((src, sym))
            self.watch_listbox.insert("end", f"{sym}  ({src})")

    def _watch_add_current(self):
        if self.store is None:
            return
        src = self.ctx["source"].get()
        sym = self.ctx["symbol"].get().strip().upper()
        if not sym:
            return
        self.store.watch_add(src, sym)
        self._watch_refresh()
        self.status.set(f"{sym} ajouté à la watchlist.")

    def _watch_remove_selected(self):
        if self.store is None:
            return
        sel = self.watch_listbox.curselection()
        if not sel:
            return
        src, sym = self._watch_items[sel[0]]
        self.store.watch_remove(src, sym)
        self._watch_refresh()
        self.status.set(f"{sym} retiré de la watchlist.")

    def _watch_load(self):
        sel = self.watch_listbox.curselection()
        if not sel:
            self.status.set("Watchlist : sélectionnez d'abord un actif.")
            return
        src, sym = self._watch_items[sel[0]]
        self._set_source(src)
        self.ctx["symbol"].set(sym)
        self.status.set(f"{sym} ({src}) chargé dans l'instrument global.")

    def _log_run(self, kind: str, symbol: str, summary: str, payload: dict):
        if self.store is None:
            return
        try:
            self.store.log_run(kind, symbol, summary, payload)
        except Exception:
            pass  # l'historique ne doit jamais faire échouer un calcul

    def _show_history(self):
        if self.store is None:
            messagebox.showinfo("Historique", "Stockage local indisponible.")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(t("history_title", self.lang))
        dlg.geometry("760x420")
        cols = ("date", "type", "symbole", "résumé")
        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=16)
        for c, w in zip(cols, (150, 110, 110, 380)):
            tree.heading(c, text=c)
            tree.column(c, width=w)
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        def refresh():
            for i in tree.get_children():
                tree.delete(i)
            for r in self.store.list_runs(300):
                tree.insert("", "end", values=(r["created_at"], r["kind"], r["symbol"], r["summary"]))

        refresh()
        b_clear = ttk.Button(dlg, command=lambda: (
            self.store.clear_runs(), refresh(),
            self.status.set("Historique effacé.")))
        self._set_text(b_clear, "history_clear")
        b_clear.pack(pady=(0, 8))
        b_close2 = ttk.Button(dlg, command=dlg.destroy)
        self._set_text(b_close2, "close")
        b_close2.pack(pady=(0, 8))

    # ------------------------------------------------------------- onglet 1
    def _build_analyse_tab(self):
        top = ttk.Frame(self.tab_analyse)
        top.pack(fill="x", padx=6, pady=4)
        lbl = ttk.Label(top)
        self._set_text(lbl, "analyse_strat_label")
        lbl.pack(side="left", padx=(0, 4))
        self.strat_var = tk.StringVar(value=list_strategies()[0]["key"])
        self.strat_box = ttk.Combobox(top, values=[s["key"] for s in list_strategies()],
                                      textvariable=self.strat_var, state="readonly", width=18)
        self.strat_box.pack(side="left")
        self.btn_analyse = ttk.Button(top, command=self.on_analyse)
        self._set_text(self.btn_analyse, "btn_analyse")
        self.btn_analyse.pack(side="left", padx=8)

        paned = ttk.PanedWindow(self.tab_analyse, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=4)
        left = ttk.Frame(paned)
        self.fig_a, self.canvas_a = self._make_chart(left)
        paned.add(left, weight=3)
        right = ttk.Frame(paned, width=400)
        paned.add(right, weight=1)
        adv_lbl = ttk.Label(right, font=("TkDefaultFont", 10, "bold"))
        self._set_text(adv_lbl, "advice_panel")
        adv_lbl.pack(anchor="w", padx=6, pady=(6, 2))
        self.adv_text = scrolledtext.ScrolledText(right, wrap="word", width=50, height=32)
        self.adv_text.pack(fill="both", expand=True, padx=6, pady=4)

    def on_analyse(self):
        vals = self._ctx_values(self.ctx)
        strat_key = self.strat_var.get()
        self.btn_analyse.config(state="disabled")
        self._progress_begin()
        self.status.set(f"Chargement de {vals['symbol']}...")

        def work():
            df = self._load_df(vals)
            signals = get_strategy(strat_key).generate(df)
            advice = advise(df, vals["symbol"], source=vals["source"], interval=vals["interval"])
            return df, signals, advice

        def done(result, err):
            self._progress_end()
            self.btn_analyse.config(state="normal")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur", str(err))
                return
            self.df, self.last_signals, self.adv = result
            draw_price_chart(self.fig_a, self.df, self.last_signals,
                             title=f"{vals['symbol']} — {vals['interval']} — stratégie {strat_key}")
            self.canvas_a.draw_idle()
            self._attach_crosshair()
            self._fill_advice()
            try:
                first, last, n_db = data_range(vals["source"], vals["symbol"], vals["interval"])
                plage = f" | base: {n_db} barres du {first.date()} au {last.date()}" if n_db else ""
            except Exception:
                plage = ""
            self.status.set(f"{vals['symbol']} : {len(self.df)} barres{plage}. "
                            f"Avis : {self.adv['label']} (score {self.adv['score']:+.2f}).")

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
        if adv.get("earnings_date"):
            t.insert("end", f"Prochaine publication (résultats) : {adv['earnings_date']}\n")
        if adv["stop_suggestion"]:
            t.insert("end", f"Stop suggéré (2x ATR) : {adv['stop_suggestion']:.2f}\n")
        t.insert("end", "\nDétail :\n")
        for r in adv["reasons"]:
            t.insert("end", f"• {r}\n")
        t.config(state="disabled")

    # ------------------------------------------------------------- onglet 2
    def _build_backtest_tab(self):
        mid = ttk.LabelFrame(self.tab_backtest)
        self._set_text(mid, "bt_frame")
        mid.pack(fill="x", padx=6, pady=4)
        l0 = ttk.Label(mid)
        self._set_text(l0, "strat_label")
        l0.grid(row=0, column=0, sticky="w", padx=4, pady=4)
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

        def fld(row, col, key):
            lbl = ttk.Label(mid)
            self._set_text(lbl, key)
            lbl.grid(row=row, column=col, sticky="w", padx=4, pady=4)

        fld(2, 0, "capital")
        self.bt_capital = ttk.Entry(mid, width=10)
        self.bt_capital.insert(0, "10000")
        self.bt_capital.grid(row=2, column=1, sticky="w")
        fld(2, 2, "slippage")
        self.bt_slippage = ttk.Entry(mid, width=7)
        self.bt_slippage.insert(0, "0.05")
        self.bt_slippage.grid(row=2, column=3, sticky="w")
        fld(2, 4, "sizing")
        self.bt_sizing = ttk.Combobox(mid, values=["all_in", "risk"], state="readonly", width=10)
        self.bt_sizing.set("all_in")
        self.bt_sizing.grid(row=2, column=5, sticky="w")
        fld(3, 0, "risk_per_trade")
        self.bt_risk = ttk.Entry(mid, width=7)
        self.bt_risk.insert(0, "1.0")
        self.bt_risk.grid(row=3, column=1, sticky="w")
        self.bt_short = tk.BooleanVar(value=False)
        self.chk_short = ttk.Checkbutton(mid, variable=self.bt_short)
        self._set_text(self.chk_short, "allow_short")
        self.chk_short.grid(row=3, column=2, sticky="w", padx=8)

        fld(4, 0, "fee_preset")
        self.bt_fee = ttk.Combobox(mid, values=list_presets(), state="readonly", width=26)
        self.bt_fee.set("trade_republic_fr_auto")  # défaut : Trade Republic FR
        self.bt_fee.grid(row=4, column=1, columnspan=2, sticky="w")
        fld(4, 3, "fee_flat")
        self.bt_fee_flat = ttk.Entry(mid, width=7)
        self.bt_fee_flat.insert(0, "0")
        self.bt_fee_flat.grid(row=4, column=4, sticky="w")
        fld(4, 5, "fee_pct")
        self.bt_fee_pct = ttk.Entry(mid, width=7)
        self.bt_fee_pct.insert(0, "0")
        self.bt_fee_pct.grid(row=4, column=6, sticky="w")

        fld(5, 0, "vol_target")
        self.bt_voltarget = ttk.Entry(mid, width=7)
        self.bt_voltarget.insert(0, "")
        self.bt_voltarget.grid(row=5, column=1, sticky="w")
        fld(5, 2, "dd_stop")
        self.bt_ddstop = ttk.Entry(mid, width=7)
        self.bt_ddstop.insert(0, "")
        self.bt_ddstop.grid(row=5, column=3, sticky="w")
        fld(5, 4, "funding")
        self.bt_funding = ttk.Entry(mid, width=9)
        self.bt_funding.insert(0, "0")
        self.bt_funding.grid(row=5, column=5, sticky="w")

        self.btn_bt = ttk.Button(mid, command=self.on_backtest)
        self._set_text(self.btn_bt, "btn_backtest")
        self.btn_bt.grid(row=6, column=0, padx=4, pady=6)
        for col, key, cmd in ((1, "btn_walkforward", self.on_walkforward),
                              (2, "btn_gridsearch", self.on_gridsearch),
                              (3, "btn_montecarlo", self.on_montecarlo)):
            b = ttk.Button(mid, command=cmd)
            self._set_text(b, key)
            b.grid(row=6, column=col, padx=4)

        paned = ttk.PanedWindow(self.tab_backtest, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=4)
        left = ttk.Frame(paned)
        self.fig_bt, self.canvas_bt = self._make_chart(left)
        paned.add(left, weight=3)
        right = ttk.Frame(paned, width=460)
        paned.add(right, weight=1)
        lbr = ttk.Label(right)
        self._set_text(lbr, "bt_results")
        lbr.pack(anchor="w", padx=6)
        self.bt_metrics = scrolledtext.ScrolledText(right, wrap="word", width=56, height=12)
        self.bt_metrics.pack(fill="x", padx=6, pady=4)
        lbt = ttk.Label(right)
        self._set_text(lbt, "trades_label")
        lbt.pack(anchor="w", padx=6)
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

    # --- helpers backtest -------------------------------------------------
    def _fee_from_widgets(self, source: str):
        preset = self.bt_fee.get()
        if preset == "custom":
            from ..fees import FeeModel
            return FeeModel(f"Custom {self.bt_fee_flat.get()}€ + {self.bt_fee_pct.get()}%",
                            flat=float(self.bt_fee_flat.get() or 0),
                            pct=float(self.bt_fee_pct.get() or 0) / 100.0)
        return get_fee_model(preset, source)

    def _bt_common(self):
        """Snapshot des réglages (thread principal) -> (vals, strat_key, strat_params, bt_opts)."""
        vals = self._ctx_values(self.ctx)
        strat_key = self.bt_strat_var.get()
        self._rebuild_params(self._params_parent)
        strat_params = self._strategy_params()
        fee = self._fee_from_widgets(vals["source"])
        bt_opts = {
            "initial_capital": float(self.bt_capital.get() or 10000),
            "fee": fee,
            "slippage": float(self.bt_slippage.get() or 0) / 100.0,
            "allow_short": bool(self.bt_short.get()),
            "sizing": self.bt_sizing.get(),
            "risk_per_trade": float(self.bt_risk.get() or 1) / 100.0,
            "funding_per_bar": float(self.bt_funding.get() or 0),
            "periods_per_year": periods_per_year(vals["interval"], vals["source"]),
        }
        vt = self.bt_voltarget.get().strip()
        bt_opts["vol_target"] = float(vt) / 100.0 if vt else None
        dd = self.bt_ddstop.get().strip()
        bt_opts["max_drawdown_stop"] = float(dd) / 100.0 if dd else None
        return vals, strat_key, strat_params, bt_opts

    def _show_bt_result(self, res, strat, vals, title_suffix=""):
        self.backtest_result = res
        self._bt_draw = (res, strat, vals, title_suffix)
        draw_equity_chart(self.fig_bt, res.equity, res.trades,
                          title=f"Equity — {strat.label} sur {vals['symbol']} ({vals['interval']}) {title_suffix}")
        self.canvas_bt.draw_idle()
        m = res.metrics
        lines = [
            f"Actif : {vals['symbol']} ({vals['source']}, {vals['interval']}, {vals['period']})   Stratégie : {strat.label}",
            f"Frais : {res.params.get('fee', '')}",
            f"Rendement total : {m['total_return'] * 100:+.2f} %   CAGR : {m['cagr'] * 100:+.2f} %   Frais payés : {m.get('total_fees', 0):.2f}",
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

    def on_backtest(self):
        vals, strat_key, strat_params, bt_opts = self._bt_common()
        self.btn_bt.config(state="disabled")
        self._progress_begin()
        self.status.set(f"Backtest de {strat_key} sur {vals['symbol']}...")

        def work():
            df = self._load_df(vals)
            strat = get_strategy(strat_key, **strat_params)
            signals = strat.generate(df)
            result = run_backtest(df, signals, **bt_opts)
            return result, strat

        def done(result, err):
            self._progress_end()
            self.btn_bt.config(state="normal")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur de backtest", str(err))
                return
            res, strat = result
            self._show_bt_result(res, strat, vals)
            m = res.metrics
            self._log_run("backtest", vals["symbol"],
                          f"{strat_key} : {m['n_trades']} trades, "
                          f"rendement {m['total_return'] * 100:+.2f} %, Sharpe {m['sharpe']:.2f}",
                          {"strategy": strat_key, "params": strat_params, "metrics": m})
            self.status.set(f"Backtest {vals['symbol']} terminé : {m['n_trades']} trades, rendement {m['total_return'] * 100:+.2f} %, frais {m.get('total_fees', 0):.2f}.")

        self._run_async(work, done)

    def on_walkforward(self):
        vals, strat_key, strat_params, bt_opts = self._bt_common()
        self._progress_begin()
        self.status.set(f"Walk-forward sur {vals['symbol']}...")

        def work():
            df = self._load_df(vals)
            grid = {"fast": [8, 12, 20], "slow": [26, 40, 60]} if strat_key == "ma_cross" else None
            if grid:
                for k, v in strat_params.items():
                    if k not in grid:
                        grid[k] = [v]
            res = walk_forward(df, strat_key, params=strat_params, param_grid=grid,
                               n_splits=5, **{k: v for k, v in bt_opts.items() if k != "periods_per_year"},
                               periods_per_year=bt_opts["periods_per_year"])
            return res, df

        def done(result, err):
            self._progress_end()
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur walk-forward", str(err))
                return
            res, df = result
            oos = res["oos_metrics"]
            ins = res["in_sample_metrics"]
            lines = [f"WALK-FORWARD — {vals['symbol']} ({len(res['splits'])} blocs OOS)",
                     f"OOS : rendement {oos.get('total_return', 0) * 100:+.2f} %   Sharpe {oos.get('sharpe', 0):+.2f}   DD max {oos.get('max_drawdown', 0) * 100:.2f} %   trades {oos.get('n_trades', 0)}",
                     f"In-sample (référence) : rendement {ins['total_return'] * 100:+.2f} %   Sharpe {ins['sharpe']:+.2f}",
                     "Écart OOS vs in-sample = mesure d'overfitting (plus il est grand, plus la stratégie est fragile).", ""]
            for s in res["splits"]:
                p = ", ".join(f"{k}={v}" for k, v in s["params"].items())
                lines.append(f"  bloc {pd.Timestamp(s['test_start']):%Y-%m-%d} → {pd.Timestamp(s['test_end']):%Y-%m-%d} : "
                             f"params [{p}]  train_sharpe={s.get('train_sharpe', 0):+.2f}  test_ret={s['test_return'] * 100:+.2f} %  trades={s['test_trades']}")
            self.bt_metrics.config(state="normal")
            self.bt_metrics.delete("1.0", "end")
            self.bt_metrics.insert("end", "\n".join(lines))
            self.bt_metrics.config(state="disabled")
            draw_equity_chart(self.fig_bt, res["oos_equity"], None,
                              title=f"Equity OUT-OF-SAMPLE — {vals['symbol']} (walk-forward)")
            self.canvas_bt.draw_idle()
            self.status.set(f"Walk-forward terminé : {len(res['splits'])} blocs, Sharpe OOS {oos.get('sharpe', 0):+.2f} vs in-sample {ins['sharpe']:+.2f}.")

        self._run_async(work, done)

    def on_gridsearch(self):
        vals, strat_key, strat_params, bt_opts = self._bt_common()
        self._progress_begin()
        self.status.set(f"Grid search sur {vals['symbol']}...")
        grid = {"fast": [5, 8, 12, 20], "slow": [26, 40, 60], "trend_filter": [True, False]} \
            if strat_key == "ma_cross" else {"window": [10, 20, 30]} if strat_key == "donchian_breakout" \
            else {"period": [7, 14, 21], "oversold": [25, 30, 35]} if strat_key == "rsi_reversion" \
            else {"fast": [8, 12], "slow": [21, 26, 34]}

        def work():
            df = self._load_df(vals)
            return grid_search(df, strat_key, grid, fee=bt_opts["fee"],
                               slippage=bt_opts["slippage"],
                               periods_per_year=bt_opts["periods_per_year"], top=10)

        def done(result, err):
            self._progress_end()
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur grid search", str(err))
                return
            out = result
            if out.empty:
                self.status.set("Grid search : aucune configuration valide.")
                return
            lines = [f"GRID SEARCH — {vals['symbol']} — top {len(out)} configurations (score = Sharpe pénalisé par l'instabilité vs voisins)", ""]
            for _, r in out.head(10).iterrows():
                params = {k: r[k] for k in grid.keys()}
                lines.append(f"  {params}  → sharpe={r['sharpe']:+.2f} (voisins {r['neighbor_sharpe']:+.2f})  ret={r['total_return'] * 100:+.1f}%  "
                             f"DD={r['max_drawdown'] * 100:.1f}%  trades={int(r['n_trades'])}  score={r['score']:+.2f}")
            self.bt_metrics.config(state="normal")
            self.bt_metrics.delete("1.0", "end")
            self.bt_metrics.insert("end", "\n".join(lines))
            self.bt_metrics.config(state="disabled")
            self.status.set(f"Grid search terminé : {len(out)} configs testées, meilleure : {out.iloc[0][list(grid.keys())].to_dict()}.")

        self._run_async(work, done)

    def on_montecarlo(self):
        if self.backtest_result is None:
            messagebox.showinfo("Monte Carlo", "Lancez d'abord un backtest simple.")
            return
        res = self.backtest_result
        self._progress_begin()
        self.status.set("Monte Carlo sur les trades...")

        def work():
            return monte_carlo_trades(res.trades, initial_capital=res.params["initial_capital"],
                                      n_sims=1000, seed=42)

        def done(mc, err):
            self._progress_end()
            if err is not None:
                messagebox.showerror("Erreur Monte Carlo", str(err))
                return
            if "error" in mc:
                self.status.set("Monte Carlo : aucun trade à simuler.")
                return
            lines = [
                f"MONTE CARLO — {mc['n_sims']} simulations sur {mc['n_trades']} trades (bootstrap des rendements)",
                f"Probabilité de profit : {mc['prob_profit'] * 100:.1f} %",
                f"Valeur finale : p5 = {mc['p5_final']:.0f}  médiane = {mc['p50_final']:.0f}  p95 = {mc['p95_final']:.0f}  (capital initial {res.params['initial_capital']:.0f})",
                f"Drawdown max : p5 = {mc['p5_maxdd'] * 100:.1f} %  p95 = {mc['p95_maxdd'] * 100:.1f} %   pire simu = {mc['worst_final']:.0f}",
            ]
            self.bt_metrics.config(state="normal")
            self.bt_metrics.delete("1.0", "end")
            self.bt_metrics.insert("end", "\n".join(lines))
            self.bt_metrics.config(state="disabled")
            self.status.set(f"Monte Carlo : proba de profit {mc['prob_profit'] * 100:.1f} %, médiane {mc['p50_final']:.0f}.")

        self._run_async(work, done)

    # ------------------------------------------------------------- onglet 4 : portefeuille
    def _build_portfolio_tab(self):
        top = ttk.Frame(self.tab_portfolio)
        top.pack(fill="x", padx=6, pady=4)
        b_pf_fill = ttk.Button(top, command=self._fill_pf_symbols)
        self._set_text(b_pf_fill, "btn_fill_pf")
        b_pf_fill.pack(side="left", padx=4)
        lps = ttk.Label(top)
        self._set_text(lps, "strat_label")
        lps.pack(side="left", padx=(12, 2))
        self.pf_strat = ttk.Combobox(top, values=[s["key"] for s in list_strategies()],
                                     state="readonly", width=18)
        self.pf_strat.set("ma_cross")
        self.pf_strat.pack(side="left")
        lpf = ttk.Label(top)
        self._set_text(lpf, "pf_fee")
        lpf.pack(side="left", padx=(12, 2))
        self.pf_fee = ttk.Combobox(top, values=list_presets(), state="readonly", width=24)
        self.pf_fee.set("trade_republic_fr_auto")
        self.pf_fee.pack(side="left")
        lpc = ttk.Label(top)
        self._set_text(lpc, "pf_capital")
        lpc.pack(side="left", padx=(12, 2))
        self.pf_capital = ttk.Entry(top, width=8)
        self.pf_capital.insert(0, "10000")
        self.pf_capital.pack(side="left")
        self.btn_pf = ttk.Button(top, command=self.on_portfolio)
        self._set_text(self.btn_pf, "btn_portfolio")
        self.btn_pf.pack(side="left", padx=12)
        lpa = ttk.Label(self.tab_portfolio)
        self._set_text(lpa, "pf_actifs_label")
        lpa.pack(anchor="w", padx=6)
        self.pf_text = scrolledtext.ScrolledText(self.tab_portfolio, height=4, width=70)
        self.pf_text.pack(anchor="w", padx=6, pady=2)
        self._fill_pf_symbols()
        paned = ttk.PanedWindow(self.tab_portfolio, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=4)
        left = ttk.Frame(paned)
        self.fig_pf, self.canvas_pf = self._make_chart(left)
        paned.add(left, weight=3)
        right = ttk.Frame(paned, width=440)
        paned.add(right, weight=1)
        self.pf_metrics = scrolledtext.ScrolledText(right, wrap="word", width=54, height=30)
        self.pf_metrics.pack(fill="both", expand=True, padx=6, pady=4)

    def _fill_pf_symbols(self):
        src = self.ctx["source"].get()
        self.pf_text.delete("1.0", "end")
        self.pf_text.insert("1.0", "\n".join(default_symbols(src, 6)))

    def on_portfolio(self):
        vals = self._ctx_values(self.ctx)
        symbols = [s.strip().upper() for s in self.pf_text.get("1.0", "end").splitlines() if s.strip()]
        if not symbols:
            messagebox.showinfo("Portefeuille", "Entrez au moins un actif.")
            return
        strat_key = self.pf_strat.get()
        fee = get_fee_model(self.pf_fee.get(), vals["source"])
        capital = float(self.pf_capital.get() or 10000)
        slippage = float(self.bt_slippage.get() or 0) / 100.0
        self.btn_pf.config(state="disabled")
        self._progress_begin()
        self.status.set(f"Backtest portefeuille : {len(symbols)} actifs...")

        def work():
            dfs = {s: self._load_df({**vals, "symbol": s}) for s in symbols}
            return run_portfolio(dfs, strategy_key=strat_key, initial_capital=capital,
                                 fee=fee, slippage=slippage,
                                 periods_per_year=periods_per_year(vals["interval"], vals["source"]))

        def done(result, err):
            self._progress_end()
            self.btn_pf.config(state="normal")
            if err is not None:
                self.status.set(f"Erreur : {err}")
                messagebox.showerror("Erreur portefeuille", str(err))
                return
            res = result
            draw_equity_chart(self.fig_pf, res.equity, res.trades,
                              title=f"Equity portefeuille — {len(symbols)} actifs — {strat_key}")
            self.canvas_pf.draw_idle()
            m = res.metrics
            corr_lines = [f"  {a} : " + ", ".join(f"{b}={res.correlation.loc[a, b]:+.2f}"
                                                  for b in res.correlation.columns)
                          for a in res.correlation.index]
            lines = [
                f"PORTEFEUILLE — {len(symbols)} actifs ({vals['source']}, {vals['interval']}, {vals['period']}) — stratégie {strat_key}",
                f"Frais : {fee.describe()}",
                f"Rendement total : {m['total_return'] * 100:+.2f} %   CAGR : {m['cagr'] * 100:+.2f} %   Frais payés : {m.get('total_fees', 0):.2f}",
                f"Sharpe : {m['sharpe']:.2f}   DD max : {m['max_drawdown'] * 100:.2f} %   Win rate : {m['win_rate'] * 100:.1f} %   Trades : {m['n_trades']}",
                "", "Corrélation des rendements journaliers :", *corr_lines,
            ]
            self.pf_metrics.config(state="normal")
            self.pf_metrics.delete("1.0", "end")
            self.pf_metrics.insert("end", "\n".join(lines))
            self.pf_metrics.config(state="disabled")
            self.status.set(f"Portefeuille terminé : rendement {m['total_return'] * 100:+.2f} %, Sharpe {m['sharpe']:.2f}.")
            self._log_run("portefeuille", ",".join(symbols),
                          f"{len(symbols)} actifs, {strat_key} : rendement {m['total_return'] * 100:+.2f} %, "
                          f"Sharpe {m['sharpe']:.2f}, DD max {m['max_drawdown'] * 100:.2f} %",
                          {"strategy": strat_key, "symbols": symbols, "metrics": m})

        self._run_async(work, done)

    # ------------------------------------------------------------- onglet 3
    def _build_screener_tab(self):
        top = ttk.Frame(self.tab_screener)
        top.pack(fill="x", padx=6, pady=4)
        b_fill = ttk.Button(top, command=self._fill_symbols)
        self._set_text(b_fill, "btn_fill_default")
        b_fill.pack(side="left", padx=4)
        self.btn_scr = ttk.Button(top, command=self.on_screener)
        self._set_text(self.btn_scr, "btn_screener")
        self.btn_scr.pack(side="left", padx=8)
        b_open = ttk.Button(top, command=self._open_screener_selection)
        self._set_text(b_open, "btn_open_selection")
        b_open.pack(side="left", padx=4)
        self.scr_progress = ttk.Label(top, text="")
        self.scr_progress.pack(side="left", padx=8)
        self.advice_frame = ttk.LabelFrame(self.tab_screener)
        self._set_text(self.advice_frame, "advice_frame")
        self.advice_frame.pack(fill="x", padx=6, pady=4)
        self.scr_advice_var = tk.StringVar(value=t("advice_wait", self.lang))
        ttk.Label(self.advice_frame, textvariable=self.scr_advice_var,
                  wraplength=1150, justify="left").pack(anchor="w", padx=6, pady=4)
        lbl_scr = ttk.Label(self.tab_screener)
        self._set_text(lbl_scr, "screener_symbols_label")
        lbl_scr.pack(anchor="w", padx=6)
        mid = ttk.Frame(self.tab_screener)
        mid.pack(fill="x", padx=6, pady=2)
        self.scr_text = scrolledtext.ScrolledText(mid, height=5, width=60)
        self.scr_text.pack(side="left", anchor="w")
        self._fill_symbols()
        wf = ttk.LabelFrame(mid)
        self._set_text(wf, "watchlist_frame")
        wf.pack(side="left", fill="y", padx=(10, 0))
        self.watch_listbox = tk.Listbox(wf, height=5, width=24, exportselection=False)
        self.watch_listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.watch_listbox.bind("<Double-Button-1>", lambda _=None: self._watch_load())
        wbtns = ttk.Frame(wf)
        wbtns.pack(side="left", fill="y", padx=4, pady=4)
        for key, cmd in (("watch_add", self._watch_add_current),
                         ("watch_remove", self._watch_remove_selected),
                         ("watch_load", self._watch_load)):
            b = ttk.Button(wbtns, command=cmd)
            self._set_text(b, key)
            b.pack(fill="x", pady=1)
        self._watch_refresh()
        cols = ("symbole", "type", "cours", "avis", "stratégie", "sharpe", "ret%", "trades",
                "position", "horizon", "entrée", "stop", "objectif")
        self.scr_tree = ttk.Treeview(self.tab_screener, columns=cols, show="headings", height=18)
        widths = {"symbole": 90, "type": 60, "cours": 80, "avis": 150, "stratégie": 170, "sharpe": 60,
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
        self.scr_text.insert("1.0", "\n".join(default_symbols(src, 10)))

    def on_screener(self):
        vals = self._ctx_values(self.ctx)
        symbols = [s.strip().upper() for s in self.scr_text.get("1.0", "end").splitlines() if s.strip()]
        if not symbols:
            messagebox.showinfo("Screener", "Entrez au moins un symbole.")
            return
        self.btn_scr.config(state="disabled")
        # unités de progression = symboles × stratégies (la barre avance à chaque stratégie)
        total_units = len(symbols) * len(list_strategies())
        self._progress_begin(determinate=True, maximum=total_units)
        self.scr_progress.config(text=f"0/{total_units} — démarrage du calcul…")
        self._scr_t0 = time.time()
        prog_q: queue.Queue = queue.Queue()
        finished = {"done": False}

        def poll_progress():
            try:
                while True:
                    item = prog_q.get_nowait()  # ("step", done, total, symbole)
                    done, total, sym = item[1], item[2], item[3]
                    self._progress_set(done, total)
                    el = time.time() - self._scr_t0
                    if done > 0:
                        eta = el * (total - done) / done
                        self.scr_progress.config(
                            text=f"{done}/{total} — {sym} — écoulé {el:.0f} s — ETA ~{eta:.0f} s")
                    else:
                        self.scr_progress.config(text=f"0/{total} — démarrage du calcul…")
            except queue.Empty:
                pass
            if not finished["done"]:
                self.root.after(100, poll_progress)

        def work():
            return screen_assets(
                vals["source"], symbols, period=vals["period"], interval=vals["interval"],
                backend=vals.get("backend", "auto"),
                on_progress=lambda d, t, s: prog_q.put(("step", d, t, s)),
            )

        def done(result, err):
            finished["done"] = True
            self._progress_end()
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
                    r["symbol"], r.get("type", ""), r["last_close"], r["label"], r["strategy_label"],
                    r["sharpe"], f"{r['total_return'] * 100:+.1f}" if isinstance(r["total_return"], float) else r["total_return"],
                    r["n_trades"], r["position"], r["horizon"], r["entry"], r["stop"], r["target"]))
            n_ok = sum(1 for r in result if not r["error"])
            from ..compute import gpu_info
            be = vals.get("backend", "auto")
            if be == "cuda" and gpu_info() is None:
                be_label = "CPU (repli : 'cuda' demandé mais aucune carte CUDA détectée)"
            else:
                be_used = detect_backend() if be == "auto" else be
                be_label = backend_label(be_used)
            self.status.set(f"Screener terminé : {n_ok}/{len(result)} actifs analysés "
                            f"({vals['source']}, {vals['interval']}) — calcul : {be_label}. "
                            f"Double-clic pour ouvrir un actif.")
            pick = top_pick(result)
            if pick:
                self.scr_advice_var.set(
                    f"À ACHETER aujourd'hui : {pick['symbol']} à ~{pick['entry']} — "
                    f"stop {pick['stop']}, objectif {pick['target']}, horizon {pick['horizon']}. "
                    f"{pick['why']}.")
            else:
                self.scr_advice_var.set(
                    "Aucun candidat acheteur pertinent aujourd'hui : rester en liquidités / attendre.")
            self._log_run("screener", vals["symbol"] or "multi",
                          f"{n_ok}/{len(result)} actifs analysés ({vals['source']}, {vals['interval']}), "
                          f"conseil : {pick['symbol'] if pick else 'aucun'}",
                          {"source": vals["source"], "interval": vals["interval"], "n": len(result),
                           "n_ok": n_ok, "top_pick": pick["symbol"] if pick else None,
                           "rows": [{k: r.get(k) for k in ("symbol", "type", "score", "label", "position",
                                                          "best_strategy", "sharpe")} for r in result]})

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
