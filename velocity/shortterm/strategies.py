"""The candidates from PLAN.md section 5. Each function turns causal
features into a list of setups, in the order they become known.

A setup is a dict:
    i        one-minute index at which it is KNOWN (end of a 5-minute bar)
    side     +1 long, -1 short
    entry    "market" (fills at the next one-minute open) or
             "stop"   (resting stop orders in `legs`, live until `expiry_i`)
    stop     stop price (None for rules that set none)
    r_mult   profit target in multiples of the entry-to-stop distance, or None
    trail    trailing stop in ATRs behind the best price, or None
    time_min market exit after this many minutes, or None
    exit_i   one-minute index at which the rule itself says exit, or None
    reject   set when the setup formed but may not be traded (logged, with
             the reason), e.g. the stop would be too tight
    q        QUANT's context score at that moment (hourly z-score)

Nothing here looks at an account. Sizing and vetoes are WARDEN's
(sim.size_position); these are CHARTIST's and QUANT's output.
"""
from __future__ import annotations

import numpy as np

MIN_STOP, MAX_STOP = 0.0015, 0.015


def registry() -> list[dict]:
    """Every configuration that may be tried. 30 selectable, in PLAN.md order."""
    out = []
    for T in (24, 72):
        for k in (1.5, 2.5):
            for R in (1.5, 3.0):
                out.append({"id": f"F1-T{T}-k{k}-R{R}", "family": "F1", "T": T, "k": k, "R": R})
    for c in (0.6, 0.8):
        for filt in (True, False):
            for m in (3.0, 5.0):
                out.append({"id": f"F2-c{c}-{'trend' if filt else 'any'}-m{m}", "family": "F2",
                            "c": c, "filter": filt, "m": m})
    for N in (4, 12):
        for filt in (True, False):
            for R in (1.0, 2.0):
                out.append({"id": f"F3-N{N}-{'trend' if filt else 'any'}-R{R}", "family": "F3",
                            "N": N, "filter": filt, "R": R})
    for T in (24, 72):
        for short in (True, False):
            out.append({"id": f"S1-T{T}-{'ls' if short else 'lf'}", "family": "S1", "T": T, "short": short})
    for minutes in (15, 60):
        out.append({"id": f"S2-{minutes}m", "family": "S2", "minutes": minutes})
    return out


FAMILY_NAMES = {"F1": "Trend pullback", "F2": "Volatility breakout", "F3": "Failed-breakout fade",
                "S1": "Hourly trend", "S2": "Channel breakout"}


def _dist_ok(entry: float, stop: float) -> str:
    d = abs(entry - stop) / entry
    if d < MIN_STOP:
        return "stop closer than 0.15%"
    if d > MAX_STOP:
        return "stop further than 1.5%"
    return ""


def trend_pullback(f: dict, cfg: dict, asset: str) -> list[dict]:
    b = f["b5"]
    h, l, c, atr, end_i = b["high"], b["low"], b["close"], f["atr"], b["end_i"]
    T, k_atr, R = cfg["T"], cfg["k"], cfg["R"]
    trend, hsma, hz = f[f"trend{T}"], f[f"hsma{T}"], f[f"hz{T}"]
    with np.errstate(invalid="ignore"):
        up = (trend > 0) & (c <= f["hh36"] - k_atr * atr) & (c > hsma)
        dn = (trend < 0) & (c >= f["ll36"] + k_atr * atr) & (c < hsma)
    out, busy, n = [], 0, len(c)
    for k in np.nonzero(up | dn)[0]:
        if k < busy or k + 1 >= n:
            continue
        side = 1 if up[k] else -1
        ext = l[k] if side > 0 else h[k]
        fired = False
        for j in range(k + 1, min(k + 7, n)):
            ext = min(ext, l[j]) if side > 0 else max(ext, h[j])
            if (c[j] > h[j - 1]) if side > 0 else (c[j] < l[j - 1]):
                stop = ext - 0.25 * atr[j] if side > 0 else ext + 0.25 * atr[j]
                out.append({"asset": asset, "cfg": cfg["id"], "i": int(end_i[j]), "k": int(j), "side": side,
                            "entry": "market", "stop": float(stop), "r_mult": R, "trail": None,
                            "time_min": 360, "exit_i": None, "q": float(hz[j]),
                            "reject": _dist_ok(c[j], stop), "setup_i": int(end_i[k])})
                busy, fired = j + 1, True
                break
        if not fired:
            out.append({"asset": asset, "cfg": cfg["id"], "i": int(end_i[min(k + 6, n - 1)]), "k": int(k),
                        "side": side, "entry": "market", "stop": None, "r_mult": R, "trail": None,
                        "time_min": 360, "exit_i": None, "q": float(hz[k]),
                        "reject": "no trigger within 6 bars", "setup_i": int(end_i[k])})
            busy = k + 7
    return out


def vol_breakout(f: dict, cfg: dict, asset: str) -> list[dict]:
    b = f["b5"]
    end_i, atr = b["end_i"], f["atr"]
    rh, rl, trend, hz = f["range_hi"], f["range_lo"], f["trend24"], f["hz24"]
    with np.errstate(invalid="ignore"):
        tight = f["range4h"] <= cfg["c"] * f["range_med7d"]
    out, busy, n = [], 0, len(end_i)
    for k in np.nonzero(tight)[0]:
        if k < busy or k + 1 >= n or not np.isfinite(atr[k]):
            continue
        mid = (rh[k] + rl[k]) / 2
        legs = [(1, float(rh[k] + 0.1 * atr[k]), float(mid)), (-1, float(rl[k] - 0.1 * atr[k]), float(mid))]
        reject = ""
        if cfg["filter"]:
            legs = [g for g in legs if g[0] == trend[k]]
            if not legs:
                reject = "no 24-hour trend to side with"
        if legs:
            reject = reject or _dist_ok(legs[0][1], legs[0][2])
        out.append({"asset": asset, "cfg": cfg["id"], "i": int(end_i[k]), "k": int(k), "side": legs[0][0] if legs else 0,
                    "entry": "stop", "legs": legs, "expiry_i": int(end_i[min(k + 12, n - 1)]),
                    "stop": float(mid), "r_mult": None, "trail": cfg["m"], "time_min": 480, "exit_i": None,
                    "q": float(hz[k]), "reject": reject, "setup_i": int(end_i[k])})
        busy = k + 13
    return out


def failed_breakout(f: dict, cfg: dict, asset: str) -> list[dict]:
    b = f["b5"]
    h, l, c, atr, end_i = b["high"], b["low"], b["close"], f["atr"], b["end_i"]
    N, R = cfg["N"], cfg["R"]
    lh, ll, trend, hz = f[f"lvl_hi{N}"], f[f"lvl_lo{N}"], f["trend24"], f["hz24"]
    with np.errstate(invalid="ignore"):
        up = h >= lh + 0.1 * atr
        dn = l <= ll - 0.1 * atr
    out, busy, n = [], 0, len(c)
    for k in np.nonzero(up | dn)[0]:
        if k < busy:
            continue
        brk = 1 if up[k] else -1                   # direction of the break being faded
        level = lh[k] if brk > 0 else ll[k]
        ext = h[k] if brk > 0 else l[k]
        busy = k + 4
        for j in range(k, min(k + 4, n)):
            ext = max(ext, h[j]) if brk > 0 else min(ext, l[j])
            if (c[j] < level) if brk > 0 else (c[j] > level):
                stop = ext + 0.25 * atr[j] if brk > 0 else ext - 0.25 * atr[j]
                reject = _dist_ok(c[j], stop)
                if not reject and cfg["filter"] and trend[j] == brk:
                    reject = "24-hour trend points the way of the break"
                out.append({"asset": asset, "cfg": cfg["id"], "i": int(end_i[j]), "k": int(j), "side": -brk,
                            "entry": "market", "stop": float(stop), "r_mult": R, "trail": None,
                            "time_min": 240, "exit_i": None, "q": float(hz[j]), "reject": reject,
                            "setup_i": int(end_i[k]), "level": float(level)})
                busy = j + 1
                break
    return out


def _runs(desired: np.ndarray, end_i: np.ndarray, asset: str, cfg_id: str, q=None) -> list[dict]:
    out, n, a = [], len(desired), 0
    while a < n:
        if desired[a] == 0:
            a += 1
            continue
        b = a
        while b < n and desired[b] == desired[a]:
            b += 1
        out.append({"asset": asset, "cfg": cfg_id, "i": int(end_i[a]), "k": int(a), "side": int(desired[a]),
                    "entry": "market", "stop": None, "r_mult": None, "trail": None, "time_min": None,
                    "exit_i": int(end_i[b]) if b < n else None, "q": float(q[a]) if q is not None else 0.0,
                    "reject": "", "setup_i": int(end_i[a])})
        a = b
    return out


def hourly_trend(f: dict, cfg: dict, asset: str) -> list[dict]:
    z = f["b60"]
    hc = z["close"]
    T = cfg["T"]
    from .features import roll
    sma, sd = roll(hc, T, "mean"), roll(hc, T, "std")
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.nan_to_num((hc - sma) / np.where(sd > 0, sd, np.nan))
    want = np.sign(score)
    if not cfg["short"]:
        want = np.maximum(want, 0)
    return _runs(want, z["end_i"], asset, cfg["id"], score)


def channel(m1: dict, cfg: dict, asset: str) -> list[dict]:
    from .data import resample
    from ..research.edge_search import breakout
    b = resample(m1, cfg["minutes"])
    pos = np.array(breakout([float(x) for x in b["close"]], 20))
    return _runs(pos, b["end_i"], asset, cfg["id"])


def setups_for(m1: dict, f: dict, cfg: dict, asset: str) -> list[dict]:
    fam = cfg["family"]
    if fam == "F1":
        s = trend_pullback(f, cfg, asset)
    elif fam == "F2":
        s = vol_breakout(f, cfg, asset)
    elif fam == "F3":
        s = failed_breakout(f, cfg, asset)
    elif fam == "S1":
        s = hourly_trend(f, cfg, asset)
    elif fam == "S2":
        s = channel(m1, cfg, asset)
    else:
        raise ValueError(fam)
    s.sort(key=lambda x: x["i"])
    for n, x in enumerate(s):
        x["uid"] = f"{asset}:{cfg['id']}:{x['i']}:{x['side']}"
    return s
