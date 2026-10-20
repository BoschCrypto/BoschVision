"""`agency`: run the business from one command.

    agency up                 website + HQ + all six agents, around the clock
    agency up --no-agents     website + HQ only
    agency run sales          run one agent once, right now, and print its work
    agency run ads --task "…" give an agent a one-off instruction and run it
    agency status             each agent's state, the model, today's budget
    agency hq-token           print the HQ login token (--rotate for a new one)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import signal
import time
import urllib.request
import webbrowser
from dataclasses import replace
from typing import Optional

import click
from dotenv import find_dotenv, load_dotenv

from .agents import AGENTS, Context
from .config import BusinessFileError, Settings, load_business
from .llm import LLM
from .notify import make_notifier
from .pages import ago, until
from .scheduler import Scheduler
from .store import Store, start_of_day
from .web import ensure_hq_token, serve


# The .env this run loaded (found next to the project, whatever folder the
# command is run from); shown in the banner so a misnamed file is obvious.
_ENV_FILE = ""


def build_context(**overrides) -> Context:
    try:
        business = load_business()
    except BusinessFileError as ex:
        raise click.ClickException(str(ex)) from ex
    settings = Settings.from_env(**overrides)
    store = Store(settings.db_path)
    settings = replace(settings, hq_token=ensure_hq_token(store, settings.hq_token))
    return Context(store=store, llm=LLM(settings, store), business=business,
                   settings=settings, notify=make_notifier(settings.webhook_url))


def _model_line(ctx: Context) -> str:
    if not ctx.llm.connected:
        line = "offline: writing agents file templates (agency/README.md, step 3: Connect the AI)"
    else:
        line = f"{ctx.llm.backend} · {ctx.llm.model} · cap {ctx.llm.cap} calls/day"
    if os.environ.get("ANTHROPIC_API_KEY") and not importlib.util.find_spec("anthropic"):
        line += ("\n            ANTHROPIC_API_KEY is set but the Claude SDK isn't installed: "
                 'run  pip install -e ".[agency]"')
    return line


# `agency up` exits with this when another copy already holds the port, so
# run-agency.bat can say so instead of "stopped".
ALREADY_RUNNING = 3


def _agency_answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=3) as resp:
            return resp.read().decode().strip() == "ok"
    except (OSError, ValueError):
        return False


def _disable_quick_edit() -> None:
    """In the classic Windows console a click inside the window starts a text
    selection that pauses every write to it; the agents log each run, so they
    would all freeze until a key is pressed. Turn QuickEdit off for this window."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # clear ENABLE_QUICK_EDIT_MODE (0x40); ENABLE_EXTENDED_FLAGS (0x80) makes it stick
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)
    except (AttributeError, OSError):
        pass


def _stop_on_sigterm(signum, frame):
    raise KeyboardInterrupt  # same clean shutdown as Ctrl+C (systemd sends SIGTERM)


@click.group()
def cli():
    """Run the agency: public website, HQ, and six always-on agents."""
    global _ENV_FILE
    _ENV_FILE = find_dotenv()
    if _ENV_FILE:
        load_dotenv(_ENV_FILE)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M")
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@cli.command()
@click.option("--host", default=None, help="Bind address (default 127.0.0.1; 0.0.0.0 to expose).")
@click.option("--port", type=int, default=None, help="Port (default 8500).")
@click.option("--no-agents", is_flag=True, help="Serve the site and HQ without running agents.")
@click.option("--open", "open_browser", is_flag=True, help="Open HQ in the browser once up.")
def up(host: Optional[str], port: Optional[int], no_agents: bool, open_browser: bool):
    """Serve the website and HQ and keep all six agents working."""
    ctx = build_context(host=host, port=port)
    s = ctx.settings
    local = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}"
    try:
        srv = serve(ctx, s.host, s.port)
    except OSError as ex:
        ctx.store.close()
        if _agency_answers(local):
            click.echo(f"The agency is already running in another window. HQ: {local}/hq")
            if open_browser:
                webbrowser.open(f"{local}/hq/login")
            click.get_current_context().exit(ALREADY_RUNNING)
        raise click.ClickException(
            f"could not listen on {s.host}:{s.port} ({ex}). Another program is using that "
            "port: set AGENCY_PORT in .env to a free one, e.g. AGENCY_PORT=8501") from ex
    _disable_quick_edit()
    scheduler = None
    if not no_agents:
        scheduler = Scheduler(ctx)
        scheduler.start()
    names = " ".join(a.codename for a in AGENTS.values())
    click.echo("\n" + "=" * 68)
    click.echo(f"  {ctx.business['brand']} is open for business")
    click.echo(f"  Website   {local}/")
    click.echo(f"  HQ        {local}/hq      token: {s.hq_token}")
    click.echo(f"  Model     {_model_line(ctx)}")
    click.echo(f"  Agents    {'working: ' + names if scheduler else 'off (--no-agents)'}")
    click.echo(f"  Settings  {_ENV_FILE or 'no .env file found (using defaults)'}")
    click.echo(f"  Data      {s.db_path}")
    click.echo("=" * 68)
    click.echo("  Keep this window open: closing it stops the website and the agents.")
    click.echo("  To stop on purpose, press Ctrl+C.\n")
    if open_browser:
        webbrowser.open(f"{local}/hq/login")
    signal.signal(signal.SIGTERM, _stop_on_sigterm)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        click.echo("Stopping…")
    finally:
        if scheduler:
            scheduler.stop()
        srv.shutdown()
        ctx.store.close()


@cli.command()
@click.argument("agent", type=click.Choice(list(AGENTS)))
@click.option("--task", default=None, help="A one-off instruction for the agent.")
def run(agent: str, task: Optional[str]):
    """Run one agent once, now, and print what it produced."""
    ctx = build_context()
    if task:
        ctx.store.add_task(agent, task)
    result = Scheduler(ctx).run_agent(agent, trigger="cli")
    click.echo(f"{AGENTS[agent].codename} [{result.status}] {result.summary}")
    for item in result.items:
        click.echo(f"  - {item['kind']}: {item['title']}")
    if result.items:
        click.echo("Open HQ to review and approve.")
    ctx.store.close()


@cli.command()
def status():
    """Each agent's state, the model backend, and today's budget."""
    ctx = build_context()
    states, now = ctx.store.agent_states(), time.time()
    click.echo(f"Model: {_model_line(ctx)}")
    click.echo(f"Data:  {ctx.settings.db_path}  ·  settings: {_ENV_FILE or 'no .env file'}")
    usage = ctx.store.llm_usage_since(start_of_day())
    click.echo(f"Today: {usage['calls']} model calls, ~${usage['cost_usd']:.2f} · "
               f"{ctx.store.pending_count()} item(s) waiting for approval\n")
    for key, agent in AGENTS.items():
        st = states.get(key, {})
        state = "paused" if st and not st.get("enabled") else (st.get("status") or "not started")
        click.echo(f"{agent.codename:<7} {agent.title:<10} {state:<11} last {ago(st.get('last_run_ts'), now):<10} "
                   f"next {until(st.get('next_run_ts'), now):<9} {st.get('last_error') or st.get('last_summary') or ''}")
    ctx.store.close()


@cli.command("hq-token")
@click.option("--rotate", is_flag=True, help="Generate a new token (logs out every browser).")
def hq_token(rotate: bool):
    """Print the HQ login token."""
    settings = Settings.from_env()
    if settings.hq_token:
        if rotate:
            raise click.ClickException("The token comes from AGENCY_HQ_TOKEN: change it in .env instead.")
        click.echo(settings.hq_token)
        return
    store = Store(settings.db_path)
    click.echo(ensure_hq_token(store, rotate=rotate))
    if rotate:
        click.echo("Rotated. Restart `agency up` for the new token to take effect.")
    store.close()


if __name__ == "__main__":
    cli()
