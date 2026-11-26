import sys; sys.path.insert(0, "."); from simport import *
P = "/root/.claude/projects/-home-user-BoschVision/559afb0d-07df-51ed-8406-ebaf94ed0db2/tool-results/mcp-Lovable-query_database-1794281117950.txt"
hours = load(P)
print("=== PARITY 2: first run after the 16:00 bar (2026-10-28 16:30Z) ===")
r = run(hours, ms("2026-10-28T16:30:00Z"))
for t in r["trades"]: print(f'  {t["engine"]:13} {"L" if t["side"]==1 else "S"} {t["contracts"]} {iso(t["etime"])} @{t["entry"]:.2f} -> {iso(t["xtime"])} @{t["exit"]:.2f} {t["reason"]} net {t["net"]:+.2f}')
for o in r["open"]: print(f'  OPEN {o["engine"]:13} {"L" if o["side"]==1 else "S"} {o["contracts"]} {iso(o["etime"])} @{o["entry"]:.3f} unreal {o["unreal"]:+.2f}')
print(f'  cash {r["cash"]:.2f} equity {r["equity"]:.2f}')

def stats(r, label):
    tr = r["trades"]; w = [t for t in tr if t["net"] > 0]; l = [t for t in tr if t["net"] <= 0]
    gp = sum(t["net"] for t in w); gl = -sum(t["net"] for t in l)
    peak = -1e18; mdd = 0
    for _, eq in r["curve"]:
        peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
    d0 = r["curve"][0][0]; days = (r["end"]-d0)/86_400_000
    ret = r["equity"]/10_000-1
    print(f'{label}: {days:.0f}d  equity {r["equity"]:.2f} ({ret*100:+.2f}%)  realised {r["cash"]-10000:+.2f}  open {sum(o["unreal"] for o in r["open"]):+.2f}  trades {len(tr)}  wins {len(w)} ({(len(w)/len(tr)*100 if tr else 0):.0f}%)  PF {(gp/gl if gl else float("nan")):.2f}  maxDD {mdd*100:.2f}%  costs {sum(t["costs"] for t in tr):.2f}')
    return tr
print("\n=== REPLAYS OVER STORED BARS (all three engines, $10,000, frozen rules and costs) ===")
for s in ["2026-05-20T00:30:00Z", "2026-06-15T00:30:00Z", "2026-07-10T00:30:00Z", "2026-08-10T00:30:00Z"]:
    r = run(hours, ms(s)); tr = stats(r, "start "+s[:10])
    if s.startswith("2026-07-10"):
        main = r
print("\n--- per engine, start 2026-07-10 ---")
for k in ["btc_8h_ls", "sysa_1h_long", "eth_8h_ls"]:
    tr = [t for t in main["trades"] if t["engine"] == k]; w = [t for t in tr if t["net"] > 0]
    gp = sum(t["net"] for t in w); gl = -sum(t["net"] for t in tr if t["net"] <= 0)
    op = sum(o["unreal"] for o in main["open"] if o["engine"] == k)
    lo = [t for t in tr if t["side"] == 1]; sh = [t for t in tr if t["side"] == -1]
    print(f'  {k:13} trades {len(tr):3} wins {len(w):2} realised {sum(t["net"] for t in tr):+8.2f} open {op:+7.2f}  PF {(gp/gl if gl else float("nan")):.2f}  best {max([t["net"] for t in tr], default=0):+.2f} worst {min([t["net"] for t in tr], default=0):+.2f}  longs {len(lo)} ({sum(t["net"] for t in lo):+.2f}) shorts {len(sh)} ({sum(t["net"] for t in sh):+.2f})')
print("\n--- start-time sensitivity: 21 starts, every 8h from 2026-07-06 ---")
eqs = []
for j in range(21):
    r = run(hours, ms("2026-07-06T00:30:00Z")+j*8*H); eqs.append(r["equity"])
eqs.sort(); print(f'  final equity min {eqs[0]:.0f}  median {eqs[10]:.0f}  max {eqs[-1]:.0f}')
def px(a, s): 
    t = ms(s); return next(h["c"] for h in hours[a] if h["t"] >= t)
for a in ["BTC", "ETH"]:
    p0 = px(a, "2026-07-10T00:00:00Z"); p1 = hours[a][-2]["c"]; print(f'  buy&hold {a} 07-10 -> 11-10: {p0:.2f} -> {p1:.2f} ({(p1/p0-1)*100:+.1f}%)')
