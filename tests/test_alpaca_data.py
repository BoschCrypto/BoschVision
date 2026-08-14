"""Offline tests for the Alpaca data provider. All HTTP is mocked — these
must never touch the network."""
from unittest.mock import patch

import pytest

from hf_trading_bot.data import alpaca_data


class FakeResponse:
    def __init__(self, payload, status_code=200, text="ok"):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")


def _bar_row(day: str, close: float) -> dict:
    return {"t": f"{day}T04:00:00Z", "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 1000}


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    with pytest.raises(alpaca_data.AlpacaCredentialsMissing):
        alpaca_data.fetch_daily_bars("NVDA", 10)


def test_rfc3339_timestamp_truncated_to_date():
    payload = {"bars": {"NVDA": [_bar_row("2026-08-03", 100.0)]}, "next_page_token": None}
    with patch.object(alpaca_data.requests, "get", return_value=FakeResponse(payload)):
        bars = alpaca_data.fetch_daily_bars("NVDA", 10)
    assert len(bars) == 1
    # Must match the yfinance provider's "YYYY-MM-DD" so Bar.t stays comparable.
    assert bars[0].t == "2026-08-03"
    assert bars[0].c == 100.0
    assert bars[0].h == 101.0


def test_pagination_follows_next_page_token():
    pages = [
        FakeResponse({"bars": {"NVDA": [_bar_row("2026-08-03", 100.0)]}, "next_page_token": "tok"}),
        FakeResponse({"bars": {"NVDA": [_bar_row("2026-08-04", 101.0)]}, "next_page_token": None}),
    ]
    with patch.object(alpaca_data.requests, "get", side_effect=pages) as mock_get:
        bars = alpaca_data.fetch_daily_bars_range("NVDA", "2026-08-01")
    assert mock_get.call_count == 2
    assert [b.t for b in bars] == ["2026-08-03", "2026-08-04"]


def test_symbols_are_chunked_above_50():
    symbols = [f"S{i}" for i in range(120)]
    payload = {"bars": {}, "next_page_token": None}
    with patch.object(alpaca_data.requests, "get", return_value=FakeResponse(payload)) as mock_get:
        alpaca_data.fetch_batch_daily_bars(symbols, 10)
    # 120 symbols / 50 per chunk = 3 requests
    assert mock_get.call_count == 3


def test_non_200_raises_with_status():
    with patch.object(
        alpaca_data.requests, "get", return_value=FakeResponse({}, status_code=403, text="forbidden")
    ):
        with pytest.raises(RuntimeError, match="403"):
            alpaca_data.fetch_daily_bars("NVDA", 10)


def test_bars_sorted_oldest_first():
    payload = {
        "bars": {"NVDA": [_bar_row("2026-08-05", 102.0), _bar_row("2026-08-03", 100.0)]},
        "next_page_token": None,
    }
    with patch.object(alpaca_data.requests, "get", return_value=FakeResponse(payload)):
        bars = alpaca_data.fetch_daily_bars_range("NVDA", "2026-08-01")
    assert [b.t for b in bars] == ["2026-08-03", "2026-08-05"]


def test_lookback_trims_to_requested_count():
    rows = [_bar_row(f"2026-08-{d:02d}", 100.0 + d) for d in range(1, 11)]
    payload = {"bars": {"NVDA": rows}, "next_page_token": None}
    with patch.object(alpaca_data.requests, "get", return_value=FakeResponse(payload)):
        bars = alpaca_data.fetch_daily_bars("NVDA", 3)
    assert len(bars) == 3
    assert bars[-1].t == "2026-08-10"


def test_feed_defaults_to_iex(monkeypatch):
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    payload = {"bars": {}, "next_page_token": None}
    with patch.object(alpaca_data.requests, "get", return_value=FakeResponse(payload)) as mock_get:
        alpaca_data.fetch_daily_bars("NVDA", 10)
    assert mock_get.call_args.kwargs["params"]["feed"] == "iex"
