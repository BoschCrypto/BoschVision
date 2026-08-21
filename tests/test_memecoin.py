"""Memecoin trading: caps, wallet, jupiter, dexscreener, orchestration.
All network and signing calls are monkeypatched — no real key, no real chain.
"""
import pytest

from hf_trading_bot import jupiter, memecoin, memecoin_data, solana_wallet
from hf_trading_bot.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "m.db"))
    yield s
    s.close()


# --- pure cap logic ----------------------------------------------------

def test_cap_reasons_clean_buy_passes():
    assert memecoin.cap_reasons(10, net_deployed_usd=0, budget=50, max_trade=25,
                                kill_switch=False, confirmed=True) == []


def test_cap_reasons_requires_confirm_flag():
    r = memecoin.cap_reasons(10, net_deployed_usd=0, budget=50, max_trade=25,
                             kill_switch=False, confirmed=False)
    assert any("HF_BOT_I_UNDERSTAND_MEMECOIN_RISK" in x for x in r)


def test_cap_reasons_kill_switch_blocks():
    r = memecoin.cap_reasons(10, net_deployed_usd=0, budget=50, max_trade=25,
                             kill_switch=True, confirmed=True)
    assert any("kill switch" in x for x in r)


def test_cap_reasons_per_trade_ceiling():
    r = memecoin.cap_reasons(30, net_deployed_usd=0, budget=50, max_trade=25,
                             kill_switch=False, confirmed=True)
    assert any("ceiling" in x for x in r)


def test_cap_reasons_wallet_budget():
    r = memecoin.cap_reasons(20, net_deployed_usd=40, budget=50, max_trade=25,
                             kill_switch=False, confirmed=True)
    assert any("wallet budget" in x for x in r)


def test_cap_reasons_at_exact_budget_is_allowed():
    assert memecoin.cap_reasons(10, net_deployed_usd=40, budget=50, max_trade=25,
                                kill_switch=False, confirmed=True) == []


def test_sell_block_reasons_ignores_budget():
    # A sell of any size is never blocked by the wallet budget — it reduces exposure.
    assert memecoin.sell_block_reasons(kill_switch=False, confirmed=True) == []
    assert memecoin.sell_block_reasons(kill_switch=True, confirmed=True) != []
    assert memecoin.sell_block_reasons(kill_switch=False, confirmed=False) != []


def test_lamports_and_usd_roundtrip():
    lam = memecoin.lamports_for_usd(150.0, sol_price_usd=150.0)
    assert lam == 1_000_000_000
    assert memecoin.usd_for_lamports(lam, sol_price_usd=150.0) == pytest.approx(150.0)


def test_token_amount_from_raw():
    assert memecoin.token_amount_from_raw(1_500_000, 6) == 1.5


# --- storage: net deployed budget tracking -----------------------------

def test_net_deployed_tracks_buys_and_sells(storage):
    assert storage.memecoin_net_deployed_usd() == 0.0
    storage.record_memecoin_trade(side="buy", token_address="MINT1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=20.0, price_usd=1.0,
                                  tx_signature="sig1", status="confirmed")
    assert storage.memecoin_net_deployed_usd() == 20.0
    storage.record_memecoin_trade(side="sell", token_address="MINT1", token_symbol="X",
                                  sol_amount=0.05, usd_amount=12.0, price_usd=1.2,
                                  tx_signature="sig2", status="confirmed")
    assert storage.memecoin_net_deployed_usd() == pytest.approx(8.0)


def test_net_deployed_never_goes_negative(storage):
    storage.record_memecoin_trade(side="buy", token_address="M", token_symbol=None,
                                  sol_amount=0.1, usd_amount=10.0, price_usd=None,
                                  tx_signature="s1", status="confirmed")
    storage.record_memecoin_trade(side="sell", token_address="M", token_symbol=None,
                                  sol_amount=0.2, usd_amount=25.0, price_usd=None,
                                  tx_signature="s2", status="confirmed")
    assert storage.memecoin_net_deployed_usd() == 0.0


def test_failed_trades_dont_consume_budget(storage):
    storage.record_memecoin_trade(side="buy", token_address="M", token_symbol=None,
                                  sol_amount=0.1, usd_amount=10.0, price_usd=None,
                                  tx_signature=None, status="failed")
    assert storage.memecoin_net_deployed_usd() == 0.0


# --- jupiter client ------------------------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status = payload, status
    def read(self):
        import json
        return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_quote_raises_on_no_route(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", lambda req, timeout=None: _Resp({}))
    with pytest.raises(jupiter.JupiterError):
        jupiter.quote("A", "B", 1000)


def test_quote_returns_data(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({"outAmount": "123", "priceImpactPct": "0.01"}))
    q = jupiter.quote("A", "B", 1000)
    assert q["outAmount"] == "123"
    assert jupiter.price_impact_pct(q) == pytest.approx(1.0)


def test_swap_transaction_requires_field(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen", lambda req, timeout=None: _Resp({}))
    with pytest.raises(jupiter.JupiterError):
        jupiter.swap_transaction({"outAmount": "1"}, "pubkey")


def test_swap_transaction_returns_tx(monkeypatch):
    monkeypatch.setattr(jupiter.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({"swapTransaction": "b64data"}))
    assert jupiter.swap_transaction({}, "pubkey") == "b64data"


# --- dexscreener -----------------------------------------------------------

def test_normalize_and_search_filters_solana(monkeypatch):
    payload = {"pairs": [
        {"chainId": "solana", "baseToken": {"address": "M1", "symbol": "FOO"},
         "priceUsd": "0.001", "liquidity": {"usd": 5000}, "volume": {"h24": 1000}},
        {"chainId": "ethereum", "baseToken": {"address": "M2", "symbol": "BAR"}},
    ]}
    monkeypatch.setattr(memecoin_data, "_get", lambda path, env=None: payload)
    rows = memecoin_data.search("foo")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "FOO"
    assert rows[0]["liquidity_usd"] == 5000


def test_get_token_none_when_no_pairs(monkeypatch):
    monkeypatch.setattr(memecoin_data, "_get", lambda path, env=None: {"pairs": []})
    assert memecoin_data.get_token("M1") is None


def test_trending_dedupes_and_enriches(monkeypatch):
    def fake_get(path, env=None):
        if "token-boosts" in path:
            return [{"chainId": "solana", "tokenAddress": "M1"},
                   {"chainId": "solana", "tokenAddress": "M1"},   # duplicate
                   {"chainId": "ethereum", "tokenAddress": "M2"}]  # wrong chain
        return {"pairs": [{"chainId": "solana", "baseToken": {"address": "M1", "symbol": "FOO"},
                          "liquidity": {"usd": 100}}]}
    monkeypatch.setattr(memecoin_data, "_get", fake_get)
    rows = memecoin_data.trending(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "FOO"


# --- orchestration wiring (guards fire before any network call) --------

def test_execute_buy_blocked_before_any_network_call(storage, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network should never be reached when a guard blocks")
    monkeypatch.setattr(memecoin, "preview_buy", boom)
    with pytest.raises(memecoin.MemecoinError, match="HF_BOT_I_UNDERSTAND_MEMECOIN_RISK"):
        memecoin.execute_buy("MINT", 10.0, storage, kill_switch=False, env={})


def test_execute_buy_dry_run_never_signs_or_records(storage, monkeypatch):
    monkeypatch.setattr(memecoin, "preview_buy",
                        lambda *a, **k: memecoin.TradePreview(
                            side="buy", token_address="MINT", sol_amount=0.05,
                            usd_amount=10.0, price_impact_pct=0.5, quote={"outAmount": "1000"}))
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("dry run must not touch the wallet")))
    result = memecoin.execute_buy("MINT", 10.0, storage, kill_switch=False, dry_run=True,
                                  env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"})
    assert result["dry_run"] is True
    assert storage.memecoin_net_deployed_usd() == 0.0


def test_execute_buy_records_trade_on_success(storage, monkeypatch):
    monkeypatch.setattr(memecoin, "preview_buy",
                        lambda *a, **k: memecoin.TradePreview(
                            side="buy", token_address="MINT", sol_amount=0.05,
                            usd_amount=10.0, price_impact_pct=0.5, quote={"outAmount": "1000000"}))
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUBKEY")
    monkeypatch.setattr(jupiter, "swap_transaction", lambda q, pub, env=None: "b64tx")
    monkeypatch.setattr(solana_wallet, "sign_and_submit", lambda tx, kp, env=None: "SIG123")
    monkeypatch.setattr(solana_wallet, "get_signature_status",
                        lambda sig, env=None: {"confirmed": True, "err": None})
    monkeypatch.setattr(solana_wallet, "get_token_decimals", lambda mint, env=None: 6)

    result = memecoin.execute_buy("MINT", 10.0, storage, kill_switch=False,
                                  env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"})
    assert result["status"] == "confirmed"
    assert storage.memecoin_net_deployed_usd() == 10.0


def test_execute_buy_over_budget_blocked(storage, monkeypatch):
    monkeypatch.setattr(memecoin, "preview_buy", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not reach network")))
    storage.record_memecoin_trade(side="buy", token_address="M", token_symbol=None,
                                  sol_amount=0.3, usd_amount=45.0, price_usd=None,
                                  tx_signature="s", status="confirmed")
    with pytest.raises(memecoin.MemecoinError, match="wallet budget"):
        memecoin.execute_buy("MINT", 10.0, storage, kill_switch=False,
                             env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true",
                                  "MEMECOIN_WALLET_BUDGET_USD": "50"})


def test_execute_sell_blocked_without_confirm(storage):
    with pytest.raises(memecoin.MemecoinError, match="HF_BOT_I_UNDERSTAND_MEMECOIN_RISK"):
        memecoin.execute_sell("MINT", 100, storage, kill_switch=False, env={})


def test_execute_sell_no_balance_raises(storage, monkeypatch):
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUBKEY")
    monkeypatch.setattr(solana_wallet, "get_token_balance", lambda pub, mint, env=None: None)
    with pytest.raises(memecoin.MemecoinError, match="no balance"):
        memecoin.execute_sell("MINT", 100, storage, kill_switch=False,
                              env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"})


def test_execute_sell_dry_run_never_signs(storage, monkeypatch):
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUBKEY")
    monkeypatch.setattr(solana_wallet, "get_token_balance",
                        lambda pub, mint, env=None: {"amount_raw": 1_000_000, "decimals": 6, "ui_amount": 1.0})
    monkeypatch.setattr(jupiter, "quote", lambda *a, **k: {"outAmount": "50000000", "priceImpactPct": "0.01"})
    monkeypatch.setattr(memecoin, "get_sol_price_usd", lambda env=None: 150.0)
    monkeypatch.setattr(solana_wallet, "sign_and_submit", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("dry run must not sign")))
    result = memecoin.execute_sell("MINT", 100, storage, kill_switch=False, dry_run=True,
                                   env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"})
    assert result["dry_run"] is True
    assert storage.memecoin_net_deployed_usd() == 0.0


def test_execute_sell_invalid_pct(storage, monkeypatch):
    monkeypatch.setattr(solana_wallet, "load_keypair", lambda env=None: object())
    monkeypatch.setattr(solana_wallet, "pubkey_str", lambda kp: "PUBKEY")
    with pytest.raises(memecoin.MemecoinError, match="between 0"):
        memecoin.execute_sell("MINT", 0, storage, kill_switch=False,
                              env={"HF_BOT_I_UNDERSTAND_MEMECOIN_RISK": "true"})


# --- solana_wallet ---------------------------------------------------------

def test_load_keypair_requires_env():
    with pytest.raises(solana_wallet.WalletError, match="SOLANA_PRIVATE_KEY"):
        solana_wallet.load_keypair({})


def test_load_keypair_rejects_garbage():
    with pytest.raises(solana_wallet.WalletError):
        solana_wallet.load_keypair({"SOLANA_PRIVATE_KEY": "not-a-valid-key"})


def test_load_keypair_accepts_real_base58_key():
    from solders.keypair import Keypair
    kp = Keypair()
    loaded = solana_wallet.load_keypair({"SOLANA_PRIVATE_KEY": str(kp)})
    assert solana_wallet.pubkey_str(loaded) == str(kp.pubkey())


def test_rpc_url_default_and_override():
    assert solana_wallet.rpc_url({}) == solana_wallet.DEFAULT_RPC_URL
    assert solana_wallet.rpc_url({"SOLANA_RPC_URL": "https://x"}) == "https://x"
