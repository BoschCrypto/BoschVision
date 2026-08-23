"""Jupiter aggregator client — base URL configuration and response parsing.
No network — HTTP is stubbed. Exists specifically so a wrong/stale base
URL (the DEFAULT_BASE_URL migration from the deprecated quote-api.jup.ag
to api.jup.ag/swap/v1 was only caught via live testing, not a test here)
gets flagged immediately next time, not after another live-testing round."""
import json
import urllib.error

import pytest

from hf_trading_bot import jupiter


class _Resp:
    def __init__(self, payload):
        self._p = payload
    def read(self):
        return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_base_url_default_and_override():
    assert jupiter.base_url({}) == jupiter.DEFAULT_BASE_URL
    assert jupiter.base_url({"JUPITER_BASE_URL": "https://x"}) == "https://x"


def test_quote_builds_request_against_the_configured_base_url(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp({"outAmount": "1000", "priceImpactPct": "0.01"})
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", fake_urlopen)

    jupiter.quote("IN", "OUT", 1_000_000, env={})
    assert captured["url"].startswith(jupiter.DEFAULT_BASE_URL + "/quote?")


def test_quote_raises_on_no_route(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({}))
    with pytest.raises(jupiter.JupiterError, match="no route"):
        jupiter.quote("IN", "OUT", 1_000_000)


def test_quote_raises_jupiter_error_when_unreachable(monkeypatch):
    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", raise_url_error)
    with pytest.raises(jupiter.JupiterError, match="unreachable"):
        jupiter.quote("IN", "OUT", 1_000_000)


def test_swap_transaction_returns_the_base64_transaction(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({"swapTransaction": "BASE64TX"}))
    assert jupiter.swap_transaction({"outAmount": "1000"}, "PUBKEY") == "BASE64TX"


def test_swap_transaction_raises_when_missing_from_response(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({}))
    with pytest.raises(jupiter.JupiterError, match="no swapTransaction"):
        jupiter.swap_transaction({"outAmount": "1000"}, "PUBKEY")


def test_price_impact_pct_converts_fraction_to_percent():
    assert jupiter.price_impact_pct({"priceImpactPct": "0.015"}) == pytest.approx(1.5)


def test_price_impact_pct_defaults_to_zero_when_missing():
    assert jupiter.price_impact_pct({}) == 0.0


# --- API key header ---------------------------------------------------
# Live testing: an unauthenticated request to api.jup.ag is capped at
# ~0.5 requests/second -- one autotrade cycle blows past that easily,
# surfacing as "Jupiter quote HTTP 429: ... Too many requests". Jupiter
# expects the key as an x-api-key header once JUPITER_API_KEY is set.

def test_quote_sends_no_api_key_header_when_unset(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = req.headers
        return _Resp({"outAmount": "1000", "priceImpactPct": "0.01"})
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", fake_urlopen)

    jupiter.quote("IN", "OUT", 1_000_000, env={})
    assert "X-api-key" not in captured["headers"]


def test_quote_sends_api_key_header_when_set(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = req.headers
        return _Resp({"outAmount": "1000", "priceImpactPct": "0.01"})
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", fake_urlopen)

    jupiter.quote("IN", "OUT", 1_000_000, env={"JUPITER_API_KEY": "test-key-123"})
    assert captured["headers"]["X-api-key"] == "test-key-123"


def test_swap_transaction_sends_api_key_header_when_set(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = req.headers
        return _Resp({"swapTransaction": "BASE64TX"})
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", fake_urlopen)

    jupiter.swap_transaction({"outAmount": "1000"}, "PUBKEY",
                             env={"JUPITER_API_KEY": "test-key-123"})
    assert captured["headers"]["X-api-key"] == "test-key-123"
