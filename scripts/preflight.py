#!/usr/bin/env python3
"""Validate the supported desktop headless-node path before launch."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import SOLANA_ENDPOINTS


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"[ok]   {message}")

    def warn(self, message: str) -> None:
        print(f"[warn] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[fail] {message}")


def config_file(config_dir: str | None) -> Path:
    return Path(config_dir or "~/.reticulum").expanduser() / "config"


def check_python(checks: Checks) -> None:
    version = sys.version_info
    label = f"Python {version.major}.{version.minor}.{version.micro}"
    if version >= (3, 10):
        checks.ok(label)
    else:
        checks.fail(f"{label}; Python 3.10+ is required")


def check_module(checks: Checks, module: str, label: str) -> None:
    try:
        importlib.import_module(module)
    except ImportError as exc:
        checks.fail(f"{label} import failed: {exc}")
    else:
        checks.ok(f"{label} import")


def read_config(checks: Checks, path: Path) -> str | None:
    if not path.is_file():
        checks.fail(f"Reticulum config not found: {path}")
        return None

    text = path.read_text()
    missing = [
        section for section in ("[reticulum]", "[interfaces]")
        if section not in text
    ]
    if missing:
        checks.fail(f"Reticulum config missing sections: {', '.join(missing)}")
        return None

    checks.ok(f"Reticulum config: {path}")
    return text


def check_optional_transports(checks: Checks, text: str | None, args: argparse.Namespace) -> None:
    text = text or ""
    has_ble = "BLEInterface" in text
    has_rnode = "RNodeInterface" in text
    has_meshtastic = "Meshtastic" in text

    if args.ble or has_ble:
        checks.fail(
            "desktop BLE is experimental: installing bleak does not provide "
            "a supported Reticulum BLEInterface"
        )

    if args.rnode and not has_rnode:
        checks.fail("RNode requested but no RNodeInterface exists in the Reticulum config")

    if has_rnode:
        ports = re.findall(r"^\s*port\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
        if not ports:
            checks.fail("RNodeInterface exists but no serial port is configured")
        for port in ports:
            if Path(port).exists():
                checks.ok(f"RNode serial port: {port}")
            else:
                checks.fail(f"RNode serial port not found: {port}")

    if args.meshtastic or has_meshtastic:
        check_module(checks, "meshtastic", "Meshtastic")


def check_rpc(checks: Checks, rpc_url: str) -> None:
    try:
        import requests

        response = requests.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            timeout=8,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("result") != "ok":
            checks.fail(f"Solana RPC returned unexpected getHealth response: {body}")
            return
    except Exception as exc:
        checks.fail(f"Solana RPC unreachable: {rpc_url} ({exc})")
    else:
        checks.ok(f"Solana RPC reachable: {rpc_url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=None, help="Reticulum config directory")
    parser.add_argument("--network", "-n", choices=sorted(SOLANA_ENDPOINTS), default="devnet")
    parser.add_argument("--rpc", default=None, help="Custom Solana RPC URL")
    parser.add_argument("--skip-rpc", action="store_true", help="Skip the Solana RPC reachability check")
    parser.add_argument("--ble", action="store_true", help="Check the experimental desktop BLE path")
    parser.add_argument("--rnode", action="store_true", help="Require an RNodeInterface in the config")
    parser.add_argument("--meshtastic", action="store_true", help="Require the Meshtastic Python package")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checks = Checks()

    check_python(checks)
    check_module(checks, "RNS", "Reticulum")
    check_module(checks, "requests", "requests")
    text = read_config(checks, config_file(args.config))
    check_optional_transports(checks, text, args)
    if not args.skip_rpc:
        check_rpc(checks, args.rpc or SOLANA_ENDPOINTS[args.network])

    if checks.failures:
        print(f"\npreflight failed: {checks.failures} check(s)")
        return 1
    print("\npreflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
