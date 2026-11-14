"""Univers d'actifs connus, par source (listes de démarrage ; saisie libre possible)."""

UNIVERSE: dict[str, list[str]] = {
    "stock": [
        # US — tech / growth
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "CRM",
        "AVGO", "ORCL", "ADBE", "INTC", "QCOM", "CSCO", "UBER", "ABNB", "SHOP", "PLTR",
        # US — finance / conso / santé / énergie
        "JPM", "BAC", "V", "MA", "XOM", "CVX", "JNJ", "PFE", "KO", "PEP", "WMT", "MCD", "NKE",
        # Europe (suffixes Yahoo)
        "AIR.PA", "MC.PA", "OR.PA", "SAN.PA", "SAP.DE", "SIE.DE", "ALV.DE", "BNP.PA", "TTE.PA", "NESN.SW",
        # ETF (US puis Europe — les UCITS type VWCE/IWDA sont souvent dispo chez Trade Republic FR)
        "SPY", "QQQ", "VOO", "VTI", "SCHD", "JEPI", "ARKK",
        "VWCE.DE", "IWDA.AS", "EQQQ.PA", "CSPX.L", "SXRV.PA", "CW8.PA", "EWLD.PA", "ACWI.PA",
    ],
    "crypto": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
        "AVAXUSDT", "LINKUSDT", "MATICUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT",
    ],
    "forex": [
        "EURUSD", "GBPUSD", "USDJPY", "EURGBP", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURJPY", "GBPJPY", "EURCHF", "AUDJPY",
    ],
}

ETF_LIST: list[str] = [
    "SPY", "QQQ", "VOO", "VTI", "SCHD", "JEPI", "ARKK",
    "VWCE.DE", "IWDA.AS", "EQQQ.PA", "CSPX.L", "SXRV.PA", "CW8.PA", "EWLD.PA", "ACWI.PA",
]


def asset_type(source: str, symbol: str) -> str:
    """Type d'actif lisible : ETF / Action / Crypto / Forex."""
    if source == "stock":
        return "ETF" if symbol.upper() in ETF_LIST else "Action"
    return {"crypto": "Crypto", "forex": "Forex"}.get(source, source)


def default_symbols(source: str, n: int = 10) -> list[str]:
    """Liste par défaut pour screener/portefeuille : mélange actions + ETF pour les actions."""
    uni = universe_for(source)
    if source != "stock" or n >= len(uni):
        return uni[:n]
    n_etf = max(2, n // 3)
    stocks = [s for s in uni if s not in ETF_LIST]
    return stocks[:n - n_etf] + ETF_LIST[:n_etf]


def universe_for(source: str) -> list[str]:
    return list(UNIVERSE.get(source, []))
