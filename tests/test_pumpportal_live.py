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


# --- _record_creation / recent_new_coins: new-coin detection ---------------

def test_record_creation_captures_symbol_and_name():
    f = pumpportal_live.PumpPortalFeed()
    f._record_creation("M1", {"symbol": "FOO", "name": "Foo Coin"})
    coins = f.recent_new_coins(10)
    assert coins[0]["address"] == "M1"
    assert coins[0]["symbol"] == "FOO"
    assert coins[0]["name"] == "Foo Coin"
    assert coins[0]["source"] == "pumpportal"


def test_record_creation_leaves_market_data_none_for_later_enrichment():
    # Deliberate: market_cap_usd/price_usd/has_social_links come from the
    # already-verified pumpfun_data.get_coin() enrichment fetch, not a guess
    # at PumpPortal's numeric field semantics.
    f = pumpportal_live.PumpPortalFeed()
    f._record_creation("M1", {"symbol": "FOO", "marketCapSol": 12.5})
    coin = f.recent_new_coins(1)[0]
    assert coin["market_cap_usd"] is None
    assert coin["price_usd"] is None
    assert coin["has_social_links"] is None


def test_recent_new_coins_newest_first():
    f = pumpportal_live.PumpPortalFeed()
    f._record_creation("M1", {"symbol": "A"})
    f._record_creation("M2", {"symbol": "B"})
    f._record_creation("M3", {"symbol": "C"})
    assert [c["address"] for c in f.recent_new_coins(10)] == ["M3", "M2", "M1"]


def test_recent_new_coins_respects_limit():
    f = pumpportal_live.PumpPortalFeed()
    for i in range(5):
        f._record_creation(f"M{i}", {"symbol": f"S{i}"})
    assert len(f.recent_new_coins(2)) == 2


def test_status_tracks_creations_seen():
    f = pumpportal_live.PumpPortalFeed()
    assert f.status()["creations_seen"] == 0
    f._record_creation("M1", {"symbol": "A"})
    assert f.status()["creations_seen"] == 1


def test_handle_message_create_records_a_new_coin_candidate():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    asyncio.run(f._handle_message(
        '{"txType": "create", "mint": "NEWMINT", "symbol": "NEW", "name": "New Coin"}', ws))
    coins = f.recent_new_coins(1)
    assert coins[0]["address"] == "NEWMINT"
    assert coins[0]["symbol"] == "NEW"


def test_pop_expired_evicts_old_mints_and_returns_them():
    f = pumpportal_live.PumpPortalFeed()
    f._track("OLD")
    f._watched["OLD"]["first_seen"] = time.time() - f._watch_ttl_s - 1
    f._track("FRESH")
    expired = f._pop_expired()
    assert expired == ["OLD"]
    assert f.buyer_stats("OLD") is None
    assert f.buyer_stats("FRESH") is not None


# --- pin/unpin/last_market_cap_sol: live price for held positions ----------
# A scalp position is very often held longer than PUMPPORTAL_WATCH_TTL_S
# (default 180s) -- these fix the real bug where a held position would
# silently stop receiving trade updates once its normal tracking window
# expired, with no visible warning that price monitoring had gone stale.

def test_pin_adds_a_brand_new_mint_and_flags_a_resubscribe():
    f = pumpportal_live.PumpPortalFeed()
    assert f.buyer_stats("HELD1") is None
    f.pin("HELD1")
    assert f.buyer_stats("HELD1") is not None
    assert f._pop_needs_resubscribe() is True


def test_pin_survives_the_max_watched_mints_cap():
    # A held position must never fail to get a tracking slot because
    # scanning already filled the candidate cap -- capacity limits are for
    # candidates, never for money already deployed.
    f = pumpportal_live.PumpPortalFeed(env={"PUMPPORTAL_MAX_WATCHED_MINTS": "1"})
    f._track("CANDIDATE")
    f.pin("HELD1")
    assert f.buyer_stats("HELD1") is not None


def test_pinned_mint_is_exempt_from_ttl_expiry():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    f._watched["HELD1"]["first_seen"] = time.time() - f._watch_ttl_s - 1
    expired = f._pop_expired()
    assert "HELD1" not in expired
    assert f.buyer_stats("HELD1") is not None


def test_unpin_lets_the_mint_expire_normally_again():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    f.unpin("HELD1")
    f._watched["HELD1"]["first_seen"] = time.time() - f._watch_ttl_s - 1
    expired = f._pop_expired()
    assert expired == ["HELD1"]


def test_pin_on_already_tracked_mint_does_not_reset_its_state():
    f = pumpportal_live.PumpPortalFeed()
    f._track("M1")
    f._record_buy("M1", "walletA")
    f.pin("M1")
    stats = f.buyer_stats("M1")
    assert stats["buy_count"] == 1   # not wiped out by pin()


def test_pop_needs_resubscribe_clears_after_reading():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    assert f._pop_needs_resubscribe() is True
    assert f._pop_needs_resubscribe() is False


def test_last_market_cap_sol_none_before_any_trade():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    assert f.last_market_cap_sol("HELD1") is None


def test_last_market_cap_sol_none_for_unwatched_mint():
    f = pumpportal_live.PumpPortalFeed()
    assert f.last_market_cap_sol("UNKNOWN") is None


def test_record_price_captures_market_cap_and_age():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    f._record_price("HELD1", 42.5)
    reading = f.last_market_cap_sol("HELD1")
    assert reading["market_cap_sol"] == 42.5
    assert reading["age_s"] < 1.0


def test_record_price_ignores_non_numeric_values():
    f = pumpportal_live.PumpPortalFeed()
    f.pin("HELD1")
    f._record_price("HELD1", "not-a-number")
    assert f.last_market_cap_sol("HELD1") is None


def test_record_price_on_untracked_mint_is_a_noop():
    f = pumpportal_live.PumpPortalFeed()
    f._record_price("NEVER_TRACKED", 10.0)
    assert f.last_market_cap_sol("NEVER_TRACKED") is None


def test_handle_message_sell_also_records_price():
    # A held position's price should update on a sell just as much as a
    # buy -- both carry marketCapSol, and a sell is just as much a real
    # price tick.
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    f.pin("M1")
    asyncio.run(f._handle_message(
        json.dumps({"txType": "sell", "mint": "M1", "traderPublicKey": "walletA",
                   "marketCapSol": 88.0}), ws))
    reading = f.last_market_cap_sol("M1")
    assert reading["market_cap_sol"] == 88.0
    # A sell must not be counted as a buy for buyer-diversity purposes.
    assert f.buyer_stats("M1")["buy_count"] == 0


def test_handle_message_buy_also_records_price():
    f = pumpportal_live.PumpPortalFeed()
    ws = _FakeWS()
    f.pin("M1")
    asyncio.run(f._handle_message(
        json.dumps({"txType": "buy", "mint": "M1", "traderPublicKey": "walletA",
                   "marketCapSol": 55.0}), ws))
    assert f.last_market_cap_sol("M1")["market_cap_sol"] == 55.0
    assert f.buyer_stats("M1")["buy_count"] == 1


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


def test_subscribe_once_resubscribes_a_pinned_mint_on_the_next_message(monkeypatch):
    # pin() runs on a different thread (run_exit_check) than the async
    # WebSocket loop -- it can only set a flag, not send over the socket
    # directly. The loop must pick that flag up and resubscribe on the very
    # next message it processes, not wait for an unrelated create/expiry
    # event to incidentally trigger one.
    captured = {}

    class FakeWS:
        def __init__(self):
            self.sent = []
            # A "buy" on an unrelated, already-tracked mint -- deliberately
            # NOT a "create" or an expiry event, neither of which would
            # prove the flag-driven path works (both already trigger their
            # own resubscribe for unrelated reasons).
            self._messages = iter(['{"txType": "buy", "mint": "ALREADY_TRACKED", '
                                   '"traderPublicKey": "w1"}'])
        async def send(self, msg):
            self.sent.append(msg)
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

    @asynccontextmanager
    async def fake_connect(url, **kwargs):
        ws = FakeWS()
        yield ws
        captured["sent"] = ws.sent

    import websockets
    monkeypatch.setattr(websockets, "connect", fake_connect)
    f = pumpportal_live.PumpPortalFeed()
    f._track("ALREADY_TRACKED")
    f.pin("HELD1")   # simulates a pin() call from run_exit_check's thread
    asyncio.run(f._subscribe_once())
    subscribe_sends = [s for s in captured["sent"] if "subscribeTokenTrade" in s]
    assert subscribe_sends, "pinning must trigger a resubscribe on the next message"
    assert "HELD1" in subscribe_sends[-1]
