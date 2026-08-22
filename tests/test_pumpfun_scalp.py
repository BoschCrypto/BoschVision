"""pump.fun discovery, the scalp exit profile, and pumpfun-native entry
scoring. No network — HTTP is stubbed."""
import json
import time
import urllib.error

import pytest

from hf_trading_bot import memecoin, memecoin_strategy, pumpfun_data

CLEAN_MINT = {"mint_authority": None, "freeze_authority": None}


# --- pumpfun_data ------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._p = payload
    def read(self):
        return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_list_new_coins_normalizes_and_excludes_migrated(monkeypatch):
    raw = [
        {"mint": "M1", "symbol": "FOO", "created_timestamp": 1_700_000_000,
        "usd_market_cap": 5000, "real_sol_reserves": 12.5, "complete": False},
        {"mint": "M2", "symbol": "BAR", "created_timestamp": 1_700_000_100,
        "complete": True},   # migrated -> excluded by default
    ]
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    rows = pumpfun_data.list_new_coins(limit=10)
    assert len(rows) == 1
    assert rows[0]["address"] == "M1"
    assert rows[0]["symbol"] == "FOO"
    assert rows[0]["created_at_ms"] == 1_700_000_000_000   # seconds -> ms
    assert rows[0]["market_cap_usd"] == 5000
    assert rows[0]["source"] == "pumpfun"


def test_list_new_coins_include_migrated(monkeypatch):
    raw = [{"mint": "M2", "symbol": "BAR", "complete": True}]
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    rows = pumpfun_data.list_new_coins(limit=10, include_migrated=True)
    assert len(rows) == 1


def test_list_new_coins_skips_rows_without_a_mint(monkeypatch):
    raw = [{"symbol": "NOMINT"}, {"mint": "M1", "symbol": "OK"}]
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    rows = pumpfun_data.list_new_coins(limit=10)
    assert len(rows) == 1 and rows[0]["address"] == "M1"


def test_list_new_coins_handles_dict_wrapper_shape(monkeypatch):
    # Defensive against the API wrapping the array in {"coins": [...]}.
    raw = {"coins": [{"mint": "M1", "symbol": "OK"}]}
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    rows = pumpfun_data.list_new_coins(limit=10)
    assert len(rows) == 1


def test_base_url_default_and_override():
    assert pumpfun_data.base_url({}) == pumpfun_data.DEFAULT_BASE_URL
    assert pumpfun_data.base_url({"PUMPFUN_BASE_URL": "https://x"}) == "https://x"


def test_get_coin_normalizes_a_single_coin(monkeypatch):
    raw = {"mint": "M1", "symbol": "FOO", "usd_market_cap": 12_500, "real_sol_reserves": 15.0}
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    coin = pumpfun_data.get_coin("M1")
    assert coin["market_cap_usd"] == 12_500
    assert coin["address"] == "M1"


def test_get_coin_returns_none_on_404(monkeypatch):
    def raise_404(req, timeout=None):
        raise urllib.error.HTTPError("url", 404, "not found", {}, None)
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen", raise_404)
    assert pumpfun_data.get_coin("BRAND_NEW") is None


def test_get_coin_raises_on_other_errors(monkeypatch):
    def raise_500(req, timeout=None):
        raise urllib.error.HTTPError("url", 500, "server error", {}, None)
    monkeypatch.setattr(pumpfun_data.urllib.request, "urlopen", raise_500)
    with pytest.raises(pumpfun_data.PumpFunError):
        pumpfun_data.get_coin("M1")


# --- scalp exit profile ------------------------------------------------

def test_scalp_stop_loss_is_tighter_than_swing():
    assert memecoin_strategy.SCALP_STOP_LOSS_PCT > memecoin_strategy.STOP_LOSS_PCT
    # (both negative; "tighter" means closer to zero, less room to fall)


def test_scalp_exit_signal_stop_loss():
    sig = memecoin_strategy.scalp_exit_signal(
        entry_price_usd=1.0, current_price_usd=0.83, peak_price_usd=1.0, hours_held=0.1)
    assert sig.exit is True and "stop-loss" in sig.reason
    assert sig.sell_pct == 100.0


def test_scalp_exit_signal_does_not_trigger_swing_thresholds():
    # +20% would not trigger a SWING trim (needs +100%) but DOES trigger the
    # scalp trim-1 (+15%) -- confirms the tighter profile is actually used.
    swing = memecoin_strategy.exit_signal(
        entry_price_usd=1.0, current_price_usd=1.20, peak_price_usd=1.20, hours_held=0.1)
    scalp = memecoin_strategy.scalp_exit_signal(
        entry_price_usd=1.0, current_price_usd=1.20, peak_price_usd=1.20, hours_held=0.1)
    assert swing.exit is False
    assert scalp.exit is True and "take-profit" in scalp.reason


def test_scalp_exit_signal_stall_is_much_faster():
    assert memecoin_strategy.SCALP_STALL_HOURS < memecoin_strategy.STALL_HOURS
    sig = memecoin_strategy.scalp_exit_signal(
        entry_price_usd=1.0, current_price_usd=1.02, peak_price_usd=1.05,
        hours_held=memecoin_strategy.SCALP_STALL_HOURS + 0.01)
    assert sig.exit is True and "stall" in sig.reason


def test_exit_signal_no_token_data_past_stall_now_exits():
    # A swing position with no DexScreener data available past the stall
    # deadline now exits rather than holding forever with no way to check
    # momentum -- a deliberate tightening alongside the scalp work.
    sig = memecoin_strategy.exit_signal(
        entry_price_usd=1.0, current_price_usd=1.02, peak_price_usd=1.05,
        hours_held=memecoin_strategy.STALL_HOURS + 0.1, token=None)
    assert sig.exit is True and "no data" in sig.reason


# --- pumpfun momentum / entry ---------------------------------------------

def test_pumpfun_momentum_fresh_coin_with_raise_scores_high():
    # Freshness (25 max) + SOL-raised (15 max) is the ceiling with no
    # market-cap or buyer-diversity data at all -- 40, not the full 100.
    now_ms = int(time.time() * 1000)
    coin = {"created_at_ms": now_ms - 60_000, "sol_raised": 20.0}   # 1 min old
    result = memecoin_strategy.pumpfun_momentum_score(coin)
    assert result["score"] > 35


def test_pumpfun_momentum_market_cap_scores_higher_than_no_market_cap():
    # The signal live-testing actually validated (Photon's own >=$10k
    # Memescope filter) -- a coin already carrying real market cap should
    # score meaningfully higher than an otherwise-identical one with none.
    coin_base = {"created_at_ms": int(time.time() * 1000)}
    no_mc = memecoin_strategy.pumpfun_momentum_score(coin_base)
    with_mc = memecoin_strategy.pumpfun_momentum_score(
        dict(coin_base, market_cap_usd=10_000))
    assert with_mc["score"] > no_mc["score"]


def test_pumpfun_momentum_market_cap_capped_at_the_full_score_threshold():
    coin = {"market_cap_usd": memecoin_strategy.MARKET_CAP_FULL_SCORE_USD * 5}
    result = memecoin_strategy.pumpfun_momentum_score(coin)
    mc_component = next(c for c in result["components"] if "market cap" in c["reason"])
    assert mc_component["points"] == 35.0


def test_pumpfun_momentum_many_distinct_buyers_scores_higher_than_one_buyer():
    # The exact distinction PumpPortal's buyer count exists to make: same
    # freshness and same SOL raised, but one whale (or a bundle) vs many
    # distinct wallets should NOT score the same.
    now_ms = int(time.time() * 1000)
    coin = {"created_at_ms": now_ms - 60_000, "sol_raised": 5.0}
    one_buyer = memecoin_strategy.pumpfun_momentum_score(
        coin, buyer_stats={"unique_buyers": 1, "buy_count": 5, "age_s": 30})
    many_buyers = memecoin_strategy.pumpfun_momentum_score(
        coin, buyer_stats={"unique_buyers": 12, "buy_count": 15, "age_s": 30})
    assert many_buyers["score"] > one_buyer["score"]


def test_pumpfun_momentum_buyer_diversity_capped_at_25():
    coin = {}
    result = memecoin_strategy.pumpfun_momentum_score(
        coin, buyer_stats={"unique_buyers": 50, "buy_count": 60, "age_s": 30})
    assert result["score"] == 25.0


def test_pumpfun_momentum_missing_buyer_stats_scores_that_component_zero():
    coin = {"sol_raised": 5.0}
    result = memecoin_strategy.pumpfun_momentum_score(coin, buyer_stats=None)
    buyer_component = next(c for c in result["components"] if "PumpPortal" in c["reason"]
                           or "buyer-diversity" in c["reason"])
    assert buyer_component["points"] == 0.0


def test_pumpfun_momentum_old_coin_scores_low_on_freshness():
    now_ms = int(time.time() * 1000)
    coin = {"created_at_ms": now_ms - 60 * 60_000, "sol_raised": 0.0}   # 1h old, no raise
    result = memecoin_strategy.pumpfun_momentum_score(coin)
    assert result["score"] < 10


def test_pumpfun_momentum_missing_data_scores_zero():
    result = memecoin_strategy.pumpfun_momentum_score({})
    assert result["score"] == 0.0


def test_pumpfun_entry_signal_red_flag_blocks():
    hot_mint = dict(CLEAN_MINT, mint_authority="Creator")
    coin = {"created_at_ms": int(time.time() * 1000), "sol_raised": 30.0}
    sig = memecoin_strategy.pumpfun_entry_signal(coin, hot_mint)
    assert sig.enter is False


def test_pumpfun_entry_signal_passes_fresh_clean_coin_with_market_cap():
    coin = {"created_at_ms": int(time.time() * 1000), "sol_raised": 30.0,
           "market_cap_usd": 10_000}
    sig = memecoin_strategy.pumpfun_entry_signal(coin, CLEAN_MINT)
    assert sig.enter is True


def test_pumpfun_entry_signal_uses_buyer_stats_to_clear_threshold():
    # A coin too thin to clear the entry bar on freshness + SOL-raised alone
    # can still clear it on genuine buyer diversity -- this is the actual
    # fix for a bot that otherwise never enters.
    now_ms = int(time.time() * 1000)
    coin = {"created_at_ms": now_ms - 60_000, "sol_raised": 2.0}   # 1 min old, thin raise
    without = memecoin_strategy.pumpfun_entry_signal(coin, CLEAN_MINT)
    with_buyers = memecoin_strategy.pumpfun_entry_signal(
        coin, CLEAN_MINT, buyer_stats={"unique_buyers": 10, "buy_count": 12, "age_s": 60})
    assert without.enter is False
    assert with_buyers.enter is True


def test_pumpfun_entry_signal_uses_market_cap_to_clear_threshold():
    now_ms = int(time.time() * 1000)
    coin_no_mc = {"created_at_ms": now_ms - 60_000, "sol_raised": 2.0}
    coin_with_mc = dict(coin_no_mc, market_cap_usd=10_000)
    without = memecoin_strategy.pumpfun_entry_signal(coin_no_mc, CLEAN_MINT)
    with_mc = memecoin_strategy.pumpfun_entry_signal(coin_with_mc, CLEAN_MINT)
    assert without.enter is False
    assert with_mc.enter is True


def test_pumpfun_risk_flags_only_checks_authorities():
    # No liquidity concept for a coin this fresh -- confirm the flag set is
    # exactly {mint, freeze}, nothing liquidity/volume-shaped leaks in.
    flags = memecoin.pumpfun_risk_flags({}, dict(CLEAN_MINT, mint_authority="X"))
    assert len(flags) == 1 and "mint authority" in flags[0]["reason"]
