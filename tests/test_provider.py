"""Data-provider selection and fallback behaviour. No network."""
import pytest

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.data.provider import (
    AlpacaProvider,
    FallbackProvider,
    YFinanceProvider,
    get_provider,
    source_of,
)


def _bars(n=2):
    return [Bar(t=f"2026-08-{i + 1:02d}", o=1, h=1, l=1, c=1, v=1) for i in range(n)]


class StubProvider:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0

    def daily_bars(self, symbols, lookback_days=220):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result

    def daily_bars_range(self, symbol, start, end=None):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


def test_get_provider_returns_expected_types():
    assert isinstance(get_provider("yfinance"), YFinanceProvider)
    assert isinstance(get_provider("alpaca_only"), AlpacaProvider)
    assert isinstance(get_provider("alpaca"), FallbackProvider)


def test_get_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown data_provider"):
        get_provider("bloomberg")


def test_primary_success_never_calls_secondary():
    primary = StubProvider("alpaca", result={"NVDA": _bars()})
    secondary = StubProvider("yfinance", result={"NVDA": _bars()})
    fp = FallbackProvider(primary, secondary)

    result = fp.daily_bars(["NVDA"])

    assert result == {"NVDA": _bars()}
    assert secondary.calls == 0
    assert fp.last_source == "alpaca"


def test_falls_back_when_primary_raises():
    primary = StubProvider("alpaca", error=RuntimeError("api down"))
    secondary = StubProvider("yfinance", result={"NVDA": _bars()})
    fp = FallbackProvider(primary, secondary)

    result = fp.daily_bars(["NVDA"])

    assert secondary.calls == 1
    assert result == {"NVDA": _bars()}
    assert fp.last_source == "yfinance"


def test_falls_back_when_primary_returns_empty():
    # An empty result is treated as a failure — otherwise a silently broken
    # Alpaca config would look like "no signals today".
    primary = StubProvider("alpaca", result={"NVDA": []})
    secondary = StubProvider("yfinance", result={"NVDA": _bars()})
    fp = FallbackProvider(primary, secondary)

    result = fp.daily_bars(["NVDA"])

    assert secondary.calls == 1
    assert result == {"NVDA": _bars()}


def test_fallback_is_logged_loudly(caplog):
    primary = StubProvider("alpaca", error=RuntimeError("boom"))
    secondary = StubProvider("yfinance", result={"NVDA": _bars()})
    fp = FallbackProvider(primary, secondary)

    with caplog.at_level("WARNING"):
        fp.daily_bars(["NVDA"])

    assert "DATA FALLBACK" in caplog.text
    assert "alpaca" in caplog.text and "yfinance" in caplog.text


def test_alpaca_only_propagates_errors(monkeypatch):
    # No fallback configured means a data outage must surface, not hide.
    provider = get_provider("alpaca_only")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "")
    with pytest.raises(Exception):
        provider.daily_bars(["NVDA"])


def test_source_of_reports_actual_source_used():
    primary = StubProvider("alpaca", error=RuntimeError("down"))
    secondary = StubProvider("yfinance", result=_bars())
    fp = FallbackProvider(primary, secondary)

    assert source_of(fp) == "alpaca+yfinance"  # before any call
    fp.daily_bars_range("NVDA", "2026-01-01")
    assert source_of(fp) == "yfinance"  # after falling back


def test_range_call_also_falls_back():
    primary = StubProvider("alpaca", error=RuntimeError("down"))
    secondary = StubProvider("yfinance", result=_bars(3))
    fp = FallbackProvider(primary, secondary)

    result = fp.daily_bars_range("NVDA", "2026-01-01", "2026-02-01")

    assert len(result) == 3
    assert fp.last_source == "yfinance"
