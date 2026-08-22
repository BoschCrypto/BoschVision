"""A Solana wallet: load a keypair, check its balance, sign and submit a
transaction. This is the one place a private key is ever touched.

Real money, no undo. Every function here is deliberately small and does
exactly one RPC call — there is no retry-and-hope logic around signing or
submission, because guessing at a failed transaction is how funds get lost.
Callers decide what to do with a failure; this module never hides one.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Optional

PRIVATE_KEY_ENV = "SOLANA_PRIVATE_KEY"
DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
_TIMEOUT = 30


class WalletError(RuntimeError):
    pass


def rpc_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("SOLANA_RPC_URL") or DEFAULT_RPC_URL).strip()


def load_keypair(env: Optional[dict] = None):
    """The wallet's keypair, from SOLANA_PRIVATE_KEY — the exact base58 string
    Phantom gives you under Settings -> (account) -> Export Private Key.

    Never logged, never echoed, never sent anywhere but signing calls below."""
    try:
        from solders.keypair import Keypair
    except ImportError as ex:
        raise WalletError(
            "solders is not installed — run `pip install -e \".[memecoin]\"`"
        ) from ex
    e = env if env is not None else os.environ
    raw = (e.get(PRIVATE_KEY_ENV) or "").strip()
    if not raw:
        raise WalletError(
            f"Set {PRIVATE_KEY_ENV} in .env — the base58 private key from Phantom "
            f"(Settings -> your account -> Export Private Key). Use a wallet "
            f"dedicated to this bot, never your main holdings."
        )
    try:
        return Keypair.from_base58_string(raw)
    except Exception as ex:  # noqa: BLE001
        raise WalletError(f"SOLANA_PRIVATE_KEY does not look like a valid base58 "
                          f"Solana private key: {ex}") from ex


def pubkey_str(keypair) -> str:
    return str(keypair.pubkey())


def _rpc_call(url: str, method: str, params: list) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        raise WalletError(f"Solana RPC HTTP {ex.code}: {ex.read().decode(errors='replace')[:300]}") from ex
    except urllib.error.URLError as ex:
        raise WalletError(f"Solana RPC unreachable ({url}): {ex.reason}") from ex
    if "error" in data:
        raise WalletError(f"Solana RPC error: {data['error']}")
    return data.get("result", {})


def get_balance_lamports(pubkey: str, *, env: Optional[dict] = None) -> int:
    result = _rpc_call(rpc_url(env), "getBalance", [pubkey])
    value = result.get("value") if isinstance(result, dict) else result
    if value is None:
        raise WalletError(f"unexpected getBalance response: {result!r}")
    return int(value)


def get_balance_sol(pubkey: str, *, env: Optional[dict] = None) -> float:
    return get_balance_lamports(pubkey, env=env) / 1_000_000_000.0


def get_token_balance(owner_pubkey: str, mint: str, *,
                      env: Optional[dict] = None) -> Optional[dict]:
    """The owner's balance of SPL token `mint`, or None if they hold no
    account for it. {"amount_raw": int, "decimals": int, "ui_amount": float}.
    Uses jsonParsed encoding so no associated-token-account derivation is
    needed here."""
    result = _rpc_call(rpc_url(env), "getTokenAccountsByOwner",
                       [owner_pubkey, {"mint": mint}, {"encoding": "jsonParsed"}])
    accounts = (result or {}).get("value") or []
    if not accounts:
        return None
    try:
        info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
        return {"amount_raw": int(info["amount"]), "decimals": int(info["decimals"]),
                "ui_amount": float(info.get("uiAmount") or 0)}
    except (KeyError, TypeError, ValueError) as ex:
        raise WalletError(f"unexpected token account shape: {ex}") from ex


def get_mint_info(mint: str, *, env: Optional[dict] = None) -> dict:
    """SPL token mint account info — specifically whether the mint and
    freeze authorities are still active.

    * An active **mint authority** means the creator can mint unlimited new
      supply at will and dump/dilute on holders.
    * An active **freeze authority** means the creator can freeze any
      wallet's tokens, blocking them from ever selling.
    Both being null (revoked) is the healthy state; either present is one of
    the single clearest rug-risk signals on Solana."""
    result = _rpc_call(rpc_url(env), "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    value = (result or {}).get("value")
    if not value:
        raise WalletError(f"no on-chain account found for mint {mint}")
    try:
        info = value["data"]["parsed"]["info"]
    except (KeyError, TypeError) as ex:
        raise WalletError(f"unexpected mint account shape: {ex}") from ex
    return {"mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "decimals": info.get("decimals"),
            "supply": info.get("supply")}


def get_token_decimals(mint: str, *, env: Optional[dict] = None) -> int:
    result = _rpc_call(rpc_url(env), "getTokenSupply", [mint])
    value = (result or {}).get("value") or {}
    if "decimals" not in value:
        raise WalletError(f"unexpected getTokenSupply response: {result!r}")
    return int(value["decimals"])


def get_signature_status(signature: str, *, env: Optional[dict] = None) -> Optional[dict]:
    """{"confirmed": bool, "err": ...} or None if the signature isn't known
    to the RPC node yet (still propagating)."""
    result = _rpc_call(rpc_url(env), "getSignatureStatuses", [[signature],
                       {"searchTransactionHistory": True}])
    values = (result or {}).get("value") or []
    if not values or values[0] is None:
        return None
    v = values[0]
    status = v.get("confirmationStatus")
    return {"confirmed": status in ("confirmed", "finalized"), "err": v.get("err")}


def sign_and_submit(swap_tx_b64: str, keypair, *, env: Optional[dict] = None) -> str:
    """Sign a Jupiter-built swap transaction (base64 VersionedTransaction) with
    `keypair` and submit it. Returns the transaction signature.

    This BROADCASTS a real, irreversible transaction. There is no dry-run
    inside this function — callers gate that earlier."""
    try:
        from solders.transaction import VersionedTransaction
    except ImportError as ex:
        raise WalletError(
            "solders is not installed — run `pip install -e \".[memecoin]\"`"
        ) from ex
    try:
        raw = base64.b64decode(swap_tx_b64)
        unsigned = VersionedTransaction.from_bytes(raw)
        signed = VersionedTransaction(unsigned.message, [keypair])
    except Exception as ex:  # noqa: BLE001
        raise WalletError(f"could not sign the swap transaction: {ex}") from ex

    signed_b64 = base64.b64encode(bytes(signed)).decode()
    result = _rpc_call(rpc_url(env), "sendTransaction",
                       [signed_b64, {"encoding": "base64", "skipPreflight": False,
                                     "maxRetries": 3}])
    if not result or not isinstance(result, str):
        raise WalletError(f"sendTransaction returned an unexpected result: {result!r}")
    return result
