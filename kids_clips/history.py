"""Append-only log of every video produced, so new ideas don't repeat old ones."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class History:
    def __init__(self, out_dir: Path):
        self.path = Path(out_dir) / "history.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def recent_titles(self, n: int = 40) -> list[str]:
        return [e.get("title", "") for e in self.entries()[-n:]]

    def made_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return sum(1 for e in self.entries() if e.get("created_at", "").startswith(today))

    def add(self, entry: dict) -> None:
        entry = {"created_at": datetime.now(timezone.utc).isoformat(), **entry}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
