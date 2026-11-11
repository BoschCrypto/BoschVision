import sys, csv, math, statistics as st
sys.path.insert(0,"."); from simport3 import *
def L(a): return [dict(t=int(r[0])*1000,o=float(r[1]),h=float(r[2]),l=float(r[3]),c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
H={"BTC":L("BTC"),"ETH":L("ETH")}
S=ms("2021-11-10T00:30:00Z")

base=run(H,S)
print("PARITY: base run with % fees =", f"${base['equity']:,.0f}", f"({(base['equity']/10000-1)*100:+.0f}%)  costs ${sum(t['costs'] for t in base['trades']):,.0f}\n")

# what the MODELLED 0.06% fee actually equals in dollars per contract, by era
print("What the model's 0.06%/side actually charges per contract, vs a flat $0.35:")
for yr,px_b,px_e in [(2022,20000,1500),(2024,60000,3000),(2026,75000,2100)]:
    fb=0.01*px_b*0.0006; fe=0.1*px_e*0.0006
    print(f"  {yr}: BTC ${px_b:,} -> model charges ${fb:.3f}/contract  | ETH ${px_e:,} -> ${fe:.3f}/contract")
print()

def show(label, **kw):
    r=run(H,S,**kw); tr=r["trades"]
    d={}
    for t,eq in r["curve"]: d[day(t)]=eq
    eq=[d[k] for k in sorted(d)]; peak=-1e18; mdd=0
    for e in eq: peak=max(peak,e); mdd=min(mdd,e/peak-1)
    gp=sum(t["net"] for t in tr if t["net"]>0); gl=-sum(t["net"] for t in tr if t["net"]<=0)
    cagr=((r["equity"]/10000)**(1/5)-1)*100 if r["equity"]>0 else -100
    print(f"{label:52} {cagr:+6.1f}%/yr  final ${r['equity']:>9,.0f}  PF {gp/gl if gl else 0:4.2f}  maxDD {mdd*100:6.1f}%  costs ${sum(t['costs'] for t in tr):>7,.0f}")
    return cagr

print("=== PER-CONTRACT DOLLAR FEES (how the venue actually charges) ===")
show("exchange only, $0.10/side/contract",            fee_usd=0.10)
show("exchange + cheap broker, $0.35/side/contract",  fee_usd=0.35)
show("exchange + typical broker, $0.59/side/contract",fee_usd=0.59)
show("pessimistic, $1.00/side/contract",              fee_usd=1.00)
print()
print("=== ADD SPREAD/SLIPPAGE, on top of $0.35/side/contract ===")
print("   (1 tick = $5 on BTC price, $0.50 on ETH price; charged against us on every fill)")
for t in [1,2,5,10]:
    show(f"$0.35/side + {t} tick(s) adverse on every fill", fee_usd=0.35, slip_ticks=float(t))
print()
print("=== THE MODEL'S OWN ASSUMPTION, for comparison ===")
show("0.06%/side of notional (what the backtest used)")
show("0.12%/side + 0.25% slippage (my earlier stress test)", fee_pct=0.12, slip_pct=0.25)
