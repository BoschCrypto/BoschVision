"""Top-N momentum book with real-account frictions: spread, T+1 settlement, tax.

Settlement model (cash account, the binding constraint):
  At rebalance day i, sells execute at close i. Proceeds settle T+1 and the buys
  therefore execute at close i+1. Rotated capital sits in cash for one session and
  earns nothing. Retained names keep compounding across that day.

Tax model: realised gains at each rebalance are taxed at `tax_rate` and the tax is
paid OUT OF THE ACCOUNT. Every holding period here is under a year, so every gain
is short-term -- ordinary income. The benchmark (buy and hold) defers its tax
indefinitely, which is the honest comparison for a taxable account.
"""
from __future__ import annotations
from pathlib import Path
import statistics as st
from dataclasses import dataclass
from hf_trading_bot import pricecache, factors
from hf_trading_bot.xsection import _align_calendar, _series_on_calendar, GatedMomentumParams

CACHE = Path("data/bars")

def load():
    raw = {}
    for p in sorted(CACHE.glob("*.csv")):
        b = pricecache.read(CACHE, p.stem)
        if b: raw[p.stem] = b
    dates, _ = _align_calendar(raw, "SPY")
    series = {}
    for s, b in raw.items():
        al = _series_on_calendar(b, dates)
        if al: series[s] = al
    return dates, series

def rank_at(series, i, prm):
    scored = []
    for s, b in series.items():
        if b[i].c < prm.min_price: continue
        adv = factors.avg_dollar_volume(b, i)
        if adv is None or adv < prm.min_dollar_volume: continue
        m = factors.momentum_12_1(b, i)
        if m is None: continue
        scored.append((m, s))
    scored.sort(reverse=True)
    return [s for _, s in scored]

@dataclass
class Result:
    equity: list[float]
    dates: list[str]
    turnover: float      # total dollars traded / avg equity, annualised
    cost_paid: float
    tax_paid: float
    n_rebal: int

def run_book(series, dates, n_pos=20, step=21, rt_bps=20.0, tax_rate=0.0,
             settle=True, start_capital=1289.51, prm=None):
    prm = prm or GatedMomentumParams()
    one_way = rt_bps / 2 / 10000.0
    cash = start_capital
    shares: dict[str, float] = {}
    basis: dict[str, float] = {}   # dollars of cost in each holding
    eq_curve, eq_dates = [], []
    traded = 0.0; cost_paid = 0.0; tax_paid = 0.0; n_rebal = 0
    N = len(dates)
    i = 252
    while i < N - step - 2:
        px = {s: series[s][i].c for s in series}
        held_val = sum(q * px[s] for s, q in shares.items())
        equity = held_val + cash
        target = set(rank_at(series, i, prm)[:n_pos])
        if not target:
            i += step; continue
        tgt_each = equity / len(target)

        # --- SELLS at close i ---
        realised = 0.0
        for s in list(shares):
            cur = shares[s] * px[s]
            want = tgt_each if s in target else 0.0
            if cur - want > 0.005:
                sell_val = cur - want
                frac = sell_val / cur
                cost_of_sold = basis[s] * frac
                realised += sell_val - cost_of_sold
                fee = sell_val * one_way
                cash += sell_val - fee
                cost_paid += fee; traded += sell_val
                basis[s] -= cost_of_sold
                shares[s] -= sell_val / px[s]
                if shares[s] * px[s] < 0.005:
                    shares.pop(s); basis.pop(s, None)
        if tax_rate and realised > 0:
            t = realised * tax_rate
            cash -= t; tax_paid += t
        elif tax_rate and realised < 0:
            cash += -realised * tax_rate * 0  # no loss carryback modelled

        # --- BUYS at close i (no settlement) or i+1 (T+1) ---
        bi = i + 1 if settle else i
        bpx = {s: series[s][bi].c for s in series}
        need = {}
        for s in target:
            cur = shares.get(s, 0.0) * bpx[s]
            if tgt_each - cur > 0.005:
                need[s] = tgt_each - cur
        want_total = sum(need.values())
        if want_total > 0:
            avail = cash / (1 + one_way)
            scale = min(1.0, avail / want_total)
            for s, d in need.items():
                buy_val = d * scale
                if buy_val < 0.01: continue
                fee = buy_val * one_way
                cash -= buy_val + fee
                cost_paid += fee; traded += buy_val
                shares[s] = shares.get(s, 0.0) + buy_val / bpx[s]
                basis[s] = basis.get(s, 0.0) + buy_val
        n_rebal += 1
        # mark at the buy bar so the curve is sampled consistently
        eq_curve.append(sum(q * bpx[s] for s, q in shares.items()) + cash)
        eq_dates.append(dates[bi])
        i += step
    years = (len(dates) - 252) / 252.0
    avg_eq = st.mean(eq_curve) if eq_curve else start_capital
    return Result(eq_curve, eq_dates, traded / avg_eq / years, cost_paid, tax_paid, n_rebal)

def hold(series, dates, sym="VTI", start_capital=1289.51, step=21):
    b = series[sym]
    out, od = [], []
    i = 252
    N = len(dates)
    q = start_capital / b[i].c
    while i < N - step - 2:
        bi = i + 1
        out.append(q * b[bi].c); od.append(dates[bi])
        i += step
    return Result(out, od, 0.0, 0.0, 0.0, len(out))

def stats(r: Result, step: int = 21, start: float = 1289.51) -> dict:
    """Annualise from the ACTUAL rebalance period, never an assumed month.

    An earlier version hardcoded 12 periods per year, which reported a 263% CAGR
    for the annual-rebalance arm. Any Sharpe or CAGR here must be read against
    `n`: the annual arm has n=15, far too few for a trustworthy Sharpe.

    `maxdd` is sampled at the REBALANCE frequency, so drawdowns are NOT
    comparable across steps -- the annual arm's -8.7% is an artifact of looking
    at the portfolio once a year, not a gentler strategy.
    """
    ppy = 252.0 / step
    e = [start] + r.equity
    rets = [e[k + 1] / e[k] - 1 for k in range(len(e) - 1)]
    if not rets:
        return {}
    mu, sd = st.mean(rets), st.pstdev(rets)
    # Guard against float noise: a constant-return curve has sd ~1e-17 rather
    # than exactly 0, which would report an astronomical Sharpe.
    sharpe = (mu / sd) * (ppy ** 0.5) if sd > 1e-12 else 0.0
    dd, peak = 0.0, e[0]
    for v in e:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    years = len(rets) / ppy
    cagr = (e[-1] / e[0]) ** (1 / years) - 1 if years > 0 and e[-1] > 0 else float("nan")
    return dict(final=e[-1], total=e[-1] / e[0] - 1, cagr=cagr, sharpe=sharpe,
                maxdd=dd, turn=r.turnover, cost=r.cost_paid, tax=r.tax_paid,
                n=len(rets), years=years)
