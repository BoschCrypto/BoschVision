"""Multi-token buys and position tracking — pure filtering, storage
aggregation, and orchestration wiring, all offline."""
import pytest

from hf_trading_bot import memecoin, memecoin_data, solana_wallet
from hf_trading_bot.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "m.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # list_positions() now routes get_token_balance() through the shared
    # Solana RPC throttle (_rate_limited_solana_call) -- same reasoning as
    # test_memecoin_autotrade.py's fixture of the same name: don't let
    # cross-test module state make these tests slow or order-dependent.
    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)
    monkeypatch.setattr(memecoin, "_last_mint_check_at", 0.0)


# --- filter_candidates (pure) -----------------------------------------

def _row(addr, liq, vol):
    return {"address": addr, "symbol": addr, "liquidity_usd": liq, "volume_24h_usd": vol}


def test_filter_candidates_drops_thin_liquidity():
    rows = [_row("A", 5_000, 100), _row("B", 20_000, 50)]
    out = memecoin_data.filter_candidates(rows, min_liquidity_usd=10_000, limit=5)
    assert [r["address"] for r in out] == ["B"]


def test_filter_candidates_ranks_by_volume():
    rows = [_row("A", 20_000, 100), _row("B", 20_000, 500), _row("C", 20_000, 50)]
    out = memecoin_data.filter_candidates(rows, min_liquidity_usd=10_000, limit=5)
    assert [r["address"] for r in out] == ["B", "A", "C"]


def test_filter_candidates_excludes_held():
    rows = [_row("A", 20_000, 100), _row("B", 20_000, 500)]
    out = memecoin_data.filter_candidates(rows, min_liquidity_usd=10_000, limit=5,
                                          exclude={"B"})
    assert [r["address"] for r in out] == ["A"]


def test_filter_candidates_respects_limit():
    rows = [_row(str(i), 20_000, i) for i in range(10)]
    out = memecoin_data.filter_candidates(rows, min_liquidity_usd=1, limit=3)
    assert len(out) == 3


# --- storage: per-token cost basis + distinct tokens --------------------

def test_distinct_tokens_and_net_usd(storage):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10, price_usd=1,
                                  tx_signature="s1", status="confirmed")
    storage.record_memecoin_trade(side="buy", token_address="M2", token_symbol="B",
                                  sol_amount=0.2, usd_amount=20, price_usd=1,
                                  tx_signature="s2", status="confirmed")
    storage.record_memecoin_trade(side="sell", token_address="M1", token_symbol="A",
                                  sol_amount=0.02, usd_amount=3, price_usd=1,
                                  tx_signature="s3", status="confirmed")
    assert set(storage.memecoin_distinct_tokens()) == {"M1", "M2"}
    assert storage.memecoin_token_net_usd("M1") == pytest.approx(7.0)
    assert storage.memecoin_token_net_usd("M2") == pytest.approx(20.0)


def test_net_usd_can_go_negative_on_realized_profit(storage):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10, price_usd=1,
                                  tx_signature="s1", status="confirmed")
    storage.record_memecoin_trade(side="sell", token_address="M1", token_symbol="A",
                                  sol_amount=0.15, usd_amount=15, price_usd=1,
                                  tx_signature="s2", status="confirmed")
    assert storage.memecoin_token_net_usd("M1") == pytest.approx(-5.0)


def test_failed_trades_excluded_from_distinct_tokens(storage):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10, price_usd=1,
                                  tx_signature=None, status="failed")
    assert storage.memecoin_distinct_tokens() == []


# --- multi_buy orchestration ---------------------------------------------

def test_multi_buy_one_bad_token_does_not_stop_the_rest(storage, monkeypatch):
    def fake_execute_buy(token, usd, s, *, kill_switch, slippage_bps=100, dry_run=False, env=None):
        if token == "BAD":
            raise memecoin.MemecoinError("no route")
        return {"dry_run": False, "side": "buy", "usd_amount": usd,
               "sol_amount": 0.01, "tx_signature": f"sig-{token}", "status": "confirmed",
               "price_impact_pct": 0.1}
    monkeypatch.setattr(memecoin, "execute_buy", fake_execute_buy)

    results = memecoin.multi_buy(["GOOD1", "BAD", "GOOD2"], 5.0, storage, kill_switch=False)
    assert [r["ok"] for r in results] == [True, False, True]
    assert results[1]["error"] == "no route"
    assert results[0]["tx_signature"] == "sig-GOOD1"


def test_multi_buy_jupiter_network_error_does_not_stop_the_rest(storage, monkeypatch):
    # Real bug found in live testing: execute_buy can raise jupiter.JupiterError
    # (a network/DNS failure calling Jupiter) or solana_wallet.WalletError, not
    # just memecoin.MemecoinError -- multi_buy must catch those too, or one
    # transient network hiccup on ONE token silently aborts the whole batch.
    from hf_trading_bot import jupiter

    monkeypatch.setattr(memecoin.time, "sleep", lambda s: None)   # no real delay in tests

    def fake_execute_buy(token, usd, s, *, kill_switch, slippage_bps=100, dry_run=False, env=None):
        if token == "BAD":
            raise jupiter.JupiterError("Jupiter unreachable: [Errno 11001] getaddrinfo failed")
        return {"dry_run": False, "side": "buy", "usd_amount": usd,
               "sol_amount": 0.01, "tx_signature": f"sig-{token}", "status": "confirmed",
               "price_impact_pct": 0.1}
    monkeypatch.setattr(memecoin, "execute_buy", fake_execute_buy)

    results = memecoin.multi_buy(["GOOD1", "BAD", "GOOD2"], 5.0, storage, kill_switch=False)
    assert [r["ok"] for r in results] == [True, False, True]
    assert "unreachable" in results[1]["error"]
    assert results[2]["tx_signature"] == "sig-GOOD2"


def test_multi_buy_dry_run_passthrough(storage, monkeypatch):
    monkeypatch.setattr(memecoin, "execute_buy",
                        lambda token, usd, s, **k: {"dry_run": True, "sol_amount": 0.01,
                                                    "price_impact_pct": 0.2})
    results = memecoin.multi_buy(["A", "B"], 5.0, storage, kill_switch=False, dry_run=True)
    assert all(r["ok"] and r["dry_run"] for r in results)


# --- list_positions --------------------------------------------------------

def test_list_positions_skips_zero_balance(storage, monkeypatch):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10, price_usd=1,
                                  tx_signature="s1", status="confirmed")
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUB")
    monkeypatch.setattr(solana_wallet, "get_token_balance", lambda pub, mint, env=None: None)
    assert memecoin.list_positions(storage) == []


def test_list_positions_computes_pnl(storage, monkeypatch):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUB")
    monkeypatch.setattr(solana_wallet, "get_token_balance",
                        lambda pub, mint, env=None: {"amount_raw": 10_000_000,
                                                     "decimals": 6, "ui_amount": 10.0})
    monkeypatch.setattr(memecoin_data, "get_token",
                        lambda addr, env=None: {"symbol": "A", "price_usd": 1.5})

    positions = memecoin.list_positions(storage)
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "A"
    assert p.current_value_usd == pytest.approx(15.0)
    assert p.unrealized_pnl_usd == pytest.approx(5.0)
    assert p.unrealized_pnl_pct == pytest.approx(50.0)


def test_list_positions_handles_missing_price(storage, monkeypatch):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="A",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUB")
    monkeypatch.setattr(solana_wallet, "get_token_balance",
                        lambda pub, mint, env=None: {"amount_raw": 10_000_000,
                                                     "decimals": 6, "ui_amount": 10.0})
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)

    positions = memecoin.list_positions(storage)
    assert positions[0].current_value_usd is None
    assert positions[0].unrealized_pnl_usd is None
