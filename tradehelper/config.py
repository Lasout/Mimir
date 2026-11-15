"""Configuration de tradeHelper.

Ordre de priorité : DEFAULTS < fichier `.env` (répertoire courant, puis racine du projet)
< fichier JSON pointé par TRADEHELPER_CONFIG < variables d'environnement
(TRADEHELPER_DB_PATH, ALPHAVANTAGE_API_KEY...). Les vraies variables d'environnement
gagnent toujours sur `.env` (convention dotenv). `.env` ne doit jamais être commité
(voir .gitignore) : c'est le endroit pour les clés API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    # stockage
    "db_path": "~/.local/share/tradehelper/market.db",
    # TTL de fraîcheur par intervalle (secondes) : au-delà, on retélécharge la plage manquante
    "ttl_seconds": {"1d": 6 * 3600, "1wk": 6 * 3600, "1mo": 24 * 3600, "default": 300},
    # ajuste les prix des actions (dividendes/splits) via Yahoo auto_adjust.
    # Défaut True : vérifié en backtest (KO 5 ans, ma_cross : +29,4 %/Sharpe 0,48 ajusté
    # vs +6,6 %/0,17 brut — les dividendes sont inclus et les faux drawdowns de
    # détachement disparaissent). Changer le flag vide le store (re-téléchargement).
    "adjust_prices": True,
    # plafond de barres par téléchargement (sécurité, ex. pagination Binance)
    "max_bars_per_fetch": 100_000,
    # chaîne de fournisseurs par source (le premier qui réussit gagne)
    "providers": {
        "stock": ["yahoo", "alphavantage"],
        "crypto": ["binance", "yahoo"],
        "forex": ["frankfurter", "yahoo"],
    },
    # clé Alpha Vantage (gratuite) — aussi lisible via ALPHAVANTAGE_API_KEY
    "alphavantage_api_key": None,
    "request_timeout": 20,
    # calendrier des annonces : TTL du cache (24 h)
    "events_ttl_seconds": 24 * 3600,
    # backend de calcul : "auto" (CUDA si dispo) | "cpu" | "cuda" — env TRADEHELPER_BACKEND
    "compute_backend": "auto",
    # langue de l'interface : "fr" (défaut) | "en" — env TRADEHELPER_LANG
    "lang": "fr",
    # parallélisme du screener en mode CPU (processus) ; en mode CUDA, calcul GPU séquentiel
    "screener_workers": 0,  # 0 = auto (min(8, nb CPU))
}

_ENV_MAP = {
    "TRADEHELPER_DB_PATH": ("db_path", str),
    "ALPHAVANTAGE_API_KEY": ("alphavantage_api_key", str),
    "TRADEHELPER_ADJUST_PRICES": ("adjust_prices", lambda v: v.lower() in ("1", "true", "oui")),
    "TRADEHELPER_LANG": ("lang", str),
}


def _parse_dotenv(text: str) -> dict:
    """Parse un contenu `.env` (KEY=VALUE, # commentaires, guillemets optionnels)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def _load_dotenv() -> dict:
    """Lit un fichier `.env` (répertoire courant, puis racine du projet).

    Sans dépendance externe. Retourne {} si absent.
    """
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for path in candidates:
        if path.exists():
            return _parse_dotenv(path.read_text(encoding="utf-8"))
    return {}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # copie profonde
    for key, val in _load_dotenv().items():
        if key in _ENV_MAP:  # une clé connue : on pose la valeur comme si c'était une env var
            cfg[_ENV_MAP[key][0]] = _ENV_MAP[key][1](val)
        elif key.startswith("TRADEHELPER_"):
            cfg[key] = val
    path = os.environ.get("TRADEHELPER_CONFIG")
    if path and Path(path).exists():
        with open(path) as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(cfg.get(k), dict) and isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    for env, (key, cast) in _ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = cast(os.environ[env])
    cfg["db_path"] = str(Path(cfg["db_path"]).expanduser())
    return cfg


def ttl_for_interval(cfg: dict, interval: str) -> float:
    return float(cfg["ttl_seconds"].get(interval, cfg["ttl_seconds"]["default"]))
