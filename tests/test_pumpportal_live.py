"""PumpPortal buyer-diversity feed's pure/synchronous parts: message
dispatch and the thread-safe buyer_stats()/status() read side. No real
network/websocket connection — _subscribe_once is never exercised here."""
import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest

from hf_trading_bot import pumpportal_live


# --- ws_url ---------------------------------------------------------------

def test_ws_url_default_when_nothing_set():
    assert pumpportal_live.ws_url({}) == pumpportal_live.PUMPPORTAL_WS_URL


def test_ws_url_explicit_override():
    assert pumpportal_live.ws_url({"PUMPPORTAL_WS_URL": "wss://custom"}) == "wss://custom"


# --- _track / _record_buy / buyer_stats / status ---------------------------

def test_track_returns_true_for_new_mint_false_for_repeat():
    f = pumpportal_live.PumpPortalFeed()
    assert f._track("M1") is True
    assert f._track("M1") is False


def test_track_respects_max_watched_mints_cap():
    f = pumpportal_live.PumpPortalFeed(env={"PUMPPORTAL_MAX_WATCHED_MINTS": "2"})
    assert f._track("M1") is True
    assert f._track("M2") is True
    assert f._track("M3") is False


def test_max_watched_mints_default_and_override():
    assert pumpportal_live.max_watched_mints({}) == pumpportal_live.DEFAULT_MAX_WATCHED_MINTS
    assert pumpportal_live.max_watched_mints({"PUMPPORTAL_MAX_WATCHED_MINTS": "10"}) == 10


def test_max_watched_mints_invalid_falls_back_to_default():
    assert pumpportal_live.max_watched_mints(
        {"PUMPPORTAL_MAX_WATCHED_MINTS": "garbage"}) == pumpportal_live.DEFAULT_MAX_WATCHED_MINTS


def test_watch_ttl_s_default_and_override():
    assert pumpportal_live.watch_ttl_s({}) == pumpportal_live.DEFAULT_WATCH_TTL_S
    assert pumpportal_live.watch_ttl_s({"PUMPPORTAL_WATCH_TTL_S": "60"}) == 60.0


def test_watch_ttl_s_invalid_falls_back_to_default():
    assert pumpportal_live.watch_ttl_s(
        {"PUMPPORTAL_WATCH_TTL_S": "garbage"}) == pumpportal_live.DEFAULT_WATCH_TTL_S


def test_record_buy_counts_and_dedupes_buyers():
    f = pumpportal_live.PumpPortalFeed()
    f._track("M1")
    f._record_buy("M1", "walletA")
    f._record_buy("M1", "walletA")   # same wallet buying twice
    f._record_buy("M1", "walletB")
    stats = f.buyer_stats("M1")
    assert stats["unique_buyers"] == 2
    assert stats["buy_count"] == 3


def test_record_buy_on_untracked_mint_is_a_noop():
    f = pumpportal_live.PumpPortalFeed()
    f._record_buy("NEVER_TRACKED", "walletA")
    assert f.buyer_stats("NEVER_TRACKED") is None
    assert f.status()["trades_seen"] == 0


def test_record_buy_with_no_trader_still_counts_the_trade():
    f = pumpportal_live.PumpPortalFeed()
    f._track("M1")
    f._record_buy("M1", None)
    stats = f.buyer_stats("M1")
    assert stats["buy_count"] == 1
    assert stats["unique_buyers"] == 0


def test_buyer_stats_none_for_unknown_mint():
    f = pumpportal_live.PumpPortalFeed()
    assert f.buyer_stats("UNKNOWN") is None


def test_status_starts_disconnected():
    f = pumpportal_live.PumpPortalFeed()
    st = f.status()
    assert st["connected"] is False
    assert st["last_error"] is None
    assert st["mints_tracked"] == 0


def test_status_reflects_tracked_mints():
    f = pumpportal_live.PumpPortalFeed()
    f._track("M1")
    f._track("M2")
    assert f.status()["mints_tracked"] == 2


def test_pop_expired_evicts_old_mints_and_returns_them():
    f = pumpportal_live.PumpPortalFeed()
    f._track("OLD")
    f._watched["OLD"]["first_seen"] = time.time() - f._watch_ttl_s - 1
    f._track("FRESH")
    expired = f._pop_expired()
    assert expired == ["OLD"]
    assert f.buyer_stats("OLD") is None
    assert f.buyer_stats("FRESH") is not None


# --- _handle_message (async, but no network) --------------------------------

class _FakeWS:
    def __init__(self):
        self.sent = []
    async def send(self, msg):
        self.sent.append(msg)


def test_handle_message_create_subscribes_to_trades():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message('{"txType": "create", "mint": "NEWMINT"}', ws))
    assert f.buyer_stats("NEWMINT") is not None
    assert any("subscribeTokenTrade" in s and "NEWMINT" in s for s in ws.sent)


def test_handle_message_buy_records_against_tracked_mint():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message('{"txType": "create", "mint": "M1"}', ws))
    asyncio.run(f._handle_message(
        '{"txType": "buy", "mint": "M1", "traderPublicKey": "walletA"}', ws))
    stats = f.buyer_stats("M1")
    assert stats["unique_buyers"] == 1
    assert stats["buy_count"] == 1


def test_handle_message_ignores_malformed_json():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message("not json", ws))   # must not raise
    assert f.status()["mints_tracked"] == 0


def test_handle_message_ignores_message_without_mint():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message('{"txType": "create"}', ws))
    assert f.status()["mints_tracked"] == 0


def test_handle_message_does_not_resubscribe_for_already_tracked_mint():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message('{"txType": "create", "mint": "M1"}', ws))
    asyncio.run(f._handle_message('{"txType": "create", "mint": "M1"}', ws))
    subscribe_sends = [s for s in ws.sent if "subscribeTokenTrade" in s]
    assert len(subscribe_sends) == 1


def test_handle_message_resends_full_watch_list_not_just_new_mint():
    # The real bug found in live testing: subscribing with only the newest
    # mint's key, if PumpPortal's API replaces rather than adds to the
    # subscription, silently drops every previously-tracked mint. Each new
    # subscribeTokenTrade call must carry the FULL current set.
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message('{"txType": "create", "mint": "M1"}', ws))
    asyncio.run(f._handle_message('{"txType": "create", "mint": "M2"}', ws))
    last_subscribe = json.loads([s for s in ws.sent if "subscribeTokenTrade" in s][-1])
    assert set(last_subscribe["keys"]) == {"M1", "M2"}


# --- _subscribe_once: connect() gets the widened ping_timeout ---------------

def test_subscribe_once_sends_subscribe_new_token_and_widens_ping_timeout(monkeypatch):
    captured = {}

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send(self, msg):
            self.sent.append(msg)
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration

    @asynccontextmanager
    async def fake_connect(url, **kwargs):
        captured["kwargs"] = kwargs
        ws = FakeWS()
        yield ws
        captured["sent"] = ws.sent

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)
    f = pumpportal_live.PumpPortalFeed()
    asyncio.run(f._subscribe_once())
    assert captured["kwargs"].get("ping_timeout") == pumpportal_live._PING_TIMEOUT_S
    assert any("subscribeNewToken" in s for s in captured["sent"])
    assert f.status()["connected"] is True
