"""SENTRY's routine checks: deterministic, free, and run every cycle.

They probe the site the way an outsider would (over HTTP, no cookies), plus
read the abuse counters the web server records. Each check returns
{check, severity, ok, detail}. Severity says how bad a *failure* would be.
"""
from __future__ import annotations

import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .config import Settings
from .store import Store, start_of_day

USER_AGENT = "AgencySentry/1.0"
REQUIRED_HEADERS = {
    "content-security-policy": "Content-Security-Policy",
    "x-content-type-options": "X-Content-Type-Options",
    "x-frame-options": "X-Frame-Options",
    "referrer-policy": "Referrer-Policy",
    "permissions-policy": "Permissions-Policy",
}
SENSITIVE_PATHS = ("/.env", "/.git/config", "/agency.db", "/business.yaml", "/static/../config.py")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # surface 30x instead of following
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _get(url: str, timeout: float) -> tuple[int, dict, float]:
    """(status, lower-cased headers, seconds). Status 0 = unreachable."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t0 = time.monotonic()
    try:
        with _opener.open(req, timeout=timeout) as resp:
            resp.read(65536)
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, time.monotonic() - t0
    except urllib.error.HTTPError as ex:
        return ex.code, {k.lower(): v for k, v in (ex.headers or {}).items()}, time.monotonic() - t0
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}, time.monotonic() - t0


def _finding(check: str, severity: str, ok: bool, detail: str) -> dict:
    return {"check": check, "severity": severity, "ok": ok, "detail": detail}


def _is_local(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def tls_days_left(host: str, port: int = 443, timeout: float = 10) -> float:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return (expires - datetime.now(timezone.utc)).total_seconds() / 86400


def run_checks(store: Store, settings: Settings, business: dict, timeout: float = 10) -> list[dict]:
    base = settings.site_url.rstrip("/")
    parts = urlsplit(base)
    host, https = parts.hostname or "", parts.scheme == "https"
    out: list[dict] = []

    status, headers, secs = _get(base + "/", timeout)
    out.append(_finding("site_up", "critical", status == 200,
                        f"GET / → {status or 'unreachable'} in {secs * 1000:.0f} ms"))

    if https:
        try:
            days = tls_days_left(host, parts.port or 443, timeout)
            sev = "critical" if days < 7 else "high"
            out.append(_finding("tls_certificate", sev, days >= 21,
                                f"certificate expires in {days:.0f} days"))
        except (OSError, ssl.SSLError, ValueError, KeyError) as ex:
            out.append(_finding("tls_certificate", "critical", False, f"TLS handshake failed: {ex}"))
    elif _is_local(host):
        out.append(_finding("tls_certificate", "info", True,
                            "local address over HTTP; check HTTPS once AGENCY_SITE_URL is public"))
    else:
        out.append(_finding("tls_certificate", "high", False,
                            "the public site is served over plain HTTP; put it behind HTTPS "
                            "(Caddy, nginx + Let's Encrypt, or Cloudflare)"))

    if status:
        missing = [name for key, name in REQUIRED_HEADERS.items() if key not in headers]
        if https and "strict-transport-security" not in headers:
            missing.append("Strict-Transport-Security")
        out.append(_finding("security_headers", "medium", not missing,
                            "all present" if not missing else "missing: " + ", ".join(missing)))
        banner = headers.get("server", "")
        out.append(_finding("server_banner", "low", not re.search(r"\d", banner),
                            f"Server: {banner or '(none)'}"))

        hq_status, hq_headers, _ = _get(base + "/hq", timeout)
        api_status, _, _ = _get(base + "/hq/api/status", timeout)
        locked = (hq_status in (302, 303, 401)
                  and "/hq/login" in hq_headers.get("location", "/hq/login")
                  and api_status == 401)
        out.append(_finding("hq_locked", "critical", locked,
                            f"/hq without login → {hq_status}, /hq/api/status → {api_status}"))

        exposed = [p for p in SENSITIVE_PATHS if _get(base + p, timeout)[0] == 200]
        out.append(_finding("sensitive_files", "critical", not exposed,
                            "none exposed" if not exposed else "publicly readable: " + ", ".join(exposed)))

    hour_ago = time.time() - 3600
    fails = store.count_events("login_failed", hour_ago)
    out.append(_finding("login_attempts", "high" if fails >= 20 else "low", fails < 5,
                        f"{fails} failed HQ logins in the last hour"))
    forms = store.count_events("contact", hour_ago)
    out.append(_finding("form_abuse", "medium", forms <= 20,
                        f"{forms} contact-form submissions in the last hour"))

    out.append(_finding("hq_token_strength", "high", len(settings.hq_token) >= 24,
                        f"HQ token is {len(settings.hq_token)} characters (24+ recommended)"))

    used = store.llm_usage_since(start_of_day())["calls"]
    cap = settings.max_llm_calls_per_day
    out.append(_finding("model_spend_guard", "low", used < 0.9 * cap,
                        f"{used}/{cap} model calls used today"))

    placeholder = "example.com" in (business.get("contact_email") or "") or not business.get("contact_email")
    out.append(_finding("launch_readiness", "low", not placeholder,
                        "contact_email in business.yaml is still a placeholder" if placeholder
                        else "business details set"))
    return out


def summary(findings: list[dict]) -> str:
    passing = sum(1 for f in findings if f["ok"])
    failing = [f["check"] for f in findings if not f["ok"]]
    text = f"{passing}/{len(findings)} checks passing"
    if failing:
        text += " — attention: " + ", ".join(failing)
    return text
