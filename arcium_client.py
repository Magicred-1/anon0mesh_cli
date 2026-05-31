from __future__ import annotations
"""
arcium_client.py — Arcium MPC integration for the anon0mesh beacon
====================================================================
Based on the actual anon0mesh contract:
  Program ID:  7xeQNUggKc2e5q6AQxsFBLBkXGg2p54kSx11zVainMks
  Instruction: execute_payment(computation_offset, amount, nonce, pub_key)
  Purpose:     Log ENCRYPTED payment statistics after a transaction is relayed

How it fits into the beacon flow
---------------------------------
  Client → beacon.forward_to_solana("sendTransaction", [...])
         → Solana confirms tx
         → beacon calls arcium.log_payment_stats(amount, accounts)
         → Arcium MPC nodes process payment_stats circuit
         → Encrypted stats recorded on-chain

  The beacon relays transactions AND logs encrypted stats.
  Arcium never touches balance queries — that's plain RPC.

Setup
-----
  npm install @arcium-hq/client @coral-xyz/anchor @solana/web3.js @solana/spl-token
  pip install solders solana

  ARCIUM_ENABLED=1
  ARCIUM_RPC_URL=https://api.devnet.solana.com
  ARCIUM_PAYER_KEYPAIR=~/.config/solana/id.json
  ARCIUM_MXE_PUBKEY_HEX=<from: node rescue_shim.mjs mxe_pubkey>
  ARCIUM_CLUSTER_OFFSET=456
"""

import os
import json
import time
import asyncio
import subprocess
import threading
from pathlib import Path
from typing import Optional

Pubkey = None
try:
    from solders.keypair import Keypair
    from solders.pubkey  import Pubkey
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment  import Confirmed
    HAS_SOLANA = True
except ImportError:
    HAS_SOLANA = False

from shared import (
    load_dotenv_private, read_private_file,
    log_info, log_ok, log_warn, log_err, redact_urls,
)

# ── Constants ──────────────────────────────────────────────────────────────────
# declare_id! in programs/ble-revshare/src/lib.rs + Anchor.toml [programs.devnet]
MXE_PROGRAM_ID         = "7xeQNUggKc2e5q6AQxsFBLBkXGg2p54kSx11zVainMks"

# sign_pda_account: find_program_address([b"ArciumSignerAccount"], MXE_PROGRAM_ID)
# = 4VubmLaMEPnyPXURZYPRQANwNWDTq8Jzn1Bj3YUo9zi7  (bump 255)
ARCIUM_SIGNER_PDA      = "4VubmLaMEPnyPXURZYPRQANwNWDTq8Jzn1Bj3YUo9zi7"

CLUSTER_OFFSET_DEVNET  = 456
CLUSTER_OFFSET_MAINNET = 2026
POLL_INTERVAL          = 2.0
POLL_TIMEOUT           = 120.0
SHIM_PATH              = Path(__file__).parent / "rescue_shim.mjs"
_MAX_U32               = (1 << 32) - 1
_MAX_U64               = (1 << 64) - 1
_MAX_U128              = (1 << 128) - 1
_MAX_FIELD_VALUE       = (1 << 256) - 1
_MAX_RESCUE_VALUES     = 100


def _is_fixed_hex(value: object, byte_length: int) -> bool:
    if not isinstance(value, str) or len(value) != byte_length * 2:
        return False
    try:
        return len(bytes.fromhex(value)) == byte_length
    except ValueError:
        return False


def _is_byte_array(value: object, byte_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == byte_length
        and all(isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255 for item in value)
    )


def _is_solana_pubkey(value: object) -> bool:
    if Pubkey is None or not isinstance(value, str):
        return False
    try:
        Pubkey.from_string(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_bounded_decimal(value: object, maximum: int) -> bool:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > len(str(maximum))
    ):
        return False
    return int(value) <= maximum


def _is_field_value(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_FIELD_VALUE


def _are_ciphertexts(value: object) -> bool:
    return (
        isinstance(value, list)
        and 0 < len(value) <= _MAX_RESCUE_VALUES
        and all(_is_byte_array(ciphertext, 32) for ciphertext in value)
    )


# ── Shim helpers ───────────────────────────────────────────────────────────────

def _run_shim(*args: str, stdin_data: str | None = None, timeout: int = 60) -> dict:
    """
    Run rescue_shim.mjs with the given CLI args.

    Sensitive material (private keys, shared secrets) must be passed via
    ``stdin_data`` so it never appears in the process argument list
    (visible via /proc/<pid>/cmdline or ``ps aux``).
    The shim reads from stdin when the first line of its stdin is non-empty.
    """
    if not SHIM_PATH.exists():
        raise FileNotFoundError(
            f"rescue_shim.mjs not found at {SHIM_PATH}\n"
            "Run: npm install @arcium-hq/client @coral-xyz/anchor @solana/web3.js @solana/spl-token"
        )
    result = subprocess.run(
        ["node", str(SHIM_PATH), *args],
        input=stdin_data,
        capture_output=True, text=True, timeout=timeout,
    )
    # shim writes errors as JSON to stdout (fail() uses console.log), not stderr
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raw = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"shim non-JSON output (exit {result.returncode}): {redact_urls(raw[:300])}")
    if not isinstance(data, dict):
        raise RuntimeError(f"shim returned non-object JSON (exit {result.returncode})")
    if result.returncode != 0:
        error = data.get("error") or result.stderr.strip() or f"shim error (exit {result.returncode})"
        raise RuntimeError(redact_urls(str(error)))
    if not data.get("ok"):
        error = data.get("error") or f"shim error (exit {result.returncode})"
        raise RuntimeError(redact_urls(str(error)))
    return data


def rescue_keygen() -> tuple[str, str]:
    data = _run_shim("keygen")
    private_key = data.get("privkey_hex")
    public_key = data.get("pubkey_hex")
    if not _is_fixed_hex(private_key, 32) or not _is_fixed_hex(public_key, 32):
        raise ValueError("shim keygen returned invalid keys")
    return private_key, public_key


def rescue_encrypt(mxe_pubkey_hex: str, values: list[int], nonce_hex: str | None = None) -> dict:
    if (
        not _is_fixed_hex(mxe_pubkey_hex, 32)
        or not isinstance(values, list)
        or not 0 < len(values) <= _MAX_RESCUE_VALUES
        or any(not _is_field_value(value) for value in values)
        or (nonce_hex is not None and not _is_fixed_hex(nonce_hex, 16))
    ):
        raise ValueError("invalid encrypt request")
    args = ["encrypt", mxe_pubkey_hex]
    if nonce_hex is not None:
        args.append(nonce_hex)
    data = _run_shim(*args, stdin_data=json.dumps(values))
    ciphertexts = data.get("ciphertexts")
    if (
        not isinstance(ciphertexts, list)
        or len(ciphertexts) != len(values)
        or any(not _is_byte_array(ciphertext, 32) for ciphertext in ciphertexts)
        or not _is_fixed_hex(data.get("pubkey_hex"), 32)
        or not _is_fixed_hex(data.get("nonce_hex"), 16)
        or not _is_bounded_decimal(data.get("nonce_bn"), _MAX_U128)
        or not _is_fixed_hex(data.get("shared_secret_hex"), 32)
    ):
        raise ValueError("shim encrypt returned invalid payload")
    return data


def rescue_decrypt(shared_secret_hex: str, ciphertexts: list[list[int]], nonce_hex: str) -> list[int]:
    # shared_secret_hex is sensitive — pass via stdin, not as a CLI arg
    if (
        not _is_fixed_hex(shared_secret_hex, 32)
        or not _are_ciphertexts(ciphertexts)
        or not _is_fixed_hex(nonce_hex, 16)
    ):
        raise ValueError("invalid decrypt request")
    data = _run_shim("decrypt", json.dumps(ciphertexts), nonce_hex, stdin_data=shared_secret_hex)
    values = data.get("values")
    if (
        not isinstance(values, list)
        or len(values) != len(ciphertexts)
        or any(not _is_bounded_decimal(value, _MAX_FIELD_VALUE) for value in values)
    ):
        raise ValueError("shim decrypt returned invalid values")
    return [int(value) for value in values]


def rescue_shared_secret(privkey_hex: str, mxe_pubkey_hex: str) -> str:
    # privkey_hex is sensitive — pass via stdin, not as a CLI arg
    if not _is_fixed_hex(privkey_hex, 32) or not _is_fixed_hex(mxe_pubkey_hex, 32):
        raise ValueError("invalid shared_secret request")
    shared_secret = _run_shim("shared_secret", mxe_pubkey_hex, stdin_data=privkey_hex).get("shared_secret_hex")
    if not _is_fixed_hex(shared_secret, 32):
        raise ValueError("shim shared_secret returned an invalid key")
    return shared_secret


# ── ArciumBeaconClient ─────────────────────────────────────────────────────────

class ArciumBeaconClient:
    """
    Logs encrypted payment statistics to the anon0mesh Arcium MXE
    after the beacon successfully relays a transaction.

    The execute_payment instruction takes:
      computation_offset: u64
      amount:             u64   (payment amount in lamports/tokens)
      nonce:              u128  (from client x25519 encryption)
      pub_key:            [u8;32] (client x25519 ephemeral pubkey)

    Plus all the token accounts and Arcium PDAs.
    """

    def __init__(
        self,
        rpc_url:        str,
        payer_keypair:  "Keypair",
        mxe_pubkey_hex: str,
        cluster_offset: int = CLUSTER_OFFSET_DEVNET,
        program_id:     str = MXE_PROGRAM_ID,
    ):
        if not HAS_SOLANA:
            raise ImportError("pip install solders solana")
        if not isinstance(rpc_url, str) or not rpc_url:
            raise ValueError("Arcium RPC URL must be a non-empty string")
        if not _is_fixed_hex(mxe_pubkey_hex, 32):
            raise ValueError("Arcium MXE public key must be 32 hexadecimal bytes")
        if isinstance(cluster_offset, bool) or not isinstance(cluster_offset, int) or not 0 <= cluster_offset <= _MAX_U32:
            raise ValueError(f"Arcium cluster offset must be between 0 and {_MAX_U32}")
        if not _is_solana_pubkey(program_id):
            raise ValueError("Arcium MXE program ID must be a Solana public key")
        self.rpc_url        = rpc_url
        self.payer          = payer_keypair
        self.mxe_pubkey_hex = mxe_pubkey_hex
        self.cluster_offset = cluster_offset
        self.program_id     = program_id
        self._payer_hex     = bytes(payer_keypair).hex()
        self._payer_b58     = str(payer_keypair.pubkey())
        self._client: Optional[AsyncClient] = None

    async def connect(self) -> None:
        self._client = AsyncClient(self.rpc_url, commitment=Confirmed)
        resp = await self._client.get_slot()
        log_ok(f"Arcium RPC connected  slot={resp.value}")

    async def log_payment_stats(
        self,
        amount:                    int,
        payer_token_account:       str,
        recipient:                 str,
        recipient_token_account:   str,
        mint:                      str,
        broadcaster:               str | None = None,
        broadcaster_token_account: str | None = None,
    ) -> dict:
        """
        Call execute_payment on the anon0mesh MXE to log encrypted payment stats.
        Called by the beacon after sendTransaction succeeds.

        Generates a fresh x25519 keypair per call — the nonce and pubkey are
        included in the instruction so Arcium MPC can decrypt the amount.
        """
        log_info(f"Logging payment stats  amount={amount}  via Arcium MPC")

        # Beacon is always the broadcaster (it relayed the tx) — use its keypair
        # so it actually co-signs execute_payment and receives the revenue share.
        broadcaster          = broadcaster or self._payer_b58
        broadcaster_kp_hex   = self._payer_hex
        if not broadcaster_token_account:
            broadcaster_token_account = os.getenv("ARCIUM_BROADCASTER_TOKEN_ACCOUNT") or None
        treasury_token_account = os.getenv("ARCIUM_TREASURY_TOKEN_ACCOUNT") or None

        account_fields = (
            payer_token_account,
            recipient,
            recipient_token_account,
            mint,
            broadcaster,
        )
        optional_account_fields = (broadcaster_token_account, treasury_token_account)
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or not 0 <= amount <= _MAX_U64
            or any(not _is_solana_pubkey(value) for value in account_fields)
            or any(value is not None and not _is_solana_pubkey(value) for value in optional_account_fields)
        ):
            message = "invalid Arcium payment metadata"
            log_err(message)
            return {"status": "error", "message": message}

        # The shim handles encryption (x25519 + RescueCipher) using mxePubkeyHex directly.
        shim_args = json.dumps({
            "rpcUrl":                     self.rpc_url,
            "programId":                  self.program_id,
            "payerKeypairHex":            self._payer_hex,
            "clusterOffset":              str(self.cluster_offset),
            "amount":                     str(amount),
            "mxePubkeyHex":              self.mxe_pubkey_hex,
            "recipientB58":               recipient,
            "mintB58":                    mint,
            "payerTokenAccountB58":       payer_token_account,
            "recipientTokenAccountB58":   recipient_token_account,
            "treasuryTokenAccountB58":    treasury_token_account,
            "broadcasterB58":             broadcaster,
            "broadcasterKeypairHex":      broadcaster_kp_hex,
            "broadcasterTokenAccountB58": broadcaster_token_account,
        })

        try:
            # shim_args contains payerKeypairHex — pass via stdin to keep it
            # out of the process argument list (/proc/<pid>/cmdline / ps aux)
            result = _run_shim("execute_payment", stdin_data=shim_args, timeout=60)
            signature = result.get("signature")
            if not isinstance(signature, str) or not signature:
                raise ValueError("shim execute_payment returned an invalid signature")
            log_ok(f"Payment stats logged  sig={signature[:20]}...")
            return {"status": "ok", "signature": signature}
        except Exception as exc:
            error = redact_urls(str(exc))
            log_err(f"execute_payment failed: {error}")
            return {"status": "error", "message": error}

    async def close(self):
        if self._client:
            await self._client.close()


# ── ArciumBeacon sync wrapper ──────────────────────────────────────────────────

class ArciumBeacon:
    """
    Synchronous facade for beacon.py.

    Integration in beacon.py — call after sendTransaction succeeds:

        # In forward_to_solana(), after confirming tx:
        if method == "sendTransaction" and arcium and arcium.enabled:
            # Parse token accounts from the original tx if available
            # or accept them as extra params from the client
            arcium.log_payment_stats(
                amount                   = parsed_amount,
                payer_token_account      = payer_ta,
                recipient                = recipient,
                recipient_token_account  = recipient_ta,
                mint                     = mint,
            )
    """

    def __init__(self, client: ArciumBeaconClient | None):
        self._client = client
        self._loop   = None
        self._thread = None
        self.enabled = client is not None
        if self.enabled:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
            fut = asyncio.run_coroutine_threadsafe(self._client.connect(), self._loop)
            try:
                fut.result(timeout=15)
            except Exception as exc:
                log_err(f"Arcium init failed: {redact_urls(str(exc))}")
                self.enabled = False
                self._cleanup_failed_init(fut)

    def _cleanup_failed_init(self, connect_future) -> None:
        """Bound cleanup after a failed or timed-out asynchronous connect."""
        connect_future.cancel()
        try:
            close_future = asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)
            close_future.result(timeout=5)
        except Exception as exc:
            log_warn(f"Arcium cleanup failed: {redact_urls(str(exc))}")
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                log_warn("Arcium cleanup timed out while stopping event loop")
            else:
                self._loop.close()

    @classmethod
    def from_env(cls) -> "ArciumBeacon":
        # Auto-load .env
        env_file = Path(__file__).parent / ".env"
        load_dotenv_private(env_file)

        if os.getenv("ARCIUM_ENABLED", "0") != "1":
            log_info("Arcium disabled (ARCIUM_ENABLED != 1)")
            return cls(None)

        if not HAS_SOLANA:
            log_warn("pip install solders solana")
            return cls(None)

        # Only need MXE pubkey — program ID is hardcoded from the contract
        required = {
            "ARCIUM_PAYER_KEYPAIR":  os.getenv("ARCIUM_PAYER_KEYPAIR",  "").strip(),
            "ARCIUM_MXE_PUBKEY_HEX": os.getenv("ARCIUM_MXE_PUBKEY_HEX", "").strip(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            for k in missing:
                log_warn(f"  {k} is not set")
            log_warn("Arcium disabled — set env vars:")
            log_warn("  ARCIUM_MXE_PUBKEY_HEX: node rescue_shim.mjs mxe_pubkey")
            return cls(None)

        try:
            kp_path = os.path.expanduser(required["ARCIUM_PAYER_KEYPAIR"])
            payer = Keypair.from_bytes(bytes(json.loads(read_private_file(kp_path))))

            cluster_offset = int(os.getenv("ARCIUM_CLUSTER_OFFSET", str(CLUSTER_OFFSET_DEVNET)))
            if not 0 <= cluster_offset <= _MAX_U32:
                raise ValueError(f"ARCIUM_CLUSTER_OFFSET must be between 0 and {_MAX_U32}")
            program_id     = os.getenv("ARCIUM_MXE_PROGRAM_ID", MXE_PROGRAM_ID)

            client = ArciumBeaconClient(
                rpc_url        = os.getenv("ARCIUM_RPC_URL", "https://api.devnet.solana.com"),
                payer_keypair  = payer,
                mxe_pubkey_hex = required["ARCIUM_MXE_PUBKEY_HEX"],
                cluster_offset = cluster_offset,
                program_id     = program_id,
            )
            log_ok(f"Arcium client ready  program={program_id[:16]}...  cluster={cluster_offset}")
            return cls(client)

        except Exception as exc:
            log_err(f"Arcium env error: {redact_urls(str(exc))}")
            return cls(None)

    def log_payment_stats(
        self,
        amount:                    int,
        payer_token_account:       str,
        recipient:                 str,
        recipient_token_account:   str,
        mint:                      str,
        broadcaster:               str | None = None,
        broadcaster_token_account: str | None = None,
    ) -> dict | None:
        """Fire-and-forget: log payment stats without blocking the beacon response."""
        if not self.enabled:
            return None

        def _run():
            fut = asyncio.run_coroutine_threadsafe(
                self._client.log_payment_stats(
                    amount, payer_token_account, recipient,
                    recipient_token_account, mint,
                    broadcaster, broadcaster_token_account,
                ),
                self._loop,
            )
            try:
                return fut.result(timeout=POLL_TIMEOUT + 15)
            except Exception as exc:
                log_err(f"Arcium log_payment_stats failed: {redact_urls(str(exc))}")

        # Run in background thread — don't block the RPC response to the client
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "queued"}
