"""The live WebSocket feed's pure/synchronous parts: mint-diff detection and
the thread-safe recent()/status() read side. No real network/websocket
connection — _subscribe_once is never exercised here, only what's testable
without one.
"""
import pytest

from hf_trading_bot import pumpfun_live


# --- extract_new_mint (pure) -----------------------------------------

def test_extract_new_mint_finds_the_new_one():
    tx = {"meta": {"preTokenBalances": [{"mint": "A"}],
                   "postTokenBalances": [{"mint": "A"}, {"mint": "B"}]}}
    assert pumpfun_live.extract_new_mint(tx) == "B"


def test_extract_new_mint_none_when_no_new_mint():
    tx = {"meta": {"preTokenBalances": [{"mint": "A"}],
                   "postTokenBalances": [{"mint": "A"}]}}
    assert pumpfun_live.extract_new_mint(tx) is None


def test_extract_new_mint_handles_missing_meta():
    assert pumpfun_live.extract_new_mint({}) is None
    assert pumpfun_live.extract_new_mint(None) is None


def test_extract_new_mint_handles_empty_balances():
    tx = {"meta": {"preTokenBalances": [], "postTokenBalances": []}}
    assert pumpfun_live.extract_new_mint(tx) is None


def test_extract_new_mint_ignores_entries_without_mint_key():
    tx = {"meta": {"preTokenBalances": [{}], "postTokenBalances": [{}, {"mint": "B"}]}}
    assert pumpfun_live.extract_new_mint(tx) == "B"


def test_extract_new_mint_deterministic_with_multiple_new_mints():
    tx = {"meta": {"preTokenBalances": [], "postTokenBalances": [{"mint": "Z"}, {"mint": "A"}]}}
    assert pumpfun_live.extract_new_mint(tx) == "A"   # sorted, deterministic


# --- ws_url -------------------------------------------------------------

def test_ws_url_explicit_override():
    assert pumpfun_live.ws_url({"SOLANA_WS_URL": "wss://custom"}) == "wss://custom"


def test_ws_url_derived_from_https_rpc():
    url = pumpfun_live.ws_url({"SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com"})
    assert url == "wss://api.mainnet-beta.solana.com"


def test_ws_url_derived_from_http_rpc():
    url = pumpfun_live.ws_url({"SOLANA_RPC_URL": "http://localhost:8899"})
    assert url == "ws://localhost:8899"


def test_ws_url_default_when_nothing_set():
    url = pumpfun_live.ws_url({})
    assert url.startswith("wss://")


# --- LiveFeed: thread-safe record/recent/status (no real thread/network) ---

def test_livefeed_record_and_recent_newest_first():
    f = pumpfun_live.LiveFeed()
    f._record("M1")
    f._record("M2")
    f._record("M3")
    recent = f.recent(10)
    assert [c["address"] for c in recent] == ["M3", "M2", "M1"]
    assert all(c["source"] == "pumpfun_live" for c in recent)


def test_livefeed_recent_respects_limit():
    f = pumpfun_live.LiveFeed()
    for i in range(10):
        f._record(f"M{i}")
    assert len(f.recent(3)) == 3


def test_livefeed_recent_caps_total_stored():
    f = pumpfun_live.LiveFeed()
    for i in range(pumpfun_live._MAX_RECENT + 20):
        f._record(f"M{i}")
    assert len(f.recent(1000)) == pumpfun_live._MAX_RECENT


def test_livefeed_status_tracks_detection_count():
    f = pumpfun_live.LiveFeed()
    assert f.status()["detections"] == 0
    f._record("M1")
    f._record("M2")
    s = f.status()
    assert s["detections"] == 2
    assert s["last_event_at"] is not None


def test_livefeed_status_starts_disconnected():
    f = pumpfun_live.LiveFeed()
    assert f.status()["connected"] is False
    assert f.status()["last_error"] is None


def test_livefeed_normalized_coin_shape_matches_pumpfun_data():
    # run_autotrade_cycle's scalp branch must be able to treat live and REST
    # candidates identically — same keys pumpfun_data._normalize() produces.
    f = pumpfun_live.LiveFeed()
    f._record("M1")
    live_coin = f.recent(1)[0]
    expected_keys = {"address", "symbol", "name", "created_at_ms", "market_cap_usd",
                     "sol_raised", "migrated", "source"}
    assert expected_keys.issubset(live_coin.keys())
