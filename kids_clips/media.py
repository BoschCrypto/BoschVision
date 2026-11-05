"""Music and video generation providers.

Each provider writes a file and returns its path. "local" providers need no
keys and produce placeholders, so the pipeline can be tested end to end.
"""
from __future__ import annotations

import json
import math
import random
import struct
import textwrap
import subprocess
import time
import wave
from pathlib import Path

import requests

from .concept import Concept, Shot
from .config import Settings


# ---------------------------------------------------------------- music

def make_song(concept: Concept, seconds: int, out: Path, settings: Settings) -> Path:
    if settings.music_provider == "elevenlabs":
        return _song_elevenlabs(concept, seconds, out.with_suffix(".mp3"), settings)
    if settings.music_provider == "local":
        return _song_local(concept.bpm, seconds, out.with_suffix(".wav"))
    raise ValueError(f"Unknown music provider: {settings.music_provider}")


def _song_elevenlabs(concept: Concept, seconds: int, out: Path, settings: Settings) -> Path:
    """ElevenLabs Music API: a full sung track from a prompt plus our lyrics."""
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    prompt = (f"{concept.song_style}, {concept.bpm} BPM, children's song sung by a cheerful voice. "
              "Lyrics:\n" + "\n".join(concept.lyrics))
    r = requests.post(
        "https://api.elevenlabs.io/v1/music",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={"prompt": prompt[:4000], "music_length_ms": seconds * 1000},
        timeout=600,
    )
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


def _song_local(bpm: int, seconds: int, out: Path, rate: int = 22050) -> Path:
    """A bouncy instrumental placeholder: kick, clap, and a pentatonic melody."""
    rng = random.Random(bpm)
    beat = 60.0 / max(60, min(180, bpm))
    scale = [261.63, 293.66, 329.63, 392.0, 440.0, 523.25]
    melody = [rng.choice(scale) for _ in range(16)]
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        pos = t % beat
        step = int(t / (beat / 2))
        kick = math.sin(2 * math.pi * 60 * pos) * math.exp(-pos * 25)
        half = (t % (beat * 2))
        clap = (rng.random() * 2 - 1) * math.exp(-(half - beat) * 40) * 0.3 if half >= beat else 0
        f = melody[step % len(melody)]
        npos = t % (beat / 2)
        note = 0.25 * math.sin(2 * math.pi * f * t) * math.exp(-npos * 6)
        s = max(-1.0, min(1.0, 0.6 * kick + clap + note))
        frames += struct.pack("<h", int(s * 30000))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out


# ---------------------------------------------------------------- video

def make_shot(shot: Shot, index: int, concept: Concept, out_dir: Path, settings: Settings) -> Path:
    out = out_dir / f"shot_{index:02d}.mp4"
    if settings.video_provider == "replicate":
        return _shot_replicate(shot, out, settings)
    if settings.video_provider == "local":
        return _shot_local(shot, index, concept, out, settings)
    raise ValueError(f"Unknown video provider: {settings.video_provider}")


def _shot_replicate(shot: Shot, out: Path, settings: Settings) -> Path:
    """Run any Replicate text-to-video model and download the result."""
    if not settings.replicate_api_token:
        raise RuntimeError("REPLICATE_API_TOKEN is not set")
    headers = {"Authorization": f"Bearer {settings.replicate_api_token}", "Prefer": "wait"}
    payload = {"prompt": shot.prompt, **json.loads(settings.replicate_extra_input or "{}")}
    r = requests.post(
        f"https://api.replicate.com/v1/models/{settings.replicate_video_model}/predictions",
        headers=headers, json={"input": payload}, timeout=120,
    )
    r.raise_for_status()
    pred = r.json()
    deadline = time.time() + 20 * 60
    while pred.get("status") not in ("succeeded", "failed", "canceled"):
        if time.time() > deadline:
            raise TimeoutError("Video generation took longer than 20 minutes")
        time.sleep(5)
        pred = requests.get(pred["urls"]["get"], headers=headers, timeout=60).json()
    if pred["status"] != "succeeded":
        raise RuntimeError(f"Replicate prediction {pred['status']}: {pred.get('error')}")
    url = pred["output"][0] if isinstance(pred["output"], list) else pred["output"]
    with requests.get(url, stream=True, timeout=300) as dl:
        dl.raise_for_status()
        with out.open("wb") as f:
            for chunk in dl.iter_content(1 << 20):
                f.write(chunk)
    return out


def _shot_local(shot: Shot, index: int, concept: Concept, out: Path, settings: Settings) -> Path:
    """Animated colour-gradient placeholder with a bouncing label."""
    font = settings.resolve_font()
    label_file = out.with_suffix(".txt")
    label_file.write_text(f"PLACEHOLDER SHOT {index + 1}\n" + "\n".join(textwrap.wrap(shot.prompt[:90], 26)), encoding="utf-8")
    vf = ""
    if font:
        vf = (f"drawtext=fontfile='{font}':textfile='{label_file}':fontsize=44:fontcolor=white:"
              "borderw=4:bordercolor=black:x=(w-text_w)/2:y=h/2+120*sin(t*6)")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
           "-i", f"gradients=s={settings.width}x{settings.height}:r={settings.fps}:speed=0.03:seed={index}:d={shot.seconds}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(shot.seconds), str(out)]
    subprocess.run(cmd, check=True)
    return out
