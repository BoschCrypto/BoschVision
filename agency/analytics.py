"""PRISM's numbers: computed exactly from the site's own logs, no model involved.

Traffic is recorded server-side without cookies (see web.py), so these counts
need no consent banner and no third-party script.
"""
from __future__ import annotations

import time
from typing import Optional

from .store import Store

DAY = 86400.0


def snapshot(store: Store, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    s = store.scalar
    week, prev_week, month = now - 7 * DAY, now - 14 * DAY, now - 30 * DAY
    views_7d = s("SELECT COUNT(*) FROM pageviews WHERE ts>=?", week)
    views_prev_7d = s("SELECT COUNT(*) FROM pageviews WHERE ts>=? AND ts<?", prev_week, week)
    visitors_7d = s("SELECT COUNT(DISTINCT visitor) FROM pageviews WHERE ts>=?", week)
    inbound = "source!='manual'"
    leads_7d = s(f"SELECT COUNT(*) FROM leads WHERE ts>=? AND {inbound}", week)
    leads_30d = s(f"SELECT COUNT(*) FROM leads WHERE ts>=? AND {inbound}", month)
    snap = {
        "ts": now,
        "views_24h": s("SELECT COUNT(*) FROM pageviews WHERE ts>=?", now - DAY),
        "views_7d": views_7d,
        "views_prev_7d": views_prev_7d,
        "visitors_7d": visitors_7d,
        "leads_7d": leads_7d,
        "leads_30d": leads_30d,
        "conversion_7d": round(leads_7d / visitors_7d, 4) if visitors_7d else None,
        "top_pages": store.query("SELECT path, COUNT(*) AS views FROM pageviews WHERE ts>=? "
                                 "GROUP BY path ORDER BY views DESC LIMIT 6", week),
        "sources": store.query("SELECT source, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors "
                               "FROM pageviews WHERE ts>=? GROUP BY source ORDER BY views DESC "
                               "LIMIT 8", week),
        "campaigns": store.query(
            "SELECT p.utm_campaign AS campaign, COUNT(DISTINCT p.visitor) AS visitors, "
            "(SELECT COUNT(*) FROM leads l WHERE l.campaign=p.utm_campaign AND l.ts>=?) AS leads "
            "FROM pageviews p WHERE p.ts>=? AND p.utm_campaign!='' GROUP BY p.utm_campaign "
            "ORDER BY visitors DESC LIMIT 8", month, month),
        "lead_sources_30d": store.query(f"SELECT source, COUNT(*) AS leads FROM leads WHERE ts>=? "
                                        f"AND {inbound} GROUP BY source ORDER BY leads DESC", month),
        "pipeline": {r["status"]: r["n"] for r in
                     store.query("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")},
        "won_30d": s("SELECT COUNT(*) FROM leads WHERE status='won' AND updated_ts>=?", month),
        "stale_new_leads": s("SELECT COUNT(*) FROM leads WHERE status='new' AND ts<?", now - DAY),
    }
    snap["anomalies"] = _anomalies(snap)
    return snap


def _anomalies(snap: dict) -> list[str]:
    out = []
    prev, cur = snap["views_prev_7d"], snap["views_7d"]
    if prev >= 50 and cur < 0.5 * prev:
        out.append(f"Traffic fell {100 - round(100 * cur / prev)}% week over week "
                   f"({prev} → {cur} views).")
    if snap["visitors_7d"] >= 100 and snap["leads_7d"] == 0:
        out.append(f"{snap['visitors_7d']} visitors and no leads in 7 days: the offer or the "
                   "contact page is not converting.")
    if snap["stale_new_leads"]:
        out.append(f"{snap['stale_new_leads']} lead(s) have waited over 24 hours for a reply.")
    return out


def summary_line(snap: dict) -> str:
    conv = snap["conversion_7d"]
    conv_txt = f" · {conv:.1%} conversion" if conv is not None else ""
    return (f"{snap['views_7d']} views · {snap['visitors_7d']} visitors · "
            f"{snap['leads_7d']} leads (7 days){conv_txt}")


def brief(snap: Optional[dict]) -> str:
    """The numbers as compact text for another agent's prompt."""
    if not snap:
        return "No traffic data yet: the site has just launched."
    lines = [summary_line(snap)]
    if snap["sources"]:
        lines.append("Traffic sources (7d): " + ", ".join(
            f"{r['source']} {r['visitors']} visitors" for r in snap["sources"]))
    if snap["top_pages"]:
        lines.append("Top pages (7d): " + ", ".join(
            f"{r['path']} ({r['views']})" for r in snap["top_pages"]))
    if snap["campaigns"]:
        lines.append("Campaigns (30d): " + ", ".join(
            f"{r['campaign']}: {r['visitors']} visitors, {r['leads']} leads"
            for r in snap["campaigns"]))
    if snap["lead_sources_30d"]:
        lines.append("Leads by source (30d): " + ", ".join(
            f"{r['source']} {r['leads']}" for r in snap["lead_sources_30d"]))
    if snap["pipeline"]:
        lines.append("Pipeline: " + ", ".join(f"{k} {v}" for k, v in sorted(snap["pipeline"].items())))
    lines += [f"Alert: {a}" for a in snap["anomalies"]]
    return "\n".join(lines)


def report_markdown(snap: dict) -> str:
    conv = snap["conversion_7d"]
    wow = ""
    if snap["views_prev_7d"]:
        change = (snap["views_7d"] - snap["views_prev_7d"]) / snap["views_prev_7d"]
        wow = f" ({change:+.0%} vs the week before)"
    out = [
        "## Last 7 days",
        f"- **Page views:** {snap['views_7d']}{wow}",
        f"- **Unique visitors:** {snap['visitors_7d']}",
        f"- **New inbound leads:** {snap['leads_7d']}",
        f"- **Visitor → lead conversion:** {conv:.1%}" if conv is not None else
        "- **Visitor → lead conversion:** n/a (no visitors yet)",
        f"- **Won in the last 30 days:** {snap['won_30d']}",
    ]
    if snap["sources"]:
        out += ["", "## Where visitors came from"]
        out += [f"- {r['source']}: {r['visitors']} visitors, {r['views']} views" for r in snap["sources"]]
    if snap["top_pages"]:
        out += ["", "## Most-viewed pages"]
        out += [f"- `{r['path']}`: {r['views']}" for r in snap["top_pages"]]
    if snap["campaigns"]:
        out += ["", "## Campaigns (30 days)"]
        out += [f"- {r['campaign']}: {r['visitors']} visitors → {r['leads']} leads"
                for r in snap["campaigns"]]
    if snap["pipeline"]:
        out += ["", "## Pipeline"]
        out += [f"- {k}: {v}" for k, v in sorted(snap["pipeline"].items())]
    if snap["anomalies"]:
        out += ["", "## Needs attention"]
        out += [f"- {a}" for a in snap["anomalies"]]
    return "\n".join(out)
