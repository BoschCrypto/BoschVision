"""Archiving committee console responses to clean Markdown files. No network."""
from hf_trading_bot import committee_archive
from hf_trading_bot.storage import Storage


def _done_cmd(s, message, reply):
    cid = s.enqueue_command("console", "prompt", symbol=None, message=message)
    s.update_command(cid, status="done", reply=reply, detail="completed")
    return cid


def test_archive_keeps_newest_and_writes_files(tmp_path):
    s = Storage(str(tmp_path / "c.db"))
    for i in range(6):
        _done_cmd(s, f"question {i}", f"answer {i}")

    root = tmp_path / "research" / "committee"
    written = committee_archive.archive_commands(s, keep=3, root=root)

    # 6 done, keep 3 -> 3 archived to files
    assert len(written) == 3
    assert all((tmp_path / p if not p.startswith("/") else __import__("pathlib").Path(p)).exists()
               for p in [str(root / x.name) for x in root.glob("*.md")])
    files = sorted(root.glob("*.md"))
    assert len(files) == 3
    body = files[0].read_text()
    assert "## Command" in body and "## APEX reply" in body

    # console now shows only the newest 3 (un-archived)
    remaining = s.recent_commands(limit=50, include_archived=False)
    assert len(remaining) == 3
    assert {c["message"] for c in remaining} == {"question 3", "question 4", "question 5"}
    s.close()


def test_archive_is_idempotent(tmp_path):
    s = Storage(str(tmp_path / "c.db"))
    for i in range(4):
        _done_cmd(s, f"q{i}", f"a{i}")
    root = tmp_path / "arch"
    first = committee_archive.archive_commands(s, keep=1, root=root)
    second = committee_archive.archive_commands(s, keep=1, root=root)
    assert len(first) == 3
    assert second == []          # already archived, nothing left to move
    s.close()


def test_pending_and_running_never_archived(tmp_path):
    s = Storage(str(tmp_path / "c.db"))
    cid = s.enqueue_command("console", "p", message="live one")   # pending
    _done_cmd(s, "old done", "reply")
    root = tmp_path / "arch"
    written = committee_archive.archive_commands(s, keep=0, root=root)
    # only the done one archived; the pending command stays in the console
    assert len(written) == 1
    remaining = s.recent_commands(limit=50, include_archived=False)
    assert any(c["id"] == cid for c in remaining)
    s.close()


def test_render_command_md_shapes():
    md = committee_archive.render_command_md({
        "id": 7, "kind": "tactical", "symbol": "BTC/USD", "message": "trade BTC",
        "reply": "staged proposal #3", "detail": "done", "status": "done",
        "created_at": "2026-08-18T04:00:00Z", "updated_at": "2026-08-18T04:01:00Z",
    })
    assert md.startswith("# trade BTC")
    assert "BTC/USD" in md and "staged proposal #3" in md
