"""Record what the committee decided, including everything it declined.

Waldo asked for an automated pipeline: a morning scanner, an evaluator that
judges whether its candidates are worth owning, and a head agent that reports a
buy or sell decision to him. The three agents already exist in `.claude/agents/`.
What did not exist is the only part that makes the pipeline improvable rather
than merely busy -- a record of what it decided and what happened next.

WHY THE DECLINES ARE LOGGED, AND WHY THAT IS THE WHOLE POINT. A pipeline that
writes down only its buys can never be evaluated. Six months on it would show a
list of positions and no way to tell whether the process picked them or merely
survived them -- which is the same survivorship bias §3 of the committee briefing
already measured in the momentum study, reappearing inside the process rather
than inside the data. So every verdict is logged with the price at the moment it
was made: BUY, SELL, and NO alike. A NO on a name that then beat SPY by 30% is a
cost, and it is invisible unless it is written down the day it is made.

WHAT A VERDICT IS SCORED AGAINST. SPY, always, over the same window. The
question is never "did it go up" -- in an up market almost everything does. It is
"did acting on this beat not acting", which for a BUY is (name - SPY) and for a
NO is exactly the negative of that. A NO is a position in cash against that name,
and it is right when the name lags.

HONEST LIMIT, STATED HERE BECAUSE IT BOUNDS EVERY NUMBER BELOW. Detecting a 60%
hit rate against a 50% coin needs about 194 decisions at conventional power (see
`decisions_needed`). At the handful of real candidates a week this book can
support, that is years, not months -- the same conclusion H7 reached for the
crypto sleeve by a different route. This module therefore reports a LOG, not a
result, and `summary()` says so in its own output until n is large enough to
mean anything. A pipeline cannot be judged by its track record before the track
record exists; it can only be judged by whether its reasoning was sound at the
time, which is why `rationale` is a required field.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("data/decisions/log.jsonl")

BENCHMARK = "SPY"

# Every verdict the pipeline may reach. NO is the expected one on most days.
VERDICTS = ("BUY", "SELL", "NO")

# Where in the pipeline the verdict was reached. Recorded because "the scanner
# found nothing" and "the risk gate vetoed a good idea" are different facts, and
# a pipeline that always dies at the same stage has a design problem, not a
# market problem.
STAGES = ("scan", "evaluate", "risk", "decide")


def _ts(at: str) -> datetime:
    """Parse an ISO8601 stamp into an instant, for ordering.

    Unzoned stamps are refused for the same reason signaltrack refuses them: a
    log that mixes offsets sorts wrongly the moment ET shifts to -05:00, and the
    failure is invisible until it isn't.
    """
    try:
        d = datetime.fromisoformat(at)
    except ValueError as e:
        raise ValueError(f"decision stamp {at!r} is not ISO8601: {e}") from e
    if d.tzinfo is None:
        raise ValueError(
            f"decision stamp {at!r} carries no UTC offset. An unzoned stamp "
            "cannot be ordered against a zoned one; write '-04:00' or 'Z'.")
    return d


@dataclass
class Decision:
    at: str                 # when the verdict was reached, zoned ISO8601
    ticker: str
    verdict: str            # BUY | SELL | NO
    stage: str              # scan | evaluate | risk | decide
    rationale: str          # why, in one sentence. Required -- see module docstring.
    price: float            # the name's price when the verdict was reached
    benchmark: float        # SPY at the same moment
    size_usd: float = 0.0   # proposed, not filled. 0 for NO.
    approved: bool | None = None   # None until Waldo rules on it
    executed: bool = False  # True only once the BROKER confirms, never on send

    def __post_init__(self) -> None:
        _ts(self.at)
        if self.verdict not in VERDICTS:
            raise ValueError(f"{self.ticker}: verdict {self.verdict!r} not one of {VERDICTS}")
        if self.stage not in STAGES:
            raise ValueError(f"{self.ticker}: stage {self.stage!r} not one of {STAGES}")
        if not self.ticker or self.ticker != self.ticker.strip().upper():
            raise ValueError(f"ticker {self.ticker!r} must be upper-case and unpadded")
        if not self.rationale.strip():
            raise ValueError(
                f"{self.ticker}: rationale is required. A verdict with no stated "
                "reason cannot be reviewed later, and reviewing it later is the "
                "only reason this log exists.")
        for name, v in (("price", self.price), ("benchmark", self.benchmark)):
            if not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(f"{self.ticker}: {name} must be a positive number, got {v!r}")
        if self.size_usd < 0:
            raise ValueError(f"{self.ticker}: size_usd cannot be negative")
        if self.verdict == "NO" and self.size_usd:
            raise ValueError(
                f"{self.ticker}: a NO carries no size, got ${self.size_usd}")
        if self.executed and self.approved is not True:
            raise ValueError(
                f"{self.ticker}: marked executed without approval. No order is "
                "placed without Waldo's explicit approval of that proposal.")


def read_decisions(path: Path = LOG) -> list[Decision]:
    """Load the log, validating every row and refusing duplicates.

    Validation happens on READ, not only on write, so a row hand-edited into the
    file is caught the next time anything reads it rather than silently feeding
    a statistic.
    """
    p = Path(path)
    if not p.exists():
        return []
    out: list[Decision] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = Decision(**json.loads(line))
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"{p}:{i} is not a valid decision -- {e}") from e
        key = (d.at, d.ticker)
        if key in seen:
            raise ValueError(
                f"{p}:{i} repeats {d.ticker} at {d.at}. Two verdicts on one name "
                "at one instant means a run was logged twice; the duplicate would "
                "double its weight in every statistic below.")
        seen.add(key)
        out.append(d)
    return sorted(out, key=lambda d: (_ts(d.at), d.ticker))


def log_decision(d: Decision, path: Path = LOG) -> Decision:
    """Append one verdict. Refuses a duplicate (stamp, ticker)."""
    existing = read_decisions(path)
    if any(e.at == d.at and e.ticker == d.ticker for e in existing):
        raise ValueError(
            f"{d.ticker} already has a verdict at {d.at}. Logging it twice would "
            "count one decision as two.")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(asdict(d)) + "\n")
    return d


def score(decisions: list[Decision], prices: dict[str, float]) -> list[dict]:
    """Mark each verdict against SPY over the window since it was made.

    `prices` must carry the benchmark and every decided name. An unpriceable
    name RAISES rather than being skipped -- a silently dropped verdict is the
    survivorship bias this log exists to prevent, so it cannot be allowed in via
    the scoring function's back door.
    """
    if BENCHMARK not in prices:
        raise ValueError(
            f"no {BENCHMARK} price supplied; every verdict is scored against it "
            "and a raw return answers a question nobody asked.")
    bench_now = prices[BENCHMARK]
    if bench_now <= 0:
        raise ValueError(f"{BENCHMARK} price must be positive, got {bench_now}")
    missing = sorted({d.ticker for d in decisions} - set(prices))
    if missing:
        raise ValueError(
            f"no current price for {', '.join(missing)}. Scoring the rest would "
            "quietly drop the verdicts hardest to price, which are exactly the "
            "ones most likely to have gone wrong.")
    rows = []
    for d in decisions:
        r_name = prices[d.ticker] / d.price - 1.0
        r_bench = bench_now / d.benchmark - 1.0
        edge = r_name - r_bench
        # A NO is a position in cash against the name: it is right when the name
        # lags the benchmark, so its edge is the negative of the BUY's.
        signed = edge if d.verdict in ("BUY",) else -edge
        rows.append({"at": d.at, "ticker": d.ticker, "verdict": d.verdict,
                     "stage": d.stage, "name_return": r_name,
                     "benchmark_return": r_bench, "excess": edge,
                     "value_added": signed, "right": signed > 0})
    return rows


def decisions_needed(baseline: float = 0.50, target: float = 0.60,
                     alpha: float = 0.05, power: float = 0.80) -> int:
    """How many verdicts are needed before a hit rate means anything.

    Exists so that this module can never report a hit rate without being able to
    say, in the same breath, how far it is from being a measurement. Normal
    approximation to a one-proportion test; exact enough to make the point,
    which is that the honest answer is 'not yet' for a long time.
    """
    if not 0 < baseline < 1 or not 0 < target < 1:
        raise ValueError("baseline and target must be strictly between 0 and 1")
    if baseline == target:
        raise ValueError("a target equal to the baseline needs infinite data")
    z = {0.05: 1.959964, 0.10: 1.644854, 0.01: 2.575829}.get(alpha)
    zb = {0.80: 0.841621, 0.90: 1.281552, 0.95: 1.644854}.get(power)
    if z is None or zb is None:
        raise ValueError(f"no z value tabulated for alpha={alpha}, power={power}")
    num = (z * (baseline * (1 - baseline)) ** 0.5
           + zb * (target * (1 - target)) ** 0.5) ** 2
    import math
    return math.ceil(num / (target - baseline) ** 2)


def summary(decisions: list[Decision], prices: dict[str, float] | None = None) -> dict:
    """Counts by verdict and stage, plus the scored hit rate when prices allow.

    Always reports `is_a_result`, which is False until n clears the power
    requirement. Everything this returns before then is a log.
    """
    by_verdict = {v: sum(1 for d in decisions if d.verdict == v) for v in VERDICTS}
    by_stage = {s: sum(1 for d in decisions if d.stage == s) for s in STAGES}
    need = decisions_needed()
    out = {"n": len(decisions), "by_verdict": by_verdict, "by_stage": by_stage,
           "decisions_needed": need, "is_a_result": False,
           "pending_approval": sum(1 for d in decisions if d.approved is None
                                   and d.verdict != "NO"),
           "executed": sum(1 for d in decisions if d.executed)}
    if prices and decisions:
        rows = score(decisions, prices)
        wins = sum(1 for r in rows if r["right"])
        out["hit_rate"] = wins / len(rows)
        out["mean_value_added"] = sum(r["value_added"] for r in rows) / len(rows)
        out["is_a_result"] = len(rows) >= need
    return out


def render(decisions: list[Decision], prices: dict[str, float] | None = None) -> str:
    """One block for the head agent to paste into its report."""
    s = summary(decisions, prices)
    L = [f"DECISION LOG  n={s['n']}  "
         f"BUY {s['by_verdict']['BUY']} / SELL {s['by_verdict']['SELL']} / NO {s['by_verdict']['NO']}",
         f"  died at: " + "  ".join(f"{k} {v}" for k, v in s["by_stage"].items()),
         f"  awaiting Waldo's approval: {s['pending_approval']}   executed: {s['executed']}"]
    if "hit_rate" in s:
        L.append(f"  hit rate {s['hit_rate']:.0%}, mean value added vs {BENCHMARK} "
                 f"{s['mean_value_added']:+.2%}")
    if not s["is_a_result"]:
        L.append(f"  !! THIS IS A LOG, NOT A RESULT. {s['n']} of ~{s['decisions_needed']} "
                 f"decisions needed before a hit rate distinguishes skill from a coin.")
    return "\n".join(L)


if __name__ == "__main__":        # python -m hf_trading_bot.decisions
    print(render(read_decisions()))
