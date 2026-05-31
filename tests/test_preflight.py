"""Regression tests for headless-node preflight validation."""
from __future__ import annotations

from argparse import Namespace

from RNS.vendor.configobj import ConfigObj

from scripts.preflight import Checks, check_optional_transports


def args(**overrides) -> Namespace:
    values = {"ble": False, "rnode": False, "meshtastic": False}
    values.update(overrides)
    return Namespace(**values)


def config(lines: list[str]) -> ConfigObj:
    return ConfigObj(lines)


def test_comments_do_not_enable_optional_transports():
    checks = Checks()
    parsed = config([
        "[interfaces]",
        "  # BLEInterface and Meshtastic are documentation only",
        "  [[TCP]]",
        "    type = TCPClientInterface",
        "    enabled = yes",
    ])

    check_optional_transports(checks, parsed, args())

    assert checks.failures == 0


def test_rnode_check_uses_rnode_port_only(tmp_path):
    serial_port = tmp_path / "ttyUSB0"
    serial_port.touch()
    checks = Checks()
    parsed = config([
        "[interfaces]",
        "  [[TCP]]",
        "    type = TCPClientInterface",
        "    enabled = yes",
        "    port = 4242",
        "  [[Radio]]",
        "    type = RNodeInterface",
        "    interface_enabled = True",
        f"    port = {serial_port}",
    ])

    check_optional_transports(checks, parsed, args())

    assert checks.failures == 0


def test_enabled_ble_interface_fails_until_desktop_adapter_exists():
    checks = Checks()
    parsed = config([
        "[interfaces]",
        "  [[BLE]]",
        "    type = BLEInterface",
        "    enabled = yes",
    ])

    check_optional_transports(checks, parsed, args())

    assert checks.failures == 1


def test_rnode_flag_requires_enabled_interface():
    checks = Checks()
    parsed = config([
        "[interfaces]",
        "  [[Radio]]",
        "    type = RNodeInterface",
        "    enabled = no",
        "    port = /dev/does-not-matter",
    ])

    check_optional_transports(checks, parsed, args(rnode=True))

    assert checks.failures == 1
