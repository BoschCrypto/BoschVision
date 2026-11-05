"""Idea -> song -> shot list, written by Claude, then checked by a second review pass."""
from __future__ import annotations

import random
import re

from pydantic import BaseModel, Field


class Shot(BaseModel):
    prompt: str = Field(description="Text-to-video prompt for one ~6 second shot. Must restate the full character description.")
    seconds: int = Field(description="Shot length in seconds, 4-10")


class Concept(BaseModel):
    title: str = Field(description="YouTube title, under 70 characters, no clickbait, no ALL CAPS")
    character: str = Field(description="One original character, described visually in one sentence (species, colours, outfit)")
    visual_style: str = Field(description="Art style shared by every shot, e.g. 'bright 3D cartoon, soft lighting, pastel colours'")
    dance_move: str = Field(description="The signature dance move kids can copy, in plain words")
    song_style: str = Field(description="Music prompt: genre, tempo, instruments, mood. No artist names.")
    bpm: int = Field(description="Tempo, 90-140")
    lyrics: list[str] = Field(description="8-16 short, repetitive, singable lines. Original words only.")
    shots: list[Shot] = Field(description="5-8 shots that together tell a tiny story built around the dance")
    description: str = Field(description="YouTube description, 2-4 friendly sentences, mention it is AI-animated")
    tags: list[str] = Field(description="8-15 search tags")


class Review(BaseModel):
    approved: bool
    issues: list[str] = Field(description="Every problem found; empty if approved")


THEMES = [
    "animals at the zoo", "a farm morning", "under the sea", "outer space", "dinosaurs",
    "vegetables and fruit", "bath time", "brushing teeth", "colours", "counting to ten",
    "the weather", "vehicles and trucks", "a jungle party", "bugs in the garden",
    "snowy winter", "a birthday party", "the alphabet", "shapes", "bedtime wind-down",
    "a beach day", "a rainy day with puddles", "a kitchen with dancing utensils",
]

SYSTEM = """You create short, original, joyful dance-song videos for preschool children (ages 2-6), \
published on YouTube as "made for kids".

Hard rules:
- Everything is original. Never use or imitate existing characters, shows, songs, melodies, lyrics, \
brands, or artists (no Baby Shark, Cocomelon, Peppa, Bluey, Pinkfong, Disney, Pixar, etc.).
- Age-appropriate and gentle: no scares, violence, pranks, injuries, rude humour, or behaviour that is \
dangerous to copy (climbing, eating non-food, playing near roads or water unsupervised).
- No real people, no realistic human faces, no text in the video images.
- Lyrics are simple, positive and repetitive, with a clear chorus kids can sing along to.
- Every shot prompt restates the full character description and visual style so the AI video model \
draws the same character in each shot; vertical 9:16 framing; character centred and dancing.
- Titles and descriptions are honest about what the video contains. No fake urgency, no keyword stuffing."""

REVIEW_SYSTEM = """You are a strict reviewer for a children's YouTube channel. Check the concept against \
these rules and approve it only if all of them pass:
1. Original: no existing characters, shows, songs, lyrics, brands, or artists, and nothing confusingly similar.
2. Safe for ages 2-6: nothing scary, violent, rude, or dangerous to imitate.
3. Honest metadata: title and description match the content; no clickbait or misleading claims.
4. Quality: lyrics scan and repeat, there is a chorus, shots form a small coherent story, and each shot \
prompt fully describes the same character.
List every issue you find."""

# Cheap pre-check before the review call, for names that should never appear.
BLOCKLIST = re.compile(
    r"baby shark|cocomelon|pinkfong|peppa|bluey|paw patrol|disney|pixar|mickey|elsa|"
    r"minion|pokemon|pikachu|sesame|elmo|barney|blippi|spongebob|mario|sonic|hello kitty|frozen",
    re.I,
)


def _recent_block(recent_titles: list[str]) -> str:
    if not recent_titles:
        return "No previous videos yet."
    return "Previous video titles (make something clearly different):\n" + "\n".join(f"- {t}" for t in recent_titles)


def generate_concept(client, model: str, target_seconds: int, recent_titles: list[str],
                     theme: str | None = None, notes: str = "") -> Concept:
    theme = theme or random.choice(THEMES)
    prompt = (
        f"Theme: {theme}\n"
        f"Target length: about {target_seconds} seconds.\n"
        f"{_recent_block(recent_titles)}\n"
        + (f"Extra direction from the channel owner: {notes}\n" if notes else "")
        + "Create one new video concept."
    )
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": prompt}],
        output_format=Concept,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this concept request")
    return response.parsed_output


def review_concept(client, model: str, concept: Concept) -> Review:
    text = concept.model_dump_json(indent=2)
    if BLOCKLIST.search(text):
        return Review(approved=False, issues=[f"Mentions protected IP: {BLOCKLIST.search(text).group(0)}"])
    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=REVIEW_SYSTEM,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"Review this concept:\n{text}"}],
        output_format=Review,
    )
    if response.stop_reason == "refusal":
        return Review(approved=False, issues=["Reviewer declined"])
    return response.parsed_output


def offline_concept(theme: str | None = None) -> Concept:
    """A fixed sample concept, for running the pipeline without any API keys."""
    theme = theme or random.choice(THEMES)
    char = "a round little orange fox with a blue scarf and big friendly eyes"
    style = "bright 3D cartoon, soft lighting, pastel colours"
    moves = ["wiggle wiggle", "spin around", "clap clap clap", "jump up high"]
    return Concept(
        title=f"Wiggle Fox Dance Party | {theme.title()} | Kids Dance Song",
        character=char,
        visual_style=style,
        dance_move="wiggle your hips, spin around, then clap three times",
        song_style="bouncy ukulele and xylophone kids pop, happy, hand claps",
        bpm=120,
        lyrics=[
            "Wiggle wiggle, little fox!", "Wiggle in your stripy socks!",
            "Spin around and touch the ground,", "Clap clap clap, what a sound!",
            "Wiggle wiggle, here we go!", "Fast and fast, and slow slow slow!",
            "Spin around and touch the ground,", "Clap clap clap, what a sound!",
        ],
        shots=[Shot(prompt=f"{char} doing '{m}' in a world of {theme}, {style}, vertical 9:16, centred", seconds=6)
               for m in moves],
        description="A happy dance song for little ones! Wiggle, spin and clap along with Fox. "
                    "This video is AI-animated.",
        tags=["kids songs", "dance song", "nursery rhymes", "toddler dance", "preschool", "sing along", theme],
    )
