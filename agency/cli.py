"""`agency`: run the business from one command.

    agency up                 website + HQ + all six agents, around the clock
    agency up --no-agents     website + HQ only
    agency run sales          run one agent once, right now, and print its work
    agency run ads --task "…" give an agent a one-off instruction and run it
    agency status             each agent's state, the model, today's budget
    agency hq-token           print the HQ login token (--rotate for a new one)
"""
from __future__ import annotations

import logging
import time
import webbrowser
from dataclasses import replace
from typing import Optional

import click
from dotenv import load_dotenv

from .agents import AGENTS, Context
from .config import Settings, load_business
from .llm import LLM
from .notify import make_notifier
from .pages import ago, until
from .scheduler import Scheduler
from .store import Store, start_of_day
from .web import ensure_hq_token, serve


def build_context(**overrides) -> Context:
    settings = Settings.from_env(**overrides)
    store = Store(settings.db_path)
    settings = replace(settings, hq_token=ensure_hq_token(store, settings.hq_token))
    return Context(store=store, llm=LLM(settings, store), business=load_business(),
                   settings=settings, notify=make_notifier(settings.webhook_url))


def _model_line(ctx: Context) -> str:
    if not ctx.llm.connected:
        return "offline: content agents draft from templates (see agency/README.md → Connect a model)"
    return f"{ctx.llm.backend} · {ctx.llm.model} · cap {ctx.llm.cap} calls/day"


@click.group()
def cli():
    """Run the agency: public website, HQ, and six always-on agents."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@cli.command()
@click.option("--host", default=None, help="Bind address (default 127.0.0.1; 0.0.0.0 to expose).")
@click.option("--port", type=int, default=None, help="Port (default 8500).")
@click.option("--no-agents", is_flag=True, help="Serve the site and HQ without running agents.")
@click.option("--open", "open_browser", is_flag=True, help="Open HQ in the browser once up.")
def up(host: Optional[str], port: Optional[int], no_agents: bool, open_browser: bool):
    """Serve the website and HQ and keep all six agents working."""
    ctx = build_context(host=host, port=port)
    s = ctx.settings
    try:
        srv = serve(ctx, s.host, s.port)
    except OSError as ex:
        raise click.ClickException(f"could not listen on {s.host}:{s.port}: {ex}") from ex
    scheduler = None
    if not no_agents:
        scheduler = Scheduler(ctx)
        scheduler.start()
    local = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}"
    names = " ".join(a.codename for a in AGENTS.values())
    click.echo("\n" + "=" * 68)
    click.echo(f"  {ctx.business['brand']} is open for business")
    click.echo(f"  Website   {local}/")
    click.echo(f"  HQ        {local}/hq      token: {s.hq_token}")
    click.echo(f"  Model     {_model_line(ctx)}")
    click.echo(f"  Agents    {'working: ' + names if scheduler else 'off (--no-agents)'}")
    click.echo("=" * 68 + "\n  Keep this window open. Ctrl+C stops everything.\n")
    if open_browser:
        webbrowser.open(f"{local}/hq/login")
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
