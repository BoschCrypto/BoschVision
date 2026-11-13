"""WARDEN — risk. The only agent that can say no and not be outvoted.

Everything else in this system is an opinion. WARDEN is a constraint. It
runs AFTER the arbiter has formed an intent and can reduce it, refuse it,
or force the book flat. Nothing overrides it, by construction: the engine
calls it last and acts only on what it returns.

The limits, roughly in the order they bind:

  kill switch     a file on disk. Touch it and everything flattens. This
                  exists because when you need to stop a bot you need to
                  stop it from a phone, not by finding a terminal.
  daily loss      a hard stop on the day, in dollars. Once hit, the
                  session is over — not reduced, over. Letting a bot
                  "try to make it back" is the mechanism by which bad days
                  become account-ending days.
  drawdown        peak-to-trough on equity, as a second line of defence
                  that triggers before the daily stop on a slow bleed.
  loss streak     N losers in a row forces a cooldown. Not because losses
                  are informative, but because a model that has just been
                  wrong N times is more likely mis-specified for the
                  current regime than unlucky.
  cooldown        a minimum gap after any exit. Kills the re-entry loop
                  where a bot gets stopped out and immediately re-enters
                  the same losing trade.
  trade rate      a cap on trades per minute. An overtrading bot pays the
                  spread to death; this is the circuit breaker for a
                  signal that has started oscillating.
  volatility      if ATR spikes past a multiple of its own normal, stand
                  down. The stop distance you sized on no longer exists.
  spread          if the spread is wide relative to the expected move, the
                  trade is negative-expectancy before it starts.
  session         flat by a cutoff. Overnight gap risk is not a risk an
                  intraday system is being paid to take.

Sizing is volatility-targeted: risk a fixed fraction of equity to the stop,
so position size falls automatically when the market gets violent. Fixed
share counts are how accounts die in a vol spike.
"""
from __future__ import annotations

import os
from typing import Optional

from ..bus import Blackboard
from ..indicators import EMA, StreamATR
from ..types import NS_PER_S, Signal, Tick, Verdict
from .base import FastAgent


class RiskLimits:
    __slots__ = (
        "risk_per_trade", "max_notional_pct", "daily_loss_limit",
        "max_drawdown_pct", "max_consecutive_losses", "cooldown_s",
        "max_trades_per_min", "max_trades_per_session", "vol_spike_mult",
        "max_spread_bps", "stop_atr_mult", "target_atr_mult",
        "session_max_s", "kill_switch_path", "min_stop_bps", "horizon_ticks",
        "contract_size", "min_notional",
    )

    def __init__(
        self,
        risk_per_trade: float = 0.004,      # 0.4% of equity at risk to the stop
        max_notional_pct: float = 0.25,     # never more than 25% of equity in one position
        daily_loss_limit: float = 0.02,     # 2% of starting equity ends the day
        max_drawdown_pct: float = 0.015,    # 1.5% peak-to-trough pauses trading
        max_consecutive_losses: int = 3,
        cooldown_s: float = 20.0,
        max_trades_per_min: int = 6,
        max_trades_per_session: int = 250,
        vol_spike_mult: float = 3.0,
        max_spread_bps: float = 8.0,
        stop_atr_mult: float = 2.0,
        target_atr_mult: float = 3.0,
        session_max_s: float = 6.5 * 3600,
        kill_switch_path: str = ".velocity_kill",
        min_stop_bps: float = 3.0,
        horizon_ticks: int = 300,
        contract_size: float = 0.0,
        min_notional: float = 1.0,
    ) -> None:
        self.risk_per_trade = risk_per_trade
        self.max_notional_pct = max_notional_pct
        self.daily_loss_limit = daily_loss_limit
        self.max_drawdown_pct = max_drawdown_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_s = cooldown_s
        self.max_trades_per_min = max_trades_per_min
        self.max_trades_per_session = max_trades_per_session
        self.vol_spike_mult = vol_spike_mult
        self.max_spread_bps = max_spread_bps
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.session_max_s = session_max_s
        self.kill_switch_path = kill_switch_path
        self.min_stop_bps = min_stop_bps
        # How many ticks a trade is expected to live. Stops are scaled by
        # sqrt(horizon_ticks) — see Warden.vol_horizon.
        self.horizon_ticks = horizon_ticks
        # Minimum tradeable increment, in units of the UNDERLYING. For a
        # futures contract this is the contract size (Coinbase BIP = 0.01
        # BTC); 0.0 means the instrument trades fractionally with no
        # rounding (spot crypto). Position notional is rounded DOWN to a
        # whole number of contracts.
        self.contract_size = contract_size
        self.min_notional = min_notional


class Warden(FastAgent):
    name = "WARDEN"

    __slots__ = (
        "limits", "atr", "atr_baseline", "start_equity", "ttl_ns",
        "_trade_ts", "_kill_checked_tick", "_kill_checked_ns", "_kill_active", "_last_reason",
    )

    def __init__(self, limits: Optional[RiskLimits] = None, start_equity: float = 100_000.0,
                 ttl_s: float = 5.0) -> None:
        self.limits = limits or RiskLimits()
        self.atr = StreamATR(20)
        self.atr_baseline = EMA(2000)     # slow-moving "normal" volatility
        self.start_equity = start_equity
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self._trade_ts: list[int] = []
        self._kill_checked_tick = 0
        self._kill_checked_ns = 0
        self._kill_active = False
        self._last_reason = ""

    # -- fast-lane part: maintain volatility state and publish a risk read --

    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        atr = self.atr.update(tick.price)
        if self.atr.ready:
            self.atr_baseline.update(atr)
        # WARDEN's "strength" is deliberately 0: it never expresses a
        # direction. Its confidence encodes risk appetite, which the
        # dashboard and the arbiter's sizing both read.
        appetite = self._appetite(bb)
        return Signal(self.name, tick.ts_ns, 0.0, appetite, self.ttl_ns,
                      self._last_reason or f"appetite={appetite:.2f}")

    def _appetite(self, bb: Blackboard) -> float:
        lim = self.limits
        dd = bb.drawdown
        room = 1.0 - (dd / lim.max_drawdown_pct) if lim.max_drawdown_pct > 0 else 1.0
        room = max(0.0, min(1.0, room))
        streak_pen = 1.0 - 0.3 * min(3, bb.consecutive_losses)
        return max(0.0, room * streak_pen)

    @property
    def atr_value(self) -> float:
        return self.atr.value

    @property
    def vol_horizon(self) -> float:
        """Expected price dispersion over one holding period.

        ATR on a tick stream is the size of ONE tick's move. Sizing a stop
        at 2x that puts it two ticks away, where ordinary noise removes it
        immediately — the first run of this system averaged 0.6 seconds per
        trade for exactly this reason, and every exit was a stop or a target
        reached by accident rather than by thesis.

        Under a random walk, dispersion grows with the square root of time,
        so the volatility relevant to a trade held for N ticks is
        ATR * sqrt(N). That is the unit a stop belongs in.
        """
        n = self.limits.horizon_ticks
        return self.atr.value * (n ** 0.5 if n > 1 else 1.0)

    # -- the gate ---------------------------------------------------------

    def review(self, side: int, tick: Tick, bb: Blackboard) -> Verdict:
        """Called once per candidate trade. Returns the binding answer."""
        lim = self.limits
        now = tick.ts_ns
        px = tick.price

        # 1-3. Kill switch, halt, daily loss limit, session cutoff: the hard
        #      stops. They are shared with must_flatten(), which the engine
        #      calls on every tick that has a position open.
        why = self._hard_stop(tick, bb)
        if why:
            return Verdict(False, 0.0, why, flatten=True)

        # 4. Drawdown brake — pause, not terminal.
        if bb.drawdown >= lim.max_drawdown_pct:
            return Verdict(False, 0.0, f"drawdown {bb.drawdown:.2%} >= {lim.max_drawdown_pct:.2%}")

        # 5. Loss streak.
        if bb.consecutive_losses >= lim.max_consecutive_losses:
            if bb.seconds_since_exit(now) < lim.cooldown_s * 3:
                return Verdict(False, 0.0, f"{bb.consecutive_losses} losses in a row — cooling off")

        # 6. Re-entry cooldown.
        since = bb.seconds_since_exit(now)
        if since < lim.cooldown_s:
            return Verdict(False, 0.0, f"cooldown {since:.0f}s/{lim.cooldown_s:.0f}s")

        # 7. Trade rate.
        cutoff = now - NS_PER_S * 60
        ts = self._trade_ts
        while ts and ts[0] < cutoff:
            ts.pop(0)
        if len(ts) >= lim.max_trades_per_min:
            return Verdict(False, 0.0, f"rate limit {len(ts)}/min")
        if bb.trades_today >= lim.max_trades_per_session:
            return Verdict(False, 0.0, f"session trade cap {bb.trades_today}")

        # 8. Volatility circuit breaker.
        atr = self.atr.value
        base = self.atr_baseline.value
        if self.atr_baseline.n > 200 and base > 0 and atr > base * lim.vol_spike_mult:
            return Verdict(False, 0.0, f"vol spike {atr/base:.1f}x normal — stand down")

        # 9. Spread guard. A trade must clear the round-trip cost of
        #    crossing twice before it is worth doing at all.
        sp = tick.spread_bps
        if sp > lim.max_spread_bps:
            return Verdict(False, 0.0, f"spread {sp:.1f}bps > {lim.max_spread_bps:.1f}")

        # 10. Size it. Risk a fixed fraction of equity to the stop distance.
        stop_dist = self.vol_horizon * lim.stop_atr_mult
        min_stop = px * lim.min_stop_bps / 10_000.0
        if stop_dist < min_stop:
            stop_dist = min_stop
        if stop_dist <= 0:
            return Verdict(False, 0.0, "no measurable volatility — cannot size a stop")

        equity = bb.equity
        risk_dollars = equity * lim.risk_per_trade * self._appetite(bb)
        if risk_dollars <= 0:
            return Verdict(False, 0.0, "risk appetite exhausted")
        qty = risk_dollars / stop_dist
        notional = qty * px
        cap = equity * lim.max_notional_pct
        if notional > cap:
            notional = cap

        # Round to the instrument's real tradeable increment.
        #
        # This check used to read `if notional < px`, i.e. "can we afford one
        # whole unit of the underlying". On BTC at $73,000 with a $1,250
        # position cap that is false forever, and the system silently took
        # ZERO trades across 45 days of real data while happily trading a
        # synthetic series that started at price 100. Price level is not
        # position granularity: a futures contract is a fraction of the
        # underlying, and spot crypto is divisible outright.
        if lim.contract_size > 0.0:
            unit = lim.contract_size * px
            contracts = int(notional // unit)
            if contracts < 1:
                return Verdict(
                    False, 0.0,
                    f"below 1 contract (need ${unit:,.0f}, have ${notional:,.0f})",
                )
            notional = contracts * unit
        elif notional < lim.min_notional:
            return Verdict(False, 0.0,
                           f"notional ${notional:,.2f} below exchange minimum")

        self._last_reason = f"risk ${risk_dollars:,.0f} stop {stop_dist:.4f}"
        return Verdict(True, notional, self._last_reason)

    def round_notional(self, notional: float, price: float) -> float:
        """Round a notional DOWN to the instrument's tradeable increment.
        Returns 0.0 when it is below one contract / the exchange minimum."""
        lim = self.limits
        if lim.contract_size > 0.0:
            unit = lim.contract_size * price
            return int(notional // unit) * unit
        return notional if notional >= lim.min_notional else 0.0

    def one_unit(self, price: float) -> float:
        """Notional of the smallest tradeable position."""
        lim = self.limits
        return lim.contract_size * price if lim.contract_size > 0.0 else lim.min_notional

    def _hard_stop(self, tick: Tick, bb: Blackboard) -> str:
        """Reasons the book must be flat right now. Empty string if none."""
        lim = self.limits
        now = tick.ts_ns
        # Kill switch. A stat() costs a microsecond or two, so it is checked
        # at most once a second of market time (or every 500 ticks on a feed
        # with no usable clock). It used to be every 500 ticks only, which on
        # one-minute bars is more than eight hours.
        if (now - self._kill_checked_ns >= NS_PER_S or self._kill_checked_ns == 0
                or bb.tick_count - self._kill_checked_tick >= 500):
            self._kill_checked_ns = now
            self._kill_checked_tick = bb.tick_count
            self._kill_active = os.path.exists(lim.kill_switch_path)
        if self._kill_active:
            return "KILL SWITCH active"
        if bb.halted:
            return f"halted: {bb.halt_reason}"
        day_pnl = bb.day_pnl + bb.unrealised(tick.price)
        if day_pnl <= -abs(lim.daily_loss_limit * self.start_equity):
            bb.halt(f"daily loss limit hit ({day_pnl:,.0f})")
            return "daily loss limit"
        if bb.session_seconds(now) >= lim.session_max_s:
            return "session cutoff — flat by close"
        return ""

    def must_flatten(self, tick: Tick, bb: Blackboard) -> str:
        """Called by the engine on every tick while a position is open.

        The entry gate alone is not enough: review() only runs when the
        arbiter wants to OPEN something, so with a position already on, the
        kill switch and the daily loss limit were never looked at. A kill
        file created while a trade was open left it open until its own stop
        happened to be hit.
        """
        return self._hard_stop(tick, bb)

    def record_trade(self, ts_ns: int) -> None:
        self._trade_ts.append(ts_ns)

    def stop_and_target(self, side: int, price: float) -> tuple[float, float]:
        lim = self.limits
        vh = self.vol_horizon
        dist = vh * lim.stop_atr_mult
        min_stop = price * lim.min_stop_bps / 10_000.0
        if dist < min_stop:
            dist = min_stop
        tgt = vh * lim.target_atr_mult
        if tgt < min_stop * 1.5:
            tgt = min_stop * 1.5
        if side > 0:
            return price - dist, price + tgt
        return price + dist, price - tgt
