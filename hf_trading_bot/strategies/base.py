from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Signal = Literal["entry", "exit", "hold"]


@dataclass
class StrategyResult:
    signal: Signal
    price: Optional[float]
    detail: str
    entry_kind: Optional[str] = None
