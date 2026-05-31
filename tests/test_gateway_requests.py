"""Regression tests for untrusted mesh gateway request payloads."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import beacon
from scripts import exit_node
from shared import decode_json


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
