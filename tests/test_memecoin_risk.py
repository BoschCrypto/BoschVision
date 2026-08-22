"""The mechanical rug-risk screen — pure logic, exhaustively tested, plus a
wiring test for check_token with the network mocked."""
import pytest

from hf_trading_bot import memecoin, memecoin_data, solana_wallet

CLEAN_MINT = {"mint_authority": None, "freeze_authority": None}
HEALTHY_TOKEN = {"liquidity_usd": 100_000, "volume_24h_usd": 500_000,
                 "pair_created_at": None, "price_usd": 0.001, "symbol": "OK"}


def test_clean_token_no_flags():
    flags = memecoin.risk_flags(HEALTHY_TOKEN, CLEAN_MINT)
    assert flags == []
    assert memecoin.risk_verdict(flags) == \
        "no obvious red flags detected (still speculative — not investment advice)"


def test_active_mint_authority_is_red():
    mint = dict(CLEAN_MINT, mint_authority="SomeCreatorAddress")
    flags = memecoin.risk_flags(HEALTHY_TOKEN, mint)
    assert any(f["level"] == "red" and "mint authority" in f["reason"] for f in flags)
    assert memecoin.risk_verdict(flags).startswith("HIGH RISK")


def test_active_freeze_authority_is_red():
    mint = dict(CLEAN_MINT, freeze_authority="SomeCreatorAddress")
    flags = memecoin.risk_flags(HEALTHY_TOKEN, mint)
    assert any(f["level"] == "red" and "freeze authority" in f["reason"] for f in flags)


def test_very_thin_liquidity_is_red():
    token = dict(HEALTHY_TOKEN, liquidity_usd=1_000)
    flags = memecoin.risk_flags(token, CLEAN_MINT)
    assert any(f["level"] == "red" and "liquidity" in f["reason"] for f in flags)


def test_low_liquidity_is_yellow_not_red():
    # Hold volume proportional so only the liquidity check is exercised.
    token = dict(HEALTHY_TOKEN, liquidity_usd=10_000, volume_24h_usd=50_000)
    flags = memecoin.risk_flags(token, CLEAN_MINT)
    assert flags == [{"level": "yellow", "reason": flags[0]["reason"]}]
    assert "liquidity" in flags[0]["reason"]
    assert memecoin.risk_verdict(flags).startswith("ELEVATED RISK")


def test_liquidity_at_yellow_threshold_is_clean():
    token = dict(HEALTHY_TOKEN, liquidity_usd=memecoin.MIN_LIQUIDITY_YELLOW_USD,
                volume_24h_usd=100_000)
    flags = memecoin.risk_flags(token, CLEAN_MINT)
    assert flags == []


def test_high_volume_liquidity_ratio_is_yellow():
    token = dict(HEALTHY_TOKEN, liquidity_usd=10_000_000, volume_24h_usd=500_000_000)
    flags = memecoin.risk_flags(token, CLEAN_MINT)
    assert any("wash trading" in f["reason"] for f in flags)


def test_zero_liquidity_does_not_divide_by_zero():
    token = dict(HEALTHY_TOKEN, liquidity_usd=0, volume_24h_usd=1000)
    flags = memecoin.risk_flags(token, CLEAN_MINT)
    # liquidity=0 trips the "very thin" red flag; the ratio check is skipped safely
    assert any(f["level"] == "red" for f in flags)


def test_new_pool_is_yellow():
    now = 1_000_000_000_000
    token = dict(HEALTHY_TOKEN, liquidity_usd=1_000_000, pair_created_at=now - 3_600_000)  # 1h old
    flags = memecoin.risk_flags(token, CLEAN_MINT, now_ms=now)
    assert any("very new" in f["reason"] for f in flags)


def test_old_pool_is_clean():
    now = 1_000_000_000_000
    token = dict(HEALTHY_TOKEN, liquidity_usd=1_000_000,
                pair_created_at=now - 30 * 24 * 3_600_000)  # 30 days old
    flags = memecoin.risk_flags(token, CLEAN_MINT, now_ms=now)
    assert flags == []


def test_multiple_flags_stack_and_worst_wins_verdict():
    mint = dict(CLEAN_MINT, mint_authority="X")
    token = dict(HEALTHY_TOKEN, liquidity_usd=1_000, volume_24h_usd=5_000)
    flags = memecoin.risk_flags(token, mint)
    assert len(flags) == 2
    assert all(f["level"] == "red" for f in flags)
    assert memecoin.risk_verdict(flags).startswith("HIGH RISK")


# --- check_token wiring (network mocked) ------------------------------

def test_check_token_no_dexscreener_data_raises(monkeypatch):
    monkeypatch.setattr(memecoin_data, "get_token", lambda addr, env=None: None)
    with pytest.raises(memecoin.MemecoinError, match="no DexScreener"):
        memecoin.check_token("MINT")


def test_check_token_combines_sources(monkeypatch):
    monkeypatch.setattr(memecoin_data, "get_token",
                        lambda addr, env=None: dict(HEALTHY_TOKEN, address=addr))
    monkeypatch.setattr(solana_wallet, "get_mint_info",
                        lambda mint, env=None: dict(CLEAN_MINT))
    result = memecoin.check_token("MINT")
    assert result["flags"] == []
    assert result["verdict"].startswith("no obvious red flags")
    assert result["token"]["address"] == "MINT"
