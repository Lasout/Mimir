"""Horloge utilitaire : maintenant en UTC, naive (compatible pandas 3.x)."""

import pandas as pd


def utcnow_naive() -> pd.Timestamp:
    return pd.Timestamp.now("UTC").tz_localize(None)
