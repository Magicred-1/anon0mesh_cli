"""Regression tests for untrusted mesh gateway request payloads."""
from __future__ import annotations

import json
import stat
from unittest.mock import MagicMock, patch

import pytest

import beacon
from scripts import exit_node
from shared import MAX_MESH_REQUEST_BYTES, MAX_MESH_RESPONSE_BYTES, build_rpc, decode_json


@pytest.mark.parametrize("payload", [[], "getBalance", 1, None])
def test_beacon_rejects_non_object_json_without_forwarding(payload):
    with patch.object(beacon.requests, "post") as post:
        response = decode_json(beacon.forward_to_solana(json.dumps(payload).encode()))

    assert response["error"]["message"] == "Invalid JSON-RPC payload: expected object"
    post.assert_not_called()


@pytest.mark.parametrize("payload", [[], "getBalance", 1, None])
def test_exit_node_rejects_non_object_json_without_forwarding(payload):
    with patch.object(exit_node.requests, "post") as post:
        response, method, rtt_ms = exit_node.forward_rpc(json.dumps(payload).encode())

    assert decode_json(response)["error"]["message"] == "Invalid JSON-RPC payload: expected object"
    assert method == "?"
    assert rtt_ms == 0.0
    post.assert_not_called()


def test_beacon_rejects_oversized_mesh_request_without_forwarding():
    with patch.object(beacon, "forward_to_solana") as forward:
        response = decode_json(beacon.rpc_request_handler(
            "/rpc", b"x" * (MAX_MESH_REQUEST_BYTES + 1), None, None, None, None,
        ))

    assert response["error"]["message"] == "Mesh request exceeds size limit"
    forward.assert_not_called()


def test_exit_node_rejects_oversized_mesh_request_without_forwarding():
    with patch.object(exit_node, "forward_rpc") as forward:
        response = decode_json(exit_node.rpc_request_handler(
            "/rpc", b"x" * (MAX_MESH_REQUEST_BYTES + 1), None, None, None, None,
        ))

    assert response["error"]["message"] == "Mesh request exceeds size limit"
    forward.assert_not_called()


def test_beacon_rejects_non_list_cosign_params_without_forwarding(monkeypatch):
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", object())
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "cosignTransaction",
        "params": {"tx": "not-positional"},
    }

    with patch.object(beacon.requests, "post") as post:
        response = decode_json(beacon.forward_to_solana(json.dumps(request).encode()))

    assert response["error"]["message"] == "cosignTransaction: params[0] must be a base64 tx"
    assert response["id"] == 7
    post.assert_not_called()


def test_beacon_repairs_cosign_keypair_permissions(tmp_path, monkeypatch):
    path = tmp_path / "payer.json"
    path.write_text("[1, 2, 3]")
    path.chmod(0o666)
    fake_keypair = MagicMock()
    fake_keypair.pubkey.return_value = "payer-pubkey"
    keypair_type = MagicMock()
    keypair_type.from_bytes.return_value = fake_keypair
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "_Keypair", keypair_type)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", None)
    monkeypatch.setenv("ARCIUM_PAYER_KEYPAIR", str(path))

    beacon._load_cosign_keypair()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    keypair_type.from_bytes.assert_called_once_with(bytes([1, 2, 3]))


def test_beacon_connection_error_does_not_leak_rpc_credentials(monkeypatch, capsys):
    secret_url = "https://user:pass@rpc.example.test/private-token?api-key=secret"
    monkeypatch.setattr(beacon, "rpc_endpoint", secret_url)
    error = beacon.requests.exceptions.ConnectionError(f"failed to reach {secret_url}")

    with patch.object(beacon.requests, "post", side_effect=error):
        response = decode_json(beacon.forward_plain_rpc({}, 1, 1, "getSlot"))

    output = capsys.readouterr().out
    assert response["error"]["message"] == "Solana RPC connection error"
    assert "secret" not in output
    assert "user:pass" not in output
    assert "https://rpc.example.test/..." in output


def test_exit_node_connection_error_does_not_leak_rpc_credentials(monkeypatch, capsys):
    secret_url = "https://user:pass@rpc.example.test/private-token?api-key=secret"
    monkeypatch.setattr(exit_node, "rpc_endpoint", secret_url)
    error = exit_node.requests.exceptions.ConnectionError(f"failed to reach {secret_url}")

    with patch.object(exit_node.requests, "post", side_effect=error):
        response, method, _ = exit_node.forward_rpc(build_rpc("getSlot"))

    output = capsys.readouterr().out
    assert decode_json(response)["error"]["message"] == "Solana RPC connection error"
    assert method == "getSlot"
    assert "secret" not in output
    assert "user:pass" not in output
    assert "https://rpc.example.test/..." in output


def test_beacon_logs_scalar_rpc_error_without_traceback(capsys):
    http_response = MagicMock()
    http_response.content = b'{"error":"busy"}'
    http_response.json.return_value = {"error": "busy"}

    with patch.object(beacon.requests, "post", return_value=http_response):
        response = decode_json(beacon.forward_plain_rpc({}, 1, 1, "getSlot"))

    assert response == {"error": "busy"}
    assert "Solana error: busy" in capsys.readouterr().out


def test_exit_node_logs_scalar_rpc_error_without_traceback(capsys):
    http_response = MagicMock()
    http_response.content = b'{"error":"busy"}'
    http_response.json.return_value = {"error": "busy"}

    with patch.object(exit_node.requests, "post", return_value=http_response):
        response, method, _ = exit_node.forward_rpc(build_rpc("getSlot"))

    assert decode_json(response) == {"error": "busy"}
    assert method == "getSlot"
    assert "error: busy" in capsys.readouterr().out


def test_beacon_rejects_oversized_solana_response():
    http_response = MagicMock()
    http_response.content = b"x" * (MAX_MESH_RESPONSE_BYTES + 1)

    with patch.object(beacon.requests, "post", return_value=http_response):
        response = decode_json(beacon.forward_plain_rpc({}, 1, 1, "getSlot"))

    assert response["error"]["message"] == "Solana RPC response exceeds mesh size limit"


def test_exit_node_rejects_oversized_solana_response():
    http_response = MagicMock()
    http_response.content = b"x" * (MAX_MESH_RESPONSE_BYTES + 1)

    with patch.object(exit_node.requests, "post", return_value=http_response):
        response, method, _ = exit_node.forward_rpc(build_rpc("getSlot"))

    assert decode_json(response)["error"]["message"] == "Solana RPC response exceeds mesh size limit"
    assert method == "getSlot"
