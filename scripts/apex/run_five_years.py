import sys, csv, math, statistics as st
sys.path.insert(0, "."); from simport import *
def loadcsv(a):
    return [dict(t=int(r[0])*1000, o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
hours = {"BTC": loadcsv("BTC"), "ETH": loadcsv("ETH")}
def daily(curve):
    d = {}
    for t, eq in curve: d[day(t)] = eq
    ks = sorted(d); return ks, [d[k] for k in ks]
def report(start, label):
    r = run(hours, ms(start)); tr = r["trades"]; ks, eq = daily(r["curve"])
    yrs = (r["end"] - r["curve"][0][0]) / (365.25*86_400_000)
    w = [t for t in tr if t["net"] > 0]; gp = sum(t["net"] for t in w); gl = -sum(t["net"] for t in tr if t["net"] <= 0)
    peak = -1e18; mdd = 0; ddstart = None; longest = 0; pk_day = ks[0]
    for k, e in zip(ks, eq):
        if e >= peak: peak = e; pk_day = k
        mdd = min(mdd, e/peak - 1)
        longest = max(longest, (ms(k+"T00:00:00Z") - ms(pk_day+"T00:00:00Z")) // 86_400_000)
    rets = [eq[i]/eq[i-1]-1 for i in range(1, len(eq))]
    sharpe = st.mean(rets)/st.pstdev(rets)*math.sqrt(365)
    w8 = [eq[i]/eq[i-56]-1 for i in range(56, len(eq))]
    print(f"\n===== {label}: {ks[0]} -> {ks[-1]} ({yrs:.2f} years) =====")
    print(f"  $10,000 -> ${r['equity']:,.0f}   total {(r['equity']/10000-1)*100:+.1f}%   CAGR {((r['equity']/10000)**(1/yrs)-1)*100:+.1f}%/yr   Sharpe {sharpe:.2f}")
    print(f"  max drawdown {mdd*100:.1f}%   longest stretch below a prior peak {longest} days")
    print(f"  trades {len(tr)}   wins {len(w)} ({len(w)/len(tr)*100:.0f}%)   profit factor {gp/gl:.2f}   avg win {gp/len(w):+.0f}  avg loss {-gl/(len(tr)-len(w)):+.0f}   costs paid {sum(t['costs'] for t in tr):,.0f}")
    print(f"  8-week windows: {sum(1 for x in w8 if x < 0)/len(w8)*100:.0f}% negative, median {st.median(w8)*100:+.1f}%, worst {min(w8)*100:+.1f}%, best {max(w8)*100:+.1f}%")
    print("  by calendar year:", end=" ")
    yr0 = {}
    for k, e in zip(ks, eq): yr0.setdefault(k[:4], [e, e]); yr0[k[:4]][1] = e
    prev = 10_000.0
    for y in sorted(yr0):
        print(f"{y} {(yr0[y][1]/prev-1)*100:+.1f}%", end="  "); prev = yr0[y][1]
    print()
    for k in ["btc_8h_ls", "sysa_1h_long", "eth_8h_ls"]:
        e = [t for t in tr if t["engine"] == k]; ew = [t for t in e if t["net"] > 0]
        egp = sum(t["net"] for t in ew); egl = -sum(t["net"] for t in e if t["net"] <= 0)
        lo = sum(t["net"] for t in e if t["side"] == 1); sh = sum(t["net"] for t in e if t["side"] == -1)
        print(f"    {k:13} net {sum(t['net'] for t in e):+9,.0f}  trades {len(e):4}  wins {len(ew)/len(e)*100:3.0f}%  PF {egp/egl:.2f}  longs {lo:+,.0f}  shorts {sh:+,.0f}")
    top = sorted(tr, key=lambda t: -t["net"]); tot = sum(t["net"] for t in tr)
    print(f"  concentration: top 5 trades = {sum(t['net'] for t in top[:5]):+,.0f}, top 10 = {sum(t['net'] for t in top[:10]):+,.0f}, all {len(tr)} = {tot:+,.0f}")
    for a in ["BTC", "ETH"]:
        p0 = next(h["c"] for h in hours[a] if h["t"] >= ms(start)); p1 = hours[a][-1]["c"]
        pk = -1; bdd = 0
        for h in hours[a]:
            if h["t"] < ms(start): continue
            pk = max(pk, h["c"]); bdd = min(bdd, h["c"]/pk - 1)
        print(f"  buy&hold {a}: {p0:,.0f} -> {p1:,.0f}  total {(p1/p0-1)*100:+.1f}%  CAGR {((p1/p0)**(1/yrs)-1)*100:+.1f}%/yr  max drawdown {bdd*100:.1f}%")
    return r
r5 = report("2021-11-10T00:30:00Z", "FIVE YEARS")
report("2021-07-20T00:30:00Z", "LONGEST AVAILABLE (after 200-day warm-up)")
print("\n===== cross-check vs live (Coinbase bars, seeded 2026-10-28 16:30Z; live used Alpaca bars) =====")
r = run(hours, ms("2026-10-28T16:30:00Z"))
for t in r["trades"]: print(f'  {t["engine"]:13} {"L" if t["side"]==1 else "S"} {t["contracts"]} {iso(t["etime"])} @{t["entry"]:.2f} -> {iso(t["xtime"])} @{t["exit"]:.2f} {t["reason"]} net {t["net"]:+.2f}')
for o in r["open"]: print(f'  OPEN {o["engine"]:13} {"L" if o["side"]==1 else "S"} {o["contracts"]} {iso(o["etime"])} @{o["entry"]:.2f} unreal {o["unreal"]:+.2f}')
