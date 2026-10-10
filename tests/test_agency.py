"""The agency: website, HQ, agents, scheduler and model layer — all offline."""
from __future__ import annotations

import http.client
import re
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from agency import analytics, markdown, security
from agency.agents import AGENTS, MAX_PENDING, Context
from agency.config import Settings, load_business, price_label
from agency.llm import LLM, BudgetExceeded, LLMError, normalize, parse_json_lenient
from agency.scheduler import Scheduler
from agency.store import Store
from agency.web import AgencyServer, ensure_hq_token

BROWSER = "Mozilla/5.0 (test browser)"


def make_ctx(tmp_path, **settings) -> Context:
    store = Store(str(tmp_path / "agency.db"))
    s = Settings(db_path=str(tmp_path / "agency.db"), llm_backend="offline",
                 site_url="http://127.0.0.1:1", **settings)
    s = replace(s, hq_token=ensure_hq_token(store, s.hq_token))
    return Context(store=store, llm=LLM(s, store), business=load_business(), settings=s)


@pytest.fixture
def ctx(tmp_path):
    c = make_ctx(tmp_path)
    yield c
    c.store.close()


# -- markdown ----------------------------------------------------------------
def test_markdown_escapes_html_and_unsafe_links():
    out = markdown.render("<script>alert(1)</script>\n\n[x](javascript:alert(1)) "
                          "[y](//evil.example) [z](/\\evil.example) [ok](https://a.example/?a=1&b=2)")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert 'href="javascript' not in out and 'href="//' not in out and 'href="/\\' not in out
    assert '<a href="https://a.example/?a=1&amp;b=2" rel="nofollow noopener">ok</a>' in out


def test_markdown_structure():
    out = markdown.render("# Title\n\n- one\n- **two**\n\n1. first\n\ntext with `code` and *em*")
    assert "<h2>Title</h2>" in out
    assert "<ul><li>one</li>\n<li><strong>two</strong></li>" in out.replace("<ul>\n", "<ul>")
    assert "<ol>" in out and "<code>code</code>" in out and "<em>em</em>" in out


def test_price_label():
    assert price_label({"setup": 1500, "monthly": 150}, "$") == "$1,500 setup + $150/mo"
    assert price_label({"setup": 490}, "€") == "€490 one-time"
    assert price_label({}, "$") == "Custom quote"


# -- model layer ---------------------------------------------------------------
def test_parse_json_lenient_handles_fences_and_prose():
    assert parse_json_lenient('```json\n{"summary": "x", "items": []}\n```')["summary"] == "x"
    assert parse_json_lenient('Sure! {"a": 1} hope that helps')["a"] == 1
    with pytest.raises(LLMError):
        parse_json_lenient("no json here")


def test_normalize_drops_empty_items_and_unknown_kinds():
    out = normalize({"summary": "s", "score": 42, "items": [
        {"kind": "blog_post", "title": "T", "body": "B"},
        {"kind": "weird", "title": "T2", "body": "B2"},
        {"kind": "blog_post", "title": "", "body": "no title"},
        "junk"]})
    assert out["score"] == 10
    assert [i["kind"] for i in out["items"]] == ["blog_post", "report"]


def test_offline_uses_fallback_and_spends_nothing(ctx):
    data = ctx.llm.generate("marketing", "sys", "user", lambda: {"summary": "tmpl", "items": []})
    assert data["summary"] == "tmpl"
    assert ctx.store.llm_usage_since(0)["calls"] == 0


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_response(text='{"summary": "ok", "score": 0, "items": []}', stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=500))


def anthropic_llm(ctx, response, cap=40):
    pytest.importorskip("anthropic")
    s = replace(ctx.settings, llm_backend="anthropic", max_llm_calls_per_day=cap)
    llm = LLM(s, ctx.store)
    messages = FakeMessages(response)
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
    return llm, messages


def test_anthropic_request_shape_and_ledger(ctx):
    llm, messages = anthropic_llm(ctx, fake_response())
    assert llm.generate("sales", "sys", "user", lambda: {})["summary"] == "ok"
    sent = messages.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["effort"] == "medium"
    usage = ctx.store.llm_usage_since(0)
    assert usage["calls"] == 1 and usage["ok"] == 1
    assert usage["cost_usd"] == pytest.approx((1000 * 4 + 500 * 20) / 1e6)


def test_anthropic_refusal_is_an_error_and_still_counted(ctx):
    llm, _ = anthropic_llm(ctx, fake_response(text="", stop_reason="refusal"))
    with pytest.raises(LLMError, match="declined"):
        llm.generate("security", "sys", "user", lambda: {})
    assert ctx.store.llm_usage_since(0) == {"calls": 1, "ok": 0, "cost_usd": 0}


def test_daily_budget_cap_blocks_further_calls(ctx):
    llm, messages = anthropic_llm(ctx, fake_response(), cap=2)
    llm.generate("a", "s", "u", lambda: {})
    llm.generate("a", "s", "u", lambda: {})
    with pytest.raises(BudgetExceeded):
        llm.generate("a", "s", "u", lambda: {})
    assert len(messages.calls) == 2


# -- agents + scheduler ------------------------------------------------------
def test_every_agent_runs_offline_and_files_work(ctx):
    ctx.store.add_lead(name="Ana Lind", email="ana@smile.example", company="Smile Dental",
                       vertical="Dental and medical clinics", message="We miss calls.")
    sched = Scheduler(ctx)
    for key in ("analytics", "sales", "marketing", "ads", "delivery"):
        result = sched.run_agent(key)
        assert result.status == "ok", (key, result.summary)
        assert result.items, key
    pending = {w["agent"] for w in ctx.store.list_work(status="pending")}
    assert pending == {"sales", "marketing", "ads"}  # outward-facing work waits for the owner
    assert {w["kind"] for w in ctx.store.list_work(status="info")} == {"report", "blueprint"}
    lead = ctx.store.list_leads()[0]
    assert lead["score"] and "Template reply" in lead["notes"]


def test_sales_handles_each_lead_once(ctx):
    ctx.store.add_lead(name="Bo", email="bo@x.example")
    sched = Scheduler(ctx)
    assert sched.run_agent("sales").items
    second = sched.run_agent("sales")
    assert second.status == "idle" and not second.items


def test_content_agents_hold_when_drafts_pile_up(ctx):
    for i in range(MAX_PENDING):
        ctx.store.add_work("marketing", None, "blog_post", f"Draft {i}", "body", "pending")
    result = Scheduler(ctx).run_agent("marketing")
    assert result.status == "skipped" and "waiting" in result.summary
    assert ctx.store.pending_count("marketing") == MAX_PENDING


def test_won_lead_gets_a_blueprint(ctx):
    lead_id = ctx.store.add_lead(name="Cy", company="Cy's Cuts", vertical="Hair and beauty salons")
    ctx.store.update_lead(lead_id, status="won")
    result = Scheduler(ctx).run_agent("delivery")
    assert result.items[0]["kind"] == "blueprint" and result.items[0]["lead_id"] == lead_id


def test_tasks_run_first_and_are_marked_done(ctx):
    task_id = ctx.store.add_task("ads", "Plan a spring campaign for salons")
    sched = Scheduler(ctx)
    assert "ads" in sched.due(now=0)
    sched.run_agent("ads")
    task = ctx.store.one("SELECT * FROM tasks WHERE id=?", task_id)
    assert task["status"] == "done"
    assert ctx.store.recent_runs("ads", 1)[0]["trigger"] == "task"


def test_due_respects_cadence_pause_and_run_now(ctx):
    sched = Scheduler(ctx)
    far_future = time.time() + 10 * 86400
    assert "security" in sched.due(now=far_future)
    ctx.store.set_agent("security", enabled=0)
    assert "security" not in sched.due(now=far_future)
    ctx.store.set_agent("security", requested=1)  # Run now works even while paused
    assert "security" in sched.due(now=0)


def test_budget_exhaustion_pauses_instead_of_failing(ctx):
    llm, _ = anthropic_llm(ctx, fake_response(), cap=0)
    ctx.llm = llm
    ctx.store.add_lead(name="Di", email="di@x.example")
    result = Scheduler(ctx).run_agent("sales")
    assert result.status == "skipped" and "budget" in result.summary
    assert ctx.store.get_agent("sales")["status"] == "idle"


def test_analytics_snapshot_and_stale_lead_alert(ctx):
    now = time.time()
    for i in range(3):
        ctx.store.record_pageview("/", "google", utm_source="google", utm_campaign="dentists",
                                  visitor=f"v{i}", ts=now - 60)
    lead_id = ctx.store.add_lead(name="Old", source="google", campaign="dentists")
    ctx.store.execute("UPDATE leads SET ts=? WHERE id=?", now - 2 * 86400, lead_id)
    snap = analytics.snapshot(ctx.store, now)
    assert snap["views_7d"] == 3 and snap["visitors_7d"] == 3 and snap["leads_7d"] == 1
    assert snap["campaigns"][0] == {"campaign": "dentists", "visitors": 3, "leads": 1}
    assert any("24 hours" in a for a in snap["anomalies"])


# -- the website + HQ over real HTTP -----------------------------------------
class Client:
    def __init__(self, port):
        self.port, self.cookie = port, ""

    def request(self, method, path, form=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"User-Agent": BROWSER, **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        body = None
        if form is not None:
            body = urlencode(form)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode()
        conn.close()
        return out

    def login(self, token):
        status, headers, _ = self.request("POST", "/hq/login", {"token": token})
        assert status == 303
        self.cookie = headers["set-cookie"].split(";")[0]

    def csrf(self):
        _, _, body = self.request("GET", "/hq")
        return re.search(r'name="csrf" value="([^"]+)"', body).group(1)


@pytest.fixture
def site(tmp_path):
    c = make_ctx(tmp_path)
    srv = AgencyServer(("127.0.0.1", 0), c)
    c.settings = replace(c.settings, site_url=f"http://127.0.0.1:{srv.server_address[1]}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield c, Client(srv.server_address[1])
    srv.shutdown()
    srv.server_close()
    c.store.close()


def test_public_pages_and_security_headers(site):
    ctx, client = site
    for path in ("/", "/contact", "/blog", "/privacy", "/thanks"):
        status, headers, body = client.request("GET", path)
        assert status == 200, path
        assert "script-src 'self'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["server"] == "agency"
    assert "Mainstreet AI" in client.request("GET", "/")[2]
    for path in ("/.env", "/agency.db", "/static/../config.py", "/blog/nope"):
        assert client.request("GET", path)[0] == 404, path


def test_contact_form_creates_attributed_lead(site):
    ctx, client = site
    client.request("GET", "/?utm_source=google&utm_campaign=dentists")
    status, headers, _ = client.request("POST", "/contact", {
        "name": "Ana", "email": "ana@smile.example", "company": "Smile", "message": "Calls!",
        "vertical": "Dental and medical clinics"})
    assert status == 303 and headers["location"] == "/thanks"
    lead = ctx.store.list_leads()[0]
    assert (lead["source"], lead["campaign"], lead["status"]) == ("google", "dentists", "new")
    assert ctx.store.scalar("SELECT COUNT(*) FROM pageviews") == 1


def test_contact_form_rejects_spam_and_bad_input(site):
    ctx, client = site
    assert client.request("POST", "/contact", {"name": "Bot", "email": "b@x.example", "fax": "1"})[0] == 303
    status, _, body = client.request("POST", "/contact", {"name": "", "email": "nope"})
    assert status == 400 and "valid email" in body
    assert ctx.store.list_leads() == []
    for i in range(5):
        client.request("POST", "/contact", {"name": f"N{i}", "email": f"n{i}@x.example"})
    assert client.request("POST", "/contact", {"name": "N6", "email": "n6@x.example"})[0] == 429


def test_hq_requires_login_and_rate_limits_guessing(site):
    ctx, client = site
    status, headers, _ = client.request("GET", "/hq")
    assert status == 303 and headers["location"] == "/hq/login"
    assert client.request("GET", "/hq/api/status")[0] == 401
    assert client.request("POST", "/hq/action", {"action": "run", "agent": "sales"})[0] == 401
    for _ in range(10):
        assert client.request("POST", "/hq/login", {"token": "wrong"})[0] == 401
    assert client.request("POST", "/hq/login", {"token": ctx.settings.hq_token})[0] == 429


def test_hq_pages_csrf_and_approval_publishes(site):
    ctx, client = site
    client.login(ctx.settings.hq_token)
    assert "httponly" in client.request("POST", "/hq/login", {"token": ctx.settings.hq_token})[1]["set-cookie"].lower()
    work_id = ctx.store.add_work("marketing", None, "blog_post", "Ten Things", "## Hi\nBody", "pending")
    for path in ("/hq", "/hq/approvals", "/hq/work", "/hq/leads", "/hq/security",
                 "/hq/agents/sales", f"/hq/work/{work_id}"):
        status, headers, _ = client.request("GET", path)
        assert status == 200 and headers["cache-control"] == "no-store", path
    assert client.request("POST", "/hq/action", {"action": "approve", "id": work_id})[0] == 403
    status, headers, _ = client.request("POST", "/hq/action", {
        "csrf": client.csrf(), "action": "approve", "id": work_id, "body": "## Hi\nEdited",
        "next": "//evil.example"})
    assert status == 303 and headers["location"] == "/hq"
    post = ctx.store.get_work(work_id)
    assert post["status"] == "published" and post["body"] == "## Hi\nEdited"
    status, _, body = client.request("GET", f"/blog/{post['slug']}")
    assert status == 200 and "Edited" in body


def test_hq_actions_drive_agents_and_leads(site):
    ctx, client = site
    client.login(ctx.settings.hq_token)
    csrf = client.csrf()
    client.request("POST", "/hq/action", {"csrf": csrf, "action": "run", "agent": "ads"})
    client.request("POST", "/hq/action", {"csrf": csrf, "action": "toggle", "agent": "ads"})
    client.request("POST", "/hq/action", {"csrf": csrf, "action": "task", "agent": "sales",
                                          "instruction": "Write a follow-up"})
    client.request("POST", "/hq/action", {"csrf": csrf, "action": "add_prospect", "company": "Cy's Cuts"})
    lead = ctx.store.list_leads()[0]
    client.request("POST", "/hq/action", {"csrf": csrf, "action": "lead_status", "id": lead["id"],
                                          "status": "won"})
    state = ctx.store.get_agent("ads")
    assert state["requested"] == 1 and state["enabled"] == 0
    assert ctx.store.agents_with_queued_tasks() == {"sales"}
    assert ctx.store.get_lead(lead["id"])["status"] == "won"
    status, _, body = client.request("GET", "/hq/api/status")
    assert status == 200 and '"ads"' in body


def test_sentry_passes_against_the_live_site(site):
    ctx, _ = site
    findings = {f["check"]: f for f in security.run_checks(ctx.store, ctx.settings, ctx.business)}
    failing = sorted(k for k, f in findings.items() if not f["ok"])
    assert failing == ["launch_readiness"]  # only the placeholder contact email
    ctx.business["contact_email"] = "hello@mainstreet.example.org"
    assert all(f["ok"] for f in security.run_checks(ctx.store, ctx.settings, ctx.business))


def test_sentry_alerts_once_when_a_check_starts_failing(site):
    ctx, _ = site
    sched = Scheduler(ctx)
    assert not sched.run_agent("security").items
    ctx.settings = replace(ctx.settings, hq_token="short")
    first = sched.run_agent("security")
    assert [i["title"] for i in first.items] == ["Security: hq token strength failing"]
    assert not sched.run_agent("security").items  # still failing, no repeat alert


def test_agents_registry_matches_prompts():
    assert list(AGENTS) == ["marketing", "ads", "security", "analytics", "sales", "delivery"]
    for agent in AGENTS.values():
        assert agent.codename and agent.mission
