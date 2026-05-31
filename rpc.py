from __future__ import annotations
"""
rpc.py — Solana JSON-RPC helpers
==================================
All functions that query or relay to the Solana network via the beacon pool.
No local signing happens here — see wallet.py for that.
"""

import json
import concurrent.futures

import state
from shared import (
    MAX_RENDERED_LOG_LINES, is_u64, log_info, log_ok, log_warn, log_err,
    rpc_error_message, terminal_safe_text,
    BOLD, CYAN, GREEN, RED, RESET, DIM,
)

_NO_BEACON_RESP = "No response from beacon"


# ═══════════════════════════════════════════════════════════════════════════════
# Core RPC dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

def rpc_call(method, params=None):
    return state.pool.call(method, params)


def _extract_result(resp: dict):
    """
    Solana RPC wraps some results in {"context":..., "value":...}.
    Others return the value directly. Handle both.
    """
    if not isinstance(resp, dict):
        return None
    r = resp.get("result")
    if isinstance(r, dict) and "value" in r:
        return r["value"]
    return r


def _is_nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value)


# ═══════════════════════════════════════════════════════════════════════════════
# Query functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_balance(address):
    resp = rpc_call("getBalance", [address])
    if resp is None:
        return
    if "error" in resp:
        log_err(f"RPC error: {rpc_error_message(resp['error'])}")
        return
    lamports = _extract_result(resp)
    if not is_u64(lamports):
        log_warn(f"Unexpected getBalance response: {json.dumps(resp)}")
        return
    sol = lamports / 1_000_000_000
    print(f"\n  {GREEN}{BOLD}{terminal_safe_text(address)}{RESET}")
    print(f"  Balance: {BOLD}{sol:.9f} SOL{RESET}  ({lamports:,} lamports)\n")


def confidential_get_balance(_address: str) -> None:
    """Fail closed until a real MPC balance-query handler exists."""
    log_warn("Confidential balance is unavailable: no MPC query handler is implemented")
    log_warn("Use the plain balance command explicitly only if address disclosure is acceptable")


def get_slot():
    resp = rpc_call("getSlot")
    if resp is None:
        log_err(_NO_BEACON_RESP); return
    if "error" in resp:
        log_err(f"RPC error: {resp['error']}"); return
    val = _extract_result(resp)
    if is_u64(val):
        print(f"\n  Current slot: {BOLD}{val:,}{RESET}\n")
    else:
        log_warn(f"Unexpected response: {json.dumps(resp)}")


def get_block_height():
    resp = rpc_call("getBlockHeight")
    if resp is None:
        log_err(_NO_BEACON_RESP); return
    if "error" in resp:
        log_err(f"RPC error: {resp['error']}"); return
    val = _extract_result(resp)
    if is_u64(val):
        print(f"\n  Block height: {BOLD}{val:,}{RESET}\n")
    else:
        log_warn(f"Unexpected response: {json.dumps(resp)}")


def get_transaction_count():
    resp = rpc_call("getTransactionCount")
    if resp is None:
        log_err(_NO_BEACON_RESP); return
    if "error" in resp:
        log_err(f"RPC error: {resp['error']}"); return
    val = _extract_result(resp)
    if is_u64(val):
        print(f"\n  Transaction count: {BOLD}{val:,}{RESET}\n")
    else:
        log_warn(f"Unexpected response: {json.dumps(resp)}")


def get_recent_blockhash() -> str | None:
    resp = rpc_call("getLatestBlockhash")
    if resp is None:
        log_err(_NO_BEACON_RESP); return None
    if "error" in resp:
        log_err(f"RPC error: {resp['error']}"); return None
    r = resp.get("result")
    if isinstance(r, dict):
        val = r.get("value", r)
        bh  = val.get("blockhash") if isinstance(val, dict) else None
        if _is_nonempty_string(bh):
            print(f"\n  Latest blockhash: {BOLD}{terminal_safe_text(bh)}{RESET}\n")
            return bh
    log_warn(f"Unexpected response: {json.dumps(resp)}")
    return None


def _print_sol_balance(sol_resp: dict | None) -> None:
    if not (sol_resp and "result" in sol_resp):
        log_warn("Could not fetch SOL balance")
        return
    lamports = _extract_result(sol_resp)
    if not is_u64(lamports):
        log_warn(f"Unexpected getBalance response: {json.dumps(sol_resp)}")
        return
    sol = lamports / 1_000_000_000
    print(f"  {BOLD}SOL Balance:{RESET}  {BOLD}{sol:.9f} SOL{RESET}  {DIM}({lamports:,} lamports){RESET}")


def _print_spl_tokens(token_resp: dict | None) -> None:
    if not (token_resp and "result" in token_resp):
        log_warn("Could not fetch SPL token accounts")
        return
    accounts = _extract_result(token_resp)
    if not isinstance(accounts, list):
        log_warn(f"Unexpected getTokenAccountsByOwner response: {json.dumps(token_resp)}")
        return
    if not accounts:
        print(f"  {DIM}No SPL token accounts{RESET}")
        return
    print(f"  {BOLD}SPL Tokens ({len(accounts)}){RESET}")
    for acc in accounts:
        try:
            info     = acc["account"]["data"]["parsed"]["info"]
            token_amount = info["tokenAmount"]
            mint     = info["mint"]
            decimals = token_amount["decimals"]
            amount   = token_amount["uiAmountString"]
            if (
                not isinstance(mint, str)
                or isinstance(decimals, bool) or not isinstance(decimals, int)
                or not 0 <= decimals <= 255
                or not isinstance(amount, str)
            ):
                raise TypeError("unexpected token account field type")
            symbol   = f"  {DIM}({decimals} decimals){RESET}" if decimals else ""
            print(f"  {DIM}·{RESET} {terminal_safe_text(mint)}  {BOLD}{terminal_safe_text(amount)}{RESET}{symbol}")
        except (KeyError, TypeError):
            print(f"  {DIM}· (could not parse account){RESET}")


def get_token_accounts(owner):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_sol    = ex.submit(rpc_call, "getBalance", [owner])
        fut_tokens = ex.submit(rpc_call, "getTokenAccountsByOwner", [
            owner,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ])
        try:
            sol_resp   = fut_sol.result()
            token_resp = fut_tokens.result()
        except Exception as exc:
            log_err(f"Wallet detail query failed: {exc}")
            return

    print(f"\n  {GREEN}{BOLD}{terminal_safe_text(owner)}{RESET}")
    _print_sol_balance(sol_resp)
    _print_spl_tokens(token_resp)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Beacon co-sign protocol
# ═══════════════════════════════════════════════════════════════════════════════

def get_beacon_pubkey() -> str | None:
    """Fetch the beacon's co-signing pubkey (needed to build Arcium co-sign txs)."""
    resp = rpc_call("getBeaconPubkey")
    if resp is None:
        return None
    if "error" in resp:
        log_err(f"getBeaconPubkey: {rpc_error_message(resp['error'])}")
        return None
    pubkey = _extract_result(resp)
    if not _is_nonempty_string(pubkey):
        log_warn(f"Unexpected getBeaconPubkey response: {json.dumps(resp)}")
        return None
    return pubkey


def cosign_and_send(partial_tx_b64: str, arcium_meta: dict | None = None) -> str | None:
    """
    Send a partially-signed transaction to the beacon for co-signing and relay.
    arcium_meta: optional dict forwarded to the beacon for post-relay stats logging,
                e.g. {"amount": 1000, "mint": "...", "payer_ta": "...", ...}
    Returns the Solana transaction signature on success, or None.
    """
    params = [partial_tx_b64]
    if arcium_meta:
        params.append({"arcium": arcium_meta})
    resp = rpc_call("cosignTransaction", params)
    if resp is None:
        return None
    if "error" in resp:
        log_err(f"Co-sign rejected: {rpc_error_message(resp['error'])}")
        return None
    sig = _extract_result(resp)
    if not _is_nonempty_string(sig):
        log_warn(f"Unexpected cosignTransaction response: {json.dumps(resp)}")
        return None
    log_ok("Co-signed transaction relayed via beacon!")
    print(f"\n  Signature: {BOLD}{GREEN}{terminal_safe_text(sig)}{RESET}\n")
    return sig


# ═══════════════════════════════════════════════════════════════════════════════
# Transaction relay
# ═══════════════════════════════════════════════════════════════════════════════

def send_transaction(signed_tx_b64):
    resp = rpc_call("sendTransaction", [signed_tx_b64, {"encoding": "base64"}])
    if resp is None:
        return
    if "error" in resp:
        log_err(f"Transaction rejected: {rpc_error_message(resp['error'])}")
        return
    signature = resp.get("result")
    if not _is_nonempty_string(signature):
        log_warn(f"Unexpected sendTransaction response: {json.dumps(resp)}")
        return
    log_ok("Transaction relayed via mesh!")
    print(f"\n  Signature: {BOLD}{GREEN}{terminal_safe_text(signature)}{RESET}\n")


def simulate_transaction(signed_tx_b64):
    resp = rpc_call("simulateTransaction", [signed_tx_b64, {"encoding": "base64"}])
    if resp is None:
        return
    if "error" in resp:
        log_err(f"Simulation rejected: {rpc_error_message(resp['error'])}")
        return
    sim = _extract_result(resp)
    if not isinstance(sim, dict):
        log_warn(f"Unexpected simulateTransaction response: {json.dumps(resp)}")
        return
    if sim.get("err"):
        log_warn(f"Simulation error: {sim['err']}")
    else:
        log_ok("Simulation successful")
    logs = sim.get("logs") or []
    if not isinstance(logs, list) or any(not isinstance(line, str) for line in logs):
        log_warn(f"Unexpected simulateTransaction logs: {json.dumps(logs)}")
        return
    for line in logs[:MAX_RENDERED_LOG_LINES]:
        print(f"  {DIM}{terminal_safe_text(line)}{RESET}")
    if len(logs) > MAX_RENDERED_LOG_LINES:
        log_warn(f"{len(logs) - MAX_RENDERED_LOG_LINES} simulation log lines omitted")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Nonce account query
# ═══════════════════════════════════════════════════════════════════════════════

def get_nonce_account(nonce_pubkey_str: str) -> dict | None:
    """
    Fetch a durable nonce account and return its current state.
    Returns {"nonce": <blockhash_str>, "authority": <pubkey_str>} or None on error.
    """
    resp = rpc_call("getAccountInfo", [
        nonce_pubkey_str,
        {"encoding": "jsonParsed", "commitment": "confirmed"},
    ])
    if resp is None:
        log_err(_NO_BEACON_RESP); return None
    if "error" in resp:
        log_err(f"RPC error: {resp['error']}"); return None

    account = _extract_result(resp)
    if account is None:
        log_err(f"Account {nonce_pubkey_str} not found (does it exist on this network?)")
        return None

    try:
        parsed    = account["data"]["parsed"]
        if not isinstance(parsed, dict):
            raise TypeError("parsed nonce data must be an object")
        if parsed.get("type") != "initialized":
            log_err(f"Nonce account is not initialized (type={parsed.get('type')!r})")
            return None
        info      = parsed["info"]
        if not isinstance(info, dict):
            raise TypeError("nonce info must be an object")
        nonce_val = info["blockhash"]
        authority = info["authority"]
        if not _is_nonempty_string(nonce_val) or not _is_nonempty_string(authority):
            raise TypeError("nonce blockhash and authority must be strings")
    except (KeyError, TypeError) as exc:
        log_err(f"Could not parse nonce account data: {exc}")
        log_warn('Confirm this is a nonce account: raw getAccountInfo ["<pubkey>",{"encoding":"jsonParsed"}]')
        return None

    print(f"\n  {GREEN}{BOLD}{terminal_safe_text(nonce_pubkey_str)}{RESET}  {DIM}(durable nonce account){RESET}")
    print(f"  Nonce value (use as blockhash): {BOLD}{terminal_safe_text(nonce_val)}{RESET}")
    print(f"  Authority:                      {terminal_safe_text(authority)}\n")
    return {"nonce": nonce_val, "authority": authority}
