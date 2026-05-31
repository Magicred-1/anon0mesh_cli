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
import os
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


def decode_rpc_response(raw: bytes) -> dict:
    """Decode a JSON-RPC response object containing exactly one outcome."""
    parsed = decode_json(raw)
    if (
        not isinstance(parsed, dict)
        or ("result" in parsed) == ("error" in parsed)
    ):
        raise ValueError("expected JSON-RPC response object with one outcome")
    return parsed


def redact_url(url: str | None) -> str:
    """Return a log-safe endpoint label without credentials, path tokens, or query params."""
    if not isinstance(url, str):
        return "<redacted-rpc-url>"
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


_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def terminal_safe_text(value: Any) -> str:
    """Escape terminal control bytes before rendering untrusted text."""
    return _TERMINAL_CONTROL_RE.sub(
        lambda match: f"\\x{ord(match.group(0)):02x}",
        str(value),
    )


def rpc_error_message(error: Any, default: str = "?") -> str:
    """Format a JSON-RPC error whether the peer returned an object or scalar."""
    if isinstance(error, dict):
        return terminal_safe_text(error.get("message", error))
    if error is None:
        return terminal_safe_text(default)
    return terminal_safe_text(error)


def positive_int(raw_value: str) -> int:
    """Parse a command-line integer that must be greater than zero."""
    value = int(raw_value)
    if value <= 0:
        raise ValueError("expected a positive integer")
    return value


def is_u64(value: Any) -> bool:
    """Return whether value can be represented as an unsigned Solana u64."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1 << 64


# ── Private local files ──────────────────────────────────────────────────────

def restrict_private_file_permissions(path: str) -> None:
    """Repair an existing private file to owner-only permissions."""
    if os.path.isfile(path):
        os.chmod(path, 0o600)


def save_private_identity(identity: Any, path: str) -> None:
    """Persist an RNS identity without exposing it through the process umask."""
    previous_umask = os.umask(0o077)
    try:
        identity.to_file(path)
    finally:
        os.umask(previous_umask)
    restrict_private_file_permissions(path)


_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_dotenv_private(path: str | os.PathLike[str]) -> None:
    """Load simple KEY=VALUE entries after restricting the credential file."""
    path_str = os.fspath(path)
    if not os.path.isfile(path_str):
        return
    restrict_private_file_permissions(path_str)
    with open(path_str, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if _ENV_KEY_RE.fullmatch(key) and "\x00" not in value:
                    os.environ.setdefault(key, value)


# ── Mesh payload compression ─────────────────────────────────────────────────
# Solana RPC responses are typically 1–10 KB of JSON.  LoRa links have ~1.2 kbps
# throughput with a Reticulum MTU of ~465 bytes.  Compressing before transmission
# reduces chunk count and latency significantly.
#
# Protocol: a 3-byte magic prefix (b"\x00zl") signals zlib-compressed data.
# If compression doesn't shrink the payload, raw bytes are sent instead.
# Receivers call decompress_response() which handles both cases transparently.

_COMPRESS_MAGIC = b"\x00zl"
MAX_MESH_REQUEST_BYTES = 256 * 1024
MAX_MESH_RESPONSE_BYTES = 1024 * 1024
MAX_RENDERED_LOG_LINES = 100

def compress_response(data: bytes) -> bytes:
    """Compress a response payload with zlib if it saves space."""
    compressed = zlib.compress(data, level=6)
    if len(compressed) + len(_COMPRESS_MAGIC) < len(data):
        return _COMPRESS_MAGIC + compressed
    return data

def decompress_response(data: bytes) -> bytes:
    """Decompress a response payload. Passes through uncompressed data."""
    if len(data) > MAX_MESH_RESPONSE_BYTES:
        raise ValueError("Response exceeds size limit")
    if data[:3] == _COMPRESS_MAGIC:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(
            data[3:], MAX_MESH_RESPONSE_BYTES + 1)
        if len(raw) > MAX_MESH_RESPONSE_BYTES or decompressor.unconsumed_tail:
            raise ValueError("Compressed response exceeds expanded size limit")
        raw += decompressor.flush(MAX_MESH_RESPONSE_BYTES + 1 - len(raw))
        if len(raw) > MAX_MESH_RESPONSE_BYTES:
            raise ValueError("Compressed response exceeds expanded size limit")
        if not decompressor.eof or decompressor.unused_data:
            raise ValueError("Compressed response is malformed")
        return raw
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
        print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {CYAN}ℹ {terminal_safe_text(msg)}{RESET}")

def log_ok(msg: str)    -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {GREEN}✔ {terminal_safe_text(msg)}{RESET}")
def log_warn(msg: str)  -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {YELLOW}⚠ {terminal_safe_text(msg)}{RESET}")
def log_err(msg: str)   -> None: print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {RED}✘ {terminal_safe_text(msg)}{RESET}")

def log_tx(msg: str)    -> None:
    if not _quiet_mode:
        print(f"{DIM}[{time.strftime('%H:%M:%S')}]{RESET} {BOLD}➤ {terminal_safe_text(msg)}{RESET}")
