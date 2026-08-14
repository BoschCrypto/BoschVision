"""Alpaca broker adapter — safety gate and order payload shape. No network."""
from unittest.mock import patch

import pytest

from hf_trading_bot.broker import alpaca as alpaca_broker
from hf_trading_bot.broker.alpaca import LIVE_BASE, PAPER_BASE, AlpacaBroker, _resolve_base_url


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "body"

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.delenv("ALPACA_BASE_URL", raising=False)
    monkeypatch.delenv("HF_BOT_I_UNDERSTAND_LIVE_TRADING", raising=False)


class StubProvider:
    name = "stub"

    def daily_bars(self, symbols, lookback_days=220):
        return {}

    def daily_bars_range(self, symbol, start, end=None):
        return []


def _broker():
    return AlpacaBroker(data_provider=StubProvider())


# ---- safety gate ----------------------------------------------------------


def test_defaults_to_paper_endpoint():
    broker = _broker()
    assert broker.is_paper is True
    assert broker._base == PAPER_BASE


def test_live_endpoint_refused_without_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", LIVE_BASE)
    with pytest.raises(RuntimeError, match="Refusing to trade against a non-paper"):
        _broker()


def test_live_endpoint_allowed_with_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", LIVE_BASE)
    monkeypatch.setenv("HF_BOT_I_UNDERSTAND_LIVE_TRADING", "true")
    broker = _broker()
    assert broker.is_paper is False


def test_trailing_v2_is_stripped(monkeypatch):
    # Alpaca's dashboard displays the endpoint WITH /v2; pasting it verbatim
    # must not produce /v2/v2/... on every request.
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
    base, is_paper = _resolve_base_url()
    assert base == PAPER_BASE
    assert is_paper is True


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID"):
        _broker()


def test_non_pk_key_on_paper_endpoint_warns(monkeypatch, caplog):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "AKLIVEKEY")
    with caplog.at_level("WARNING"):
        _broker()
    assert "does not start with 'PK'" in caplog.text


# ---- order payloads -------------------------------------------------------


def test_fractional_order_forced_to_market_day():
    broker = _broker()
    with patch.object(
        alpaca_broker.requests, "request", return_value=FakeResponse({"id": "o1", "status": "accepted"})
    ) as mock_req:
        order = broker.place_order("NVDA", 2.7693, "buy", stop_price=95.0)

    body = mock_req.call_args.kwargs["json"]
    assert body["qty"] == "2.7693"
    assert body["type"] == "market"
    assert body["time_in_force"] == "day"
    # Fractional orders can't carry a broker-side stop; engine monitors it.
    assert "stop_price" not in body
    assert order.id == "o1"


def test_whole_share_limit_order_keeps_limit_price():
    broker = _broker()
    with patch.object(
        alpaca_broker.requests, "request", return_value=FakeResponse({"id": "o2", "status": "accepted"})
    ) as mock_req:
        broker.place_order("NVDA", 3, "buy", order_type="limit", limit_price=101.5)

    body = mock_req.call_args.kwargs["json"]
    assert body["qty"] == "3"
    assert body["type"] == "limit"
    assert body["limit_price"] == "101.5"


def test_unknown_side_rejected():
    broker = _broker()
    with pytest.raises(ValueError, match="Unknown side"):
        broker.place_order("NVDA", 1, "hodl")


def test_http_error_surfaces_status_code():
    broker = _broker()
    with patch.object(alpaca_broker.requests, "request", return_value=FakeResponse({}, status_code=422)):
        with pytest.raises(RuntimeError, match="422"):
            broker.place_order("NVDA", 1, "buy")


# ---- response mapping -----------------------------------------------------


def test_get_account_maps_fields():
    payload = {"equity": "203.55", "last_equity": "200.00", "cash": "50.25", "buying_power": "100.50"}
    broker = _broker()
    with patch.object(alpaca_broker.requests, "request", return_value=FakeResponse(payload)):
        account = broker.get_account()
    assert account.equity == 203.55
    assert account.last_equity == 200.00
    assert account.cash == 50.25


def test_get_positions_skips_zero_quantity():
    payload = [
        {"symbol": "NVDA", "qty": "1.5", "avg_entry_price": "100", "current_price": "110",
         "market_value": "165", "unrealized_pl": "15", "unrealized_plpc": "0.1"},
        {"symbol": "QQQ", "qty": "0", "avg_entry_price": "0", "current_price": "0",
         "market_value": "0", "unrealized_pl": "0", "unrealized_plpc": "0"},
    ]
    broker = _broker()
    with patch.object(alpaca_broker.requests, "request", return_value=FakeResponse(payload)):
        positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "NVDA"
    assert positions[0].qty == 1.5
