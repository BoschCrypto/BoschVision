# The agency: an AI & automation business run by six agents

A complete small business in one command: a public **website** that sells AI
and automation services to local businesses, an **HQ** where you supervise the
work, and **six agents** that run the business around the clock.

```
agency up --open        # or double-click run-agency.bat on Windows
```

## The business, and why this one

**AI & Automation Services for local businesses**: custom GPT agents, workflow
automations, and prompt-engineering packages for clinics, salons, trades,
restaurants and the like. It was picked over virtual assistance and bookkeeping
for three reasons:

- **The agents are the product demo.** A business run by AI agents, selling AI
  agents, is its own case study.
- **It scales with software, not hours.** Fixed-price setups plus monthly care
  plans. Bookkeeping is billed by the hour and needs a person on every client's books.
- **Less liability.** You configure tools for clients; you don't hold their
  financial records or sign off on their accounts.

Everything about the business (name, region, offers, prices, target trades,
FAQ) lives in [`business.yaml`](business.yaml). The site and all six agents
read it, so edit it once and everything follows.

## The six agents

| Agent | Role | Wakes every | Each run |
|---|---|---|---|
| **BEACON** | Marketing | 6 h | Rotates through blog posts, social batches, lead magnets, newsletters, and local-SEO plans, one target trade at a time |
| **FLARE** | Ads | 12 h | Google Search and Meta campaigns ready to paste in (keywords, ad copy, audiences, budgets, UTM links), optimisation reviews, and A/B test plans. **Never spends.** |
| **SENTRY** | Security | 30 min | Probes the live site and HQ the way an attacker would: uptime, HTTPS certificate, security headers, HQ lock, exposed files, login guessing, form abuse, token strength, model spend. Alerts when a check starts failing. |
| **PRISM** | Analytics | 1 h | Traffic, sources, campaigns, leads, conversion, and pipeline from the site's own logs. A daily report with recommendations for the other agents. |
| **SCOUT** | Sales | 15 min | Qualifies every new lead (fit score 1–10), drafts the reply or a proposal, and drafts cold outreach for prospects you add |
| **FORGE** | Delivery | 3 h | When you mark a lead *won*, writes the delivery blueprint: solution design, the assistant's system prompt, automations, timeline, acceptance tests, GDPR notes. Otherwise builds a reusable template per target trade. |

Change the cadences under `agents:` in `business.yaml`. Each agent's standing
instructions (voice, standards, what it must never do) are plain Markdown in
[`prompts/`](prompts/), so edit them to tune an agent.

**SENTRY and PRISM do their core jobs without any AI model**: their checks and
numbers are exact. BEACON, FLARE, SCOUT and FORGE need a model to write; without
one they file clearly marked templates.

### You stay in control

Nothing leaves the business without you. Blog posts, social posts, newsletters,
ad campaigns, emails and proposals land in **HQ → Approvals**. Open one, edit it
if you like, then approve or reject it:

- An approved **blog post** publishes on the site's blog immediately.
- An approved **email** gets an *Open in email* button, so you send it from your own inbox.
- Approved **ads** and **social posts** are marked ready to paste into Google
  Ads, Meta Ads Manager, or the platform.

Rejected titles are shown to the agent next time, so it learns what you don't
want. BEACON and FLARE pause once six drafts are waiting, so a week away
doesn't bury you, or your budget, in drafts.

## Quick start

```bash
pip install -e ".[agency]"     # from the repo root
agency up --open               # website on :8500, HQ at /hq, agents running
```

The console prints the **HQ token**. Paste it into the login page. It is
generated once and kept in the database; `agency hq-token` prints it again, and
`agency hq-token --rotate` replaces it.

Other commands:

```bash
agency status                          # each agent's state, the model, today's spend
agency run sales                       # run one agent now and print what it made
agency run ads --task "Spring campaign for salons, $300 budget"
agency up --no-agents                  # website + HQ only
```

You can also assign a task to any agent from HQ ("Give an agent a task"). It
runs within seconds, ahead of the agent's routine work.

## Connect a model

The agents pick a backend automatically (`AGENCY_LLM=auto`):

1. **Claude API**: set `ANTHROPIC_API_KEY` in `.env` and `pip install -e ".[agency]"`.
   This uses the official `anthropic` SDK with structured JSON output.
2. **Claude Code**: if you're logged in to the `claude` CLI, the agents use
   `claude -p` with every tool disabled. No API key is needed; usage counts
   against your Claude plan.
3. **Offline**: no model. SENTRY and PRISM work fully; the writing agents use templates.

The default model is `claude-opus-5-5` at `medium` effort. Set
`AGENCY_MODEL=claude-sonnet-5-5` to roughly halve the cost. On the API, requests
opt into Anthropic's server-side refusal fallback (`fallbacks: "default"`): if a
safety classifier declines a request, which security wording occasionally
trips, the API retries it on a recommended fallback model instead of failing.

**Spend is capped.** `AGENCY_MAX_LLM_CALLS_PER_DAY` (default 40) is a hard
limit counted before each call, failures included, so a retry loop can't run up
a bill. HQ shows calls and estimated spend for the day. As a rough guide, a
typical day is 10–20 calls; on Opus 5.5 that is about $1–2/day. Expect more
while FORGE builds its template library in the first day, and more on days
with more leads. Watch the HQ tile for your real number.

## Running it 24/7

The agents work as long as `agency up` is running, so "24/7" means an always-on
machine:

- **Your PC**: run `run-agency.bat` and keep the PC awake. To start it at
  logon, add the .bat to Task Scheduler (trigger: *At log on*).
- **A small server (recommended for a real business)**: any $5–10/month VPS.
  Run `agency up` under systemd or in `tmux`, put [Caddy](https://caddyserver.com)
  in front for automatic HTTPS, and point your domain at it:

  ```
  # /etc/caddy/Caddyfile
  yourdomain.com {
      reverse_proxy 127.0.0.1:8500
  }
  ```

  with `.env`:

  ```
  AGENCY_SITE_URL=https://yourdomain.com
  AGENCY_TRUST_PROXY=1
  ```

  SENTRY then checks your real HTTPS certificate and headers from outside.

Phone pings for new leads and security alerts: set `AGENCY_WEBHOOK_URL` to a
Discord or Slack incoming webhook, or an [ntfy.sh](https://ntfy.sh) topic URL.

## Before you go live

- [ ] `business.yaml`: your brand, region, real `contact_email`, and your prices.
      SENTRY's *launch readiness* check stays amber until the email is real.
- [ ] Read the FAQ and the privacy page (`/privacy`) as promises you'll keep. Change anything that isn't true for you.
- [ ] Domain and HTTPS (above), then set `AGENCY_SITE_URL`.
- [ ] Connect a model, and set the spend cap you're comfortable with.
- [ ] Optional: `AGENCY_WEBHOOK_URL` for phone notifications.

## What the agents don't do

They don't browse the web, post to social media, launch or pay for ads, or
send email. Each of those needs your accounts and your judgement, so the agents
prepare the work and you press the button. To automate one of those steps
later, add it behind the same approval gate.

## How it's built

| File | What it does |
|---|---|
| `cli.py` | The `agency` command |
| `web.py` | HTTP server: public site + HQ, security headers, login, CSRF, cookie-free analytics, lead form |
| `pages.py` | HTML for every page; `static/` holds the CSS, the one HQ script, and the icon |
| `agents.py` | The six agents and what each does per run |
| `prompts/*.md` | Each agent's standing instructions |
| `scheduler.py` | The always-on loop: cadences, Run now, tasks, worker pool |
| `llm.py` | Model backends, structured output, the daily budget ledger |
| `security.py` / `analytics.py` | SENTRY's checks and PRISM's numbers (no model needed) |
| `store.py` | SQLite: traffic, leads, runs, work, approvals, findings, model calls |
| `markdown.py` | Escape-first Markdown rendering for agent output |

Python stdlib HTTP server and SQLite, no web framework. Tests: `pytest tests/test_agency.py`.
