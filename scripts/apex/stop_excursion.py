import sys, csv, statistics as st
sys.path.insert(0,"."); from simport3 import *
def L(a): return [dict(t=int(r[0])*1000,o=float(r[1]),h=float(r[2]),l=float(r[3]),c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
H={"BTC":L("BTC"),"ETH":L("ETH")}
r=run(H, ms("2021-11-10T00:30:00Z"))
idx={a:{b["t"]:b for b in H[a]} for a in H}
ex=[]
for t in r["trades"]:
    if t["reason"]!="stop": continue
    sp=next(s for s in SPECS if s["key"]==t["engine"])
    bar=idx[sp["asset"]].get(t["xtime"]); stop=t["exit"]
    if not bar: continue
    # how far PAST the stop did price travel inside the hour? upper bound on fill slippage
    beyond = (stop-bar["l"]) if t["side"]==1 else (bar["h"]-stop)
    ex.append(max(0.0, beyond)/stop*100)
ex.sort(); n=len(ex)
print(f"On the {n} stop-outs, how far price travelled PAST the stop inside the same hour")
print("(the sim assumes a fill exactly at the stop; this is the room available to slip):\n")
for p,lab in [(0.25,"25th"),(0.5,"median"),(0.75,"75th"),(0.9,"90th"),(0.99,"99th")]:
    print(f"  {lab:>7} pct: {ex[int(n*p)]:.2f}% past the stop")
print(f"  {'worst':>7}    : {ex[-1]:.2f}%")
print()
print("Translating a FRACTION of that excursion into realised slippage on every stop:")
for frac,lab in [(0.10,"10% of the excursion"),(0.25,"25%"),(0.50,"50%")]:
    avg=st.mean(ex)*frac
    print(f"  {lab:<22} -> average {avg:.3f}% worse per stop-out")
