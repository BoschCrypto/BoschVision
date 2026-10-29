"""Log one signal and say what it means, in a single command.

WHY A COMMAND AND NOT A CONVERSATION. Signals arrive as Discord screenshots and
reach the log by being retyped. Every failure in this project's error record
traces to a number that was handled by hand: a weight transcribed with an
explicit zero that the screenshot did not show, a sleeve valued against a price
row that had gone stale, a duplicate post that would have read as a free
no-change. So the transcription happens once, at the door, with every check
firing on the way in -- and the analysis is recomputed from the log afterwards
rather than carried forward in prose.

    python3 -m hf_trading_bot.brief log \\
        --at 2026-10-14T20:12:00-04:00 \\
        --weights "BTC=38.6,SUI=18.2,NEAR=11.4,CASH=31.8" \\
        --prices  "BTC=83717.71,SUI=1.0614,NEAR=5.1291" \\
        --note "posted 8:12 PM ET, marks taken 20:40 ET"

Run it with --dry-run first: it prints the whole brief WITHOUT writing, so a
mistyped weight is caught by reading the output rather than by editing the log
afterwards. `brief` alone re-runs the analysis on what is already stored.

WEIGHTS ARE GIVEN IN PERCENT, as published, because converting them by hand is
one more place to drop a decimal. They must sum to 100 with CASH named.

WHAT THIS DOES NOT DO: place an order. It sizes the trades the signal implies
and stops there. Nothing in this repository may move real money without Waldo
approving that specific proposal, at the time, in his own words.
"""
from __future__ import annotations
import argparse
import sys

from . import prices as P
from .signaltrack import (
    LOG, MIN_TRADE_USD, SPREAD_PCT, Signal, latest_prices, live_position,
    load_position, log_signal, read_signals, status, turnover, unit_lattice,
)

UNIT = 1.0 / 44        # the lattice step Adam's posts have fitted so far


def parse_pairs(s: str, scale: float = 1.0) -> dict:
    """"BTC=38.6,SUI=18.2" -> {"BTC": 0.386, "SUI": 0.182} at scale=0.01."""
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"{part!r} is not NAME=VALUE")
        k, v = part.split("=", 1)
        k = k.strip().upper()
        if k in out:
            raise ValueError(f"{k} given twice")
        out[k] = float(v) * scale
    if not out:
        raise ValueError("nothing to parse")
    return out


def changes(prev: Signal, cur: Signal) -> list[str]:
    """What moved, in lattice units as well as percent.

    An asset the new post does not name is at ZERO, not unchanged: the weights
    sum to 100% with cash named, so there is no room left for a holding it
    omits. Reading an omission as "no change" is what silently left a sold-out
    SOL leg in the follower's book.
    """
    out = []
    for a in sorted(set(prev.weights) | set(cur.weights)):
        before, after = prev.weights.get(a, 0.0), cur.weights.get(a, 0.0)
        if abs(after - before) < 1e-9:
            continue
        tag = ""
        if a not in cur.weights:
            tag = "  (OMITTED from the post, so it is a full exit)"
        elif a not in prev.weights:
            tag = "  (new name)"
        out.append(f"  {a:<5} {before:>6.1%} -> {after:>6.1%}   "
                   f"{(after - before) / UNIT:>+5.1f} units{tag}")
    return out


def rebalance_plan(pos, sig: Signal, px: dict,
                   min_trade: float = MIN_TRADE_USD) -> dict:
    """What the live sleeve would have to trade to reach the published weights.

    Weights are MARKET-VALUE weights of the whole sleeve, cash included. The
    denominator is the sleeve's mark, never the crypto legs alone -- using the
    invested portion as the base once reported BTC 12.9pp off target when the
    real drift was 2.1pp.

    Sells are listed before buys because that is the order they must run in: a
    sell is what funds a buy, and the sleeve may not borrow.
    """
    legs = {a: u * px[a] for a, u in pos.units.items()}
    total = sum(legs.values()) + pos.cash
    rows = []
    for a in sorted(set(sig.weights) | set(pos.units)):
        if a == "CASH":
            continue
        have = legs.get(a, 0.0)
        want = total * sig.weights.get(a, 0.0)
        rows.append({"asset": a, "have": have, "want": want, "delta": want - have,
                     "units": (want - have) / px[a] if a in px else None,
                     "act": abs(want - have) >= min_trade})
    rows.sort(key=lambda r: (r["delta"] > 0, r["asset"]))
    traded = [r for r in rows if r["act"]]
    gross = sum(abs(r["delta"]) for r in traded)
    buys = sum(r["delta"] for r in traded if r["delta"] > 0)
    return {"rows": rows, "total": total, "cash": pos.cash,
            "gross": gross, "cost": gross * SPREAD_PCT / 2,
            "buys": buys, "fundable": buys <= pos.cash + sum(
                -r["delta"] for r in traded if r["delta"] < 0) + 1e-9,
            "target_cash": total * sig.weights.get("CASH", 0.0)}


def render(log=LOG, px: dict | None = None, store=P.STORE,
           at: str | None = None) -> str:
    sigs = read_signals(log)
    if not sigs:
        return "No signals logged."
    cur = sigs[-1]
    out = [f"=== SIGNAL {cur.at[:10]}  (post {len(sigs)} of the log) ==="]

    out.append("")
    out.append("  published: " + "  ".join(
        f"{a} {w:.1%}" for a, w in sorted(cur.weights.items(), key=lambda kv: -kv[1])))
    if len(sigs) >= 2:
        ch = changes(sigs[-2], cur)
        out += ["", "WHAT MOVED vs " + sigs[-2].at[:10] + ":"] + (ch or ["  nothing -- identical weights republished"])
        t = turnover(sigs)[-1]
        out.append(f"  one-way turnover {t['turnover']:.2%} of the sleeve, "
                   f"implying {t['cost']:.3%} in spread to follow it")

    # The price series does not build itself -- say so, loudly, on any run that
    # did not capture. Named assets are everything the log has ever priced,
    # because the frozen control arm still holds legs the signal has dropped.
    watch = sorted({a for s in sigs for a in s.prices})
    stale = P.marks_missing_today(watch)
    if stale:
        out += ["", f"!! MARKS NOT CAPTURED TODAY (ET) for {', '.join(stale)} "
                    f"({len(stale)} of {len(watch)}).",
                "   Re-run with --prices to record them. The series is built only "
                "by runs that are given",
                "   live quotes; a scheduled run has no broker access and records "
                "nothing."]
        # The ET-day guard means the FIRST capture of a day takes it and every
        # later one is refused as a duplicate. So this warning, shown at 09:25,
        # invites a morning capture that would lock out the evening one -- and
        # the evening is the capture worth having: it is taken minutes after the
        # 20:12 ET post, so the mark and the signal describe the same instant,
        # and it is the closest thing a 24/7 asset has to a daily close.
        # Saying so here rather than in a comment nobody reads at 09:25.
        if P._now_et().hour < 17:
            out += ["   NOT YET, THOUGH: the evening capture owns the ET day "
                    "(taken minutes after the",
                    "   20:12 post, so mark and signal share an instant). "
                    "Capturing now would take the",
                    "   day and the evening run would be refused as a duplicate. "
                    "Leave it."]

    L = unit_lattice(sigs)
    out += ["", f"LATTICE k/44: {'holds' if L['holds'] else 'BROKEN'} "
                f"(worst {L['max_error_units']:.4f} of a {L['unit_pct']:.3%} unit, "
                f"tolerance {L['tolerance_units']:.4f})"]
    if not L["holds"]:
        out.append(f"  -> {L['worst_case']} is off-lattice. The discrete-unit model is "
                   f"falsified. Drop it; do not quietly keep it.")

    # --- capture tonight's broker marks before anything else ------------------
    # Done here, on every run that is given live quotes, so the series builds
    # itself instead of depending on anyone remembering. A second capture on a
    # day already recorded is a no-op rather than an error: the brief must not
    # fail because it was run twice.
    if px:
        try:
            P.record_marks(px, at=at)        # None -> now, never the post's time
        except ValueError as e:
            if "already has a mark" not in str(e):
                raise

    # --- risk, which is the part that is actually measurable at this n --------
    try:
        R = P.portfolio_risk(cur.weights, store=store)
    except Exception as e:                      # loud, not skipped
        out += ["", f"RISK: unavailable -- {e}"]
        st = P.marks_status()
        held = sorted(a for a in cur.weights if a != "CASH")
        short = [st[a]["short_by"] for a in held if a in st]
        if short:
            out.append(f"      Own broker-mark series building: "
                       f"{min(st[a]['n'] for a in held if a in st)} of 30 days. "
                       f"A volatility needs about {max(short)} more daily captures, "
                       f"so roughly {max(short)} days from now.")
    else:
        out += ["",
                f"RISK  sleeve vol {R['sigma']:.1%}/yr +/-{R['sigma_se']:.1%} "
                f"({R['daily_sigma']:.2%}/day), from {R['n_returns']} daily returns",
                f"      diversification ratio {R['diversification']:.2f} "
                f"(1.00 would mean correlation gave nothing back)",
                "",
                f"      {'asset':<6}{'vol':>7}{'$ share':>9}{'risk share':>12}{'ratio':>8}"]
        for a in sorted(R["risk_share"], key=lambda x: -R["risk_share"][x]):
            ws, rs = R["weight_share"][a], R["risk_share"][a]
            out.append(f"      {a:<6}{R['vols'][a]:>7.0%}{ws:>9.1%}{rs:>12.1%}{rs/ws:>7.2f}x")
        out.append("      $ share is of the INVESTED sleeve; risk share is of its movement.")
        pos0 = load_position()
        if pos0 is not None:
            floor = R["daily_sigma"] * pos0.capital
            out += ["",
                    f"      NOISE FLOOR: one day of this sleeve's volatility is "
                    f"${floor:,.2f} on ${pos0.capital:,.2f}.",
                    "      Any gap between the arms below that is not evidence of "
                    "anything. This is the",
                    "      quantity that makes \"n is small\" a measurement instead "
                    "of a disclaimer."]
        try:
            iv = P.weights_inverse_vol([a for a in cur.weights if a != "CASH"],
                                       cur.weights.get("CASH", 0.0), store=store)
            Ri = P.portfolio_risk(iv, store=store)
            out += ["",
                    f"      vs OUR inverse-vol book at his own cash level "
                    f"({Ri['sigma']:.1%}/yr vol):"]
            for a in sorted(iv):
                if a == "CASH":
                    continue
                out.append(f"      {a:<6}{cur.weights.get(a, 0.0):>7.1%} -> "
                           f"{iv[a]:>6.1%}  ({(iv[a] - cur.weights.get(a, 0.0)) * 100:+.1f}pp)")
        except Exception as e:
            out.append(f"      inverse-vol comparison unavailable -- {e}")

    # --- what it would take to follow it, on the real sleeve ------------------
    pos = live_position()
    if pos is not None:
        marks, as_of = latest_prices(sigs, px)
        missing = [a for a in pos.units if a not in marks]
        if missing:
            out += ["", f"PLAN: cannot size -- no price for {', '.join(missing)}"]
        else:
            plan = rebalance_plan(pos, cur, marks)
            stale = sorted(a for a in pos.units if as_of.get(a) not in ("live", cur.at))
            out += ["", f"TO FOLLOW IT, on the live sleeve "
                        f"(${plan['total']:,.2f} marked, ${plan['cash']:,.2f} cash):"]
            if stale:
                out.append(f"  [{', '.join(stale)} marked on an older row -- "
                           f"pass --prices for live quotes]")
            out.append(f"  {'asset':<6}{'now':>10}{'target':>10}{'delta':>10}{'units':>16}")
            for r in plan["rows"]:
                u = "" if r["units"] is None else f"{r['units']:>+16.8f}"
                mark = "" if r["act"] else "   (under the $10 floor)"
                out.append(f"  {r['asset']:<6}{r['have']:>10,.2f}{r['want']:>10,.2f}"
                           f"{r['delta']:>+10,.2f}{u}{mark}")
            out.append(f"  {'CASH':<6}{plan['cash']:>10,.2f}{plan['target_cash']:>10,.2f}"
                       f"{plan['target_cash'] - plan['cash']:>+10,.2f}")
            if plan["gross"] > 0:
                out.append(f"  ${plan['gross']:,.2f} of notional, costing "
                           f"${plan['cost']:,.2f} in spread "
                           f"({plan['cost'] / plan['total']:.3%} of the sleeve)")
                if not plan["fundable"]:
                    out.append("  NOT FUNDABLE from cash plus the sells above -- "
                               "the sleeve would have to borrow, which it may not.")
            else:
                out.append("  nothing to do: every leg is inside the $10 floor.")
            out.append("  Proposal only. No order is placed without Waldo approving "
                       "that specific trade, at the time.")
    out += ["", status(prices=px)]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hf_trading_bot.brief")
    # NOT required. Two scheduled routines are instructed to run this module
    # bare -- `python3 -m hf_trading_bot.brief` -- and with a required
    # subcommand that exits 2 with a usage message and produces no analysis at
    # all. Discovered 2026-10-21, the morning the routine was asked for its
    # output and had been returning an argparse error instead. The bare form is
    # the one people and prompts actually type, so it is the one that must
    # work; it now means `brief`, which is what every caller intended.
    sub = ap.add_subparsers(dest="cmd")
    br = sub.add_parser("brief", help="re-run the analysis on the stored log")
    br.add_argument("--prices", default=None,
                    help='live broker marks, which are always better than the '
                         'marks stored with a signal: "BTC=81508.87,SUI=1.0229,..."')
    lg = sub.add_parser("log", help="record a new signal, then analyse it")
    lg.add_argument("--at", required=True,
                    help="ISO8601 WITH a UTC offset, when the post was PUBLISHED")
    lg.add_argument("--weights", required=True,
                    help='percent, as published, CASH named: "BTC=38.6,...,CASH=31.8"')
    lg.add_argument("--prices", required=True,
                    help='broker marks: "BTC=83717.71,SUI=1.0614,NEAR=5.1291"')
    lg.add_argument("--note", default="")
    lg.add_argument("--source", default="discord")
    lg.add_argument("--dry-run", action="store_true",
                    help="print the brief WITHOUT writing to the log")
    a = ap.parse_args(argv)
    if a.cmd is None:                     # bare invocation == `brief`
        a.cmd, a.prices = "brief", None

    if a.cmd == "log":
        sig = Signal(at=a.at, weights=parse_pairs(a.weights, 0.01),
                     prices=parse_pairs(a.prices), source=a.source, note=a.note)
        sig.validate()
        if a.dry_run:
            print("DRY RUN -- nothing written.\n")
            print("would record:", sig.weights)
            print("           at:", sig.at)
            print("       prices:", sig.prices)
            return 0
        log_signal(sig)
        print(f"logged {sig.at}\n")
    print(render(px=parse_pairs(a.prices) if a.prices else None))
    if load_position() is None:
        print("\n(no position on file, so the plan section is skipped)")
    return 0


if __name__ == "__main__":
    # A refusal is the normal case here, not a crash: this tool exists to reject
    # a mistyped signal. A traceback buries the one line that says what to fix.
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        print("\nNothing was written. Fix the input and run it again.", file=sys.stderr)
        sys.exit(2)
