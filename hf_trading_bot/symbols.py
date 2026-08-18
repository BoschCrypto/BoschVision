"""Symbol helpers — tell crypto from equities, and normalise to Alpaca's form.

Alpaca trades both stocks and crypto through one account, but they differ in
ways that matter to us:

* crypto pairs are written ``BASE/QUOTE`` (``BTC/USD``); stocks are bare
  (``AAPL``);
* crypto orders must be ``time_in_force=gtc`` (Alpaca rejects ``day``);
* crypto trades 24/7 and is **not** a security, so the Pattern-Day-Trader rule
  does not apply to it;
* crypto bars come from a different market-data endpoint.

Everything that needs to branch on "is this crypto?" asks here, so the rule
lives in exactly one place.
"""
from __future__ import annotations

# Common crypto bases we accept as a bare ticker ("BTC") and expand to a USD
# pair ("BTC/USD"). Not exhaustive — anything already written with a "/" is
# treated as crypto regardless, so a pair we didn't list still works.
KNOWN_CRYPTO_BASES = frozenset({
    "BTC", "ETH", "USDT", "USDC", "SOL", "DOGE", "AVAX", "LINK", "LTC", "BCH",
    "UNI", "AAVE", "XRP", "ADA", "DOT", "MATIC", "SHIB", "XTZ", "SUSHI", "YFI",
    "MKR", "GRT", "CRV", "BAT", "TRX", "XLM", "NEAR", "PEPE",
})


def is_crypto(symbol: str) -> bool:
    """True if `symbol` is a crypto asset rather than an equity.

    A slash always means crypto (``BTC/USD``); a bare known base (``BTC``,
    ``ETH``) is crypto too. Everything else is treated as an equity.
    """
    if not symbol:
        return False
    s = symbol.strip().upper()
    if "/" in s:
        return True
    return s in KNOWN_CRYPTO_BASES


def normalize_symbol(symbol: str) -> str:
    """Canonical Alpaca form. Equities upper-cased (``aapl`` -> ``AAPL``);
    crypto returned as a ``BASE/USD`` pair (``btc`` -> ``BTC/USD``,
    ``ETH/USD`` unchanged)."""
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if "/" in s:
        return s
    if s in KNOWN_CRYPTO_BASES:
        return f"{s}/USD"
    return s


def split_symbols(symbols: list[str]) -> tuple[list[str], list[str]]:
    """Partition into (equities, crypto), each normalised. Order preserved
    within each bucket; duplicates kept (caller dedupes if it cares)."""
    equities: list[str] = []
    crypto: list[str] = []
    for sym in symbols:
        norm = normalize_symbol(sym)
        (crypto if is_crypto(norm) else equities).append(norm)
    return equities, crypto
