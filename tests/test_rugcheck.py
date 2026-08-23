"""RugCheck.xyz client — normalization and the offline-safe parts. No
network — HTTP is stubbed."""
import json
import urllib.error

import pytest

from hf_trading_bot import memecoin, rugcheck


class _Resp:
    def __init__(self, payload):
        self._p = payload
    def read(self):
        return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_get_report_normalizes_score_and_top_holder(monkeypatch):
    raw = {
        "score_normalised": 72,
        "topHolders": [
            {"address": "pool", "pct": 60.0, "isLp": True},
            {"address": "walletX", "pct": 22.5, "isLp": False},
            {"address": "walletY", "pct": 3.1, "isLp": False},
        ],
        "lpLockedPct": 95.0,
        "risks": [{"name": "Mint authority", "level": "warn", "description": "..."}],
    }
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    report = rugcheck.get_report("MINT1")
    assert report["score"] == 72
    assert report["top_holder_pct"] == 22.5   # excludes the LP holder
    assert report["top5_holders_pct"] == pytest.approx(25.6)   # 22.5 + 3.1, LP excluded
    assert report["lp_locked_pct"] == 95.0
    assert report["risks"][0]["name"] == "Mint authority"


def test_get_report_top_holder_falls_back_to_all_when_no_non_lp_marked(monkeypatch):
    raw = {"topHolders": [{"address": "a", "pct": 40.0}, {"address": "b", "pct": 10.0}]}
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    report = rugcheck.get_report("MINT1")
    assert report["top_holder_pct"] == 40.0
    assert report["top5_holders_pct"] == 50.0   # falls back to all holders too


def test_get_report_top5_holders_pct_caps_at_five_wallets(monkeypatch):
    # A bundle spread across many small wallets, none individually alarming,
    # but only the top 5 should count toward the aggregate.
    raw = {"topHolders": [{"address": f"w{i}", "pct": 8.0, "isLp": False} for i in range(10)]}
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    report = rugcheck.get_report("MINT1")
    assert report["top_holder_pct"] == 8.0
    assert report["top5_holders_pct"] == pytest.approx(40.0)   # 5 * 8.0, not 10 * 8.0


def test_get_report_handles_missing_fields():
    assert rugcheck._normalize({}) == {"score": None, "top_holder_pct": None,
                                       "top5_holders_pct": None,
                                       "lp_locked_pct": None, "risks": []}


def test_get_report_lp_locked_pct_from_nested_market(monkeypatch):
    raw = {"markets": [{"lp": {"lockedPct": 88.0}}]}
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw))
    report = rugcheck.get_report("MINT1")
    assert report["lp_locked_pct"] == 88.0


def test_get_report_returns_none_on_404(monkeypatch):
    def raise_404(req, timeout=None):
        raise urllib.error.HTTPError("url", 404, "not found", {}, None)
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen", raise_404)
    assert rugcheck.get_report("BRAND_NEW_MINT") is None


def test_get_report_raises_rugcheck_error_on_other_http_errors(monkeypatch):
    def raise_500(req, timeout=None):
        raise urllib.error.HTTPError("url", 500, "server error", {}, None)
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen", raise_500)
    with pytest.raises(rugcheck.RugCheckError):
        rugcheck.get_report("MINT1")


def test_get_report_raises_rugcheck_error_on_unreachable(monkeypatch):
    def raise_url_error(req, timeout=None):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen", raise_url_error)
    with pytest.raises(rugcheck.RugCheckError):
        rugcheck.get_report("MINT1")


def test_get_report_raises_rugcheck_error_on_bad_json(monkeypatch):
    class _BadResp:
        def read(self): return b"not json"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen",
                        lambda req, timeout=None: _BadResp())
    with pytest.raises(rugcheck.RugCheckError):
        rugcheck.get_report("MINT1")


def test_base_url_default_and_override():
    assert rugcheck.base_url({}) == rugcheck.DEFAULT_BASE_URL
    assert rugcheck.base_url({"RUGCHECK_BASE_URL": "https://x"}) == "https://x"


def test_api_key_added_as_bearer_header_when_set(monkeypatch):
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Resp({})
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen", fake_urlopen)
    rugcheck.get_report("MINT1", env={"RUGCHECK_API_KEY": "secret123"})
    assert captured["headers"].get("Authorization") == "Bearer secret123"


def test_no_authorization_header_when_no_api_key(monkeypatch):
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Resp({})
    monkeypatch.setattr(rugcheck.urllib.request, "urlopen", fake_urlopen)
    rugcheck.get_report("MINT1", env={})
    assert "Authorization" not in captured["headers"]


# --- memecoin.rugcheck_flags — the final pre-buy gate ----------------------

def test_rugcheck_flags_red_on_high_concentration(monkeypatch):
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 10, "top_holder_pct": 35.0,
                                               "lp_locked_pct": 90, "risks": []})
    flags = memecoin.rugcheck_flags("MINT1")
    assert any(f["level"] == "red" and "bundled" in f["reason"] for f in flags)


def test_rugcheck_flags_yellow_on_moderate_concentration(monkeypatch):
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 10, "top_holder_pct": 15.0,
                                               "lp_locked_pct": 90, "risks": []})
    flags = memecoin.rugcheck_flags("MINT1")
    assert flags[0]["level"] == "yellow"


def test_rugcheck_flags_red_on_bundled_launch_split_across_wallets(monkeypatch):
    # No single wallet crosses the 20% red threshold, but the top 5
    # combined hold well over a third of supply -- the same insider
    # pattern, just spread thin enough to dodge the single-holder check.
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 10, "top_holder_pct": 18.0,
                                               "top5_holders_pct": 42.0,
                                               "lp_locked_pct": 90, "risks": []})
    flags = memecoin.rugcheck_flags("MINT1")
    assert any(f["level"] == "red" and "top 5" in f["reason"] for f in flags)


def test_rugcheck_flags_no_flag_when_top5_missing_from_report(monkeypatch):
    # A report shape without the new field must not crash or false-flag.
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 10, "top_holder_pct": 5.0,
                                               "lp_locked_pct": 90, "risks": []})
    assert memecoin.rugcheck_flags("MINT1") == []


def test_rugcheck_flags_red_on_high_composite_score(monkeypatch):
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 91, "top_holder_pct": 2.0,
                                               "lp_locked_pct": 90, "risks": []})
    flags = memecoin.rugcheck_flags("MINT1")
    assert any(f["level"] == "red" and "composite" in f["reason"] for f in flags)


def test_rugcheck_flags_clean_report_yields_no_flags(monkeypatch):
    monkeypatch.setattr(memecoin.rugcheck, "get_report",
                        lambda addr, env=None: {"score": 5, "top_holder_pct": 3.0,
                                               "lp_locked_pct": 100, "risks": []})
    assert memecoin.rugcheck_flags("MINT1") == []


def test_rugcheck_flags_no_report_yields_no_flags(monkeypatch):
    monkeypatch.setattr(memecoin.rugcheck, "get_report", lambda addr, env=None: None)
    assert memecoin.rugcheck_flags("MINT1") == []


def test_rugcheck_flags_error_is_swallowed_not_raised(monkeypatch):
    def boom(addr, env=None):
        raise memecoin.rugcheck.RugCheckError("down")
    monkeypatch.setattr(memecoin.rugcheck, "get_report", boom)
    assert memecoin.rugcheck_flags("MINT1") == []
