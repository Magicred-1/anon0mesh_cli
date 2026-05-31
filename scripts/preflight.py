#!/usr/bin/env python3
"""Validate the supported desktop headless-node path before launch."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import importlib
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import ResponseSizeLimitError, SOLANA_ENDPOINTS, read_limited_http_body, redact_url, terminal_safe_text


MAX_PREFLIGHT_RPC_RESPONSE_BYTES = 64 * 1024


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"[ok]   {terminal_safe_text(message)}")

    def warn(self, message: str) -> None:
        print(f"[warn] {terminal_safe_text(message)}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[fail] {terminal_safe_text(message)}")


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


def read_config(checks: Checks, path: Path) -> Mapping[str, object] | None:
    if not path.is_file():
        checks.fail(f"Reticulum config not found: {path}")
        return None

    try:
        from RNS.vendor.configobj import ConfigObj, ConfigObjError
    except ImportError as exc:
        checks.fail(f"Reticulum config parser import failed: {exc}")
        return None
    try:
        config = ConfigObj(str(path))
    except (ConfigObjError, OSError) as exc:
        checks.fail(f"Reticulum config could not be parsed: {path} ({exc})")
        return None

    missing = [
        section for section in ("[reticulum]", "[interfaces]")
        if section[1:-1] not in config
    ]
    if missing:
        checks.fail(f"Reticulum config missing sections: {', '.join(missing)}")
        return None

    checks.ok(f"Reticulum config: {path}")
    return config


def interface_enabled(config: Mapping[str, object]) -> bool:
    value = config.get("interface_enabled", config.get("enabled", "no"))
    return str(value).lower() in {"1", "true", "yes", "on"}


def configured_interfaces(config: Mapping[str, object] | None) -> list[Mapping[str, object]]:
    if config is None:
        return []
    interfaces = config.get("interfaces", {})
    if not isinstance(interfaces, Mapping):
        return []
    return [
        section for section in interfaces.values()
        if isinstance(section, Mapping) and interface_enabled(section)
    ]


def check_optional_transports(
    checks: Checks,
    config: Mapping[str, object] | None,
    args: argparse.Namespace,
) -> None:
    interfaces = configured_interfaces(config)
    rnodes = [section for section in interfaces if section.get("type") == "RNodeInterface"]
    has_ble = any(section.get("type") == "BLEInterface" for section in interfaces)
    has_meshtastic = any("Meshtastic" in str(section.get("type", "")) for section in interfaces)

    if args.ble or has_ble:
        checks.fail(
            "desktop BLE is experimental: installing bleak does not provide "
            "a supported Reticulum BLEInterface"
        )

    if args.rnode and not rnodes:
        checks.fail("RNode requested but no enabled RNodeInterface exists in the Reticulum config")

    for rnode in rnodes:
        port = str(rnode.get("port", "")).strip()
        if not port:
            checks.fail("RNodeInterface exists but no serial port is configured")
        else:
            if Path(port).exists():
                checks.ok(f"RNode serial port: {port}")
            else:
                checks.fail(f"RNode serial port not found: {port}")

    if args.meshtastic or has_meshtastic:
        check_module(checks, "meshtastic", "Meshtastic")


def check_rpc(checks: Checks, rpc_url: str) -> None:
    display_url = redact_url(rpc_url)
    try:
        import requests

        response = requests.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            timeout=8,
            stream=True,
        )
        try:
            response.raise_for_status()
            raw_body = read_limited_http_body(response, MAX_PREFLIGHT_RPC_RESPONSE_BYTES)
        finally:
            response.close()
    except ResponseSizeLimitError:
        checks.fail("Solana RPC getHealth response exceeds size limit")
        return
    except Exception as exc:
        checks.fail(f"Solana RPC unreachable: {display_url} ({type(exc).__name__})")
        return
    try:
        body = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        checks.fail("Solana RPC returned invalid JSON for getHealth")
        return
    if not isinstance(body, Mapping) or body.get("result") != "ok":
        checks.fail(f"Solana RPC returned unexpected getHealth response: {body}")
        return
    checks.ok(f"Solana RPC reachable: {display_url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=None, help="Reticulum config directory")
    parser.add_argument("--network", "-n", choices=sorted(SOLANA_ENDPOINTS), default="devnet")
    parser.add_argument("--rpc", default=None, help="Custom RPC URL (prefer ANONMESH_RPC_URL for credentials)")
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
    config = read_config(checks, config_file(args.config))
    check_optional_transports(checks, config, args)
    if not args.skip_rpc:
        rpc_url = args.rpc or os.getenv("ANONMESH_RPC_URL") or SOLANA_ENDPOINTS[args.network]
        if rpc_url:
            check_rpc(checks, rpc_url)
        else:
            checks.fail("Custom network requires --rpc or ANONMESH_RPC_URL")

    if checks.failures:
        print(f"\npreflight failed: {checks.failures} check(s)")
        return 1
    print("\npreflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
