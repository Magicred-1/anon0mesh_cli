"""Regression tests for headless-node preflight validation."""
from __future__ import annotations

from argparse import Namespace
import json
from unittest.mock import MagicMock, patch

from RNS.vendor.configobj import ConfigObj
import requests

from scripts import preflight
from scripts.preflight import Checks, check_optional_transports, check_rpc


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


def test_rpc_failure_redacts_credentials(capsys):
    secret_url = "https://user:pass@rpc.example.test/private-token?api-key=secret"
    checks = Checks()
    error = requests.exceptions.ConnectionError(f"failed to reach {secret_url}")

    with patch("requests.post", side_effect=error):
        check_rpc(checks, secret_url)

    output = capsys.readouterr().out
    assert checks.failures == 1
    assert "secret" not in output
    assert "user:pass" not in output
    assert "https://rpc.example.test/..." in output


def test_rpc_rejects_non_object_health_response(capsys):
    response = MagicMock()
    response.iter_content.return_value = [json.dumps(["before\x1b[2Jafter"]).encode()]
    checks = Checks()

    with patch("requests.post", return_value=response):
        check_rpc(checks, "https://rpc.example.test")

    output = capsys.readouterr().out
    assert checks.failures == 1
    assert "unexpected getHealth response" in output
    assert r"\x1b[2J" in output
    assert "\x1b[2J" not in output


def test_rpc_rejects_oversized_health_response(capsys):
    response = MagicMock()
    response.iter_content.return_value = [
        b"x" * (preflight.MAX_PREFLIGHT_RPC_RESPONSE_BYTES + 1),
    ]
    checks = Checks()

    with patch("requests.post", return_value=response) as post:
        check_rpc(checks, "https://rpc.example.test")

    assert checks.failures == 1
    assert "getHealth response exceeds size limit" in capsys.readouterr().out
    assert post.call_args.kwargs["stream"] is True
    response.close.assert_called_once_with()


def test_main_custom_network_requires_rpc_url(monkeypatch, capsys):
    monkeypatch.delenv("ANONMESH_RPC_URL", raising=False)
    monkeypatch.setattr(preflight, "parse_args", lambda: Namespace(
        ble=False,
        rnode=False,
        meshtastic=False,
        config=None,
        network="custom",
        rpc=None,
        skip_rpc=False,
    ))
    monkeypatch.setattr(preflight, "check_python", lambda _checks: None)
    monkeypatch.setattr(preflight, "check_module", lambda *_args: None)
    monkeypatch.setattr(preflight, "read_config", lambda *_args: {})

    assert preflight.main() == 1
    assert "Custom network requires --rpc or ANONMESH_RPC_URL" in capsys.readouterr().out
