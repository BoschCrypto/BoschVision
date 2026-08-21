"""Trade notifications. No real network/toast — the HTTP and subprocess seams
are monkeypatched, and _toast is stubbed directly to avoid platform branching."""
from hf_trading_bot import notify
from hf_trading_bot.execution import ProposedOrder


def test_webhook_url_reads_env():
    assert notify.webhook_url({}) is None
    assert notify.webhook_url({"VANTRIX_WEBHOOK_URL": " https://x/hook "}) == "https://x/hook"


def test_order_placed_message_manual():
    p = ProposedOrder(symbol="AAPL", side="buy", qty=2, est_price=190.0,
                      est_notional=380.0, rationale="SNIPER: breakout above 188")
    title, body = notify.order_placed_message(p, order_id="ord1", broker="paper", auto=False)
    assert "Order placed" in title and "AAPL" in title
    assert "ord1" in body and "SNIPER" in body
    assert "no approval click" not in body


def test_order_placed_message_auto():
    p = ProposedOrder(symbol="BTC/USD", side="buy", qty=0.01, est_price=60000.0,
                      est_notional=600.0)
    title, body = notify.order_placed_message(p, order_id="ord2", broker="alpaca", auto=True)
    assert "AUTO-EXECUTED" in title
    assert "no approval click" in body


def test_notify_calls_both_channels(monkeypatch):
    calls = {}

    def fake_toast(t, b):
        calls["toast"] = (t, b)
        return True

    def fake_webhook(url, t, b):
        calls["webhook"] = (url, t, b)
        return True

    monkeypatch.setattr(notify, "_toast", fake_toast)
    monkeypatch.setattr(notify, "_post_webhook", fake_webhook)
    result = notify.notify("T", "B", env={"VANTRIX_WEBHOOK_URL": "https://x/hook"})
    assert result == {"toast": True, "webhook": True}
    assert calls["toast"] == ("T", "B")
    assert calls["webhook"] == ("https://x/hook", "T", "B")


def test_notify_skips_webhook_when_unconfigured(monkeypatch):
    monkeypatch.setattr(notify, "_toast", lambda t, b: True)
    result = notify.notify("T", "B", env={})
    assert result == {"toast": True, "webhook": False}


def test_notify_never_raises_on_channel_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify, "_toast", boom)
    monkeypatch.setattr(notify, "_post_webhook", boom)
    result = notify.notify("T", "B", env={"VANTRIX_WEBHOOK_URL": "https://x/hook"})
    assert result == {"toast": False, "webhook": False}


def test_post_webhook_sends_all_three_keys(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        import json
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    ok = notify._post_webhook("https://x/hook", "Title", "Body")
    assert ok is True
    assert captured["body"]["content"] == "Title\nBody"
    assert captured["body"]["text"] == "Title\nBody"
    assert captured["body"]["title"] == "Title"
    assert captured["body"]["message"] == "Body"
