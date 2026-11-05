"""Stitch shots + song + sing-along lyric captions into one vertical video."""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

from .config import Settings


def probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def assemble(shots: list[Path], song: Path, lyrics: list[str], out: Path, settings: Settings) -> Path:
    work = out.parent
    w, h, fps = settings.width, settings.height, settings.fps

    # 1. Normalise every shot to the same size, frame rate and codec (no audio).
    norm = []
    for i, shot in enumerate(shots):
        n = work / f"norm_{i:02d}.mp4"
        _ff("-i", str(shot), "-an",
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(n))
        norm.append(n)

    # 2. Concatenate.
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in norm), encoding="utf-8")
    joined = work / "joined.mp4"
    _ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined))

    # 3. Length = the song, capped at the target. Shots loop if the song is longer.
    duration = min(probe_seconds(song), float(settings.target_seconds))

    # 4. Lyric captions, spread evenly across the song, one line at a time.
    filters = []
    font = settings.resolve_font()
    if font and lyrics:
        per = duration / len(lyrics)
        for i, line in enumerate(lyrics):
            tf = work / f"lyric_{i:02d}.txt"
            tf.write_text("\n".join(textwrap.wrap(line, 18)) or " ", encoding="utf-8")
            start, end = i * per, (i + 1) * per
            filters.append(
                f"drawtext=fontfile='{font}':textfile='{tf.as_posix()}':fontsize=76:fontcolor=white:line_spacing=12:"
                f"borderw=6:bordercolor=black@0.8:x=(w-text_w)/2:y=h*0.78:"
                f"enable='between(t,{start:.2f},{end:.2f})'"
            )
    vf = ",".join(filters) if filters else "null"

    _ff("-stream_loop", "-1", "-i", str(joined), "-i", str(song),
        "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "1:a",
        "-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-af", "afade=t=out:st=" + f"{max(0, duration - 1.5):.2f}:d=1.5",
        "-movflags", "+faststart", str(out))
    return out
