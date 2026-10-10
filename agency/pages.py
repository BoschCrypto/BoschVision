"""HTML for the public website and the HQ.

Plain functions returning strings. Every dynamic value goes through `e()`
(HTML escaping) or `markdown.render()` (escape-first Markdown). No inline
scripts or styles, so the Content-Security-Policy can stay `'self'`-only.
"""
from __future__ import annotations

import html
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlencode

from . import markdown
from .agents import AGENTS
from .config import price_label
from .store import LEAD_STATUSES

KIND_LABELS = {
    "blog_post": "Blog post", "social_post": "Social post", "newsletter": "Newsletter",
    "ad_campaign": "Ad campaign", "email_draft": "Email", "proposal": "Proposal",
    "blueprint": "Blueprint", "report": "Report", "recommendation": "Recommendation",
    "alert": "Alert",
}
APPROVE_EFFECT = {
    "blog_post": "Approving publishes this post on the public blog.",
    "social_post": "Approving marks it ready to post. Copy it into the platform when you're ready.",
    "newsletter": "Approving marks it ready to send from your email tool.",
    "ad_campaign": "Approving marks it ready to launch. Nothing is spent until you build it in "
                   "Google Ads or Meta Ads Manager yourself.",
    "email_draft": "Approving marks it ready to send. Use “Open in email” to send it from your own inbox.",
    "proposal": "Approving marks it ready to send to the client.",
}


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def ago(ts: Optional[float], now: Optional[float] = None) -> str:
    if not ts:
        return "never"
    secs = (now or time.time()) - ts
    if secs < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


def until(ts: Optional[float], now: Optional[float] = None) -> str:
    if not ts:
        return "—"
    secs = ts - (now or time.time())
    if secs <= 0:
        return "due now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"in {int(secs // size)}{unit}"
    return "in <1m"


def every_label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# Public website
# ---------------------------------------------------------------------------
def public_page(b: dict, title: str, body: str, description: str = "") -> str:
    brand, email = e(b["brand"]), e(b["contact_email"])
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description or b['tagline'])}">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/site.css">
</head>
<body>
<header class="nav"><div class="wrap nav-in">
  <a class="logo" href="/"><span class="logo-mark" aria-hidden="true"></span>{brand}</a>
  <nav aria-label="Main">
    <a href="/#services">Services</a><a href="/#pricing">Pricing</a><a href="/blog">Blog</a>
    <a class="btn btn-sm" href="/contact">Free AI audit</a>
  </nav>
</div></header>
<main>
{body}
</main>
<footer class="foot"><div class="wrap foot-in">
  <div><strong>{brand}</strong><p>{e(b['tagline'])}</p></div>
  <div class="foot-links"><a href="mailto:{email}">{email}</a><a href="/blog">Blog</a><a href="/privacy">Privacy</a></div>
</div><div class="wrap fine">© {datetime.now().year} {brand}</div></footer>
</body>
</html>"""


def home(b: dict, posts: list[dict]) -> str:
    region = f" in {e(b['region'])}" if b["region"] else ""
    services = "".join(
        f"""<article class="card service"><h3>{e(s['name'])}</h3><p>{e(s.get('summary', ''))}</p>
        <ul class="ticks">{''.join(f'<li>{e(x)}</li>' for x in s.get('examples', []))}</ul></article>"""
        for s in b["services"])
    steps = "".join(
        f"""<li><span class="step-n">{i}</span><h3>{e(p['title'])}</h3><p>{e(p['text'])}</p></li>"""
        for i, p in enumerate(b["process"], 1))
    packages = "".join(
        f"""<article class="card price{' featured' if p.get('featured') else ''}">
        {'<span class="badge">Most popular</span>' if p.get('featured') else ''}
        <h3>{e(p['name'])}</h3><p class="amount">{e(price_label(p, b['currency']))}</p>
        <p>{e(p.get('blurb', ''))}</p>
        <ul class="ticks">{''.join(f'<li>{e(x)}</li>' for x in p.get('includes', []))}</ul>
        <a class="btn{'' if p.get('featured') else ' btn-ghost'}" href="/contact?{urlencode({'package': p['name']})}">Start with {e(p['name'])}</a>
        </article>""" for p in b["packages"])
    chips = "".join(f"<li>{e(v)}</li>" for v in b["verticals"])
    faq = "".join(f"<details><summary>{e(f['q'])}</summary><p>{e(f['a'])}</p></details>" for f in b["faq"])
    blog = ""
    if posts:
        cards = "".join(
            f"""<a class="card post-card" href="/blog/{e(p['slug'])}"><span class="date">{date(p['decided_ts'])}</span>
            <h3>{e(p['title'])}</h3><p>{e(markdown.plain_excerpt(p['body'], 140))}</p></a>""" for p in posts[:3])
        blog = f"""<section class="section"><div class="wrap"><div class="section-head"><h2>From the blog</h2>
        <a href="/blog">All posts →</a></div><div class="grid3">{cards}</div></div></section>"""
    body = f"""
<section class="hero"><div class="wrap hero-in">
  <div class="hero-copy">
    <p class="eyebrow">AI &amp; automation for local businesses{region}</p>
    <h1>{e(b['tagline'])}</h1>
    <p class="lead">{e(b['pitch'])}</p>
    <div class="cta-row"><a class="btn" href="/contact">Book a free 20-minute AI audit</a>
    <a class="btn btn-ghost" href="#pricing">See pricing</a></div>
    <p class="micro">No obligation. You leave with two or three concrete automations, priced.</p>
  </div>
  <div class="hero-card" role="img" aria-label="Example: an AI assistant booking an appointment late in the evening">
    <div class="chat">
      <p class="msg in">Hi! Can I book a cleaning on Thursday?</p>
      <p class="msg out">Of course. I have 10:30 or 14:00 on Thursday. Which suits you?</p>
      <p class="msg in">14:00 please</p>
      <p class="msg out">Booked ✓ You'll get a reminder the day before.</p>
    </div>
    <p class="hero-card-foot"><span class="dot" aria-hidden="true"></span>Answered at 22:47 · added to the calendar · no staff needed</p>
  </div>
</div></section>

<section id="services" class="section"><div class="wrap">
  <h2>What we build</h2>
  <p class="sub">Three ways to give your team hours back. Most clients start with one and add more.</p>
  <div class="grid3">{services}</div>
</div></section>

<section class="section soft"><div class="wrap">
  <h2>How it works</h2>
  <ol class="steps">{steps}</ol>
</div></section>

<section id="pricing" class="section"><div class="wrap">
  <h2>Simple, fixed pricing</h2>
  <p class="sub">No hourly billing and no surprises. Software and AI usage are passed through at cost.</p>
  <div class="grid3 pricing">{packages}</div>
</div></section>

<section class="section soft"><div class="wrap">
  <h2>Built for businesses like yours</h2>
  <ul class="chips">{chips}</ul>
</div></section>
{blog}
<section class="section"><div class="wrap narrow">
  <h2>Questions</h2>
  <div class="faq">{faq}</div>
</div></section>

<section class="cta-band"><div class="wrap cta-in">
  <div><h2>See what you could automate this month</h2>
  <p>A free 20-minute call. We'll point to the fixes that pay back fastest, whether or not you work with us.</p></div>
  <a class="btn btn-light" href="/contact">Book your free audit</a>
</div></section>"""
    return public_page(b, f"{b['brand']}: {b['tagline']}", body, b["pitch"])


def contact(b: dict, values: Optional[dict] = None, errors: Optional[list[str]] = None) -> str:
    v = values or {}
    options = "".join(
        f'<option{" selected" if v.get("vertical") == opt else ""}>{e(opt)}</option>'
        for opt in [*b["verticals"], "Other"])
    error_box = ""
    if errors:
        error_box = '<div class="errors" role="alert"><ul>' + "".join(
            f"<li>{e(x)}</li>" for x in errors) + "</ul></div>"
    body = f"""
<section class="section"><div class="wrap contact">
  <div class="contact-copy">
    <p class="eyebrow">Free, no obligation</p>
    <h1>Book a free 20-minute AI audit</h1>
    <p class="lead">Tell us a little about your business. We reply within one business day with a couple of times for a call.</p>
    <ul class="ticks">
      <li>Where your team's hours actually go</li>
      <li>The two or three automations that pay back fastest</li>
      <li>A fixed price for each, so you can decide calmly</li>
    </ul>
  </div>
  <form class="card form" method="post" action="/contact">
    {error_box}
    <label>Your name *<input name="name" required maxlength="120" autocomplete="name" value="{e(v.get('name'))}"></label>
    <label>Email *<input name="email" type="email" required maxlength="200" autocomplete="email" value="{e(v.get('email'))}"></label>
    <label>Business name<input name="company" maxlength="160" autocomplete="organization" value="{e(v.get('company'))}"></label>
    <label>Website<input name="website" maxlength="300" placeholder="example.com" value="{e(v.get('website'))}"></label>
    <label>Type of business<select name="vertical"><option value="">Choose…</option>{options}</select></label>
    <label>What would you like to automate?<textarea name="message" rows="5" maxlength="4000">{e(v.get('message'))}</textarea></label>
    <div class="hp" aria-hidden="true"><label>Leave this empty<input name="fax" tabindex="-1" autocomplete="off"></label></div>
    <button class="btn" type="submit">Request my free audit</button>
    <p class="micro">We only use your details to reply to you. See our <a href="/privacy">privacy notice</a>.</p>
  </form>
</div></section>"""
    return public_page(b, f"Free AI audit | {b['brand']}", body)


def thanks(b: dict) -> str:
    body = """<section class="section"><div class="wrap narrow center">
  <h1>Thank you, we've got it.</h1>
  <p class="lead">We'll reply within one business day with a couple of times for your free audit.</p>
  <p><a class="btn btn-ghost" href="/blog">Read the blog while you wait</a></p>
</div></section>"""
    return public_page(b, f"Thank you | {b['brand']}", body)


def privacy(b: dict) -> str:
    email = e(b["contact_email"])
    body = f"""<section class="section"><div class="wrap narrow prose">
  <h1>Privacy notice</h1>
  <p>{e(b['brand'])} keeps this short because we collect very little.</p>
  <h2>What we collect</h2>
  <ul>
    <li><strong>When you contact us:</strong> the details you type into the form (name, email, business, website, message). We use them only to reply and, if you become a client, to deliver the work.</li>
    <li><strong>When you browse:</strong> which pages are viewed and where the visit came from (for example a search engine or an ad). Visits are counted with an anonymous code that changes every day, so we cannot follow you from one day to the next. This site sets no cookies for visitors and loads no third-party trackers.</li>
  </ul>
  <h2>How long we keep it</h2>
  <p>Enquiries that don't become clients are deleted within 12 months. Visit statistics are kept in aggregate.</p>
  <h2>Your rights</h2>
  <p>You can ask us what we hold about you, ask us to correct or delete it, or object to how we use it. Email <a href="mailto:{email}">{email}</a>.</p>
</div></section>"""
    return public_page(b, f"Privacy | {b['brand']}", body)


def blog_index(b: dict, posts: list[dict]) -> str:
    if posts:
        cards = "".join(
            f"""<a class="card post-card" href="/blog/{e(p['slug'])}"><span class="date">{date(p['decided_ts'])}</span>
            <h3>{e(p['title'])}</h3><p>{e(markdown.plain_excerpt(p['body']))}</p></a>""" for p in posts)
        listing = f'<div class="grid3">{cards}</div>'
    else:
        listing = '<p class="sub">The first posts are on their way.</p>'
    body = f"""<section class="section"><div class="wrap">
  <h1>Blog</h1><p class="sub">Practical ideas for putting AI to work in a small business.</p>
  {listing}
</div></section>"""
    return public_page(b, f"Blog | {b['brand']}", body)


def blog_post(b: dict, post: dict) -> str:
    body = f"""<article class="section"><div class="wrap narrow prose">
  <p class="date"><a href="/blog">Blog</a> · {date(post['decided_ts'])}</p>
  <h1>{e(post['title'])}</h1>
  {markdown.render(post['body'])}
  <div class="post-cta card"><h3>Want this working in your business?</h3>
  <p>Book a free 20-minute AI audit and we'll show you where to start.</p>
  <a class="btn" href="/contact">Book your free audit</a></div>
</div></article>"""
    return public_page(b, f"{post['title']} | {b['brand']}", body, markdown.plain_excerpt(post["body"], 155))


def not_found(b: dict) -> str:
    body = """<section class="section"><div class="wrap narrow center">
  <h1>Page not found</h1><p class="lead">That page doesn't exist, or has moved.</p>
  <p><a class="btn" href="/">Back to the home page</a></p></div></section>"""
    return public_page(b, f"Not found | {b['brand']}", body)


def message_page(b: dict, title: str, text: str) -> str:
    body = f"""<section class="section"><div class="wrap narrow center"><h1>{e(title)}</h1>
    <p class="lead">{e(text)}</p></div></section>"""
    return public_page(b, f"{title} | {b['brand']}", body)


# ---------------------------------------------------------------------------
# HQ
# ---------------------------------------------------------------------------
NAV = (("/hq", "Overview"), ("/hq/approvals", "Approvals"), ("/hq/work", "Work"),
       ("/hq/leads", "Leads"), ("/hq/security", "Security"))


def hq_page(b: dict, title: str, body: str, *, active: str, pending: int, backend: str,
            csrf: str) -> str:
    links = []
    for href, label in NAV:
        badge = f' <span class="count">{pending}</span>' if href == "/hq/approvals" and pending else ""
        cls = ' class="on"' if href == active else ""
        links.append(f'<a href="{href}"{cls}>{label}{badge}</a>')
    model_cls = "ok" if backend != "offline" else "warn"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)} · {e(b['brand'])} HQ</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/hq.css">
</head>
<body>
<header class="top">
  <a class="brand" href="/hq">{e(b['brand'])} <span>HQ</span></a>
  <nav aria-label="HQ">{''.join(links)}</nav>
  <div class="top-right">
    <span class="pill {model_cls}" title="How the agents reach a model">model: {e(backend)}</span>
    <a href="/" target="_blank" rel="noopener">View site ↗</a>
    <form method="post" action="/hq/logout">{csrf_field(csrf)}<button class="linkish" type="submit">Log out</button></form>
  </div>
</header>
<main class="hq">
{body}
</main>
<script src="/static/hq.js" defer></script>
</body>
</html>"""


def csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def action_form(csrf: str, action: str, label: str, *, cls: str = "btn", next_url: str = "/hq",
                **fields) -> str:
    hidden = "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())
    return (f'<form method="post" action="/hq/action" class="inline">{csrf_field(csrf)}'
            f'<input type="hidden" name="action" value="{e(action)}">'
            f'<input type="hidden" name="next" value="{e(next_url)}">{hidden}'
            f'<button class="{cls}" type="submit">{e(label)}</button></form>')


def login(b: dict, error: str = "") -> str:
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>HQ login · {e(b['brand'])}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/hq.css">
</head><body class="login-body">
<form class="login" method="post" action="/hq/login">
  <h1>{e(b['brand'])} <span>HQ</span></h1>
  <p>Where your six agents work. Enter the HQ token printed when you ran <code>agency up</code>.</p>
  {err}
  <label>HQ token<input name="token" type="password" autocomplete="current-password" required autofocus></label>
  <button class="btn" type="submit">Enter HQ</button>
</form>
</body></html>"""


def agent_status(state: dict) -> str:
    if state.get("status") == "running":
        return "running"
    if state.get("status") == "error":
        return "error"
    return "idle" if state.get("enabled", 1) else "paused"


def agent_card(agent, state: dict, business: dict, csrf: str, now: float) -> str:
    status = agent_status(state)
    toggle = "Pause" if state.get("enabled", 1) else "Resume"
    summary = state.get("last_error") or state.get("last_summary") or "Hasn't run yet."
    return f"""<article class="agent a-{agent.key}" data-agent="{agent.key}">
  <div class="agent-head">
    <span class="glyph" aria-hidden="true">{e(agent.codename[0])}</span>
    <div><h3>{e(agent.codename)}</h3><p>{e(agent.title)}</p></div>
    <span class="status s-{status}" data-field="status">{status}</span>
  </div>
  <p class="mission">{e(agent.mission)}</p>
  <p class="last" data-field="summary">{e(summary)}</p>
  <dl class="meta">
    <div><dt>Last run</dt><dd data-field="last">{ago(state.get('last_run_ts'), now)}</dd></div>
    <div><dt>Next</dt><dd data-field="next">{until(state.get('next_run_ts'), now) if state.get('enabled', 1) else 'paused'}</dd></div>
    <div><dt>Every</dt><dd>{every_label(agent.every_minutes(business))}</dd></div>
  </dl>
  <div class="agent-actions">
    {action_form(csrf, 'run', 'Run now', cls='btn btn-sm', agent=agent.key)}
    {action_form(csrf, 'toggle', toggle, cls='btn btn-sm ghost', agent=agent.key)}
    <a href="/hq/agents/{agent.key}">Open →</a>
  </div>
</article>"""


def work_list(items: list[dict], now: float, empty: str = "Nothing here yet.") -> str:
    if not items:
        return f'<p class="empty">{e(empty)}</p>'
    rows = []
    for w in items:
        agent = AGENTS.get(w["agent"])
        who = agent.codename if agent else w["agent"]
        rows.append(f"""<li><a href="/hq/work/{w['id']}">
  <span class="kind k-{e(w['kind'])}">{e(KIND_LABELS.get(w['kind'], w['kind']))}</span>
  <span class="title">{e(w['title'])}</span>
  <span class="by">{e(who)} · {ago(w['ts'], now)}{' · ' + e(w['status']) if w['status'] not in ('info', 'pending') else ''}</span>
</a></li>""")
    return '<ul class="items">' + "".join(rows) + "</ul>"


def task_form(csrf: str, agents: list, fixed: Optional[str] = None, next_url: str = "/hq") -> str:
    if fixed:
        picker = f'<input type="hidden" name="agent" value="{e(fixed)}">'
    else:
        picker = '<label>Agent<select name="agent">' + "".join(
            f'<option value="{a.key}">{e(a.codename)} ({e(a.title)})</option>' for a in agents) + "</select></label>"
    return f"""<form method="post" action="/hq/action" class="task-form">{csrf_field(csrf)}
  <input type="hidden" name="action" value="task"><input type="hidden" name="next" value="{e(next_url)}">
  {picker}
  <label>Instruction<textarea name="instruction" rows="3" maxlength="2000" required
    placeholder="e.g. Draft a Google Ads campaign for dentists running a spring teeth-whitening offer"></textarea></label>
  <button class="btn" type="submit">Assign task</button>
</form>"""


def overview(b: dict, *, states: dict, snap: Optional[dict], pending: list[dict], runs: list[dict],
             findings: list[dict], usage: dict, cap: int, backend: str, csrf: str, now: float) -> str:
    snap = snap or {}
    conv = snap.get("conversion_7d")
    open_leads = sum(v for k, v in (snap.get("pipeline") or {}).items() if k in ("new", "contacted", "proposal"))
    passing = sum(1 for f in findings if f["ok"])
    sec = f"{passing}/{len(findings)}" if findings else "—"
    serious = any(not f["ok"] and f["severity"] in ("high", "critical") for f in findings)
    sec_cls = "" if not findings else ("bad" if serious else ("good" if passing == len(findings) else "attn"))
    tiles = [
        ("Visitors · 7d", snap.get("visitors_7d", 0), ""),
        ("Leads · 7d", snap.get("leads_7d", 0), ""),
        ("Conversion", f"{conv:.1%}" if conv is not None else "—", ""),
        ("Open pipeline", open_leads, ""),
        ("Waiting for you", len(pending), "attn" if pending else ""),
        ("Security checks", sec, sec_cls),
        ("Model calls · spend today", f"{usage['calls']}/{cap} · ${usage['cost_usd']:.2f}", ""),
    ]
    kpis = "".join(f'<div class="kpi {cls}"><span>{e(label)}</span><strong>{e(value)}</strong></div>'
                   for label, value, cls in tiles)
    cards = "".join(agent_card(a, states.get(k, {}), b, csrf, now) for k, a in AGENTS.items())
    activity = []
    for r in runs:
        agent = AGENTS.get(r["agent"])
        text = r["error"] or r["summary"] or r["status"]
        activity.append(f"""<li class="r-{e(r['status'])}"><span class="who">{e(agent.codename if agent else r['agent'])}</span>
  <span class="what">{e(text)}</span><span class="when">{ago(r['started_ts'], now)}</span></li>""")
    offline_note = ""
    if backend == "offline":
        offline_note = """<div class="notice">No model is connected, so BEACON, FLARE, SCOUT and FORGE draft from
        templates. SENTRY and PRISM work fully without one. Connect a model in <code>.env</code> (see
        <code>agency/README.md</code>) and restart.</div>"""
    return f"""{offline_note}
<section class="kpis">{kpis}</section>
<section><h2 class="section-title">Your agents</h2><div class="agents">{cards}</div></section>
<section class="cols">
  <div class="panel"><div class="panel-head"><h2>Waiting for you</h2><a href="/hq/approvals">All →</a></div>
  {work_list(pending[:6], now, "Nothing waiting. Your agents will file drafts here for approval.")}</div>
  <div class="panel"><div class="panel-head"><h2>Latest activity</h2><a href="/hq/work">All work →</a></div>
  <ul class="activity">{''.join(activity) or '<li class="empty">No runs yet. Agents start within a few minutes.</li>'}</ul></div>
</section>
<section class="panel"><h2>Give an agent a task</h2>
<p class="hint">It runs on the agent's next tick (within seconds), ahead of its routine work.</p>
{task_form(csrf, list(AGENTS.values()))}</section>"""


def approvals(items: list[dict], now: float) -> str:
    return f"""<h1>Approvals</h1>
<p class="hint">Everything that would leave the business waits here. Open an item to read it, edit it, then approve or reject it.
Rejected titles are shown to the agent next time, so it learns what you don't want.</p>
<div class="panel">{work_list(items, now, "Nothing waiting for approval.")}</div>"""


def work_page(items: list[dict], now: float, agent: Optional[str], status: Optional[str]) -> str:
    def chip(label: str, **params) -> str:
        on = all((params.get(k) or None) == v for k, v in (("agent", agent), ("status", status)))
        query = urlencode({k: v for k, v in params.items() if v})
        return f'<a class="chip{" on" if on else ""}" href="/hq/work{"?" + query if query else ""}">{e(label)}</a>'

    agent_chips = chip("All agents", status=status) + "".join(
        chip(a.codename, agent=k, status=status) for k, a in AGENTS.items())
    status_chips = "".join(chip(label, agent=agent, status=value) for label, value in (
        ("Any status", None), ("Pending", "pending"), ("Approved", "approved"),
        ("Published", "published"), ("Rejected", "rejected"), ("Internal", "info")))
    return f"""<h1>Work</h1>
<div class="filters"><div>{agent_chips}</div><div>{status_chips}</div></div>
<div class="panel">{work_list(items, now)}</div>"""


def work_detail(item: dict, lead: Optional[dict], csrf: str, now: float, site_url: str) -> str:
    agent = AGENTS.get(item["agent"])
    who = agent.codename if agent else item["agent"]
    kind = KIND_LABELS.get(item["kind"], item["kind"])
    lead_link = ""
    if lead:
        name = lead["company"] or lead["name"] or lead["email"]
        lead_link = f' · for <a href="/hq/leads#lead-{lead["id"]}">{e(name)}</a>'
    decide = ""
    if item["status"] == "pending":
        verb = "Approve & publish" if item["kind"] == "blog_post" else "Approve"
        decide = f"""<form method="post" action="/hq/action" class="panel decide">{csrf_field(csrf)}
  <input type="hidden" name="id" value="{item['id']}"><input type="hidden" name="next" value="/hq/work/{item['id']}">
  <label>Edit before approving (Markdown)<textarea name="body" rows="16">{e(item['body'])}</textarea></label>
  <p class="hint">{e(APPROVE_EFFECT.get(item['kind'], ''))}</p>
  <div class="row"><button class="btn" name="action" value="approve" type="submit">{verb}</button>
  <button class="btn ghost danger" name="action" value="reject" type="submit">Reject</button></div>
</form>"""
    extras = ['<button class="btn ghost" type="button" data-copy="body-src">Copy text</button>']
    if item["kind"] == "email_draft" and lead and lead.get("email"):
        mailto = (f"mailto:{quote(lead['email'], safe='@')}?subject={quote(item['title'])}"
                  f"&body={quote(item['body'][:1800])}")
        extras.append(f'<a class="btn ghost" href="{e(mailto)}">Open in email</a>')
    if item["status"] == "published" and item.get("slug"):
        extras.append(f'<a class="btn ghost" href="/blog/{e(item["slug"])}" target="_blank" rel="noopener">View on site ↗</a>')
    status_note = "" if item["status"] in ("pending", "info") else f' · <span class="st st-{e(item["status"])}">{e(item["status"])}</span>'
    return f"""<p class="crumbs"><a href="/hq/approvals">← Approvals</a> · <a href="/hq/work">All work</a></p>
<h1>{e(item['title'])}</h1>
<p class="meta-line"><span class="kind k-{e(item['kind'])}">{e(kind)}</span> by {e(who)} · {ago(item['ts'], now)}{lead_link}{status_note}</p>
{decide}
<div class="panel rendered">{markdown.render(item['body'])}</div>
<div class="row">{''.join(extras)}</div>
<textarea id="body-src" class="visually-hidden" aria-hidden="true" tabindex="-1" readonly>{e(item['body'])}</textarea>"""


def leads_page(b: dict, leads: list[dict], csrf: str, now: float) -> str:
    rows = []
    for lead in leads:
        options = "".join(f'<option{" selected" if s == lead["status"] else ""}>{s}</option>'
                          for s in LEAD_STATUSES)
        email = f'<a href="mailto:{e(lead["email"])}">{e(lead["email"])}</a>' if lead["email"] else ""
        rows.append(f"""<tr id="lead-{lead['id']}">
  <td>{ago(lead['ts'], now)}</td>
  <td><strong>{e(lead['name'] or '—')}</strong><br>{e(lead['company'])}<br>{email}</td>
  <td>{e(lead['vertical'])}</td>
  <td>{e(lead['source'])}{('<br><small>' + e(lead['campaign']) + '</small>') if lead['campaign'] else ''}</td>
  <td>{e(lead['score']) if lead['score'] else '—'}</td>
  <td class="note">{e(lead['notes'] or ('Waiting for SCOUT' if lead['status'] in ('new', 'prospect') else ''))}
    <br><a href="/hq/work?{urlencode({'lead': lead['id']})}">Drafts →</a></td>
  <td><form method="post" action="/hq/action" class="inline">{csrf_field(csrf)}
    <input type="hidden" name="action" value="lead_status"><input type="hidden" name="id" value="{lead['id']}">
    <input type="hidden" name="next" value="/hq/leads"><select name="status" aria-label="Status">{options}</select>
    <button class="btn btn-sm ghost" type="submit">Save</button></form></td>
</tr>""")
    table = ("""<div class="table-wrap"><table class="table"><thead><tr><th>Received</th><th>Who</th><th>Trade</th>
<th>Source</th><th>Fit</th><th>SCOUT's note</th><th>Status</th></tr></thead><tbody>""" + "".join(rows)
             + "</tbody></table></div>") if rows else '<p class="empty">No leads yet. They appear here the moment someone fills in the contact form.</p>'
    verticals = "".join(f"<option>{e(v)}</option>" for v in [*b["verticals"], "Other"])
    return f"""<h1>Leads</h1>
<p class="hint">Set a lead to <strong>won</strong> and FORGE writes the delivery blueprint. Add a <strong>prospect</strong> below and SCOUT drafts first-touch outreach.</p>
<div class="panel">{table}</div>
<section class="panel"><h2>Add a prospect</h2>
<form method="post" action="/hq/action" class="grid-form">{csrf_field(csrf)}
  <input type="hidden" name="action" value="add_prospect"><input type="hidden" name="next" value="/hq/leads">
  <label>Contact name<input name="name" maxlength="120"></label>
  <label>Business<input name="company" maxlength="160" required></label>
  <label>Email<input name="email" type="email" maxlength="200"></label>
  <label>Website<input name="website" maxlength="300"></label>
  <label>Trade<select name="vertical">{verticals}</select></label>
  <label class="wide">What you know about them<textarea name="message" rows="3" maxlength="4000"
    placeholder="e.g. Busy salon on the high street, 3 chairs, bookings only by phone, slow to reply on Instagram"></textarea></label>
  <button class="btn" type="submit">Add prospect</button>
</form></section>"""


def security_page(findings: list[dict], state: dict, every: int, csrf: str, now: float) -> str:
    rows = "".join(f"""<tr><td>{e(f['check_name'].replace('_', ' '))}</td>
  <td><span class="st {'st-ok' if f['ok'] else 'st-fail'}">{'OK' if f['ok'] else 'FAIL'}</span></td>
  <td>{e(f['severity'])}</td><td>{e(f['detail'])}</td></tr>""" for f in findings)
    table = (f"""<div class="table-wrap"><table class="table"><thead><tr><th>Check</th><th>Result</th><th>Severity if failing</th>
<th>Detail</th></tr></thead><tbody>{rows}</tbody></table></div>""" if findings
             else '<p class="empty">SENTRY hasn\'t run yet.</p>')
    return f"""<h1>Security</h1>
<p class="hint">SENTRY probes the live site and HQ every {every_label(every)} (last run {ago(state.get('last_run_ts'), now)}).
New high or critical failures appear in Approvals as alerts and ping your webhook.</p>
<div class="row">{action_form(csrf, 'run', 'Run SENTRY now', agent='security', next_url='/hq/security')}</div>
<div class="panel">{table}</div>"""


def agent_page(agent, state: dict, business: dict, runs: list[dict], work: list[dict],
               tasks: list[dict], csrf: str, now: float) -> str:
    status = agent_status(state)
    run_rows = "".join(f"""<tr class="r-{e(r['status'])}"><td>{ago(r['started_ts'], now)}</td><td>{e(r['trigger'])}</td>
  <td><span class="st st-{e(r['status'])}">{e(r['status'])}</span></td>
  <td>{e(r['error'] or r['summary'])}</td>
  <td>{f"{r['finished_ts'] - r['started_ts']:.0f}s" if r['finished_ts'] else '…'}</td></tr>""" for r in runs)
    task_rows = "".join(f"<li><span class=\"st st-{e(t['status'])}\">{e(t['status'])}</span> {e(t['instruction'])}</li>"
                        for t in tasks)
    needs = "" if agent.model_driven else '<p class="hint">Does its routine job without a model; uses one only for tasks you assign.</p>'
    return f"""<p class="crumbs"><a href="/hq">← Overview</a></p>
<section class="agent-hero a-{agent.key}" data-agent="{agent.key}">
  <span class="glyph big" aria-hidden="true">{e(agent.codename[0])}</span>
  <div class="hero-text"><h1>{e(agent.codename)} <small>{e(agent.title)}</small></h1><p>{e(agent.mission)}</p>{needs}
  <p class="meta-line"><span class="status s-{status}" data-field="status">{status}</span>
  · every {every_label(agent.every_minutes(business))} · last run <span data-field="last">{ago(state.get('last_run_ts'), now)}</span>
  · next <span data-field="next">{until(state.get('next_run_ts'), now) if state.get('enabled', 1) else 'paused'}</span></p></div>
  <div class="row">{action_form(csrf, 'run', 'Run now', agent=agent.key, next_url=f'/hq/agents/{agent.key}')}
  {action_form(csrf, 'toggle', 'Pause' if state.get('enabled', 1) else 'Resume', cls='btn ghost', agent=agent.key, next_url=f'/hq/agents/{agent.key}')}</div>
</section>
<section class="panel"><h2>Give {e(agent.codename)} a task</h2>
{task_form(csrf, [agent], fixed=agent.key, next_url=f'/hq/agents/{agent.key}')}
{'<ul class="tasks">' + task_rows + '</ul>' if task_rows else ''}</section>
<section class="cols">
  <div class="panel"><h2>Recent runs</h2>{'<div class="table-wrap"><table class="table"><thead><tr><th>When</th><th>Trigger</th><th>Status</th><th>Summary</th><th>Took</th></tr></thead><tbody>' + run_rows + '</tbody></table></div>' if run_rows else '<p class="empty">No runs yet.</p>'}</div>
  <div class="panel"><h2>Recent work</h2>{work_list(work, now)}</div>
</section>"""
