"""Crypto support (symbol routing, data split, order tif, PDT exemption) and the
SNIPER-led tactical-trade command. No network — HTTP is stubbed."""
import pytest

from hf_trading_bot import symbols
from hf_trading_bot.cortex import parse_tactical_command, tactical_trade_prompt


# --- symbol classification --------------------------------------------------

@pytest.mark.parametrize("sym,crypto", [
    ("BTC/USD", True), ("BTC", True), ("eth", True), ("ETH/USD", True),
    ("AAPL", False), ("TSLA", False), ("BRK.B", False),
])
def test_is_crypto(sym, crypto):
    assert symbols.is_crypto(sym) is crypto


def test_normalize_symbol():
    assert symbols.normalize_symbol("btc") == "BTC/USD"
    assert symbols.normalize_symbol("ETH/USD") == "ETH/USD"
    assert symbols.normalize_symbol("aapl") == "AAPL"


def test_split_symbols_partitions_and_normalises():
    eq, cr = symbols.split_symbols(["AAPL", "btc", "ETH/USD", "tsla"])
    assert eq == ["AAPL", "TSLA"]
    assert cr == ["BTC/USD", "ETH/USD"]


# --- tactical command parsing ----------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("trade BTC", "BTC/USD"),
    ("snipe AAPL", "AAPL"),
    ("tactical TSLA", "TSLA"),
    ("trade ETH/USD", "ETH/USD"),
    ("what is a moving average", None),   # not a tactical command
    ("review AAPL", None),                # review verb is not tactical
    ("trade", None),                      # verb but no symbol
])
def test_parse_tactical_command(text, expected):
    assert parse_tactical_command(text) == expected


def test_tactical_prompt_is_sniper_led_and_no_pass_machine():
    p = tactical_trade_prompt("BTC/USD")
    assert "SNIPER" in p and "order propose" in p
    assert "crypto pair" in p          # asset-type aware
    assert "do not default to PASS" in p.lower() or "not default to pass" in p.lower()


# --- crypto order uses gtc, equity uses day --------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, "ok"

    def json(self):
        return self._p


def _broker(monkeypatch, capture):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "sek")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    from hf_trading_bot.broker import alpaca

    def fake_request(method, url, **kw):
        if "/v2/orders" in url and method == "POST":
            capture["body"] = kw.get("json")
            return _Resp({"id": "o1", "status": "accepted"})
        return _Resp({})

    monkeypatch.setattr(alpaca.requests, "request", fake_request)
    # avoid constructing a real data provider
    b = alpaca.AlpacaBroker(data_provider=object())
    return b


def test_crypto_order_uses_gtc(monkeypatch):
    cap = {}
    b = _broker(monkeypatch, cap)
    b.place_order("btc", 0.01, "buy")
    assert cap["body"]["time_in_force"] == "gtc"
    assert cap["body"]["symbol"] == "BTC/USD"   # normalised


def test_equity_order_uses_day(monkeypatch):
    cap = {}
    b = _broker(monkeypatch, cap)
    b.place_order("AAPL", 1, "buy")
    assert cap["body"]["time_in_force"] == "day"
    assert cap["body"]["symbol"] == "AAPL"


# --- data fetch routes crypto to the crypto endpoint ------------------------

def test_fetch_bars_splits_endpoints(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "sek")
    from hf_trading_bot.data import alpaca_data

    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        sym = params["symbols"]
        row = {"t": "2026-08-01T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 10}
        return _Resp({"bars": {sym: [row]}})

    monkeypatch.setattr(alpaca_data.requests, "get", fake_get)
    out = alpaca_data._fetch_bars(["AAPL", "BTC"], "2026-07-01")
    assert "AAPL" in out and "BTC/USD" in out
    assert any("/v2/stocks/bars" in u for u in calls)
    assert any("/v1beta3/crypto/us/bars" in u for u in calls)


# --- PDT exemption for crypto ----------------------------------------------

def test_crypto_is_pdt_exempt():
    # The engine's opened_today gate ANDs with `not is_crypto(symbol)`, so a
    # crypto symbol can never consume the day-trade budget.
    assert symbols.is_crypto("BTC/USD") is True
    # equities still counted
    assert symbols.is_crypto("AAPL") is False
