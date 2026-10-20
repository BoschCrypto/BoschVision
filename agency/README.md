# The agency: an AI & automation business run by six agents

A complete small business in one folder: a public **website** that sells AI
and automation services to local businesses, an **HQ** where you supervise the
work, and **six agents** that run the business around the clock.

## Get it running on Windows (first time)

1. **Install Python** (3.12 or newer) from [python.org/downloads](https://www.python.org/downloads/).
   If the installer offers **"Add python.exe to PATH"**, tick it.
2. **Have the code in a folder that stays put.** This is the `BoschVision`
   folder, the same one that holds `run-dashboard.bat`. Don't keep it inside
   OneDrive, and don't move it later: the setup in step 4 is tied to its location.
3. **Connect the AI.** Do this before the first start, because drafts written
   without AI stay templates. Pick one option:
   - **A. Claude API key (pay per use).** Create a key at
     [console.anthropic.com](https://console.anthropic.com) → API keys, and add
     some credit. This is billed separately from a Claude Pro/Max subscription.
     Then, in the `BoschVision` folder (next to `run-agency.bat`), create a file
     named exactly `.env` containing one line:

     ```
     ANTHROPIC_API_KEY=sk-ant-your-key-here
     ```

     To do that in Notepad, choose *File → Save as*, set *Save as type* to
     **All files**, and type `.env` as the name, so it doesn't become `.env.txt`.
     If a `.env` already exists for the trading bot, add the line to it.
   - **B. Claude Code (uses your Claude plan).** If Claude Code is installed,
     open a terminal, run `claude`, type `/login` and finish in the browser.
     The agents then use your plan, and no key is needed.
   - **Neither?** It still runs. SENTRY and PRISM work fully, SCOUT, BEACON and
     FLARE file templates, and FORGE waits.
4. **Double-click `run-agency.bat`.** The first time, it creates a private
   Python environment and installs everything. That downloads about 270 MB,
   because it shares an install with the trading bot, and takes a few minutes.
   Then your browser opens the HQ login page, with your **HQ token already
   copied to the clipboard**: click the box and press Ctrl+V. Bookmark HQ
   (`http://127.0.0.1:8500/hq`): the login lasts 30 days in that browser.
5. **Keep it running.** The agents work while that black window stays open and
   the PC is awake:
   - *Settings → System → Power → Screen and sleep → "When plugged in, put my
     device to sleep after" → Never.*
   - Double-click **`make-agency-autostart.bat`** once, so the agency starts by
     itself whenever you sign in to Windows (after restarts and updates).
   - To stop it, bring that window to the front, press Ctrl+C, then type `Y`.

The window shows a line each time an agent finishes a run, and the startup
banner shows which settings file (`.env`) and database it is using. Lost the
token? Double-click `run-agency.bat` again. It copies the token to the
clipboard every time, and if the agency is already running it just opens HQ.

**Important:** at this stage the website is visible **only on this PC**.
Customers can't reach it until you put it on a domain (see
[Going live](#going-live-so-customers-can-find-you)).

## Your routine: about 10 minutes a day

1. Open HQ. The overview shows every agent, today's numbers, and what's waiting.
2. **Approvals:** read each draft, edit it if you like, then approve or reject it.
   - An approved **blog post** goes live on your site immediately. To take one
     down, open it in HQ and press **Unpublish**: it goes back to Approvals.
   - An **email** has an *Open in email* button, so you send it from your own inbox.
   - **Ads** and **social posts** you copy into Google Ads, Meta, or the platform yourself.
   - Rejecting teaches the agent: it sees your rejected titles next time.
3. **Leads:** update each lead's status as it moves (contacted → proposal →
   won/lost). Set **won** and FORGE writes that client's delivery plan.
4. **Give an agent a task** whenever you want something specific, for example
   "FLARE: plan a spring campaign for salons, 300 USD budget".

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
FAQ, privacy text) lives in [`business.yaml`](business.yaml). The site and all
six agents read it, so edit it once and everything follows. Open it with
Notepad, and restart the agency after saving.

## The six agents

| Agent | Role | Wakes every | Each run |
|---|---|---|---|
| **BEACON** | Marketing | 6 h | Rotates through blog posts, social batches, lead magnets, newsletters, and local-SEO plans, one target trade at a time |
| **FLARE** | Ads | 12 h | Google Search and Meta campaigns ready to paste in (keywords, ad copy, audiences, budgets, tracking links), optimisation reviews, and A/B test plans. **Never spends.** |
| **SENTRY** | Security | 30 min | Probes the live site and HQ the way an attacker would: uptime, HTTPS certificate, security headers, HQ lock, exposed files, login guessing, form abuse, token strength, model spend. Alerts when a check starts failing. |
| **PRISM** | Analytics | 1 h | Traffic, sources, campaigns, leads, conversion, and pipeline from the site's own logs. A daily report, plus written recommendations when AI is connected. |
| **SCOUT** | Sales | 15 min | Qualifies every new lead (fit score 1–10), drafts the reply or a proposal, and drafts cold outreach for prospects you add |
| **FORGE** | Delivery | 3 h | When you mark a lead *won*, writes the delivery plan: solution design, the assistant's instructions, automations, timeline, acceptance tests, GDPR notes. Otherwise builds a reusable template per target trade. Needs AI connected. |

Change the cadences under `agents:` in `business.yaml`. Each agent's standing
instructions (voice, standards, what it must never do) are plain Markdown in
[`prompts/`](prompts/), so edit them to tune an agent.

**Nothing leaves the business without you.** No agent publishes, posts, sends
email or spends money on its own. Their only outside contacts are the AI
provider and, if you set one up, your phone-notification webhook. BEACON and
FLARE pause once six drafts are waiting, so a week away doesn't bury you, or
your budget, in drafts.

## Costs

The agents count every AI call and stop for the day at
`AGENCY_MAX_LLM_CALLS_PER_DAY` (default **40**). Failed calls count too, so a
retry loop can't run up a bill. HQ shows the day's calls and estimated spend.

With an API key on the default model (Claude Opus 5.5):

- **Day one:** about 12–17 calls, roughly **$1–3**, while FORGE builds its seven trade templates.
- **A typical day after that:** about 7 calls plus one per new lead, usually **under $1**.
- **Worst case:** the 40-call cap holds it to roughly **$10–15**.

Add `AGENCY_MODEL=claude-sonnet-5-5` to `.env` to roughly halve those figures.
With Claude Code (option B), usage counts against your Claude plan instead of
being billed per call.

On the API, requests opt into Anthropic's server-side refusal fallback: if a
safety filter declines a request, which security wording occasionally trips,
the API retries it on a recommended fallback model instead of failing.

## Going live (so customers can find you)

The website must be reachable at your own address before ads, posts or emails
can bring anyone in. You need:

1. **A domain**, e.g. `yourbusiness.com`, from any registrar.
2. **A way to reach the agency from the internet.** Either option works:
   - **Keep it on your PC** and connect the domain with
     [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
     (`cloudflared`), which needs no router changes and provides HTTPS.
   - **Run it on a small server** (a $5–10/month VPS) with
     [Caddy](https://caddyserver.com) in front for automatic HTTPS:

     ```
     # /etc/caddy/Caddyfile
     yourbusiness.com {
         reverse_proxy 127.0.0.1:8500
     }
     ```

3. Then add to `.env` and restart:

   ```
   AGENCY_SITE_URL=https://yourbusiness.com
   AGENCY_TRUST_PROXY=1
   ```

   The agents put this address into every link they write, and SENTRY starts
   checking your real HTTPS certificate from outside. Blog posts you published
   earlier are fixed automatically. **But check any email, social post or ad
   drafted before this was set before you send it: its links point to
   `127.0.0.1`, which only works on your PC.**
4. Once HQ is reachable from the internet, keep the long generated HQ token.
   Don't replace it with a short `AGENCY_HQ_TOKEN` of your own.

Optional: get a phone ping for every new lead and every security alert by
setting `AGENCY_WEBHOOK_URL` to a Discord or Slack incoming webhook, or an
[ntfy.sh](https://ntfy.sh) topic URL.

## Before you go live: checklist

- [ ] `business.yaml`: your **brand** (check the name is free to use), **region**,
      real **contact_email**, **currency** and **prices**. Write prices as plain
      numbers: `1500`, not `1 500` or `1500 kr`. SENTRY stays amber until the
      email is real.
- [ ] Read the **FAQ** and the **privacy** text in `business.yaml` as promises
      you'll keep, and change anything that isn't true for you. HQ → Leads →
      **Delete** erases a person's lead and drafts if they ask.
- [ ] Domain and HTTPS, then `AGENCY_SITE_URL` (above).
- [ ] AI connected, and a daily cap you're comfortable with.

## Mac / Linux and the command line

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[agency]"
agency up --open
```

`agency up` serves the website on port 8500 with HQ at `/hq`, and runs all six
agents. It prints the HQ token. Other commands:

```bash
agency status                       # each agent's state, the model, today's spend
agency hq-token                     # print the HQ login token (--rotate for a new one)
agency run sales                    # run one agent now and print what it made
agency run ads --task "Spring campaign for salons, 300 USD budget"
agency up --no-agents               # website + HQ only
```

The `.env` file and the database (`agency.db`) live in the project folder,
whatever folder you run the commands from. Under systemd, the default stop
signal shuts the agency down cleanly.

<details>
<summary>All settings (<code>.env</code>)</summary>

| Setting | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Use the Claude API (option A) |
| `AGENCY_LLM` | `auto` | `auto`, `anthropic`, `claude-cli` or `offline`. Auto picks the API when a key is set, else a logged-in Claude Code, else offline. |
| `AGENCY_MODEL` | `claude-opus-5-5` | e.g. `claude-sonnet-5-5` for lower cost |
| `AGENCY_EFFORT` | `medium` | `low`, `medium` or `high` |
| `AGENCY_MAX_LLM_CALLS_PER_DAY` | `40` | Hard daily cap, failed calls included; resets at midnight |
| `AGENCY_SITE_URL` | `http://127.0.0.1:8500` | Your public address once live |
| `AGENCY_TRUST_PROXY` | off | Set to `1` behind Cloudflare Tunnel, Caddy or nginx |
| `AGENCY_WEBHOOK_URL` | none | Phone pings for leads and alerts |
| `AGENCY_HQ_TOKEN` | generated | Set your own HQ password, or leave unset to have one generated and kept |
| `AGENCY_HOST`, `AGENCY_PORT` | `127.0.0.1`, `8500` | Where the server listens |
| `AGENCY_DB` | `agency.db` | Database file, relative to the project folder |

</details>

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
