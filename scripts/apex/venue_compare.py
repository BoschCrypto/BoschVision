import sys, csv, copy, math, statistics as st
sys.path.insert(0,"."); from simport3 import *
def L(a): return [dict(t=int(r[0])*1000,o=float(r[1]),h=float(r[2]),l=float(r[3]),c=float(r[4])) for r in csv.reader(open(f"{a}_1h.csv"))]
H={"BTC":L("BTC"),"ETH":L("ETH")}
S=ms("2021-11-10T00:30:00Z")
def specs(eth_cs):
    o=copy.deepcopy(SPECS)
    for s in o:
        if s["asset"]=="ETH": s["cs"]=eth_cs
    return o
def go(eq, eth_cs, fee):
    r=run(H,S,cash0=eq,spec_list=specs(eth_cs),fee_usd=fee)
    d={}
    for t,e in r["curve"]: d[day(t)]=e
    c=[d[k] for k in sorted(d)]; pk=-1e18; mdd=0
    for e in c: pk=max(pk,e); mdd=min(mdd,e/pk-1)
    eth=[t for t in r["trades"] if t["engine"]=="eth_8h_ls"]
    return dict(cagr=((r["equity"]/eq)**0.2-1)*100 if r["equity"]>0 else -100,
                n=len(r["trades"]), eth_n=len(eth), mdd=mdd*100,
                costs=sum(t["costs"] for t in r["trades"]))

print("Five years, same engine logic, only the VENUE differs.")
print("  KRAKEN  : BTC 0.01, ETH 0.50 per contract, $0.0113/side all-in, perpetual (no roll)")
print("  COINBASE: BTC 0.01, ETH 0.10 per contract, $0.35/side, ETH is MONTHLY (roll not modelled -> flattered)\n")
print(f"{'account':>9} | {'KRAKEN cagr':>11} {'trades':>7} {'ETH tr':>7} {'costs':>8} | {'COINBASE cagr':>13} {'trades':>7} {'ETH tr':>7} {'costs':>8}")
print("-"*96)
for eq in [1000,2000,3000,5000,7500,10000,20000,50000]:
    k=go(eq,0.5,0.0113); c=go(eq,0.1,0.35)
    print(f"{eq:>9,} | {k['cagr']:>10.1f}% {k['n']:>7} {k['eth_n']:>7} {k['costs']:>8,.0f} | {c['cagr']:>12.1f}% {c['n']:>7} {c['eth_n']:>7} {c['costs']:>8,.0f}")
print("\n(332 trades and 102 ETH trades = the full, undegraded run)")
