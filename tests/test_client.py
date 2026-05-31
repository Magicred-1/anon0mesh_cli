"""Focused tests for one-shot client helpers."""

import builtins
import importlib.util
import sys
import types
from pathlib import Path

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
