import sys, csv, math, copy, statistics as st
sys.path.insert(0, "."); from simport2 import *

def loadcsv(a): return [dict(t=int(r[0])*1000, o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
HOURS = {"BTC": loadcsv("BTC"), "ETH": loadcsv("ETH")}

def upto(t_end): return {a: [h for h in HOURS[a] if h["t"] <= t_end] for a in HOURS}

def mod(lb, tr, stp):
    out = copy.deepcopy(SPECS)
    for s in out:
        s["lookback"] = max(2, round(s["lookback"]*lb))
        s["trendLen"] = max(2, round(s["trendLen"]*tr))
        s["stopAtr"]  = s["stopAtr"]*stp
    return out

def metrics(r):
    d = {}
    for t, eq in r["curve"]: d[day(t)] = eq
    ks = sorted(d); eq = [d[k] for k in ks]
    if len(eq) < 3: return None
    yrs = (r["end"] - r["curve"][0][0])/(365.25*86_400_000)
    peak = -1e18; mdd = 0
    for e in eq: peak = max(peak, e); mdd = min(mdd, e/peak-1)
    rets = [eq[i]/eq[i-1]-1 for i in range(1, len(eq))]
    sd = st.pstdev(rets)
    sharpe = (st.mean(rets)/sd*math.sqrt(365)) if sd > 0 else 0.0
    cagr = ((r["equity"]/10_000)**(1/yrs)-1)*100 if r["equity"] > 0 and yrs > 0 else -100.0
    return dict(equity=r["equity"], cagr=cagr, sharpe=sharpe, mdd=mdd*100, trades=len(r["trades"]), yrs=yrs)

GRID = [(lb, tr, stp) for lb in (0.67, 0.85, 1.0, 1.2, 1.5) for tr in (0.6, 1.0, 1.6) for stp in (0.75, 1.0, 1.5)]
FOLDS = [("2021-11-10", "2023-11-10", "2024-11-10"),
         ("2021-11-10", "2024-11-10", "2025-11-10"),
         ("2021-11-10", "2025-11-10", "2026-11-09")]

print(f"Anchored walk-forward. Grid of {len(GRID)} parameter sets, selected by training Sharpe, then frozen for the test year.\n")
rows = []
for (tr0, tr1, te1) in FOLDS:
    train_h = upto(ms(tr1+"T00:00:00Z"))
    best = None
    for (lb, trm, stp) in GRID:
        r = run(train_h, ms(tr0+"T00:30:00Z"), spec_list=mod(lb, trm, stp))
        m = metrics(r)
        if m and (best is None or m["sharpe"] > best[1]["sharpe"]): best = ((lb, trm, stp), m)
    (lb, trm, stp), trm_m = best
    test_h = upto(ms(te1+"T00:00:00Z"))
    fit  = metrics(run(test_h, ms(tr1+"T00:30:00Z"), spec_list=mod(lb, trm, stp)))
    froz = metrics(run(test_h, ms(tr1+"T00:30:00Z")))
    bh = {}
    for a in HOURS:
        p0 = next(h["c"] for h in HOURS[a] if h["t"] >= ms(tr1+"T00:30:00Z"))
        p1 = [h["c"] for h in HOURS[a] if h["t"] <= ms(te1+"T00:00:00Z")][-1]
        bh[a] = (p1/p0-1)*100
    rows.append((tr0, tr1, te1, (lb, trm, stp), trm_m, fit, froz, bh))
    print(f"--- train {tr0} -> {tr1}  ({trm_m['yrs']:.1f}y) | TEST {tr1} -> {te1} ---")
    print(f"  fitted params: lookback x{lb}  trendEMA x{trm}  stop x{stp}   (training: {trm_m['cagr']:+.1f}%/yr, Sharpe {trm_m['sharpe']:.2f})")
    print(f"  OUT-OF-SAMPLE, fitted params : {fit['cagr']:+7.1f}%/yr  Sharpe {fit['sharpe']:5.2f}  maxDD {fit['mdd']:6.1f}%  trades {fit['trades']:3}  ${fit['equity']:,.0f}")
    print(f"  OUT-OF-SAMPLE, frozen params : {froz['cagr']:+7.1f}%/yr  Sharpe {froz['sharpe']:5.2f}  maxDD {froz['mdd']:6.1f}%  trades {froz['trades']:3}  ${froz['equity']:,.0f}")
    print(f"  buy & hold that year         : BTC {bh['BTC']:+.1f}%   ETH {bh['ETH']:+.1f}%\n")

print("=== SUMMARY ===")
cf = [r[5]["cagr"] for r in rows]; cz = [r[6]["cagr"] for r in rows]
print(f"  out-of-sample years: {len(rows)}")
print(f"  fitted-each-fold  : {' '.join(f'{x:+.1f}%' for x in cf)}   mean {st.mean(cf):+.1f}%/yr   positive {sum(1 for x in cf if x>0)}/{len(cf)}")
print(f"  frozen live params: {' '.join(f'{x:+.1f}%' for x in cz)}   mean {st.mean(cz):+.1f}%/yr   positive {sum(1 for x in cz if x>0)}/{len(cz)}")
print(f"  fitting beat frozen in {sum(1 for a,b in zip(cf,cz) if a>b)}/{len(cf)} test years")
print(f"  params chosen per fold: {[r[3] for r in rows]}  (1.0,1.0,1.0 = the live settings)")
