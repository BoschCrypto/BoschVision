from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AppConfig:
    db_path: str = "trading_bot.db"
    broker: str = "paper"  # "paper" (default, safe) | "robinhood" (LIVE, opt-in)
    starting_cash: float = 100_000.0
    watchlist: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        path = path or os.environ.get("HF_BOT_CONFIG", "config/settings.yaml")
        p = Path(path)
        if not p.exists():
            return cls()
        import yaml

        data = yaml.safe_load(p.read_text()) or {}
        defaults = cls()
        return cls(
            db_path=data.get("db_path", defaults.db_path),
            broker=data.get("broker", defaults.broker),
            starting_cash=data.get("starting_cash", defaults.starting_cash),
            watchlist=data.get("watchlist", []),
        )
