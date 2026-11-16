"""Grille go/no-go capital réel — SPEC §5 (GNG-xx, P6.4).

Les 6 critères doivent être remplis **simultanément** ; un seul manquant =
« on continue le paper trading ». Le déblocage du broker réel (IBKR) exige cette
grille verte **et** `TRADEHELPER_ALLOW_LIVE_TRADING=1` + confirmation + plafond
(voir `broker.py`).

`evaluate_gonogo` est une fonction PURE (testable hors-ligne) ; `PaperEngine.gonogo()`
l'assemble à partir du dashboard, du benchmark B&H et des régimes détectés sur les
actifs tradés pendant le test.
"""

from __future__ import annotations

from .regime import regime_coverage

# Seuils SPEC §5
MIN_TRADES = 100  # GNG-01
MIN_EXPECTANCY_R = 0.15  # GNG-02 : espérance nette > +0,15 R, IC excluant 0
MIN_PAYOFF = 1.5  # GNG-03 : payoff >= 1,5 ...
MIN_WIN_RATE = 0.45  # GNG-03 : ... ou win rate >= 45 % (avec espérance positive)
MAX_DD_PCT = 0.25  # GNG-04 : max drawdown <= 25 % du capital simulé (borne haute 20-25 %)


def evaluate_gonogo(
    *,
    n_closed: int,
    expectancy_r: float | None,
    expectancy_ic95: float | None,
    win_rate: float | None,
    payoff: float | None,
    max_drawdown_pct: float | None,
    total_return: float | None,
    benchmark_return: float | None,
    regime_labels: list[str],
    min_trades: int = MIN_TRADES,
    min_expectancy_r: float = MIN_EXPECTANCY_R,
    min_payoff: float = MIN_PAYOFF,
    min_win_rate: float = MIN_WIN_RATE,
    max_dd_pct: float = MAX_DD_PCT,
) -> dict:
    """Évalue les 6 critères GNG. Retourne un dict :

    - `criteria` : liste de {id, label, ok, detail} (un par critère, dans l'ordre) ;
    - `n_ok` / `n_total` ; `missing` = ids des critères non remplis ;
    - `go` : True si les 6 sont ok ;
    - `verdict` : texte lisible (« GO ... » / « NO-GO — on continue le paper ... »).
    """
    ic_excludes_zero = expectancy_r is not None and expectancy_ic95 is not None and expectancy_r - expectancy_ic95 > 0
    gng02 = expectancy_r is not None and expectancy_r > min_expectancy_r and ic_excludes_zero
    gng03 = (expectancy_r is not None and expectancy_r > 0) and (
        (payoff is not None and payoff >= min_payoff) or (win_rate is not None and win_rate >= min_win_rate)
    )
    gng04 = max_drawdown_pct is not None and max_drawdown_pct >= -max_dd_pct
    gng05 = total_return is not None and benchmark_return is not None and total_return > benchmark_return
    cov = regime_coverage([x for x in regime_labels if x])
    criteria = [
        {
            "id": "GNG-01",
            "label": f"Trades clôturés >= {min_trades}",
            "ok": n_closed >= min_trades,
            "detail": f"{n_closed} clôturés",
        },
        {
            "id": "GNG-02",
            "label": f"Espérance nette > +{min_expectancy_r} R, IC95 exclut 0",
            "ok": gng02,
            "detail": (
                f"espérance {expectancy_r:+.3f} R, IC95 ±{expectancy_ic95:.3f}"
                if expectancy_r is not None and expectancy_ic95 is not None
                else "espérance inconnue"
            ),
        },
        {
            "id": "GNG-03",
            "label": f"Payoff >= {min_payoff} ou win rate >= {min_win_rate * 100:.0f} % (espérance > 0)",
            "ok": gng03,
            "detail": (
                f"payoff {payoff:.2f}, win rate {win_rate * 100:.0f} %"
                if payoff is not None and win_rate is not None
                else "payoff/win rate inconnus"
            ),
        },
        {
            "id": "GNG-04",
            "label": f"Max drawdown <= {max_dd_pct * 100:.0f} % du capital simulé",
            "ok": gng04,
            "detail": f"max DD {max_drawdown_pct * 100:.1f} %" if max_drawdown_pct is not None else "DD inconnu",
        },
        {
            "id": "GNG-05",
            "label": "Performance nette > buy-and-hold mêmes actifs, même période",
            "ok": gng05,
            "detail": (
                f"stratégie {total_return * 100:+.1f} % vs B&H {benchmark_return * 100:+.1f} %"
                if total_return is not None and benchmark_return is not None
                else "benchmark indisponible"
            ),
        },
        {
            "id": "GNG-06",
            "label": "Échantillon couvrant >= 2 régimes de marché",
            "ok": cov["ok"],
            "detail": f"{cov['n_regimes']}/3 régimes couverts {cov['counts']}",
        },
    ]
    missing = [c["id"] for c in criteria if not c["ok"]]
    go = not missing
    verdict = (
        "GO — grille verte : le déblocage du broker réel est possible (avec "
        "TRADEHELPER_ALLOW_LIVE_TRADING=1 + confirmation + plafond de notionnel)."
        if go
        else f"NO-GO — on continue le paper trading (critères manquants : {', '.join(missing)})."
    )
    return {
        "criteria": criteria,
        "n_ok": sum(1 for c in criteria if c["ok"]),
        "n_total": len(criteria),
        "missing": missing,
        "go": go,
        "verdict": verdict,
    }
