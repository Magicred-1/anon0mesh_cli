"""Focused response-validation tests for the durable nonce demo."""

import importlib.util
import stat
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from solders.keypair import Keypair

import state


_mesh_stub = types.ModuleType("mesh")
_mesh_stub.BeaconPool = object
_mesh_stub.BeaconAnnounceHandler = object
_mesh_stub.start_reticulum = lambda *_: None
_prior_mesh = sys.modules.get("mesh")
sys.modules["mesh"] = _mesh_stub
try:
    _path = Path(__file__).parents[1] / "scripts" / "demo_durable_nonce_relay.py"
    _spec = importlib.util.spec_from_file_location("demo_durable_nonce_relay_under_test", _path)
    assert _spec is not None and _spec.loader is not None
    demo = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(demo)
finally:
    if _prior_mesh is None:
        sys.modules.pop("mesh", None)
    else:
        sys.modules["mesh"] = _prior_mesh


ZERO_HASH = "11111111111111111111111111111111"


@pytest.fixture
def timer():
    value = MagicMock()
    value.mark.return_value = 0
    return value


def test_extract_result_rejects_non_object_response():
    assert demo.extract_result(["not", "an", "object"]) is None


def test_mesh_rpc_rejects_non_object_response(monkeypatch, capsys):
    pool = MagicMock()
    pool.call.return_value = ["not", "an", "object"]
    monkeypatch.setattr(state, "pool", pool)

    assert demo.mesh_rpc("getSlot") is None
    assert "expected an object" in capsys.readouterr().out


@pytest.mark.parametrize("signature", [[], {}, "", True])
def test_step_airdrop_rejects_non_string_signature(monkeypatch, timer, signature):
    wait = MagicMock()
    monkeypatch.setattr(demo, "mesh_rpc", lambda *_: {"result": signature})
    monkeypatch.setattr(demo, "wait_for_confirmation", wait)

    assert demo.step_airdrop("address", 1, timer) is None
    wait.assert_not_called()


def test_step_create_nonce_rejects_boolean_rent(monkeypatch, tmp_path, timer, capsys):
    monkeypatch.setattr(demo, "mesh_rpc", lambda *_: {"result": True})

    assert demo.step_create_nonce(Keypair(), str(tmp_path), timer) == (None, None)
    nonce_path = tmp_path / "demo_nonce.json"
    assert stat.S_IMODE(nonce_path.stat().st_mode) == 0o600
    assert "Unexpected rent exemption response" in capsys.readouterr().out


def test_step_create_nonce_rejects_non_string_signature(monkeypatch, tmp_path, timer, capsys):
    responses = iter([
        {"result": 1_447_680},
        {"result": {"value": {"blockhash": ZERO_HASH}}},
        {"result": ["not", "a", "signature"]},
    ])
    wait = MagicMock()
    monkeypatch.setattr(demo, "mesh_rpc", lambda *_: next(responses))
    monkeypatch.setattr(demo, "wait_for_confirmation", wait)

    assert demo.step_create_nonce(Keypair(), str(tmp_path), timer) == (None, None)
    assert "invalid signature" in capsys.readouterr().out
    wait.assert_not_called()


@pytest.mark.parametrize("parsed, expected", [
    ([], "parsed nonce data must be an object"),
    ({"type": "initialized", "info": []}, "nonce info must be an object"),
    (
        {"type": "initialized", "info": {"blockhash": ["not", "a", "string"]}},
        "nonce blockhash must be a non-empty string",
    ),
])
def test_step_fetch_nonce_rejects_malformed_fields(monkeypatch, timer, capsys, parsed, expected):
    monkeypatch.setattr(
        demo,
        "mesh_rpc",
        lambda *_: {"result": {"value": {"data": {"parsed": parsed}}}},
    )

    assert demo.step_fetch_nonce("nonce-pubkey", timer) is None
    assert expected in capsys.readouterr().out


def test_step_relay_tx_rejects_non_string_signature(monkeypatch, timer, capsys):
    monkeypatch.setattr(demo, "mesh_rpc", lambda *_: {"result": {"bad": "signature"}})

    assert demo.step_relay_tx("transaction", timer) is None
    assert "invalid signature" in capsys.readouterr().out


def test_wait_for_confirmation_ignores_non_object_status(monkeypatch, capsys):
    clock = iter([0, 0, 2])
    monkeypatch.setattr(demo.time, "time", lambda: next(clock))
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo, "mesh_rpc", lambda *_: {"result": {"value": ["bad-status"]}})

    assert demo.wait_for_confirmation("signature", "transfer", timeout=1) is False
    assert "Unexpected getSignatureStatuses entry" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-1", str(1 << 64)])
def test_positive_u64_rejects_invalid_value(value):
    with pytest.raises(ValueError):
        demo._positive_u64(value)


def test_extract_balance_rejects_boolean(capsys):
    assert demo._extract_balance({"result": True}) == 0
    assert "Unexpected getBalance response" in capsys.readouterr().out


def test_step_sign_nonce_transfer_rejects_invalid_lamports(timer, capsys):
    assert demo.step_sign_nonce_transfer(Keypair(), "recipient", -1, "nonce", ZERO_HASH, timer) is None
    assert "positive u64 integer" in capsys.readouterr().out
