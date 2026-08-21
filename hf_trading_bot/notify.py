"""Tell the principal when an order actually reaches the broker.

Auto-execute places trades with nobody watching, so a placement that is only
written to a database row is effectively silent. This module is the seam that
makes it audible.

Two independent channels, both best-effort — a notification failure must never
take down a trade that already went through:

* **Desktop toast** (Windows), zero configuration. Useful when you are at the
  machine the bot runs on.
* **Webhook** (``VANTRIX_WEBHOOK_URL``), for anything that reaches your phone —
  a Discord/Slack incoming webhook, or an ntfy.sh topic. Posted as JSON with
  the keys those services accept, so one URL works for all three.

Nothing here decides anything; it only reports what already happened.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

WEBHOOK_ENV = "VANTRIX_WEBHOOK_URL"
_TIMEOUT = 10


def webhook_url(env: Optional[dict] = None) -> Optional[str]:
    e = env if env is not None else os.environ
    return (e.get(WEBHOOK_ENV) or "").strip() or None


def _post_webhook(url: str, title: str, body: str) -> bool:
    """POST to a Discord / Slack / ntfy-style incoming webhook.

    Each service reads a different key, so we send all three: Discord uses
    ``content``, Slack uses ``text``, ntfy uses ``message`` (plus ``title``).
    Extra keys are ignored by each, which keeps this one code path for all."""
    text = f"{title}\n{body}"
    payload = json.dumps({
        "content": text,      # Discord
        "text": text,         # Slack
        "title": title,       # ntfy
        "message": body,      # ntfy
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        log.warning("notification webhook failed: %s", e)
        return False


def _toast(title: str, body: str) -> bool:
    """Windows desktop toast via PowerShell. No-op on other platforms.

    Uses the shell's own toast API rather than a dependency, so there is
    nothing to install. Quotes in the text are escaped for PowerShell.
    """
    if not sys.platform.startswith("win"):
        return False

    def esc(s: str) -> str:
        return s.replace("'", "''")

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x = $t.GetElementsByTagName('text');"
        f"$x.Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) > $null;"
        f"$x.Item(1).AppendChild($t.CreateTextNode('{esc(body)}')) > $null;"
        "$n = [Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'VANTRIX').Show($n);"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True, timeout=20, check=False,
        )
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("desktop toast failed: %s", e)
        return False


def notify(title: str, body: str, *, env: Optional[dict] = None) -> dict:
    """Send on every configured channel. Returns which ones succeeded.

    Never raises: notification is a side effect of a trade that has already
    happened, and must not be able to break the caller.
    """
    result = {"toast": False, "webhook": False}
    try:
        result["toast"] = _toast(title, body)
    except Exception as e:  # noqa: BLE001
        log.warning("toast channel error: %s", e)
    url = webhook_url(env)
    if url:
        try:
            result["webhook"] = _post_webhook(url, title, body)
        except Exception as e:  # noqa: BLE001
            log.warning("webhook channel error: %s", e)
    return result


def order_placed_message(proposal, *, order_id: str, broker: str,
                         auto: bool) -> tuple[str, str]:
    """(title, body) for a placed order. `proposal` is an execution.ProposedOrder."""
    how = "AUTO-EXECUTED" if auto else "Order placed"
    title = f"VANTRIX · {how}: {proposal.side.upper()} {proposal.symbol}"
    lines = [proposal.summary(), f"broker: {broker} (paper) · id {order_id}"]
    if getattr(proposal, "rationale", ""):
        lines.append(proposal.rationale)
    if auto:
        lines.append("Placed with no approval click — auto-execute is on.")
    return title, "\n".join(lines)
