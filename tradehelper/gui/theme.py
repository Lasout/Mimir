"""Thèmes clair / sombre pour l'interface (ttk + matplotlib) et zoom de police.

La partie « pure » (palettes, rcParams matplotlib, calcul de taille de police) ne
dépend pas de Tk et est testée hors-ligne ; `apply_ttk` applique la palette à un
`ttk.Style` (thème de base « clam », disponible partout).
"""

from __future__ import annotations

THEMES = ("clair", "sombre")

# Palettes ttk. bg = fond, fg = texte, field = champs de saisie, accent = sélection/boutons.
PALETTES: dict[str, dict[str, str]] = {
    "clair": {
        "bg": "#f2f3f5", "fg": "#1a1a1a", "field": "#ffffff", "accent": "#1565c0",
        "accent_fg": "#ffffff", "select_bg": "#cfe0f5", "heading": "#e3e5e8",
        "disabled": "#8a8a8a", "border": "#c8cacc", "tooltip_bg": "#ffffe1",
        "tooltip_fg": "#1a1a1a", "status_bg": "#e3e5e8",
    },
    "sombre": {
        "bg": "#1e1e24", "fg": "#e6e6ea", "field": "#2a2a33", "accent": "#4f9cf9",
        "accent_fg": "#101014", "select_bg": "#31435e", "heading": "#2e2e38",
        "disabled": "#7a7a85", "border": "#3a3a46", "tooltip_bg": "#3a3a2e",
        "tooltip_fg": "#f0f0d8", "status_bg": "#26262e",
    },
}

# rcParams matplotlib pour chaque thème (les chandeliers gardent vert/rouge partout).
def mpl_rc(name: str) -> dict:
    if name == "sombre":
        fg, grid, edge = "#e6e6ea", "#55555f", "#55555f"
        return {
            "figure.facecolor": "#1e1e24", "savefig.facecolor": "#1e1e24",
            "axes.facecolor": "#26262e", "axes.edgecolor": edge, "axes.labelcolor": fg,
            "axes.grid": True, "grid.color": grid, "grid.alpha": 0.35,
            "text.color": fg, "xtick.color": fg, "ytick.color": fg,
            "legend.facecolor": "#2a2a33", "legend.edgecolor": edge,
            "axes.prop_cycle": __import__("matplotlib").rcParams["axes.prop_cycle"],
        }
    fg, grid, edge = "#1a1a1a", "#c0c0c0", "#888888"
    return {
        "figure.facecolor": "#ffffff", "savefig.facecolor": "#ffffff",
        "axes.facecolor": "#fdfdfd", "axes.edgecolor": edge, "axes.labelcolor": fg,
        "axes.grid": True, "grid.color": grid, "grid.alpha": 0.3,
        "text.color": fg, "xtick.color": fg, "ytick.color": fg,
        "legend.facecolor": "#ffffff", "legend.edgecolor": edge,
    }


def apply_mpl_theme(name: str) -> None:
    import matplotlib
    matplotlib.rcParams.update(mpl_rc(name))


BASE_FONT_SIZE = 10
FONT_SCALE_MIN, FONT_SCALE_MAX = 0.8, 1.6


def font_size(scale: float = 1.0) -> int:
    return max(7, round(BASE_FONT_SIZE * scale))


def apply_ttk(style, name: str, scale: float = 1.0) -> None:
    """Applique la palette `name` au ttk.Style (thème de base clam) + taille de police."""
    if name not in PALETTES:
        name = "clair"
    p = PALETTES[name]
    fs = font_size(scale)
    style.theme_use("clam")
    style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                    font=("TkDefaultFont", fs), troughcolor=p["bg"])
    for t in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label", "TPanedwindow",
              "TSeparator", "TCheckbutton", "TRadiobutton"):
        style.configure(t, background=p["bg"], foreground=p["fg"], bordercolor=p["border"])
    style.configure("TLabelframe.Label", font=("TkDefaultFont", fs, "bold"))
    style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
    style.configure("TNotebook.Tab", background=p["heading"], foreground=p["fg"],
                    font=("TkDefaultFont", fs), padding=(10, 4))
    style.map("TNotebook.Tab",
              background=[("selected", p["select_bg"]), ("active", p["heading"])],
              foreground=[("selected", p["fg"])])
    style.configure("TButton", background=p["heading"], foreground=p["fg"],
                    font=("TkDefaultFont", fs), padding=(8, 3))
    style.map("TButton",
              background=[("disabled", p["bg"]), ("active", p["select_bg"]), ("pressed", p["accent"])],
              foreground=[("disabled", p["disabled"])])
    style.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                    bordercolor=p["border"], insertcolor=p["fg"])
    style.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"],
                    background=p["heading"], arrowcolor=p["fg"], bordercolor=p["border"],
                    insertcolor=p["fg"])
    style.map("TCombobox", fieldbackground=[("readonly", p["field"])],
              foreground=[("readonly", p["fg"]), ("disabled", p["disabled"])])
    style.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                    foreground=p["fg"], bordercolor=p["border"], font=("TkDefaultFont", fs))
    style.configure("Treeview.Heading", background=p["heading"], foreground=p["fg"],
                    font=("TkDefaultFont", fs, "bold"), relief="flat")
    style.map("Treeview", background=[("selected", p["accent"])],
              foreground=[("selected", p["accent_fg"])])
    style.map("Treeview.Heading", background=[("active", p["select_bg"])])
    style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
    style.map("TCheckbutton", foreground=[("disabled", p["disabled"])])
    style.configure("TMenubutton", background=p["bg"], foreground=p["fg"])
    style.configure("Horizontal.TScrollbar", background=p["heading"], troughcolor=p["bg"])
    style.configure("Vertical.TScrollbar", background=p["heading"], troughcolor=p["bg"])
    style.configure("Horizontal.TProgressbar", troughcolor=p["bg"], background=p["accent"],
                    bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])
    # widgets tk purs (barre de statut, tooltips)
    style.configure("Status.TLabel", background=p["status_bg"], foreground=p["fg"],
                    font=("TkDefaultFont", fs - 1), relief="sunken")
