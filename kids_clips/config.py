"""Settings, read from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # Where finished videos, intermediate files and the history log go.
    out_dir: Path = field(default_factory=lambda: Path(_env("KIDCLIPS_OUT_DIR", "kidclips_output")))

    # Claude writes the concept, lyrics and shot list, then a second call reviews it.
    claude_model: str = field(default_factory=lambda: _env("KIDCLIPS_CLAUDE_MODEL", "claude-opus-5-5"))

    # "local" works offline with no API keys (placeholder visuals + synth beat),
    # so you can test the whole pipeline before paying for generation.
    music_provider: str = field(default_factory=lambda: _env("KIDCLIPS_MUSIC_PROVIDER", "local"))
    video_provider: str = field(default_factory=lambda: _env("KIDCLIPS_VIDEO_PROVIDER", "local"))

    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    replicate_api_token: str = field(default_factory=lambda: _env("REPLICATE_API_TOKEN"))
    # Any Replicate text-to-video model. Check its input schema on replicate.com
    # and adjust KIDCLIPS_REPLICATE_EXTRA_INPUT (JSON) to match.
    replicate_video_model: str = field(default_factory=lambda: _env("KIDCLIPS_REPLICATE_VIDEO_MODEL", "minimax/hailuo-02"))
    replicate_extra_input: str = field(default_factory=lambda: _env("KIDCLIPS_REPLICATE_EXTRA_INPUT", '{"duration": 6}'))

    # Video length. Shorts allow up to 3 minutes; 30-60s performs best for kids' songs.
    target_seconds: int = field(default_factory=lambda: int(_env("KIDCLIPS_TARGET_SECONDS", "45")))
    width: int = 1080
    height: int = 1920
    fps: int = 30
    font_file: str = field(default_factory=lambda: _env("KIDCLIPS_FONT_FILE", ""))

    # YouTube upload (optional). Uploads are PRIVATE by default so a human
    # watches every video before it goes public.
    youtube_client_secrets: Path = field(default_factory=lambda: Path(_env("YOUTUBE_CLIENT_SECRETS", "config/youtube_client_secret.json")))
    youtube_token_file: Path = field(default_factory=lambda: Path(_env("YOUTUBE_TOKEN_FILE", "config/youtube_token.json")))
    youtube_privacy: str = field(default_factory=lambda: _env("KIDCLIPS_YOUTUBE_PRIVACY", "private"))

    def resolve_font(self) -> str | None:
        candidates = [self.font_file,
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                      "/Library/Fonts/Arial Bold.ttf",
                      "C:/Windows/Fonts/arialbd.ttf"]
        for c in candidates:
            if c and Path(c).exists():
                return c
        return None
