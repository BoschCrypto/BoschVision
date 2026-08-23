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


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # _rate_limited_get_mint_info, _get_mint_info_for_fresh_candidate, and
    # _with_jupiter_retry all call time.sleep() for real-world rate
    # limiting/backoff -- none of that should ever cost real wall-clock
    # time in tests, and the rate limiter's last-call timestamp is shared
    # module state, so without this tests would end up throttling each
    # other based on unrelated prior tests' timing.
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)
    monkeypatch.setattr(memecoin, "_last_mint_check_at", 0.0)


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


def test_exit_check_stop_loss_uses_emergency_slippage(storage, monkeypatch):
    # Real failure seen in live testing: a position crashing -66% had sell
    # attempts rejected outright by pump.fun's own program at the normal
    # 150bps slippage tolerance -- during a real crash the price moves
    # faster than that between quote and landing. A stop-loss must use a
    # much wider tolerance so the exit can actually execute.
    storage.set_kill_switch(False)
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [_position(price=0.5)])
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)
    calls = []

    def fake_execute_sell(token, pct, s, *, kill_switch, slippage_bps=None, env=None, **k):
        calls.append(slippage_bps)
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
               "usd_amount": 5.0}
    monkeypatch.setattr(memecoin, "execute_sell", fake_execute_sell)

    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["exits"][0]["reason"].startswith("stop-loss")
    assert calls == [memecoin.EMERGENCY_EXIT_SLIPPAGE_BPS]


def test_exit_check_take_profit_trim_uses_normal_slippage(storage, monkeypatch):
    storage.set_kill_switch(False)
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    # +150% triggers TRIM_1 (take-profit), not a stop-loss/trailing-stop.
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [_position(price=2.5)])
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)
    calls = []

    def fake_execute_sell(token, pct, s, *, kill_switch, slippage_bps=None, env=None, **k):
        calls.append(slippage_bps)
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
               "usd_amount": 5.0}
    monkeypatch.setattr(memecoin, "execute_sell", fake_execute_sell)

    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert report["exits"][0]["reason"].startswith("take-profit")
    assert calls == [memecoin.DEFAULT_SELL_SLIPPAGE_BPS]


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


def test_exit_check_jupiter_network_error_does_not_stop_other_positions(storage, monkeypatch):
    # Real bug found in live testing: a Jupiter DNS/network failure during a
    # sell attempt raises jupiter.JupiterError, not memecoin.MemecoinError --
    # this is the fast, risk-critical exit loop, so letting that propagate
    # would skip checking every OTHER held position for this pass too.
    from hf_trading_bot import jupiter

    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)   # no real delay in tests
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
            raise jupiter.JupiterError("Jupiter unreachable: [Errno 11001] getaddrinfo failed")
        return {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.05,
               "usd_amount": 5.0}
    monkeypatch.setattr(memecoin, "execute_sell", fake_execute_sell)

    report = memecoin.run_exit_check(storage, env=CONFIRMED_ENV)
    assert len(report["errors"]) == 1 and report["errors"][0]["token_address"] == "BAD"
    assert len(report["exits"]) == 1 and report["exits"][0]["token_address"] == "GOOD"


# --- _rate_limited_get_mint_info: avoid tripping Solana RPC's rate limit ---

def test_mint_check_min_interval_default_and_override():
    assert memecoin.mint_check_min_interval_s({}) == memecoin.DEFAULT_MINT_CHECK_MIN_INTERVAL_S
    assert memecoin.mint_check_min_interval_s({"MEMECOIN_MINT_CHECK_MIN_INTERVAL_S": "1.0"}) == 1.0


def test_mint_check_min_interval_invalid_falls_back_to_default():
    assert memecoin.mint_check_min_interval_s(
        {"MEMECOIN_MINT_CHECK_MIN_INTERVAL_S": "garbage"}
    ) == memecoin.DEFAULT_MINT_CHECK_MIN_INTERVAL_S


def test_rate_limited_get_mint_info_spaces_out_calls(monkeypatch):
    # Real bug found in live testing: merging PumpPortal's detections put
    # enough candidates through mint-info checks in one cycle to trip
    # Helius's rate limit ("Solana RPC HTTP 429: Too Many Requests").
    monkeypatch.setattr(memecoin, "_last_mint_check_at", 0.0)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda address, env=None: {"mint_authority": None,
                                                    "freeze_authority": None})
    sleeps = []
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: sleeps.append(s))

    memecoin._rate_limited_get_mint_info("M1", env={"MEMECOIN_MINT_CHECK_MIN_INTERVAL_S": "1.0"})
    memecoin._rate_limited_get_mint_info("M2", env={"MEMECOIN_MINT_CHECK_MIN_INTERVAL_S": "1.0"})
    # First call has nothing to wait on; the second must be throttled.
    assert sleeps and sleeps[-1] > 0


# --- enrich_candidates_with_market_cap: parallel, not sequential -----------

def test_enrich_skips_candidates_that_already_have_market_cap(monkeypatch):
    from hf_trading_bot import pumpfun_data

    def boom(mint, env=None):
        raise AssertionError("must not fetch a candidate that already has market_cap_usd")
    monkeypatch.setattr(pumpfun_data, "get_coin", boom)

    candidates = [{"address": "M1", "market_cap_usd": 5_000}]
    enriched, outcomes = memecoin.enrich_candidates_with_market_cap(candidates)
    assert enriched[0]["market_cap_usd"] == 5_000
    assert outcomes["M1"]["status"] == "already_present"


def test_enrich_fetches_all_missing_candidates_concurrently(monkeypatch):
    from hf_trading_bot import pumpfun_data

    def fake_get_coin(mint, env=None):
        return {"market_cap_usd": 1_000.0 * int(mint[1:]), "price_usd": 0.001,
               "has_social_links": True}
    monkeypatch.setattr(pumpfun_data, "get_coin", fake_get_coin)

    candidates = [{"address": f"M{i}"} for i in range(1, 6)]
    enriched, outcomes = memecoin.enrich_candidates_with_market_cap(candidates, max_workers=3)
    assert {c["address"]: c["market_cap_usd"] for c in enriched} == \
        {f"M{i}": 1_000.0 * i for i in range(1, 6)}
    assert all(outcomes[f"M{i}"]["status"] == "fetched" for i in range(1, 6))


def test_enrich_one_candidate_error_does_not_affect_others(monkeypatch):
    from hf_trading_bot import pumpfun_data

    def flaky(mint, env=None):
        if mint == "BAD":
            raise pumpfun_data.PumpFunError("pump.fun unreachable")
        return {"market_cap_usd": 7_000.0, "price_usd": None, "has_social_links": False}
    monkeypatch.setattr(pumpfun_data, "get_coin", flaky)

    candidates = [{"address": "BAD", "market_cap_usd": None},
                 {"address": "GOOD", "market_cap_usd": None}]
    enriched, outcomes = memecoin.enrich_candidates_with_market_cap(candidates)
    by_addr = {c["address"]: c for c in enriched}
    assert by_addr["BAD"]["market_cap_usd"] is None
    assert by_addr["GOOD"]["market_cap_usd"] == 7_000.0
    assert outcomes["BAD"]["status"] == "error"
    assert "unreachable" in outcomes["BAD"]["error"]
    assert outcomes["GOOD"]["status"] == "fetched"


def test_enrich_no_data_returned_leaves_market_cap_none(monkeypatch):
    from hf_trading_bot import pumpfun_data
    monkeypatch.setattr(pumpfun_data, "get_coin", lambda mint, env=None: None)

    enriched, outcomes = memecoin.enrich_candidates_with_market_cap(
        [{"address": "M1", "market_cap_usd": None}])
    assert enriched[0]["market_cap_usd"] is None
    assert outcomes["M1"]["status"] == "no_data"


def test_enrich_does_not_mutate_the_original_candidate_dicts(monkeypatch):
    from hf_trading_bot import pumpfun_data
    monkeypatch.setattr(pumpfun_data, "get_coin",
                        lambda mint, env=None: {"market_cap_usd": 9_000.0, "price_usd": None,
                                                "has_social_links": None})
    original = {"address": "M1"}
    memecoin.enrich_candidates_with_market_cap([original])
    assert original.get("market_cap_usd") is None   # the input dict itself is untouched


# --- _get_mint_info_for_fresh_candidate: the PumpPortal-speed race fix -----

def test_get_mint_info_for_fresh_candidate_retries_on_missing_account(monkeypatch):
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)   # no real delay in tests
    calls = []

    def flaky(address, env=None):
        calls.append(address)
        if len(calls) < 2:
            raise solana_wallet.WalletError(f"no on-chain account found for mint {address}")
        return {"mint_authority": None, "freeze_authority": None}
    monkeypatch.setattr(solana_wallet, "get_mint_info", flaky)

    result = memecoin._get_mint_info_for_fresh_candidate("M1", env={})
    assert result == {"mint_authority": None, "freeze_authority": None}
    assert len(calls) == 2


def test_get_mint_info_for_fresh_candidate_gives_up_after_retries_exhausted(monkeypatch):
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)

    def always_missing(address, env=None):
        raise solana_wallet.WalletError(f"no on-chain account found for mint {address}")
    monkeypatch.setattr(solana_wallet, "get_mint_info", always_missing)

    with pytest.raises(solana_wallet.WalletError, match="no on-chain account"):
        memecoin._get_mint_info_for_fresh_candidate("M1", env={})


def test_get_mint_info_for_fresh_candidate_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)
    calls = []

    def boom(address, env=None):
        calls.append(address)
        raise solana_wallet.WalletError("unexpected mint account shape: 'info'")
    monkeypatch.setattr(solana_wallet, "get_mint_info", boom)

    with pytest.raises(solana_wallet.WalletError, match="unexpected mint account shape"):
        memecoin._get_mint_info_for_fresh_candidate("M1", env={})
    assert len(calls) == 1   # no retry for a non-race-condition failure


# --- _with_jupiter_retry: safe because JupiterError means nothing was sent -

def test_with_jupiter_retry_succeeds_on_second_attempt(monkeypatch):
    from hf_trading_bot import jupiter
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)
    calls = []

    def flaky(x):
        calls.append(x)
        if len(calls) < 2:
            raise jupiter.JupiterError("Jupiter unreachable: [Errno 11001] getaddrinfo failed")
        return "ok"

    assert memecoin._with_jupiter_retry(flaky, "arg") == "ok"
    assert len(calls) == 2


def test_with_jupiter_retry_gives_up_after_retries_exhausted(monkeypatch):
    from hf_trading_bot import jupiter
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)

    def always_fails():
        raise jupiter.JupiterError("Jupiter unreachable: [Errno 11001] getaddrinfo failed")

    with pytest.raises(jupiter.JupiterError, match="unreachable"):
        memecoin._with_jupiter_retry(always_fails)


def test_with_jupiter_retry_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)
    calls = []

    def boom():
        calls.append(1)
        raise memecoin.MemecoinError("kill switch is ON")

    with pytest.raises(memecoin.MemecoinError):
        memecoin._with_jupiter_retry(boom)
    assert len(calls) == 1   # MemecoinError/WalletError must never be retried here


def test_cycle_entry_buy_jupiter_network_error_recorded_not_raised(storage, monkeypatch):
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)   # no real delay in tests
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    candidate = {"address": "NEW1", "symbol": "NEW", "liquidity_usd": 200_000,
                "volume_24h_usd": 500_000, "price_change_h1_pct": 15.0,
                "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
                "buys_h1": 90, "sells_h1": 10, "buys_h24": 500, "sells_h24": 300,
                "pair_created_at": None}
    monkeypatch.setattr(memecoin_data, "trending", lambda limit=30, env=None: [candidate])
    monkeypatch.setattr(memecoin_data, "filter_candidates", lambda rows, **k: rows)
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])

    from hf_trading_bot import jupiter

    def fake_execute_buy(token, usd, s, **k):
        raise jupiter.JupiterError("Jupiter unreachable: [Errno 11001] getaddrinfo failed")
    monkeypatch.setattr(memecoin, "execute_buy", fake_execute_buy)

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"))
    assert report["entries"] == []
    assert report["errors"][0]["stage"] == "entry-buy"
    assert "unreachable" in report["errors"][0]["error"]


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

    env = dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25", MEMECOIN_WALLET_BUDGET_USD="50")
    report = memecoin.run_autotrade_cycle(storage, env=env)
    # Size is score-scaled, not flat -- this candidate's momentum score is
    # well above the entry threshold but not a perfect 100, so it should
    # land strictly between the configured floor and the $25 ceiling.
    assert len(buy_calls) == 1
    assert buy_calls[0][0] == "NEW1"
    assert memecoin.DEFAULT_MIN_TRADE_USD < buy_calls[0][1] < 25.0
    expected = memecoin.size_for_score(report["entries"][0]["score"],
                                       memecoin_strategy.MIN_ENTRY_SCORE, env=env)
    assert buy_calls[0][1] == pytest.approx(expected)
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

    # Size is score-scaled (see size_for_score), so each buy here lands well
    # under the $30 ceiling -- the cycle keeps buying against the $45 budget
    # until what's left can't clear the "worth trying" threshold, but never
    # exceeds the total.
    env = dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="30", MEMECOIN_WALLET_BUDGET_USD="45")
    report = memecoin.run_autotrade_cycle(storage, env=env, max_new_positions=5)
    assert len(report["entries"]) == 2
    total = sum(e["usd_amount"] for e in report["entries"])
    assert total < 45.0
    per_buy = memecoin.size_for_score(report["entries"][0]["score"],
                                      memecoin_strategy.MIN_ENTRY_SCORE, env=env)
    assert total == pytest.approx(per_buy * 2)


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

    env = dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25", MEMECOIN_WALLET_BUDGET_USD="50")
    report = memecoin.run_autotrade_cycle(storage, env=env, scalp=True)
    assert len(buy_calls) == 1
    assert buy_calls[0][0] == "PF1"
    expected = memecoin.size_for_score(report["entries"][0]["score"],
                                       memecoin_strategy.SCALP_MIN_ENTRY_SCORE, env=env)
    assert buy_calls[0][1] == pytest.approx(expected)
    assert report["entries"][0]["token_address"] == "PF1"


def test_cycle_scalp_entries_use_wider_slippage_than_normal_entries(storage, monkeypatch):
    """Live testing: a scalp-mode buy on a fresh pump.fun bonding-curve coin
    was rejected with custom program error 0x1771 (Anchor 6001 -- slippage
    tolerance exceeded) at the 100bps default meant for established pairs.
    Scalp entries must use the wider SCALP_BUY_SLIPPAGE_BPS instead."""
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])

    from hf_trading_bot import pumpfun_data
    import time as _t
    coin = {"address": "PF1", "symbol": "FRESH", "created_at_ms": int(_t.time() * 1000),
           "sol_raised": 30.0, "market_cap_usd": 4000, "migrated": False, "source": "pumpfun"}
    monkeypatch.setattr(pumpfun_data, "list_new_coins", lambda limit=30, env=None: [coin])
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])

    slippage_seen = []
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, *, kill_switch, slippage_bps=100, env=None, **k:
                        slippage_seen.append(slippage_bps) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    memecoin.run_autotrade_cycle(storage, env=CONFIRMED_ENV, scalp=True)
    assert slippage_seen == [memecoin.SCALP_BUY_SLIPPAGE_BPS]


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


class _FakePumpPortalFeed:
    """Test double: only the two methods run_autotrade_cycle actually calls."""
    def __init__(self, new_coins=None, buyer_stats_by_mint=None):
        self._new_coins = new_coins or []
        self._buyer_stats = buyer_stats_by_mint or {}

    def recent_new_coins(self, limit=30):
        return list(self._new_coins)[:limit]

    def buyer_stats(self, mint):
        return self._buyer_stats.get(mint)


def test_cycle_merges_pumpportal_new_coins_into_live_candidates(storage, monkeypatch):
    # The real fix for "hard to find a coin": pumpfun_live.py's RPC feed
    # can only afford a handful of getTransaction calls/sec across ALL
    # pump.fun activity, so it misses most real new coins. PumpPortal's
    # subscribeNewToken sees every creation with no rate limit -- its
    # detections must show up as real candidates, not just enrich existing
    # ones.
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    buy_calls = []
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: buy_calls.append(token) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    import time as _t
    now_ms = int(_t.time() * 1000)
    live_coin = {"address": "FROM_LIVE", "symbol": "L", "created_at_ms": now_ms - 60_000,
                "sol_raised": 1.0, "market_cap_usd": None, "migrated": False,
                "source": "pumpfun_live"}
    pp_coin = {"address": "FROM_PUMPPORTAL", "symbol": "P", "name": "P Coin",
              "created_at_ms": now_ms, "market_cap_usd": 10_000, "price_usd": None,
              "has_social_links": None, "sol_raised": None, "migrated": False,
              "source": "pumpportal"}
    feed = _FakePumpPortalFeed(new_coins=[pp_coin])

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"),
        scalp=True, live_candidates=[live_coin], pumpportal_feed=feed)
    # FROM_PUMPPORTAL clears the score threshold (market cap); FROM_LIVE
    # (thin sol_raised, no market cap) doesn't -- proves the merged
    # candidate was actually scored and bought, not just carried along.
    assert buy_calls == ["FROM_PUMPPORTAL"]


def test_cycle_pumpportal_candidates_deduplicated_by_address(storage, monkeypatch):
    storage.set_kill_switch(False)
    monkeypatch.setattr(memecoin, "list_positions", lambda s, env=None: [])
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: {"mint_authority": None, "freeze_authority": None})
    monkeypatch.setattr(memecoin, "rugcheck_flags", lambda addr, env=None: [])
    buy_calls = []
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: buy_calls.append(token) or
                        {"tx_signature": "sig", "status": "confirmed", "sol_amount": 0.1,
                         "usd_amount": usd})

    import time as _t
    now_ms = int(_t.time() * 1000)
    # Same address from both sources -- the live-feed version (with market
    # cap already set) must win, not be duplicated into two entries.
    live_coin = {"address": "SAME", "symbol": "L", "created_at_ms": now_ms,
                "sol_raised": 1.0, "market_cap_usd": 10_000, "migrated": False,
                "source": "pumpfun_live"}
    pp_dupe = {"address": "SAME", "symbol": "P", "name": None, "created_at_ms": now_ms,
              "market_cap_usd": None, "price_usd": None, "has_social_links": None,
              "sol_raised": None, "migrated": False, "source": "pumpportal"}
    feed = _FakePumpPortalFeed(new_coins=[pp_dupe])

    report = memecoin.run_autotrade_cycle(
        storage, env=dict(CONFIRMED_ENV, MEMECOIN_MAX_TRADE_USD="25",
                          MEMECOIN_WALLET_BUDGET_USD="50"),
        scalp=True, live_candidates=[live_coin], pumpportal_feed=feed)
    assert buy_calls == ["SAME"]   # bought exactly once, not twice


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
