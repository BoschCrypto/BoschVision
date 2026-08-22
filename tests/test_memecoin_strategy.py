"""Momentum scoring, entry criteria, exit rules — and the storage state
(peak price ratchet, trim tracking, position basis) they depend on."""
import pytest

from hf_trading_bot import memecoin, memecoin_strategy
from hf_trading_bot.storage import Storage

CLEAN_MINT = {"mint_authority": None, "freeze_authority": None}


def _token(**overrides):
    base = {
        "symbol": "TEST", "liquidity_usd": 100_000, "volume_24h_usd": 500_000,
        "price_change_h1_pct": 5.0, "price_change_h6_pct": 10.0, "price_change_h24_pct": 20.0,
        "buys_h1": 60, "sells_h1": 20, "buys_h24": 500, "sells_h24": 300,
        "pair_created_at": None,
    }
    base.update(overrides)
    return base


# --- momentum_score ------------------------------------------------------

def test_momentum_score_strong_token_scores_high():
    t = _token(price_change_h1_pct=15.0, price_change_h6_pct=10.0,
               buys_h1=90, sells_h1=10, liquidity_usd=200_000)
    result = memecoin_strategy.momentum_score(t)
    assert result["score"] > 60
    assert len(result["components"]) > 0


def test_momentum_score_dead_token_scores_low():
    t = _token(price_change_h1_pct=-5.0, price_change_h6_pct=-10.0,
              price_change_h24_pct=-20.0, buys_h1=5, sells_h1=5,
              liquidity_usd=1_000, volume_24h_usd=500)
    result = memecoin_strategy.momentum_score(t)
    assert result["score"] < 30


def test_momentum_score_thin_transactions_ignored():
    t = _token(buys_h1=2, sells_h1=1)   # total 3 < 10 minimum
    result = memecoin_strategy.momentum_score(t)
    reasons = " ".join(c["reason"] for c in result["components"])
    assert "too few 1h transactions" in reasons


def test_momentum_score_wash_trading_shaped_volume_flagged():
    t = _token(liquidity_usd=1_000_000, volume_24h_usd=50_000_000)  # 50x ratio
    result = memecoin_strategy.momentum_score(t)
    reasons = " ".join(c["reason"] for c in result["components"])
    assert "wash trading" in reasons


def test_momentum_score_capped_at_100():
    t = _token(price_change_h1_pct=1000.0, price_change_h6_pct=1.0,
              price_change_h24_pct=1000.0, buys_h1=1000, sells_h1=0,
              liquidity_usd=10_000_000)
    result = memecoin_strategy.momentum_score(t)
    assert result["score"] <= 100.0


# --- entry_signal ----------------------------------------------------------

def test_entry_signal_red_flag_always_blocks_regardless_of_momentum():
    hot_mint = dict(CLEAN_MINT, mint_authority="Creator")   # red flag
    t = _token(price_change_h1_pct=50.0, buys_h1=100, sells_h1=5)  # very hot momentum
    sig = memecoin_strategy.entry_signal(t, hot_mint)
    assert sig.enter is False
    assert "red risk flag" in sig.reasons[0]


def test_entry_signal_passes_on_strong_clean_token():
    t = _token(price_change_h1_pct=15.0, buys_h1=90, sells_h1=10, liquidity_usd=200_000)
    sig = memecoin_strategy.entry_signal(t, CLEAN_MINT)
    assert sig.enter is True
    assert sig.score >= memecoin_strategy.MIN_ENTRY_SCORE


def test_entry_signal_fails_below_score_threshold():
    # Liquidity kept above the red-flag floor so this isolates a low momentum
    # score, not the risk screen's separate liquidity check.
    t = _token(price_change_h1_pct=-5.0, buys_h1=5, sells_h1=5,
              liquidity_usd=25_000, volume_24h_usd=10_000)
    sig = memecoin_strategy.entry_signal(t, CLEAN_MINT)
    assert sig.enter is False
    assert any("below the" in r for r in sig.reasons)


def test_entry_signal_respects_custom_min_score():
    t = _token(price_change_h1_pct=1.0, buys_h1=15, sells_h1=13, liquidity_usd=25_000)
    weak_sig = memecoin_strategy.entry_signal(t, CLEAN_MINT, min_score=99.0)
    lenient_sig = memecoin_strategy.entry_signal(t, CLEAN_MINT, min_score=1.0)
    assert weak_sig.enter is False
    assert lenient_sig.enter is True


# --- exit_signal -------------------------------------------------------

def test_exit_signal_stop_loss_fires_first():
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=0.60,
                                        peak_price_usd=1.0, hours_held=1.0)
    assert sig.exit is True and sig.sell_pct == 100.0
    assert "stop-loss" in sig.reason


def test_exit_signal_stop_loss_outranks_take_profit_math():
    # Even if peak was high, a current price at stop-loss level still exits fully.
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=0.60,
                                        peak_price_usd=5.0, hours_held=10.0)
    assert sig.exit is True and "stop-loss" in sig.reason


def test_exit_signal_trim_1_at_plus_100():
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=2.05,
                                        peak_price_usd=2.05, hours_held=2.0)
    assert sig.exit is True
    assert sig.sell_pct == memecoin_strategy.TRIM_1_SELL_PCT
    assert "+100" in sig.reason or "100.0" in sig.reason


def test_exit_signal_trim_1_skipped_if_already_trimmed():
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=2.05,
                                        peak_price_usd=2.05, hours_held=2.0,
                                        already_trimmed_1=True)
    assert sig.exit is False   # falls through since 2.05 < trim-2 threshold too


def test_exit_signal_trim_2_at_plus_300():
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=4.5,
                                        peak_price_usd=4.5, hours_held=5.0,
                                        already_trimmed_1=True)
    assert sig.exit is True
    assert sig.sell_pct == memecoin_strategy.TRIM_2_SELL_PCT


def test_exit_signal_trailing_stop_protects_gains():
    # Peaked at +80%, now given back 35% from that peak -> exceeds 30% trail limit.
    peak = 1.80
    current = peak * (1 - 0.35)
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=current,
                                        peak_price_usd=peak, hours_held=3.0,
                                        already_trimmed_1=True, already_trimmed_2=True)
    assert sig.exit is True
    assert "trailing stop" in sig.reason


def test_exit_signal_no_trailing_stop_below_activation_gain():
    # Only up 20% at peak — trailing stop shouldn't have activated yet.
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=1.05,
                                        peak_price_usd=1.20, hours_held=3.0)
    assert sig.exit is False


def test_exit_signal_momentum_stall_after_hold_period():
    t = _token(price_change_h1_pct=-2.0, buys_h1=5, sells_h1=15)
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=1.05,
                                        peak_price_usd=1.10, hours_held=8.0, token=t)
    assert sig.exit is True
    assert "stalled" in sig.reason


def test_exit_signal_no_stall_before_hold_threshold():
    t = _token(price_change_h1_pct=-2.0, buys_h1=5, sells_h1=15)
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=1.05,
                                        peak_price_usd=1.10, hours_held=1.0, token=t)
    assert sig.exit is False


def test_exit_signal_holds_when_nothing_triggers():
    sig = memecoin_strategy.exit_signal(entry_price_usd=1.0, current_price_usd=1.10,
                                        peak_price_usd=1.15, hours_held=1.0)
    assert sig.exit is False
    assert "no exit rule" in sig.reason


def test_exit_signal_invalid_entry_price_raises():
    with pytest.raises(memecoin.MemecoinError):
        memecoin_strategy.exit_signal(entry_price_usd=0, current_price_usd=1.0,
                                      peak_price_usd=1.0, hours_held=1.0)


# --- storage: position basis, peak ratchet, trim tracking ------------------

@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "m.db"))
    yield s
    s.close()


def test_position_basis_weighted_average(storage):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature="s1", status="confirmed")
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=20.0, price_usd=2.0,
                                  tx_signature="s2", status="confirmed")
    basis = storage.memecoin_position_basis("M1")
    # 10 tokens @ $1 (qty=10) + 10 tokens @ $2 (qty=10) => 20 qty, $30 cost => avg $1.50
    assert basis["avg_entry_price"] == pytest.approx(1.50)
    assert basis["first_buy_at"] is not None


def test_position_basis_no_trades_returns_none(storage):
    basis = storage.memecoin_position_basis("NOPE")
    assert basis == {"avg_entry_price": None, "first_buy_at": None}


def test_position_basis_ignores_failed_trades(storage):
    storage.record_memecoin_trade(side="buy", token_address="M1", token_symbol="X",
                                  sol_amount=0.1, usd_amount=10.0, price_usd=1.0,
                                  tx_signature=None, status="failed")
    basis = storage.memecoin_position_basis("M1")
    assert basis["avg_entry_price"] is None


def test_peak_price_ratchets_up_never_down(storage):
    assert storage.memecoin_update_peak("M1", 1.0) == 1.0
    assert storage.memecoin_update_peak("M1", 2.0) == 2.0
    assert storage.memecoin_update_peak("M1", 1.5) == 2.0   # doesn't drop


def test_mark_trimmed_and_state(storage):
    storage.memecoin_update_peak("M1", 1.0)
    storage.memecoin_mark_trimmed("M1", 1)
    state = storage.memecoin_peak_state("M1")
    assert state["trimmed_1"] == 1
    assert state["trimmed_2"] == 0
    storage.memecoin_mark_trimmed("M1", 2)
    state = storage.memecoin_peak_state("M1")
    assert state["trimmed_1"] == 1 and state["trimmed_2"] == 1


def test_mark_trimmed_invalid_level_raises(storage):
    with pytest.raises(ValueError):
        storage.memecoin_mark_trimmed("M1", 3)


def test_clear_position_state(storage):
    storage.memecoin_update_peak("M1", 5.0)
    storage.memecoin_mark_trimmed("M1", 1)
    storage.memecoin_clear_position_state("M1")
    assert storage.memecoin_peak_state("M1") is None
