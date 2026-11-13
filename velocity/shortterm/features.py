"""Causal features. Everything at 5-minute bar k uses only bars that had
closed by the end of bar k. Windows are finite, so recomputing on a trailing
window (as paper trading does) gives the same numbers as the full history.

test_shortterm.py changes the future and checks the past does not move.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _win

from .data import resample


def roll(x: np.ndarray, w: int, fn: str) -> np.ndarray:
    """fn over the last w values INCLUDING the current one; nan until full."""
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        v = _win(x, w)
        out[w - 1:] = {"max": v.max, "min": v.min, "mean": v.mean, "std": v.std}[fn](axis=1)
    return out


def prior(x: np.ndarray) -> np.ndarray:
    """The same series one bar earlier (so 'previous N bars' excludes now)."""
    out = np.empty(len(x))
    out[0] = np.nan
    out[1:] = x[:-1]
    return out


def build(m1: dict, trend_hours=(24, 72)) -> dict:
    b5 = resample(m1, 5)
    h, l, c = b5["high"], b5["low"], b5["close"]
    pc = prior(c)
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    f = {"b5": b5, "atr": roll(tr, 24, "mean")}

    # last COMPLETED hourly bar as of each 5-minute bar
    b60 = resample(m1, 60)
    hi = np.searchsorted(b60["end_i"], b5["end_i"], side="right") - 1
    ok = hi >= 0
    hc = b60["close"]
    for T in trend_hours:
        sma = roll(hc, T, "mean")
        sd = roll(hc, T, "std")
        f[f"hsma{T}"] = np.where(ok, sma[np.maximum(hi, 0)], np.nan)
        z = (hc - sma) / np.where(sd > 0, sd, np.nan)
        f[f"hz{T}"] = np.where(ok, z[np.maximum(hi, 0)], np.nan)        # QUANT's score
        f[f"trend{T}"] = np.sign(np.nan_to_num(f[f"hz{T}"]))
    f["b60"], f["h_of_5"] = b60, hi

    # 4-hour range now, and the median 4-hour range of the previous 7 days
    rng = roll(h, 48, "max") - roll(l, 48, "min")
    f["range_hi"], f["range_lo"], f["range4h"] = roll(h, 48, "max"), roll(l, 48, "min"), rng
    lags = np.arange(48, 2016 + 1, 48)
    n = len(rng)
    med = np.full(n, np.nan)
    if n > lags[-1]:
        idx = np.arange(lags[-1], n)[:, None] - lags[None, :]
        med[lags[-1]:] = np.median(rng[idx], axis=1)
    f["range_med7d"] = med

    f["hh36"], f["ll36"] = roll(h, 36, "max"), roll(l, 36, "min")
    for N in (4, 12):
        f[f"lvl_hi{N}"] = prior(roll(h, N * 12, "max"))     # previous N hours, excluding now
        f[f"lvl_lo{N}"] = prior(roll(l, N * 12, "min"))

    # per-minute helpers for the fill simulator
    n1 = len(m1["ts"])
    is_end = np.zeros(n1, dtype=bool)
    is_end[b5["end_i"]] = True
    k_of = np.searchsorted(b5["end_i"], np.arange(n1), side="right") - 1   # last completed 5m bar
    f["is5end"], f["k_of_min"] = is_end, k_of
    return f
