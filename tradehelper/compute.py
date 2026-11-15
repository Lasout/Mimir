"""Backend de calcul : CPU (pandas/numpy) ou GPU (CUDA via cupy + xgboost).

Activable à façon :
  - config `compute_backend` ("auto" | "cpu" | "cuda")
  - ou variable d'environnement TRADEHELPER_BACKEND=auto|cpu|cuda

"auto" = CUDA si une carte NVIDIA + cupy sont détectés, sinon CPU.
Sur un PC avec RTX 5060 Ti : pip install -r requirements-gpu.txt (CUDA >= 12.8 requis
pour les RTX 50 / Blackwell), puis le backend passe en "cuda" automatiquement.

Vérification : python3 -m tradehelper.compute
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .config import load_config


def gpu_info() -> dict | None:
    """Infos GPU si une carte CUDA est utilisable, sinon None."""
    try:
        import cupy as cp
        n = cp.cuda.runtime.getDeviceCount()
        if n <= 0:
            return None
        props = cp.cuda.runtime.getDeviceProperties(0)
        name = props.get("name", b"?")
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        return {"backend": "cuda", "device": str(name), "devices": int(n),
                "vram_gb": round(props.get("totalGlobalMem", 0) / 1e9, 1)}
    except Exception:
        return None


def detect_backend(cfg: dict | None = None) -> str:
    """Retourne "cuda" ou "cpu" selon la config/env et le matériel détecté."""
    cfg = cfg or load_config()
    want = (os.environ.get("TRADEHELPER_BACKEND") or cfg.get("compute_backend", "auto")).lower()
    if want in ("cuda", "gpu"):
        if gpu_info() is None:
            raise RuntimeError("Backend 'cuda' demandé mais aucune carte CUDA détectée. "
                               "Installer cupy (requirements-gpu.txt) ou passer en cpu.")
        return "cuda"
    if want == "cpu":
        return "cpu"
    return "cuda" if gpu_info() is not None else "cpu"  # auto


def backend_label(backend: str) -> str:
    if backend == "cuda":
        info = gpu_info()
        return f"GPU CUDA — {info['device']} ({info['vram_gb']} Go VRAM)" if info else "GPU CUDA"
    return "CPU (pandas/numpy)"


# ---------------------------------------------------------------------------
# Indicateurs sur GPU (cupy). Mêmes définitions que indicators.py (pandas).
# ---------------------------------------------------------------------------

def _sma_gpu(x, w: int):
    import cupy as cp
    cs = cp.concatenate([[0.0], cp.cumsum(x)])
    out = (cs[w:] - cs[:-w]) / w
    return cp.concatenate([cp.full(w - 1, cp.nan), out])


def _ema_gpu(x, span: int):
    """EMA adjust=False, identique à pandas ewm(span, adjust=False)."""
    import cupy as cp
    from cupyx.scipy.signal import lfilter
    alpha = 2.0 / (span + 1.0)
    zi = (1.0 - alpha) * x[0]
    y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], x, zi=[zi])
    return y


def _wilder_gpu(x, period: int):
    """Lissage de Wilder (alpha = 1/period), identique à ewm(alpha=1/period, adjust=False)."""
    import cupy as cp
    from cupyx.scipy.signal import lfilter
    alpha = 1.0 / period
    zi = (1.0 - alpha) * x[0]
    y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], x, zi=[zi])
    return y


def _rolling_std_gpu(x, w: int):
    import cupy as cp
    from cupy.lib.stride_tricks import sliding_window_view
    win = sliding_window_view(x, w)
    out = win.std(axis=1, ddof=0)
    return cp.concatenate([cp.full(w - 1, cp.nan), out])


def _rolling_max_gpu(x, w: int):
    import cupy as cp
    from cupy.lib.stride_tricks import sliding_window_view
    out = sliding_window_view(x, w).max(axis=1)
    return cp.concatenate([cp.full(w - 1, cp.nan), out])


def _rolling_min_gpu(x, w: int):
    import cupy as cp
    from cupy.lib.stride_tricks import sliding_window_view
    out = sliding_window_view(x, w).min(axis=1)
    return cp.concatenate([cp.full(w - 1, cp.nan), out])


def _rsi_gpu(close, period: int = 14):
    import cupy as cp
    delta = cp.diff(close, prepend=close[0])
    gain = cp.clip(delta, 0.0, None)
    loss = cp.clip(-delta, 0.0, None)
    avg_gain = _wilder_gpu(gain, period)
    avg_loss = _wilder_gpu(loss, period)
    rs = avg_gain / cp.where(avg_loss == 0, cp.nan, avg_loss)
    out = 100.0 - 100.0 / (1.0 + rs)
    return cp.where(avg_loss > 0, out, 100.0)


def add_all_gpu(df: pd.DataFrame, periods_per_year: float = 252) -> pd.DataFrame:
    """Equivalent GPU (cupy) de indicators.add_all. Retourne un DataFrame pandas."""
    import cupy as cp
    out = df.copy()
    o = cp.asarray(df["Open"].to_numpy(), dtype=cp.float64)
    h = cp.asarray(df["High"].to_numpy(), dtype=cp.float64)
    l = cp.asarray(df["Low"].to_numpy(), dtype=cp.float64)
    c = cp.asarray(df["Close"].to_numpy(), dtype=cp.float64)
    v = cp.asarray(df["Volume"].fillna(0.0).to_numpy(), dtype=cp.float64)

    def put(name, arr):
        out[name] = cp.asnumpy(arr)

    put("SMA20", _sma_gpu(c, 20))
    put("SMA50", _sma_gpu(c, 50))
    put("EMA12", _ema_gpu(c, 12))
    put("EMA26", _ema_gpu(c, 26))
    put("EMA50", _ema_gpu(c, 50))
    put("EMA200", _ema_gpu(c, 200))
    put("RSI14", _rsi_gpu(c, 14))
    macd_line = _ema_gpu(c, 12) - _ema_gpu(c, 26)
    put("MACD", macd_line)
    put("MACD_signal", _ema_gpu(macd_line, 9))
    put("MACD_hist", macd_line - _ema_gpu(macd_line, 9))
    mid = _sma_gpu(c, 20)
    std = _rolling_std_gpu(c, 20)
    put("BB_middle", mid)
    put("BB_upper", mid + 2 * std)
    put("BB_lower", mid - 2 * std)
    put("BB_pctb", (c - (mid - 2 * std)) / ((mid + 2 * std) - (mid - 2 * std)))
    prev_c = cp.concatenate([[c[0]], c[:-1]])
    tr = cp.maximum(cp.maximum(h - l, cp.abs(h - prev_c)), cp.abs(l - prev_c))
    atr14 = _wilder_gpu(tr, 14)
    put("ATR14", atr14)
    put("ATR_pct", atr14 / c)
    # ADX
    up = cp.diff(h, prepend=h[0])
    down = -cp.diff(l, prepend=l[0])
    plus_dm = cp.where((up > down) & (up > 0), up, 0.0)
    minus_dm = cp.where((down > up) & (down > 0), down, 0.0)
    plus_di = 100.0 * _wilder_gpu(plus_dm, 14) / atr14
    minus_di = 100.0 * _wilder_gpu(minus_dm, 14) / atr14
    denom = plus_di + minus_di
    dx = 100.0 * cp.abs(plus_di - minus_di) / cp.where(denom == 0, cp.nan, denom)
    put("ADX14", _wilder_gpu(dx, 14))
    put("Donchian_upper", _rolling_max_gpu(h, 20))
    put("Donchian_lower", _rolling_min_gpu(l, 20))
    direction = cp.sign(cp.diff(c, prepend=c[0]))
    put("OBV", cp.cumsum(v * direction))
    typical = (h + l + c) / 3.0
    cum_v = cp.cumsum(v)
    put("VWAP", cp.cumsum(typical * v) / cp.where(cum_v == 0, cp.nan, cum_v))
    log_ret = cp.log(c / cp.concatenate([[c[0]], c[:-1]]))
    put("Vol20", _rolling_std_gpu(log_ret, 20) * np.sqrt(periods_per_year))
    return out


def add_all_dispatch(df: pd.DataFrame, backend: str = "auto",
                     periods_per_year: float = 252) -> pd.DataFrame:
    """add_all avec dispatch CPU/GPU. Toute erreur GPU retombe en CPU."""
    from .indicators import add_all as add_all_cpu
    if backend == "auto":
        backend = detect_backend()
    if backend == "cuda":
        try:
            return add_all_gpu(df, periods_per_year=periods_per_year)
        except ImportError:
            pass  # pas de cupy -> CPU
        except Exception:
            pass  # erreur GPU quelconque -> CPU (résultat identique, plus lent)
    return add_all_cpu(df)


if __name__ == "__main__":
    print("Backend détecté :", detect_backend(), "-", backend_label(detect_backend()))
    info = gpu_info()
    print("GPU :", info if info else "aucune carte CUDA détectée (mode CPU)")
