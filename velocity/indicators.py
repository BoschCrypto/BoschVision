"""Streaming O(1) indicators.

Every class here updates in constant time from a single new value. None of
them keep a pandas frame, recompute over a window, or allocate per update.
This is the difference between a bot that can process 10k ticks/sec and one
that can process 200.

The rule for the hot path: if an update is O(window), it does not belong
here. Rolling max/min use a monotonic deque (amortised O(1)); rolling
variance uses running sums; everything else is recursive.
"""
from __future__ import annotations

from collections import deque
from math import sqrt


class EMA:
    """Exponential moving average. O(1), no history kept."""
    __slots__ = ("alpha", "value", "n", "warmup")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.alpha = 2.0 / (period + 1.0)
        self.value = 0.0
        self.n = 0
        self.warmup = period

    def update(self, x: float) -> float:
        if self.n == 0:
            self.value = x
        else:
            self.value += self.alpha * (x - self.value)
        self.n += 1
        return self.value

    @property
    def ready(self) -> bool:
        return self.n >= self.warmup


class RollingStats:
    """Rolling mean and population stdev over a fixed window, O(1).

    Running sums drift in float over millions of updates, so the sums are
    recomputed from the buffer every `_recompute_every` pushes. That is an
    O(window) step amortised to O(1) and it keeps the stdev exact enough to
    threshold on. Without it, a z-score can silently go wrong after a few
    hours of ticks.
    """
    __slots__ = ("n", "buf", "_sum", "_sumsq", "_since_recompute", "_recompute_every")

    def __init__(self, window: int, recompute_every: int = 100_000) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self.n = window
        self.buf: deque[float] = deque(maxlen=window)
        self._sum = 0.0
        self._sumsq = 0.0
        self._since_recompute = 0
        self._recompute_every = recompute_every

    def update(self, x: float) -> None:
        if len(self.buf) == self.n:
            old = self.buf[0]
            self._sum -= old
            self._sumsq -= old * old
        self.buf.append(x)
        self._sum += x
        self._sumsq += x * x
        self._since_recompute += 1
        if self._since_recompute >= self._recompute_every:
            self._sum = 0.0
            self._sumsq = 0.0
            for v in self.buf:
                self._sum += v
                self._sumsq += v * v
            self._since_recompute = 0

    @property
    def count(self) -> int:
        return len(self.buf)

    @property
    def ready(self) -> bool:
        return len(self.buf) == self.n

    @property
    def mean(self) -> float:
        c = len(self.buf)
        return 0.0 if c == 0 else self._sum / c

    @property
    def var(self) -> float:
        c = len(self.buf)
        if c < 2:
            return 0.0
        m = self._sum / c
        v = self._sumsq / c - m * m
        return v if v > 0.0 else 0.0

    @property
    def std(self) -> float:
        return sqrt(self.var)

    def z(self, x: float) -> float:
        """Z-score of x against the window. Returns 0.0 when stdev is ~0 —
        a flat window makes every deviation look infinite otherwise, which
        is exactly how a quiet market produces a fake signal."""
        s = self.std
        return 0.0 if s < 1e-12 else (x - self.mean) / s


class RollingExtrema:
    """Rolling max and min over a window in amortised O(1).

    Two monotonic deques of (index, value). Each value is pushed and popped
    at most once, so N updates cost O(N) total however wide the window is.
    The naive max(window) version is O(N*W) and is the usual reason a
    "breakout" check dominates a tick loop's profile.
    """
    __slots__ = ("n", "_i", "_max", "_min", "_count")

    def __init__(self, window: int) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.n = window
        self._i = 0
        self._count = 0
        self._max: deque[tuple[int, float]] = deque()
        self._min: deque[tuple[int, float]] = deque()

    def update(self, x: float) -> None:
        i = self._i
        self._i += 1
        self._count += 1
        mx = self._max
        while mx and mx[-1][1] <= x:
            mx.pop()
        mx.append((i, x))
        if mx[0][0] <= i - self.n:
            mx.popleft()
        mn = self._min
        while mn and mn[-1][1] >= x:
            mn.pop()
        mn.append((i, x))
        if mn[0][0] <= i - self.n:
            mn.popleft()

    @property
    def ready(self) -> bool:
        return self._count >= self.n

    @property
    def max(self) -> float:
        return self._max[0][1] if self._max else 0.0

    @property
    def min(self) -> float:
        return self._min[0][1] if self._min else 0.0

    @property
    def range(self) -> float:
        return self.max - self.min


class StreamRSI:
    """Wilder's RSI, O(1) per update.

    Wilder smoothing is itself recursive, so there is no reason to keep a
    window. Seeded with a simple average of the first `period` deltas, which
    is what Wilder specified; seeding with the first delta alone makes the
    first ~50 values wrong in a way that shows up as phantom signals.
    """
    __slots__ = ("period", "_prev", "_avg_gain", "_avg_loss", "_n", "_seed_g", "_seed_l")

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev = 0.0
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._n = 0
        self._seed_g = 0.0
        self._seed_l = 0.0

    def update(self, price: float) -> float:
        if self._n == 0:
            self._prev = price
            self._n = 1
            return 50.0
        delta = price - self._prev
        self._prev = price
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        p = self.period
        if self._n <= p:
            self._seed_g += gain
            self._seed_l += loss
            self._n += 1
            if self._n == p + 1:
                self._avg_gain = self._seed_g / p
                self._avg_loss = self._seed_l / p
            else:
                return 50.0
        else:
            self._avg_gain = (self._avg_gain * (p - 1) + gain) / p
            self._avg_loss = (self._avg_loss * (p - 1) + loss) / p
            self._n += 1
        if self._avg_loss < 1e-12:
            return 100.0 if self._avg_gain > 0 else 50.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @property
    def ready(self) -> bool:
        return self._n > self.period


class StreamATR:
    """Average true range on a tick stream, O(1).

    True range on ticks is |price - prev_price| — there is no intrabar high
    and low to straddle. This is a volatility *unit*, used to normalise
    moves and to size stops, so being a tick-scale proxy is the point.
    """
    __slots__ = ("period", "_prev", "_ema", "_n")

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev = 0.0
        self._ema = EMA(period)
        self._n = 0

    def update(self, price: float) -> float:
        if self._n == 0:
            self._prev = price
            self._n = 1
            return 0.0
        tr = abs(price - self._prev)
        self._prev = price
        self._n += 1
        return self._ema.update(tr)

    @property
    def value(self) -> float:
        return self._ema.value

    @property
    def ready(self) -> bool:
        return self._n > self.period


class VWAP:
    """Session volume-weighted average price. Reset at session start.

    Falls back to a plain running mean when the feed carries no sizes —
    better than dividing by zero and better than silently reporting the
    last price as the VWAP.
    """
    __slots__ = ("_pv", "_v", "_n", "_psum")

    def __init__(self) -> None:
        self._pv = 0.0
        self._v = 0.0
        self._n = 0
        self._psum = 0.0

    def update(self, price: float, size: float) -> float:
        self._n += 1
        self._psum += price
        if size > 0:
            self._pv += price * size
            self._v += size
        return self.value

    @property
    def value(self) -> float:
        if self._v > 0:
            return self._pv / self._v
        return self._psum / self._n if self._n else 0.0

    @property
    def ready(self) -> bool:
        return self._n > 0

    def reset(self) -> None:
        self._pv = self._v = self._psum = 0.0
        self._n = 0
