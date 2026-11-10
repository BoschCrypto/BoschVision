import sys, csv, math, copy, time
sys.path.insert(0, "."); from simport import *
def loadcsv(a): return [dict(t=int(r[0])*1000, o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
hours = {"BTC": loadcsv("BTC"), "ETH": loadcsv("ETH")}
START = ms("2021-11-10T00:30:00Z")
def line(label, **kw):
    t0 = time.time(); r = run(hours, START, **kw); tr = r["trades"]
    d = {}
    for t, eq in r["curve"]: d[day(t)] = eq
    eq = [d[k] for k in sorted(d)]; peak = -1e18; mdd = 0
    for e in eq: peak = max(peak, e); mdd = min(mdd, e/peak-1)
    gp = sum(t["net"] for t in tr if t["net"] > 0); gl = -sum(t["net"] for t in tr if t["net"] <= 0)
    yrs = 5.0; cagr = ((r["equity"]/10000)**(1/yrs)-1)*100 if r["equity"] > 0 else -100
    y22 = None
    print(f"{label:46} CAGR {cagr:+6.1f}%  maxDD {mdd*100:6.1f}%  PF {gp/gl:4.2f}  trades {len(tr):4}  final ${r['equity']:>9,.0f}  max gross {r['max_gross']:.2f}x  ({time.time()-t0:.0f}s)")
def mod(**f):
    out = copy.deepcopy(SPECS)
    for s in out:
        for k, fn in f.items(): s[k] = fn(s[k])
    return out
line("BASE (frozen rules, 0.06% fee, 10% carry)")
line("costs: 0.12% fee + 0.10% slippage per fill", fee_pct=0.12, slip_pct=0.10)
line("costs: 0.12% fee + 0.25% slippage per fill", fee_pct=0.12, slip_pct=0.25)
line("lookback x0.67", spec_list=mod(lookback=lambda v: round(v*0.67)))
line("lookback x1.33", spec_list=mod(lookback=lambda v: round(v*1.33)))
line("trend EMA x0.6", spec_list=mod(trendLen=lambda v: round(v*0.6)))
line("trend EMA x1.6", spec_list=mod(trendLen=lambda v: round(v*1.6)))
line("stop x0.75", spec_list=mod(stopAtr=lambda v: v*0.75))
line("stop x1.5", spec_list=mod(stopAtr=lambda v: v*1.5))
line("daily EMA x0.5", spec_list=mod(dailyLen=lambda v: round(v*0.5)))
line("daily EMA x2", spec_list=mod(dailyLen=lambda v: round(v*2)))
line("btc_8h_ls alone", keys=["btc_8h_ls"])
line("sysa_1h_long alone", keys=["sysa_1h_long"])
line("eth_8h_ls alone", keys=["eth_8h_ls"])
