"""The production loop: idea -> review -> song -> shots -> edit -> (optional) upload."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from .assemble import assemble
from .concept import Concept, generate_concept, offline_concept, review_concept
from .config import Settings
from .history import History
from .media import make_shot, make_song

log = logging.getLogger("kidclips")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


def produce_one(settings: Settings, *, offline: bool = False, theme: str | None = None,
                notes: str = "", do_upload: bool = False, max_attempts: int = 3) -> dict:
    history = History(settings.out_dir)

    concept: Concept | None = None
    if offline:
        concept = offline_concept(theme)
    else:
        import anthropic
        client = anthropic.Anthropic()
        for attempt in range(1, max_attempts + 1):
            candidate = generate_concept(client, settings.claude_model, settings.target_seconds,
                                         history.recent_titles(), theme=theme, notes=notes)
            review = review_concept(client, settings.claude_model, candidate)
            if review.approved:
                concept = candidate
                break
            log.warning("Concept %r rejected (attempt %d): %s", candidate.title, attempt, "; ".join(review.issues))
            notes = (notes + "\nAvoid these problems from the last attempt: " + "; ".join(review.issues)).strip()
        if concept is None:
            raise RuntimeError("No concept passed review")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = settings.out_dir / f"{stamp}-{_slug(concept.title)}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "concept.json").write_text(concept.model_dump_json(indent=2), encoding="utf-8")
    log.info("Making %r in %s", concept.title, work)

    song = make_song(concept, settings.target_seconds, work / "song", settings)
    shots = [make_shot(s, i, concept, work, settings) for i, s in enumerate(concept.shots)]
    video = assemble(shots, song, concept.lyrics, work / "final.mp4", settings)

    entry = {"title": concept.title, "dir": str(work), "video": str(video), "youtube_id": None}
    if do_upload:
        from .upload import upload
        entry["youtube_id"] = upload(video, concept, settings)
        log.info("Uploaded as https://youtu.be/%s (%s)", entry["youtube_id"], settings.youtube_privacy)
    (work / "result.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    history.add(entry)
    return entry


def run_forever(settings: Settings, *, per_day: int, offline: bool, do_upload: bool, notes: str = "") -> None:
    """Keep producing, spread evenly across the day, never more than `per_day`."""
    gap = 24 * 3600 / max(1, per_day)
    while True:
        history = History(settings.out_dir)
        if history.made_today() < per_day:
            try:
                entry = produce_one(settings, offline=offline, do_upload=do_upload, notes=notes)
                log.info("Done: %s", entry["video"])
            except Exception:  # keep the loop alive; one bad run shouldn't stop the channel
                log.exception("Run failed; will try again next slot")
        else:
            log.info("Daily limit of %d reached", per_day)
        time.sleep(gap)
