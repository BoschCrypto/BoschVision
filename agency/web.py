"""The HTTP server: the public website and the HQ, in one process.

Public pages are open; everything under /hq requires the HQ token. Security
choices worth knowing, because SENTRY checks most of them from outside:

* Every response carries a strict CSP (`'self'` only, no inline script or
  style), nosniff, DENY framing, a referrer policy, a permissions policy, and
  HSTS when served over HTTPS. The Server header carries no version.
* HQ login sets an HttpOnly, SameSite=Strict session cookie derived from the
  token (never the token itself). Every HQ form also carries a CSRF token.
  Login failures are rate-limited per client.
* Visitor analytics are cookie-free: a visitor is a hash of IP + user agent
  with a salt that rotates daily, so no one is tracked across days.
* The contact form has a honeypot field, a per-client hourly limit, length
  limits and a request-size cap.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from . import pages
from .agents import AGENTS, Context
from .config import PACKAGE_DIR
from .scheduler import register_agents
from .store import LEAD_STATUSES, Store, start_of_day

log = logging.getLogger(__name__)

STATIC_DIR = PACKAGE_DIR / "static"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024
CONTACT_LIMIT_PER_HOUR = 5
LOGIN_FAILURES_PER_15M = 10
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python|AgencySentry", re.I)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
SESSION_COOKIE = "hq_session"


def ensure_hq_token(store: Store, configured: str = "", rotate: bool = False) -> str:
    """The configured AGENCY_HQ_TOKEN, else one generated once and kept in the
    database so it survives restarts."""
    if configured:
        return configured
    token = None if rotate else store.get_kv("hq_token")
    if not token:
        token = secrets.token_urlsafe(24)
        store.set_kv("hq_token", token)
    return token


def _derive(token: str, purpose: str) -> str:
    return hmac.new(token.encode(), purpose.encode(), hashlib.sha256).hexdigest()


class AgencyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, ctx: Context):
        self.ctx = ctx
        self.token = ensure_hq_token(ctx.store, ctx.settings.hq_token)
        self.session_value = _derive(self.token, "hq-session")
        self.csrf = _derive(self.token, "hq-csrf")
        secret = ctx.store.get_kv("visitor_secret")
        if not secret:
            secret = secrets.token_hex(16)
            ctx.store.set_kv("visitor_secret", secret)
        self.visitor_secret = secret
        register_agents(ctx.store)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: AgencyServer

    # -- plumbing ----------------------------------------------------------
    def version_string(self) -> str:  # no software versions in the Server header
        return "agency"

    def log_message(self, fmt, *args):  # keep the console for the startup banner
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def ctx(self) -> Context:
        return self.server.ctx

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str) -> None:
        self._method = method
        try:
            url = urlsplit(self.path)
            self._query = parse_qs(url.query)
            path = url.path
            if path == "/hq" or path.startswith("/hq/"):
                self._hq(method, path)
            else:
                self._public(method, path)
        except Exception:
            log.exception("request failed: %s %s", method, self.path)
            self._send(500, pages.message_page(self.ctx.business, "Something went wrong",
                                               "Please try again in a moment."))

    def _https(self) -> bool:
        if self.ctx.settings.trust_proxy and self.headers.get("X-Forwarded-Proto", "") == "https":
            return True
        return self.ctx.settings.site_url.startswith("https://")

    def _client_ip(self) -> str:
        if self.ctx.settings.trust_proxy:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _client_key(self) -> str:
        return hashlib.sha256(f"{self.server.visitor_secret}|{self._client_ip()}".encode()).hexdigest()[:24]

    def _visitor(self) -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        ua = self.headers.get("User-Agent", "")
        raw = f"{self.server.visitor_secret}|{day}|{self._client_ip()}|{ua}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8",
              headers: Optional[dict] = None, private: bool = False) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if private:
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self._method != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str, headers: Optional[dict] = None) -> None:
        self._send(303, b"", headers={"Location": location, **(headers or {})}, private=True)

    def _form(self) -> Optional[dict]:
        """URL-encoded POST body as {field: value}; None if oversized."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length > MAX_BODY:
            return None
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _q(self, name: str) -> str:
        return (self._query.get(name) or [""])[0]

    # -- public site -------------------------------------------------------
    def _public(self, method: str, path: str) -> None:
        b, store = self.ctx.business, self.ctx.store
        if method == "POST":
            if path == "/contact":
                return self._contact_submit()
            return self._send(405, pages.not_found(b), headers={"Allow": "GET"})
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/robots.txt":
            return self._send(200, "User-agent: *\nDisallow: /hq\n", "text/plain; charset=utf-8")
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8", private=True)
        if path == "/favicon.ico":
            return self._redirect("/static/favicon.svg")

        if path == "/":
            html = pages.home(b, store.published_posts(limit=3))
        elif path == "/contact":
            prefill = {}
            if self._q("package"):
                prefill["message"] = f"I'm interested in the {self._q('package')[:80]} package."
            html = pages.contact(b, prefill)
        elif path == "/thanks":
            html = pages.thanks(b)
        elif path == "/privacy":
            html = pages.privacy(b)
        elif path in ("/blog", "/blog/"):
            html = pages.blog_index(b, store.published_posts())
        elif path.startswith("/blog/"):
            post = store.post_by_slug(path[len("/blog/"):].strip("/"))
            if not post:
                return self._send(404, pages.not_found(b))
            html = pages.blog_post(b, post)
        else:
            return self._send(404, pages.not_found(b))
        self._track(path)
        self._send(200, html)

    def _static(self, name: str) -> None:
        # Exact names from one flat directory: no traversal is possible.
        if not re.fullmatch(r"[a-z0-9-]+\.(css|js|svg)", name):
            return self._send(404, pages.not_found(self.ctx.business))
        f = STATIC_DIR / name
        if not f.is_file():
            return self._send(404, pages.not_found(self.ctx.business))
        self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix],
                   headers={"Cache-Control": "public, max-age=3600"})

    def _track(self, path: str) -> None:
        if self._method != "GET" or BOT_UA.search(self.headers.get("User-Agent", "")):
            return
        referrer = self.headers.get("Referer", "")
        ref_host = urlsplit(referrer).hostname or ""
        own_host = urlsplit(self.ctx.settings.site_url).hostname or ""
        utm_source = self._q("utm_source").lower()
        source = utm_source or (ref_host.removeprefix("www.") if ref_host and ref_host != own_host else "direct")
        self.ctx.store.record_pageview(path, source, referrer, utm_source,
                                       self._q("utm_medium").lower(), self._q("utm_campaign").lower(),
                                       self._visitor())

    def _contact_submit(self) -> None:
        b, store = self.ctx.business, self.ctx.store
        form = self._form()
        if form is None:
            return self._send(413, pages.message_page(b, "Too long", "That message is too long."))
        if form.get("fax"):  # honeypot: humans never see this field
            return self._redirect("/thanks")
        key = self._client_key()
        if store.count_events("contact", time.time() - 3600, key) >= CONTACT_LIMIT_PER_HOUR:
            return self._send(429, pages.message_page(
                b, "Thanks, we have your messages", "Please wait a little before sending another."))
        values = {k: (form.get(k) or "").strip() for k in
                  ("name", "email", "company", "website", "vertical", "message")}
        errors = []
        if not values["name"]:
            errors.append("Please tell us your name.")
        if not EMAIL_RE.match(values["email"]):
            errors.append("Please enter a valid email address.")
        limits = {"name": 120, "email": 200, "company": 160, "website": 300, "message": 4000}
        errors += [f"{k.title()} is too long." for k, n in limits.items() if len(values[k]) > n]
        if values["vertical"] not in [*b["verticals"], "Other", ""]:
            values["vertical"] = "Other"
        if errors:
            return self._send(400, pages.contact(b, values, errors))
        touch = store.first_touch(self._visitor(), start_of_day())
        lead_id = store.add_lead(**values, source=touch["source"], campaign=touch["utm_campaign"])
        store.add_event("contact", key)
        who = values["company"] or values["name"]
        self.ctx.notify(f"New lead: {who}", f"{values['name']} <{values['email']}> via "
                                            f"{touch['source']}. SCOUT is drafting the reply (lead #{lead_id}).")
        self._redirect("/thanks")

    # -- HQ ----------------------------------------------------------------
    def _authed(self) -> bool:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and hmac.compare_digest(value, self.server.session_value):
                return True
        return False

    def _hq(self, method: str, path: str) -> None:
        b = self.ctx.business
        if path == "/hq/login":
            return self._login(method)
        if not self._authed():
            if method == "GET" and not path.startswith("/hq/api/"):
                return self._redirect("/hq/login")
            return self._send(401, json.dumps({"error": "login required"}),
                              "application/json", private=True)
        if method == "POST":
            form = self._form()
            if form is None:
                return self._send(413, "too large", "text/plain; charset=utf-8", private=True)
            if not hmac.compare_digest(form.get("csrf", ""), self.server.csrf):
                return self._send(403, "invalid form token: reload the page and try again",
                                  "text/plain; charset=utf-8", private=True)
            if path == "/hq/logout":
                return self._redirect("/hq/login", {"Set-Cookie": self._cookie("", 0)})
            if path == "/hq/action":
                return self._action(form)
            return self._send(404, "not found", "text/plain; charset=utf-8", private=True)
        if path == "/hq/api/status":
            return self._send(200, json.dumps(self._status()), "application/json", private=True)
        self._hq_page(path, b)

    def _cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self._https() else ""
        return f"{SESSION_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def _login(self, method: str) -> None:
        b, store = self.ctx.business, self.ctx.store
        if method != "POST":
            if self._authed():
                return self._redirect("/hq")
            return self._send(200, pages.login(b), private=True)
        key = self._client_key()
        if store.count_events("login_failed", time.time() - 900, key) >= LOGIN_FAILURES_PER_15M:
            return self._send(429, pages.login(b, "Too many attempts. Wait 15 minutes."), private=True)
        form = self._form() or {}
        if hmac.compare_digest(form.get("token", "").strip(), self.server.token):
            return self._redirect("/hq", {"Set-Cookie": self._cookie(self.server.session_value, 30 * 86400)})
        store.add_event("login_failed", key)
        self._send(401, pages.login(b, "That token isn't right."), private=True)

    def _status(self) -> dict:
        states = self.ctx.store.agent_states()
        now = time.time()
        out = {}
        for key in AGENTS:
            st = states.get(key, {})
            out[key] = {
                "status": pages.agent_status(st),
                "last": pages.ago(st.get("last_run_ts"), now),
                "next": pages.until(st.get("next_run_ts"), now) if st.get("enabled", 1) else "paused",
                "summary": st.get("last_error") or st.get("last_summary") or "Hasn't run yet.",
            }
        return {"agents": out, "pending": self.ctx.store.pending_count()}

    def _hq_page(self, path: str, b: dict) -> None:
        store, now = self.ctx.store, time.time()
        csrf = self.server.csrf
        active, title = path, "Overview"
        if path == "/hq":
            body = pages.overview(
                b, states=store.agent_states(), snap=store.get_kv("analytics_snapshot"),
                pending=store.list_work(status="pending", limit=50), runs=store.recent_runs(limit=12),
                findings=store.latest_findings(), usage=store.llm_usage_since(start_of_day()),
                cap=self.ctx.settings.max_llm_calls_per_day, backend=self.ctx.llm.backend,
                csrf=csrf, now=now)
        elif path == "/hq/approvals":
            title, body = "Approvals", pages.approvals(store.list_work(status="pending", limit=200), now)
        elif path == "/hq/work":
            agent = self._q("agent") if self._q("agent") in AGENTS else None
            status = self._q("status") or None
            lead = int(self._q("lead")) if self._q("lead").isdigit() else None
            title = "Work"
            body = pages.work_page(store.list_work(agent=agent, status=status, lead_id=lead, limit=200),
                                   now, agent, status)
        elif m := re.fullmatch(r"/hq/work/(\d+)", path):
            item = store.get_work(int(m.group(1)))
            if not item:
                return self._send(404, "not found", "text/plain; charset=utf-8", private=True)
            lead = store.get_lead(item["lead_id"]) if item["lead_id"] else None
            active = "/hq/approvals" if item["status"] == "pending" else "/hq/work"
            title = item["title"]
            body = pages.work_detail(item, lead, csrf, now, self.ctx.settings.site_url)
        elif path == "/hq/leads":
            title, body = "Leads", pages.leads_page(b, store.list_leads(), csrf, now)
        elif path == "/hq/security":
            title = "Security"
            body = pages.security_page(store.latest_findings(), store.get_agent("security"),
                                       AGENTS["security"].every_minutes(b), csrf, now)
        elif (m := re.fullmatch(r"/hq/agents/([a-z]+)", path)) and m.group(1) in AGENTS:
            key = m.group(1)
            agent = AGENTS[key]
            title, active = agent.codename, "/hq"
            body = pages.agent_page(agent, store.get_agent(key), b, store.recent_runs(key, 15),
                                    store.list_work(agent=key, limit=15), store.list_tasks(key, 8),
                                    csrf, now)
        else:
            return self._send(404, "not found", "text/plain; charset=utf-8", private=True)
        self._send(200, pages.hq_page(b, title, body, active=active, pending=store.pending_count(),
                                      backend=self.ctx.llm.backend, csrf=csrf), private=True)

    def _action(self, form: dict) -> None:
        store = self.ctx.store
        action = form.get("action", "")
        nxt = form.get("next", "/hq")
        if not nxt.startswith("/hq") or "//" in nxt or "\\" in nxt:
            nxt = "/hq"
        agent = form.get("agent", "")
        if action in ("approve", "reject") and form.get("id", "").isdigit():
            store.decide_work(int(form["id"]), approve=action == "approve", body=form.get("body"))
        elif action == "run" and agent in AGENTS:
            store.set_agent(agent, requested=1)
        elif action == "toggle" and agent in AGENTS:
            store.set_agent(agent, enabled=0 if store.get_agent(agent).get("enabled", 1) else 1)
        elif action == "task" and agent in AGENTS and form.get("instruction", "").strip():
            store.add_task(agent, form["instruction"].strip()[:2000])
        elif action == "lead_status" and form.get("id", "").isdigit() and form.get("status") in LEAD_STATUSES:
            store.update_lead(int(form["id"]), status=form["status"])
        elif action == "add_prospect" and form.get("company", "").strip():
            store.add_lead(**{k: (form.get(k) or "").strip()[:4000] for k in
                              ("name", "email", "company", "website", "vertical", "message")},
                           source="manual", status="prospect")
        else:
            return self._send(400, "unknown or incomplete action", "text/plain; charset=utf-8", private=True)
        self._redirect(nxt)


def serve(ctx: Context, host: str, port: int) -> AgencyServer:
    """Bind the server and start it on a background thread; returns it so the
    caller can `shutdown()`."""
    srv = AgencyServer((host, port), ctx)
    threading.Thread(target=srv.serve_forever, name="web", daemon=True).start()
    return srv
