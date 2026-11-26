"""Python port of Apex sim-core.ts runSim, for replaying stored bars. Faithful to the TS logic."""
import json, sys, math
from datetime import datetime, timezone
H = 3_600_000
SETTLE = 60_000
ATR_LEN = 14
SPECS = [
 dict(key="btc_8h_ls", asset="BTC", barHours=8, lookback=90, trendLen=50, dailyLen=20, stopAtr=2, shorts=True, cs=0.01),
 dict(key="sysa_1h_long", asset="BTC", barHours=1, lookback=48, trendLen=400, dailyLen=50, stopAtr=3, shorts=False, cs=0.01),
 dict(key="eth_8h_ls", asset="ETH", barHours=8, lookback=90, trendLen=50, dailyLen=20, stopAtr=2, shorts=True, cs=0.1),
]
def ms(s): return int(datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()*1000)
def iso(m): return datetime.fromtimestamp(m/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
def day(m): return datetime.fromtimestamp(m/1000, tz=timezone.utc).strftime("%Y-%m-%d")
def ema(v, n):
    a = 2/(n+1); out = []
    for i, x in enumerate(v): out.append(x if i == 0 else a*x + (1-a)*out[-1])
    return out
def atr_series(bars, n):
    a = 1/n; out = []
    for i, b in enumerate(bars):
        tr = b["h"]-b["l"] if i == 0 else max(b["h"]-b["l"], abs(b["h"]-bars[i-1]["c"]), abs(b["l"]-bars[i-1]["c"]))
        out.append(tr if i == 0 else a*tr + (1-a)*out[-1])
    return out
def to_bars(hours, now, bh):
    bm = bh*H; minh = max(1, math.ceil(bh*0.75)); out = []
    for b in hours:
        st = (b["t"]//bm)*bm
        if not out or out[-1]["start"] != st: out.append(dict(start=st, o=b["o"], h=b["h"], l=b["l"], c=b["c"], n=1, last=b["t"]))
        else:
            c = out[-1]; c["h"] = max(c["h"], b["h"]); c["l"] = min(c["l"], b["l"]); c["c"] = b["c"]; c["last"] = b["t"]; c["n"] += 1
    res = []
    for b in out:
        end = b["start"]+bm
        if now < end+SETTLE: continue
        if b["n"] >= minh and (b["last"] == end-H or now >= end+45*60_000): res.append(b)
    return res
def daily_lookup(hours, now, n):
    today = day(now); closes = {}
    for b in hours:
        k = day(b["t"])
        if k < today: closes[k] = b["c"]
    keys = sorted(closes); vals = [closes[k] for k in keys]; e = ema(vals, n)
    by = {k: vals[i] > e[i] for i, k in enumerate(keys)}
    def f(bar_start):
        d0 = ms(day(bar_start)+"T00:00:00Z") - 86_400_000
        return by.get(day(d0))
    return f

def run(hours, start_ms, cash0=10_000.0, weight=33.33, risk=2.0, cap=3.0, fee_pct=0.06, carry_yr=10.0, keys=None, slip_pct=0.0, spec_list=None):
    now = max(hours[a][-1]["t"] for a in hours) + SETTLE + 1000   # newest stored hour treated as still forming
    fee = fee_pct/100; cph = carry_yr/100/8760; cash = cash0
    done = {a: [b for b in hours[a] if now >= b["t"]+H+SETTLE] for a in hours}
    last_close = {a: None for a in hours}
    specs = [s for s in (spec_list or SPECS) if keys is None or s["key"] in keys]; slip = slip_pct/100; mg = [0.0]
    eng = []; evs = []
    for idx, sp in enumerate(specs):
        allh = hours[sp["asset"]]; hrs = done[sp["asset"]]
        bars = to_bars(allh, now, sp["barHours"]); closes = [b["c"] for b in bars]
        st = dict(side=0, contracts=0, entry=None, etime=None, stop=None, efee=0.0, pending=0, patr=None, pexit=False)
        # seed as a first run at start_ms would: latest completed bar / hour at that moment
        lbe = max([b["start"]+sp["barHours"]*H for b in bars if start_ms >= b["start"]+sp["barHours"]*H+SETTLE], default=-1)
        lh = max([h["t"] for h in hrs if start_ms >= h["t"]+H+SETTLE], default=-1)
        for i, b in enumerate(bars):
            end = b["start"]+sp["barHours"]*H
            if end > lbe: evs.append((end, 0, idx, "close", i))
        for i, h in enumerate(hrs):
            if h["t"] > lh: evs.append((h["t"], 1, idx, "hour", i))
        eng.append(dict(sp=sp, st=st, bars=bars, closes=closes, trend=ema(closes, sp["trendLen"]), atr=atr_series(bars, ATR_LEN),
                        day=daily_lookup(allh, now, sp["dailyLen"]), hrs=hrs))
    first_t = min(e[0] for e in evs)
    for a in hours:
        for h in reversed(done[a]):
            if h["t"] < first_t: last_close[a] = h["c"]; break
    evs.sort(key=lambda e: (e[0], e[1], e[2]))
    def unreal(e, px, at):
        s = e["st"]
        if s["side"] == 0 or px is None: return 0.0
        q = s["contracts"]*e["sp"]["cs"]; hrs_ = max(0, (at-s["etime"])/H)
        return s["side"]*q*(px-s["entry"]) - s["efee"] - q*s["entry"]*cph*hrs_
    def marked(at): return cash + sum(unreal(e, last_close[e["sp"]["asset"]], at) for e in eng)
    trades = []; curve = []
    def close(e, px, at, reason):
        nonlocal cash
        s = e["st"]; q = s["contracts"]*e["sp"]["cs"]; hrs_ = max(0, (at-s["etime"])/H); px = px*(1 - s["side"]*slip)
        gross = s["side"]*q*(px-s["entry"]); xfee = q*px*fee; carry = q*s["entry"]*cph*hrs_
        net = gross - s["efee"] - xfee - carry; cash += net
        trades.append(dict(engine=e["sp"]["key"], side=s["side"], contracts=s["contracts"], etime=s["etime"], entry=s["entry"], xtime=at, exit=px, reason=reason, net=net, costs=s["efee"]+xfee+carry))
        s.update(side=0, contracts=0, entry=None, etime=None, stop=None, efee=0.0, pexit=False)
    for (t, pri, idx, kind, i) in evs:
        e = eng[idx]; s = e["st"]; sp = e["sp"]
        if kind == "hour":
            h = e["hrs"][i]
            if s["pexit"] and s["side"] != 0: close(e, h["o"], t, "signal")
            s["pexit"] = False
            if s["pending"] != 0 and s["side"] == 0:
                side = s["pending"]; atr = s["patr"] or 0
                if atr > 0:
                    entry = h["o"]*(1 + side*slip); dist = sp["stopAtr"]*atr
                    sleeve = max(0, marked(t))*(weight/100)
                    notional = min(cap*sleeve, (risk/100)*sleeve/(dist/entry))
                    n = math.floor(notional/(sp["cs"]*entry)+0.5)
                    if n >= 1:
                        q = n*sp["cs"]
                        s.update(side=side, contracts=n, entry=entry, etime=t, stop=entry-side*dist, efee=q*entry*fee)
            s["pending"] = 0; s["patr"] = None
            if s["side"] == 1 and s["stop"] is not None and h["l"] <= s["stop"]: close(e, s["stop"] if h["o"] > s["stop"] else h["o"], t, "stop")
            elif s["side"] == -1 and s["stop"] is not None and h["h"] >= s["stop"]: close(e, s["stop"] if h["o"] < s["stop"] else h["o"], t, "stop")
            last_close[sp["asset"]] = h["c"]
            m_ = marked(t); curve.append((t, m_))
            g_ = sum(x["st"]["contracts"]*x["sp"]["cs"]*(last_close[x["sp"]["asset"]] or 0) for x in eng)
            if m_ > 0: mg[0] = max(mg[0], g_/m_)
        else:
            b = e["bars"][i]
            if i < sp["lookback"] or i < sp["trendLen"]: continue
            cl = e["closes"][i]; back = e["closes"][i-sp["lookback"]]; tr = e["trend"][i]
            long_raw = cl > back and cl > tr; short_raw = cl < back and cl < tr; dup = e["day"](b["start"])
            if s["side"] != 0:
                if (s["side"] == 1 and short_raw) or (s["side"] == -1 and long_raw): s["pexit"] = True
            elif long_raw and dup is True: s["pending"] = 1; s["patr"] = e["atr"][i]
            elif sp["shorts"] and short_raw and dup is False: s["pending"] = -1; s["patr"] = e["atr"][i]
    end_t = curve[-1][0]
    open_pos = [dict(engine=e["sp"]["key"], side=e["st"]["side"], contracts=e["st"]["contracts"], entry=e["st"]["entry"], etime=e["st"]["etime"],
                     unreal=unreal(e, last_close[e["sp"]["asset"]], end_t)) for e in eng if e["st"]["side"] != 0]
    return dict(cash=cash, equity=marked(end_t), trades=trades, open=open_pos, curve=curve, end=end_t, max_gross=mg[0])

def load(path):
    rows = json.load(open(path))["rows"]; hours = {}
    for r in rows:
        t0 = ms(r["first_t"].replace(" ", "T").replace("+00", "+00:00")); parts = r["bars"].split(";")
        assert len(parts) == r["n"], (len(parts), r["n"])
        hours[r["asset"]] = [dict(t=t0+i*H, **dict(zip("ohlc", map(float, p.split(","))))) for i, p in enumerate(parts)]
    return hours
