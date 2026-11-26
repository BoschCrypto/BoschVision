"""Candidate universes for the cross-sectional backtester, with their bias declared.

Universe construction is where a cross-sectional backtest is most often quietly
wrong. If you rank today's S&P 500 members over 2005-2026, every company that
failed out of the index in between is missing from the sample — and those are
disproportionately the names a momentum strategy would have bought and then lost
money on. The backtest then reports the returns of a portfolio that could not
have been assembled at the time.

So every universe here returns its `UniverseMode` alongside its symbols, and the
backtester's `integrity_warnings` refuses to print performance without saying
which one was used. None of this removes the bias; it makes it impossible to
forget about.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from hf_trading_bot.xsection import UniverseMode

# A liquid mega/large-cap list, hand-assembled. Survivorship-biased by
# construction: these are companies that are still around and still liquid in
# 2026, which is exactly the selection a momentum backtest must not make. Useful
# for exercising the machinery and for sanity-checking plumbing; not a basis for
# committing capital. Kept deliberately broad across sectors so the regime and
# ranking logic sees genuine dispersion rather than one industry's cycle.
LIQUID_LARGE_CAP: list[str] = [
    # Technology & communications
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "ADBE", "CRM", "AMD", "INTC",
    "QCOM", "TXN", "IBM", "NOW", "INTU", "AMAT", "MU", "ADI", "LRCX", "KLAC",
    "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG", "TGT",
    "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "MDLZ", "CL", "KMB",
    # Financials
    "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "SPGI",
    "AXP", "USB", "PNC", "TFC", "CB", "MMC", "AON", "ICE", "CME", "COF",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "CVS", "MDT", "ISRG", "SYK", "BSX", "ZTS", "HUM", "CI",
    # Industrials & energy & materials
    "CAT", "DE", "HON", "GE", "BA", "LMT", "RTX", "UNP", "UPS", "FDX",
    "MMM", "EMR", "ETN", "ITW", "NOC", "GD", "CSX", "NSC", "WM", "PH",
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "VLO", "MPC", "OXY", "KMI",
    "LIN", "APD", "SHW", "ECL", "NEM", "FCX", "DOW", "NUE",
    # Utilities & real estate & staples
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL",
    "AMT", "PLD", "CCI", "EQIX", "PSA", "O", "SPG", "WELL",
]

UNIVERSE_KEYS = ("liquid_large_cap", "sp500_current", "csv")


def _wikipedia_sp500() -> list[str]:
    """Current S&P 500 membership, scraped. Requires network."""
    import re
    import urllib.request

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "hf-trading-bot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed URL
        html = resp.read().decode("utf-8", errors="replace")
    # The constituents table is the first one; symbols link to their own pages.
    body = html.split('id="constituents"', 1)[-1]
    syms = re.findall(r'<td><a[^>]*>([A-Z][A-Z.\-]{0,6})</a>', body)
    seen: set[str] = set()
    out: list[str] = []
    for s in syms:
        s = s.replace(".", "-")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _from_csv(path: Path) -> tuple[list[str], UniverseMode]:
    """Load a universe from CSV.

    Two shapes are accepted:

    * one ``symbol`` column — treated as a STATIC_LIST, bias unknown;
    * ``date,symbol`` rows — point-in-time membership, the only form whose
      results mean what they appear to mean. The backtester currently takes a
      flat symbol list, so this returns the union, but the mode is recorded as
      POINT_IN_TIME because the caller has supplied delisted names too.
    """
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"{path} is empty")
    header = [c.strip().lower() for c in rows[0]]
    body = rows[1:] if header and not header[0].isupper() else rows

    if "date" in header and "symbol" in header:
        di, si = header.index("date"), header.index("symbol")
        syms = sorted({r[si].strip().upper() for r in body if len(r) > max(di, si) and r[si].strip()})
        return syms, UniverseMode.POINT_IN_TIME

    col = header.index("symbol") if "symbol" in header else 0
    syms = sorted({r[col].strip().upper() for r in body if r and r[col].strip()})
    return syms, UniverseMode.STATIC_LIST


def load_universe(key: str, csv_path: Optional[str] = None) -> tuple[list[str], UniverseMode]:
    """Return (symbols, mode) for a universe key.

    The mode travels with the symbols so no caller can report a backtest without
    knowing how the sample was built.
    """
    if key == "liquid_large_cap":
        return list(LIQUID_LARGE_CAP), UniverseMode.STATIC_LIST
    if key == "sp500_current":
        return _wikipedia_sp500(), UniverseMode.CURRENT_MEMBERS
    if key == "csv":
        if not csv_path:
            raise ValueError("universe 'csv' requires --universe-csv PATH")
        return _from_csv(Path(csv_path))
    raise ValueError(f"unknown universe {key!r}; expected one of {UNIVERSE_KEYS}")
