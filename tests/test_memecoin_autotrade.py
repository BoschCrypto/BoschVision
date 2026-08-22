"""The autonomous cycle: exits before entries, guards respected, one bad
token never stops the pass. All network monkeypatched."""
import pytest

from hf_trading_bot import memecoin, memecoin_data, memecoin_strategy, solana_wallet
from hf_trading_bot.storage import Storage

CONFIRMED_ENV = {"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"}


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "m.db"))
    yield s
    s.close()


def test_cycle_skips_when_kill_switch_on(storage):
    storage.set_kill_switch(True)
    report = memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV)
    assert report["skipped"] == "kill switch is ON"
    assert report["exits"] == [] and report["entries"] == []


def test_cycle_skips_when_not_confirmed(storage):
    storage.set_kill_switch(False)
    report = memecoin.run_autotrade_cycle(storage, env={})
    assert "HF_BOT_I_UNDERSTAND_MEMECOIN_RISK" in report["skipped"]


def test_cycle_no_positions_no_budget_room_does_nothing(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    # Budget already fully deployed -> no room for new entries.
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.3, usd_amount=50.0, price_usd=1.0,
                                  tx_signature="s", status="confirmed")
    report = memecoin.run_autotrade_cycle(storage, env=dict(CONFIRMED_ENV,
                                                            MEMECOIN_WALLET_BUDGET_USD="50"))
    assert report["entries"] == [] and report["exits"] == []
    assert report["errors"] == []


def test_cycle_positions_error_recorded_not_raised(storage, monkeypatch):
    storage.set_kill_switch(False)

    def boom(s, env=None):
        raise solana_wallet.WalletError("no key configured")
    monkeypatch.setattr(memecoin, "list_positions", boom)
    report = memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV)
    assert report["errors"][0]["stage"] == "positions"
    assert "no key" in report["errors"][0]["error"]


# --- run_exit_check: the fast, standalone half of the cycle -----------------

def test_exit_check_skips_when_kill_switch_on(storage):
    storage.set_kill_switch(True)
    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["skipped"] == "kill switch is ON"
    assert report["held_addresses"] is None


def test_exit_check_skips_when_not_confirmed(storage):
    storage.set_kill_switch(False)
    report = memecoin.run_exit_check(storage, env={})
    assert "HF_BOT_I_UNDERSTAND_MEMECOIN_RISK" in report["skipped"]


def test_exit_check_held_addresses_none_on_positions_error(storage, monkeypatch):
    storage.set_kill_switch(False)

    def boom(s, env=None):
        raise solana_wallet.WalletError("no key configured")
    monkeypatch.setattr(memecoin, "list_positions", boom)
    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["errors"][0]["stage"] == "positions"
    assert report["held_addresses"] is None


def test_exit_check_held_addresses_populated_on_success(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions",
                        lambda s, env=None: [_position("M1"), _position("M2")])
    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["held_addresses"] == {"M1", "M2"}


def test_exit_check_no_positions_still_returns_empty_set(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["held_addresses"] == set()
    assert report["exits"] == []


def _position(addr="M1", symbol="X", price=1.0):
    return memecoin.Position(token_address=addr, symbol=symbol, balance=10.0,
                             cost_basis_usd=10.0, current_price_usd=price,
                             current_value_usd=10.0 * price,
                             unrealized_pnl_usd=None, unrealized_pnl_pct=None)


def test_cycle_exits_a_stop_loss_position(storage, monkeypatch):
    storage.set_kill_switch(False)
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [_position(price=0.5)])
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)
    sell_calls = []

    def fake_execute_sell(token, pct, s, *, kill_switch, env=None, **k):
        sell_calls.append((token, pct))
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
               "usd_amount": 5.0}
    monkeypatch.setattr(memecoin, "execute_sell", fake_execute_sell)
    # No entry-side network calls should happen if budget check short-circuits;
    # but to be safe, make trending() explode if it's reached unexpectedly is
    # NOT asserted here since entries may still run after a full exit — fine.
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: [])

    report = memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV)
    assert sell_calls == [("M1", 100.0)]
    assert report["exits"][0]["reason"].startswith("stop-loss")
    assert storage.memecoin_peak_state("M1") is None   # cleared on full exit


def test_cycle_one_bad_position_does_not_stop_the_rest(storage, monkeypatch):
    storage.set_kill_switch(False)
    for addr in ("BAD", "GOOD"):
        storage.record_memecoin_trade(side="buy", token_address=addr, token_symbol=addr,
                                      sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                      tx_signature=f"s-{addr}", status="confirmed")
    monkeypatch.setattr(memecoin, "list_positions",
                        lambda s, env=None: [_position("BAD", "BAD", 0.5),
                                             _position("GOOD", "GOOD", 0.5)])
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)

    def fake_execute_sell(token, pct, s, *, kill_switch, env=None, **k):
        if token == "BAD":
            raise memecoin.MemecoinError("no route")
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
               "usd_amount": 5.0}
    monkeypatch.setattr(memecoin, "execute_sell", fake_execute_sell)
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: [])

    report = memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV)
    assert len(report["errors"]) == 1 and report["errors"][0]["token_address"] == "BAD"
    assert len(report["exits"]) == 1 and report["exits"][0]["token_address"] == "GOOD"


def test_cycle_enters_on_passing_candidate(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    candidate = {"address": "NEW1", "symbol": "NEW", "liquidity_usd": 200_000,
                "volume_24h_usd": 500_000, "price_change_h1_pct": 15.0,
                "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
                "buys_h1": 90, "sells_h1": 10, "buys_h24": 500, "sells_h24": 300,
                "pair_created_at": None}
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: [candidate])
    monkeypatch.setattr(memecoin_data, "filter_candidates",
                        lambda rows, **k: rows)   # bypass real filtering for this test
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})

    buy_calls = []

    def fake_execute_buy(token, usd, s, *, kill_switch, env=None, **k):
        buy_calls.append((token, usd))
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
               "usd_amount": usd}
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy", fake_execute_buy)

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"))
    assert buy_calls == [("NEW1", 25.0)]
    assert report["entries"][0]["token_address"] == "NEW1"


def test_cycle_rugcheck_red_flag_vetoes_the_buy(storage, monkeypatch):
    # A candidate that clears the momentum score can still be blocked at the
    # final gate -- this is the whole point of calling RugCheck right before
    # money moves, not just during scanning.
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    candidate = {"address": "BUNDLED1", "symbol": "BUNDLED", "liquidity_usd": 200_000,
                "volume_24h_usd": 500_000, "price_change_h1_pct": 15.0,
                "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
                "buys_h1": 90, "sells_h1": 10, "buys_h24": 500, "sells_h24": 300,
                "pair_created_at": None}
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: [candidate])
    monkeypatch.setattr(memecoin_data, "filter_candidates", lambda rows, **k: rows)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags",
                        lambda addr, env=None: [{"level": "red", "reason":
                                                 "a single wallet holds 40% of supply"}])

    def fail_if_called(token, usd, s, **k):
        raise AssertionError("execute_buy must not be called after a red RugCheck veto")
    monkeypatch.setattr(memecoin, "execute_buy", fail_if_called)

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"))
    assert report["entries"] == []
    assert report["errors"][0]["stage"] == "entry-rugcheck-veto"
    assert "40%" in report["errors"][0]["error"]


def test_cycle_never_exceeds_max_new_positions_per_cycle(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    candidates = [
        {"address": f"T{i}", "symbol": f"T{i}", "liquidity_usd": 200_000,
        "volume_24h_usd": 500_000, "price_change_h1_pct": 15.0,
        "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
        "buys_h1": 90, "sells_h1": 10, "buys_h24": 500, "sells_h24": 300,
        "pair_created_at": None}
        for i in range(5)
    ]
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: candidates)
    monkeypatch.setattr(memecoin_data, "filter_candidates", lambda rows, **k: rows)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: {"tx_signature": "sig", "status": "confirmed",
                                                    "sol_amount": 0.1, "usd_amount": usd})

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="10",
                          MEMECOIN_WALLET_BUDGET_USD="100"),
        max_new_positions=2)
    assert len(report["entries"]) == 2


def test_cycle_stops_entries_when_budget_exhausted_mid_cycle(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    candidates = [
        {"address": f"T{i}", "symbol": f"T{i}", "liquidity_usd": 200_000,
        "volume_24h_usd": 500_000, "price_change_h1_pct": 15.0,
        "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
        "buys_h1": 90, "sells_h1": 10, "buys_h24": 500, "sells_h24": 300,
        "pair_created_at": None}
        for i in range(5)
    ]
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: candidates)
    monkeypatch.setattr(memecoin_data, "filter_candidates", lambda rows, **k: rows)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: {"tx_signature": "sig", "status": "confirmed",
                                                    "sol_amount": 0.1, "usd_amount": usd})

    # Budget covers one full $30 trade plus a reduced $15 final buy from the
    # leftover — the cycle uses remaining budget rather than stranding it, but
    # never exceeds the total.
    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="30",
                          MEMECOIN_WALLET_BUDGET_USD="45"),
        max_new_positions=5)
    assert len(report["entries"]) == 2
    assert sum(e["usd_amount"] for e in report["entries"]) == pytest.approx(45.0)


def test_cycle_scalp_mode_uses_pumpfun_discovery_not_dexscreener(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])

    def boom_trending(*a, **k):
        raise AssertionError("scalp mode must not call DexScreener trending()")
    monkeypatch.setattr(memecoin_data, "trending", boom_trending)

    from hf_trading_bot import pumpfun_data
    import time as _t
    coin = {"address": "PF1", "symbol": "FRESH", "created_at_ms": int(_t.time() * 1000),
           "sol_raised": 30.0, "market_cap_usd": 4000, "migrated": False, "source": "pumpfun"}
    monkeypatch.setattr(pumpfun_data, "list_new_coins", lambda limit=30, env=None: [coin])
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    buy_calls = []
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: buy_calls.append((token, usd)) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"), scalp=True)
    assert buy_calls == [("PF1", 25.0)]
    assert report["entries"][0]["token_address"] == "PF1"


def test_cycle_scalp_mode_uses_live_candidates_when_given(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])

    def boom(*a, **k):
        raise AssertionError("live_candidates provided -> pumpfun_data must not be called")
    from hf_trading_bot import pumpfun_data
    monkeypatch.setattr(pumpfun_data, "list_new_coins", boom)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    buy_calls = []
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: buy_calls.append(token) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    import time as _t
    # market_cap_usd is set explicitly so this candidate clears the entry
    # score threshold AND so run_autotrade_cycle doesn't attempt a live
    # pumpfun_data.get_coin() enrichment fetch (real network in this test).
    live_coin = {"address": "LIVE1", "symbol": "FRESH", "created_at_ms": int(_t.time() * 1000),
                "sol_raised": 30.0, "market_cap_usd": 10_000, "migrated": False,
                "source": "pumpfun_live"}
    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"),
        scalp=True, live_candidates=[live_coin])
    assert buy_calls == ["LIVE1"]


def test_cycle_fetches_market_cap_when_live_candidate_lacks_one(storage, monkeypatch):
    # pumpfun_live.py's live feed never knows market cap (bare mint address
    # only) -- run_autotrade_cycle must fetch it live via pumpfun_data so
    # scoring isn't permanently blind to the strongest available signal.
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    from hf_trading_bot import pumpfun_data
    fetch_calls = []

    def fake_get_coin(mint, env=None):
        fetch_calls.append(mint)
        return {"address": mint, "market_cap_usd": 10_000}
    monkeypatch.setattr(pumpfun_data, "get_coin", fake_get_coin)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    buy_calls = []
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: buy_calls.append(token) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    import time as _t
    live_coin = {"address": "LIVE2", "symbol": "FRESH", "created_at_ms": int(_t.time() * 1000),
                "sol_raised": 2.0, "market_cap_usd": None, "migrated": False,
                "source": "pumpfun_live"}
    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"),
        scalp=True, live_candidates=[live_coin])
    assert fetch_calls == ["LIVE2"]
    # Without the fetched market cap, freshness + thin sol_raised alone
    # can't clear the entry threshold -- the fetch is what makes this enter.
    assert buy_calls == ["LIVE2"]


def test_cycle_skips_market_cap_fetch_when_candidate_already_has_one(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    from hf_trading_bot import pumpfun_data

    def boom(*a, **k):
        raise AssertionError("market cap already present -> get_coin must not be called")
    monkeypatch.setattr(pumpfun_data, "get_coin", boom)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: {"tx_signature": "sig", "status": "confirmed",
                                                    "sol_amount": 0.1, "usd_amount": usd})

    import time as _t
    live_coin = {"address": "LIVE3", "symbol": "FRESH", "created_at_ms": int(_t.time() * 1000),
                "sol_raised": 2.0, "market_cap_usd": 10_000, "migrated": False,
                "source": "pumpfun_live"}
    memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"),
        scalp=True, live_candidates=[live_coin])   # must not raise


def test_cycle_scalp_mode_exit_uses_tight_thresholds(storage, monkeypatch):
    storage.set_kill_switch(False)
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    # +20% would NOT trigger a swing exit (needs +100%) but DOES trigger a
    # scalp trim (+15%) -- confirms scalp=True actually swaps the profile.
    monkeypatch.setattr(memecoin, "list_positions",
                        lambda s, env=None: [_position(price=1.20)])
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)
    sell_calls = []
    monkeypatch.setattr(memecoin, "execute_sell",
                        lambda token, pct, s, **k: sell_calls.append((token, pct)) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
                         "usd_amount": 5.0})
    from hf_trading_bot import pumpfun_data
    monkeypatch.setattr(pumpfun_data, "list_new_coins", lambda limit=30, env=None: [])

    report = memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV, scalp=True)
    assert len(sell_calls) == 1
    assert sell_calls[0][1] == memecoin_strategy.SCALP_TRIM_1_SELL_PCT
