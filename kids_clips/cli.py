"""kidclips command line."""
from __future__ import annotations

import logging

import click

from .config import Settings


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Make original AI kids' dance-song videos for YouTube."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


@cli.command()
@click.option("--theme", help="e.g. 'dinosaurs'. Random if omitted.")
@click.option("--notes", default="", help="Extra creative direction for Claude.")
@click.option("--offline", is_flag=True, help="No API calls: sample concept, placeholder media.")
@click.option("--upload", "do_upload", is_flag=True, help="Upload to YouTube (private by default).")
@click.option("--seconds", type=int, help="Target length in seconds.")
def make(theme, notes, offline, do_upload, seconds):
    """Produce one video."""
    from .agent import produce_one
    s = Settings()
    if seconds:
        s.target_seconds = seconds
    entry = produce_one(s, offline=offline, theme=theme, notes=notes, do_upload=do_upload)
    click.echo(f"Video: {entry['video']}")
    if entry["youtube_id"]:
        click.echo(f"YouTube: https://youtu.be/{entry['youtube_id']}")


@cli.command()
@click.option("--per-day", default=3, show_default=True, help="Videos per day, spread evenly.")
@click.option("--notes", default="")
@click.option("--offline", is_flag=True)
@click.option("--upload", "do_upload", is_flag=True)
def loop(per_day, notes, offline, do_upload):
    """Keep producing videos until stopped (Ctrl+C)."""
    from .agent import run_forever
    run_forever(Settings(), per_day=per_day, offline=offline, do_upload=do_upload, notes=notes)


@cli.command("youtube-auth")
def youtube_auth():
    """One-time Google login so uploads can run unattended."""
    from .upload import authorize
    authorize(Settings())
    click.echo("YouTube login saved.")


@cli.command()
def history():
    """List videos made so far."""
    from .history import History
    for e in History(Settings().out_dir).entries():
        yt = f"  https://youtu.be/{e['youtube_id']}" if e.get("youtube_id") else ""
        click.echo(f"{e['created_at'][:16]}  {e['title']}{yt}")


if __name__ == "__main__":
    cli()
