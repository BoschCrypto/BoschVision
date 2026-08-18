"""Archive committee console responses to clean Markdown files.

The dashboard's APEX console is a live workspace, not a filing cabinet — old
committee responses pile up and clutter it. This moves finished responses out to
`research/committee/` as one tidy Markdown file each, then marks them archived so
the dashboard shows only the recent, live exchanges. Nothing is lost: the full
history lives in the folder, git-committable and readable outside the app.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

ARCHIVE_DIR = Path("research") / "committee"


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:maxlen] or "command").strip("-")


def _stamp(iso: Optional[str]) -> tuple[str, str]:
    """(YYYY-MM-DD_HHMM for filenames, human string for the body)."""
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.utcnow()
    return dt.strftime("%Y-%m-%d_%H%M"), dt.strftime("%Y-%m-%d %H:%M UTC")


def render_command_md(cmd: dict) -> str:
    """One committee response as clean Markdown."""
    _, when = _stamp(cmd.get("updated_at") or cmd.get("created_at"))
    title = cmd.get("message") or (
        f"review {cmd['symbol']}" if cmd.get("symbol") else f"{cmd.get('kind', 'command')} #{cmd.get('id')}")
    lines = [
        f"# {title}",
        "",
        f"- **When:** {when}",
        f"- **Kind:** {cmd.get('kind', 'console')}",
    ]
    if cmd.get("symbol"):
        lines.append(f"- **Symbol:** {cmd['symbol']}")
    if cmd.get("run_id"):
        lines.append(f"- **Run:** {cmd['run_id']}")
    lines.append(f"- **Status:** {cmd.get('status', 'unknown')}")
    if cmd.get("message"):
        lines += ["", "## Command", "", cmd["message"].strip()]
    if cmd.get("reply"):
        lines += ["", "## APEX reply", "", cmd["reply"].strip()]
    if cmd.get("detail"):
        lines += ["", f"> {cmd['detail'].strip()}"]
    return "\n".join(lines).rstrip() + "\n"


def archive_path(cmd: dict, root: Optional[Path] = None) -> Path:
    base = root or ARCHIVE_DIR
    fstamp, _ = _stamp(cmd.get("updated_at") or cmd.get("created_at"))
    tag = _slug(cmd.get("message") or cmd.get("symbol") or cmd.get("kind") or "command")
    return base / f"{fstamp}_{tag}_{cmd.get('id')}.md"


def archive_commands(storage, keep: int = 3, root: Optional[Path] = None) -> list[str]:
    """Export finished, un-archived console responses (all but the newest
    `keep`) to Markdown files and mark them archived. Returns the paths written.
    """
    base = root or ARCHIVE_DIR
    written: list[str] = []
    to_archive = storage.archivable_commands(keep=keep)
    if not to_archive:
        return written
    base.mkdir(parents=True, exist_ok=True)
    for cmd in to_archive:
        path = archive_path(cmd, root=base)
        path.write_text(render_command_md(cmd), encoding="utf-8")
        storage.mark_command_archived(cmd["id"])
        written.append(str(path))
    return written
