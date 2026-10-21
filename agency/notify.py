"""Phone pings for the moments that matter: a new lead, a security alert.

Set AGENCY_WEBHOOK_URL to a Discord or Slack incoming webhook, or an ntfy.sh
topic URL. One JSON body carries the key each of them reads. Sent on a
background thread and best-effort: a failed ping never breaks a request or a run.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Callable

log = logging.getLogger(__name__)


def _post(url: str, title: str, body: str) -> None:
    text = f"{title}\n{body}"
    payload = json.dumps({"content": text, "text": text, "title": title, "message": body}).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except (urllib.error.URLError, OSError) as ex:
        log.warning("notification webhook failed: %s", ex)


def make_notifier(url: str) -> Callable[[str, str], None]:
    if not url:
        return lambda title, body: None

    def notify(title: str, body: str) -> None:
        threading.Thread(target=_post, args=(url, title, body), daemon=True).start()

    return notify
