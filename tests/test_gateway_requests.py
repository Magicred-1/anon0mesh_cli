"""Regression tests for untrusted mesh gateway request payloads."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import beacon
from scripts import exit_node
from shared import build_rpc, decode_json


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
