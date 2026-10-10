"""Settings for the agency: the business brief and the runtime knobs.

Two sources, kept apart on purpose:

* ``business.yaml`` — *what* the business is (brand, offers, prices, who it
  sells to). Safe to commit; the website and every agent read it.
* environment / ``.env`` — *how* it runs (ports, model, budget, the HQ
  token). Secrets live here and never in the YAML.

    AGENCY_DB                     SQLite file (default agency.db)
    AGENCY_HOST / AGENCY_PORT     bind address (default 127.0.0.1:8500)
    AGENCY_SITE_URL               public URL; links and SENTRY's checks use it
    AGENCY_HQ_TOKEN               HQ login token (generated and stored if unset)
    AGENCY_LLM                    auto | anthropic | claude-cli | offline
    AGENCY_MODEL                  default claude-opus-5-5
    AGENCY_EFFORT                 low | medium | high (default medium)
    AGENCY_MAX_LLM_CALLS_PER_DAY  hard daily cap on model calls (default 40)
    AGENCY_WEBHOOK_URL            Discord/Slack/ntfy URL for new leads + alerts
    AGENCY_TRUST_PROXY            1 when behind a reverse proxy (X-Forwarded-For)
    AGENCY_BUSINESS               path to an alternative business.yaml
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_BUSINESS = PACKAGE_DIR / "business.yaml"


def load_business(path: Optional[str] = None) -> dict:
    """Read business.yaml, filling the keys the site and agents rely on so a
    trimmed-down file still renders."""
    p = Path(path or os.environ.get("AGENCY_BUSINESS") or DEFAULT_BUSINESS)
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("brand", "Your Agency")
    data.setdefault("tagline", "AI assistants and automations for local businesses")
    data.setdefault("pitch", "")
    data["region"] = (data.get("region") or "").strip()
    data.setdefault("contact_email", "")
    data.setdefault("currency", "$")
    for key in ("services", "packages", "process", "verticals", "faq"):
        data[key] = data.get(key) or []
    data["ads"] = data.get("ads") or {}
    data["agents"] = data.get("agents") or {}
    return data


def price_label(pkg: dict, currency: str) -> str:
    """'$1,500 setup + $150/mo', '$490 one-time', or '$250/mo'."""
    setup, monthly = pkg.get("setup"), pkg.get("monthly")
    if setup and monthly:
        return f"{currency}{setup:,} setup + {currency}{monthly:,}/mo"
    if setup:
        return f"{currency}{setup:,} one-time"
    if monthly:
        return f"{currency}{monthly:,}/mo"
    return "Custom quote"


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: str = "agency.db"
    host: str = "127.0.0.1"
    port: int = 8500
    site_url: str = ""
    hq_token: str = ""
    llm_backend: str = "auto"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_llm_calls_per_day: int = 40
    webhook_url: str = ""
    trust_proxy: bool = False

    @classmethod
    def from_env(cls, env: Optional[dict] = None, **overrides) -> "Settings":
        e = env if env is not None else os.environ
        s = cls(
            db_path=e.get("AGENCY_DB") or cls.db_path,
            host=e.get("AGENCY_HOST") or cls.host,
            port=_int(e.get("AGENCY_PORT"), cls.port),
            site_url=(e.get("AGENCY_SITE_URL") or "").rstrip("/"),
            hq_token=e.get("AGENCY_HQ_TOKEN") or "",
            llm_backend=(e.get("AGENCY_LLM") or cls.llm_backend).strip().lower(),
            model=e.get("AGENCY_MODEL") or cls.model,
            effort=e.get("AGENCY_EFFORT", cls.effort).strip().lower(),
            max_llm_calls_per_day=_int(e.get("AGENCY_MAX_LLM_CALLS_PER_DAY"),
                                       cls.max_llm_calls_per_day),
            webhook_url=(e.get("AGENCY_WEBHOOK_URL") or "").strip(),
            trust_proxy=(e.get("AGENCY_TRUST_PROXY") or "").lower() in ("1", "true", "yes"),
        )
        s = replace(s, **{k: v for k, v in overrides.items() if v is not None})
        if not s.site_url:
            # A wildcard bind is reachable on loopback, which is where SENTRY
            # should knock when no public URL has been configured yet.
            local = "127.0.0.1" if s.host in ("0.0.0.0", "::", "") else s.host
            s = replace(s, site_url=f"http://{local}:{s.port}")
        return s
