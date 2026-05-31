"""Focused tests for one-shot client helpers."""

import builtins
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def client_module(monkeypatch):
    mesh = types.ModuleType("mesh")
    mesh.BeaconPool = object
    mesh.BeaconAnnounceHandler = object
    mesh.start_reticulum = lambda *_: None
    mesh.connect_all_parallel = lambda *_: 0
    monkeypatch.setitem(sys.modules, "mesh", mesh)

    path = Path(__file__).parents[1] / "client.py"
    spec = importlib.util.spec_from_file_location("client_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relay_requested_accepts_yes(monkeypatch, client_module):
    monkeypatch.setattr(builtins, "input", lambda *_: "y")
    assert client_module._relay_requested()


def test_relay_requested_declines_on_eof(monkeypatch, client_module):
    def raise_eof(*_):
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)
    assert not client_module._relay_requested()


def test_relay_requested_declines_on_interrupt(monkeypatch, client_module):
    def raise_interrupt(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", raise_interrupt)
    assert not client_module._relay_requested()


def test_timeout_accepts_positive_integer(client_module):
    assert client_module._build_parser().parse_args(["--timeout", "15"]).timeout == 15


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_timeout_rejects_non_positive_or_invalid_integer(client_module, value):
    with pytest.raises(SystemExit):
        client_module._build_parser().parse_args(["--timeout", value])


def test_setup_beacons_waits_for_discovery_after_explicit_connect_failure(monkeypatch, client_module):
    pool = MagicMock()
    pool.active_links.return_value = []
    pool.status_table.return_value = ""
    wait = MagicMock()
    monkeypatch.setattr(client_module.state, "pool", pool)
    monkeypatch.setattr(client_module, "BeaconAnnounceHandler", MagicMock(return_value=object()))
    monkeypatch.setattr(client_module, "_connect_beacons", MagicMock())
    monkeypatch.setattr(client_module, "_wait_for_discover_beacon", wait)
    args = types.SimpleNamespace(discover=True, beacon=["a" * 32])

    client_module._setup_beacons(args, one_shot=True)

    wait.assert_called_once_with()


def test_setup_beacons_skips_discovery_wait_when_explicit_link_is_active(monkeypatch, client_module):
    pool = MagicMock()
    pool.active_links.return_value = [object()]
    pool.status_table.return_value = ""
    wait = MagicMock()
    monkeypatch.setattr(client_module.state, "pool", pool)
    monkeypatch.setattr(client_module, "BeaconAnnounceHandler", MagicMock(return_value=object()))
    monkeypatch.setattr(client_module, "_connect_beacons", MagicMock())
    monkeypatch.setattr(client_module, "_wait_for_discover_beacon", wait)
    args = types.SimpleNamespace(discover=True, beacon=["a" * 32])

    client_module._setup_beacons(args, one_shot=True)

    wait.assert_not_called()
