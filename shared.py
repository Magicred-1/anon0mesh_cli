from __future__ import annotations

"""
shared.py — Common types, constants, and utilities for the
anon0mesh / Reticulum Solana RPC bridge MVP.

Architecture (mirroring anonme.sh's proof-of-relay):

  [Client / Sender]  ──RNS Link──►  [Beacon / Receiver]  ──HTTP──►  [Solana RPC]
       (you)               encrypted          (relay node)            mainnet/devnet
                           mesh hop

The Beacon registers request handlers on a Reticulum destination.
The Client opens a Link, then calls link.request("/rpc", payload, ...)
Reticulum handles all encryption (X25519 + AES-256-GCM) automatically.
"""

import json
import re
import time
import zlib
from typing import Any
from urllib.parse import urlsplit

# ── App identity (both sides must agree) ───────────────────────────────────────
APP_NAME      = "anonmesh"
APP_ASPECT    = "rpc_beacon"      # destination aspect
RPC_PATH      = "/rpc"            # request handler path on the beacon
ANNOUNCE_DATA = b"anonmesh::beacon::v1"

# ── Solana RPC endpoints ───────────────────────────────────────────────────────
SOLANA_ENDPOINTS = {
    "mainnet":  "https://api.mainnet-beta.solana.com",
    "devnet":   "https://api.devnet.solana.com",
    "testnet":  "https://api.testnet.solana.com",
    # QuickNode / Helius style custom endpoint — set via env var
    "custom":   None,
}

# ── Packet budget ──────────────────────────────────────────────────────────────
# Reticulum max payload is ~465 bytes per raw packet.
# For larger payloads (signed tx blobs) we rely on RNS Resources (auto-used
# when the data exceeds MTU — Reticulum handles chunking transparently through
# the request/response API).
RNS_REQUEST_TIMEOUT = 30          # seconds — generous for mesh hops

# ── JSON-RPC helpers ───────────────────────────────────────────────────────────

def build_rpc(method: str, params: list | None = None, req_id: int = 1) -> bytes:
    """Encode a JSON-RPC 2.0 request as bytes for transmission."""
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or [],
    }
    return json.dumps(payload).encode("utf-8")


def build_response(result: Any = None, error: str | None = None, req_id: int = 1) -> bytes:
    """Encode a JSON-RPC 2.0 response as bytes."""
    if error:
        payload = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": error}}
    else:
        payload = {"jsonrpc": "2.0", "id": req_id, "result": result}
    return json.dumps(payload).encode("utf-8")


def decode_json(raw: bytes) -> Any:
    """Safely decode JSON bytes."""
    return json.loads(raw.decode("utf-8"))


def redact_url(url: str) -> str:
    """Return a log-safe endpoint label without credentials, path tokens, or query params."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if not parts.scheme or not host:
            return "<redacted-rpc-url>"
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "<redacted-rpc-url>"

    suffix = "/..." if (
        parts.username or parts.password or parts.path not in ("", "/")
        or parts.query or parts.fragment
    ) else ""
    return f"{parts.scheme}://{host}{port}{suffix}"


_URL_RE = re.compile(r"""https?://[^\s'"`]+""")


def redact_urls(text: str) -> str:
    """Redact embedded HTTP(S) endpoints in diagnostic text."""
    return _URL_RE.sub(lambda match: redact_url(match.group(0)), text)


# ── Mesh payload compression ─────────────────────────────────────────────────
# Solana RPC responses are typically 1–10 KB of JSON.  LoRa links have ~1.2 kbps
# throughput with a Reticulum MTU of ~465 bytes.  Compressing before transmission
# reduces chunk count and latency significantly.
#
# Protocol: a 3-byte magic prefix (b"\x00zl") signals zlib-compressed data.
# If compression doesn't shrink the payload, raw bytes are sent instead.
# Receivers call decompress_response() which handles both cases transparently.

_COMPRESS_MAGIC = b"\x00zl"

def compress_response(data: bytes) -> bytes:
    """Compress a response payload with zlib if it saves space."""
    compressed = zlib.compress(data, level=6)
    if len(compressed) + len(_COMPRESS_MAGIC) < len(data):
        return _COMPRESS_MAGIC + compressed
    return data

def decompress_response(data: bytes) -> bytes:
    """Decompress a response payload. Passes through uncompressed data."""
    if data[:3] == _COMPRESS_MAGIC:
        return zlib.decompress(data[3:])
    return data


# ── Pretty printers ────────────────────────────────────────────────────────────

RESET  = "\033[0m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


def banner(role: str) -> None:
    print(f"""
{BOLD}{CYAN}
  █████╗ ███╗   ██╗ ██████╗ ███╗   ██╗███╗   ███╗███████╗███████╗██╗  ██╗
 ██╔══██╗████╗  ██║██╔═══██╗████╗  ██║████╗ ████║██╔════╝██╔════╝██║  ██║
 ███████║██╔██╗ ██║██║   ██║██╔██╗ ██║██╔████╔██║█████╗  ███████╗███████║
 ██╔══██║██║╚██╗██║██║   ██║██║╚██╗██║██║╚██╔╝██║██╔══╝  ╚════██║██╔══██║
 ██║  ██║██║ ╚████║╚██████╔╝██║ ╚████║██║ ╚═╝ ██║███████╗███████║██║  ██║
 ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝
{RESET}{BOLD} Mesh First, Chain When It Matters  ·  {role}  ·  Powered by Reticulum && Solana {RESET}
""")


_quiet_mode = False


def set_quiet(quiet: bool) -> None:
    """Suppress log_info and log_tx during interactive operations."""
    global _quiet_mode
    _quiet_mode = quiet


def log_info(msg: str)  -> None:
    if not _quiet_mode:
        print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {CYAN}ℹ {msg}{RESET}")

def log_ok(msg: str)    -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {GREEN}✔ {msg}{RESET}")
def log_warn(msg: str)  -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {YELLOW}⚠ {msg}{RESET}")
def log_err(msg: str)   -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {RED}✘ {msg}{RESET}")

def log_tx(msg: str)    -> None:
    if not _quiet_mode:
        print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {BOLD}➤ {msg}{RESET}")
