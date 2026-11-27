import json, time, sys, csv, urllib.request, os
from datetime import datetime, timezone
H = 3600
def fetch(prod, t0, t1):
    out = {}; s = t0
    while s < t1:
        e = min(s + 300*H, t1)
        url = f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=3600&start={datetime.fromtimestamp(s, tz=timezone.utc).isoformat()}&end={datetime.fromtimestamp(e - 1, tz=timezone.utc).isoformat()}".replace("+00:00", "Z")
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "backtest-research-client"})
                rows = json.load(urllib.request.urlopen(req, timeout=30)); break
            except Exception as ex:
                if attempt == 4: raise
                time.sleep(1.5 * (attempt + 1))
        for r in rows: out[int(r[0])] = (r[3], r[2], r[1], r[4])  # o,h,l,c
        s = e; time.sleep(0.12)
    return out
t0 = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp()); t1 = int(time.time()) // H * H
for prod, a in [("BTC-USD", "BTC"), ("ETH-USD", "ETH")]:
    d = fetch(prod, t0, t1); ks = sorted(d)
    with open(f"{a}_1h.csv", "w", newline="") as f:
        w = csv.writer(f); [w.writerow([k, *d[k]]) for k in ks]
    exp = (ks[-1] - ks[0]) // H + 1
    print(a, len(ks), "bars", datetime.fromtimestamp(ks[0], tz=timezone.utc), "->", datetime.fromtimestamp(ks[-1], tz=timezone.utc), "missing hours:", exp - len(ks))
