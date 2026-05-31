"""Regression tests for untrusted mesh gateway request payloads."""
from __future__ import annotations

import base64
import json
import stat
from unittest.mock import MagicMock, patch

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import (
    advance_nonce_account, transfer, AdvanceNonceAccountParams, TransferParams,
)
from solders.transaction import Transaction

import beacon
from scripts import exit_node
from shared import MAX_MESH_REQUEST_BYTES, MAX_MESH_RESPONSE_BYTES, build_rpc, decode_json


def _execute_payment_transaction(extra_instructions=None, broadcaster=None, include_ata=False):
    payer = Keypair()
    broadcaster = broadcaster or Keypair()
    nonce = Keypair().pubkey()
    advance = advance_nonce_account(AdvanceNonceAccountParams(
        nonce_pubkey=nonce,
        authorized_pubkey=payer.pubkey(),
    ))
    ata_instructions = []
    if include_ata:
        ata_instructions.append(Instruction(
            program_id=Pubkey.from_string(beacon._ATA_PROGRAM_ID),
            accounts=[
                AccountMeta(payer.pubkey(), True, True),
                AccountMeta(Keypair().pubkey(), False, True),
                AccountMeta(broadcaster.pubkey(), False, False),
                AccountMeta(Keypair().pubkey(), False, False),
                AccountMeta(Pubkey.from_string(beacon._SYSTEM_PROGRAM_ID), False, False),
                AccountMeta(Keypair().pubkey(), False, False),
            ],
            data=b"\x01",
        ))
    accounts = [
        AccountMeta(payer.pubkey(), True, True),
        AccountMeta(broadcaster.pubkey(), True, False),
        *[AccountMeta(Keypair().pubkey(), False, True) for _ in range(19)],
    ]
    execute_payment = Instruction(
        program_id=Pubkey.from_string(beacon._MXE_PROGRAM_ID),
        accounts=accounts,
        data=beacon._EXECUTE_PAYMENT_DISC + b"\x00" * 96,
    )
    blockhash = Hash.default()
    message = Message.new_with_blockhash(
        [advance, *ata_instructions, *(extra_instructions or []), execute_payment],
        payer.pubkey(),
        blockhash,
    )
    tx = Transaction.new_unsigned(message)
    tx.partial_sign([payer], blockhash)
    return tx, broadcaster


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


def test_beacon_custom_network_requires_rpc_url(monkeypatch, capsys):
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    with pytest.raises(SystemExit):
        beacon.setup_beacon(None, "custom", None)
    assert "Custom network requires --rpc or SOLANA_RPC_URL" in capsys.readouterr().out


def test_exit_node_custom_network_requires_rpc_url(monkeypatch, capsys):
    monkeypatch.delenv("ANONMESH_RPC_URL", raising=False)
    with pytest.raises(SystemExit):
        exit_node.setup_exit_node(None, "custom", None)
    assert "Custom network requires --rpc or ANONMESH_RPC_URL" in capsys.readouterr().out


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


def test_beacon_cosigns_allowlisted_execute_payment_shape(monkeypatch):
    tx, broadcaster = _execute_payment_transaction(include_ata=True)
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", broadcaster)

    with patch.object(beacon, "forward_plain_rpc", return_value=b'{"result":"sig"}') as forward:
        response = decode_json(beacon._handle_cosign_transaction(
            [base64.b64encode(bytes(tx)).decode()], 7, 1,
        ))

    assert response["result"] == "sig"
    submitted_b64 = forward.call_args.args[0]["params"][0]
    submitted = Transaction.from_bytes(base64.b64decode(submitted_b64))
    assert submitted.verify_with_results() == [True, True]


def test_beacon_rejects_noncanonical_base64_cosign_transaction(monkeypatch):
    tx, broadcaster = _execute_payment_transaction()
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", broadcaster)
    encoded = base64.b64encode(bytes(tx)).decode() + "!"

    with patch.object(beacon, "forward_plain_rpc") as forward:
        response = decode_json(beacon._handle_cosign_transaction([encoded], 7, 1))

    assert response["error"]["message"].startswith("Co-sign failed:")
    forward.assert_not_called()


def test_beacon_rejects_cosign_shape_that_can_spend_broadcaster_funds(monkeypatch):
    _, broadcaster = _execute_payment_transaction()
    spend = transfer(TransferParams(
        from_pubkey=broadcaster.pubkey(),
        to_pubkey=Keypair().pubkey(),
        lamports=1,
    ))
    tx, _ = _execute_payment_transaction([spend], broadcaster)
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", broadcaster)

    with patch.object(beacon, "forward_plain_rpc") as forward:
        response = decode_json(beacon._handle_cosign_transaction(
            [base64.b64encode(bytes(tx)).decode()], 7, 1,
        ))

    assert response["error"]["message"].startswith("Co-sign rejected:")
    forward.assert_not_called()


def test_beacon_rejects_cosign_shape_with_unallowlisted_instruction(monkeypatch):
    unrelated = Instruction(
        program_id=Keypair().pubkey(),
        accounts=[],
        data=b"",
    )
    tx, broadcaster = _execute_payment_transaction([unrelated])
    monkeypatch.setattr(beacon, "HAS_SOLDERS", True)
    monkeypatch.setattr(beacon, "beacon_cosign_keypair", broadcaster)

    with patch.object(beacon, "forward_plain_rpc") as forward:
        response = decode_json(beacon._handle_cosign_transaction(
            [base64.b64encode(bytes(tx)).decode()], 7, 1,
        ))

    assert response["error"]["message"].startswith("Co-sign rejected:")
    forward.assert_not_called()


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


@pytest.mark.parametrize("params", [1, ["tx", {"arcium": "not-an-object"}]])
def test_beacon_logs_skip_for_malformed_arcium_stats_metadata(params, monkeypatch, capsys):
    arcium = MagicMock(enabled=True)
    monkeypatch.setattr(beacon, "arcium", arcium)
    for variable in (
        "ARCIUM_MINT",
        "ARCIUM_PAYER_TOKEN_ACCOUNT",
        "ARCIUM_RECIPIENT_TOKEN_ACCOUNT",
        "ARCIUM_BROADCASTER_TOKEN_ACCOUNT",
    ):
        monkeypatch.delenv(variable, raising=False)

    beacon._maybe_log_arcium_stats(params, b'{"result":"signature"}', 1)

    arcium.log_payment_stats.assert_not_called()
    assert "Arcium skipped" in capsys.readouterr().out


@pytest.mark.parametrize("amount", [-1, True, 1.5, str(1 << 64), "9" * 100_000, "١"])
def test_beacon_rejects_invalid_arcium_stats_amount(amount, monkeypatch, capsys):
    arcium = MagicMock(enabled=True)
    monkeypatch.setattr(beacon, "arcium", arcium)

    beacon._fire_arcium_stats(
        {"amount": amount, "mint": "mint", "payer_ta": "payer-ta"},
        1,
        "test",
    )

    arcium.log_payment_stats.assert_not_called()
    assert "amount must be a u64 integer" in capsys.readouterr().out


def test_beacon_rejects_non_string_arcium_stats_account(monkeypatch, capsys):
    arcium = MagicMock(enabled=True)
    monkeypatch.setattr(beacon, "arcium", arcium)

    beacon._fire_arcium_stats(
        {"amount": 1, "mint": ["not", "a", "string"], "payer_ta": "payer-ta"},
        1,
        "test",
    )

    arcium.log_payment_stats.assert_not_called()
    assert "account metadata must be strings" in capsys.readouterr().out


def test_beacon_queues_valid_arcium_stats_metadata(monkeypatch):
    arcium = MagicMock(enabled=True)
    monkeypatch.setattr(beacon, "arcium", arcium)

    beacon._fire_arcium_stats(
        {"amount": "42", "mint": "mint", "payer_ta": "payer-ta"},
        1,
        "test",
    )

    assert arcium.log_payment_stats.call_args.kwargs["amount"] == 42


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


@pytest.mark.parametrize("body", [
    b"not-json",
    b"[]",
    b"{}",
    b'{"result":1,"error":"bad"}',
])
def test_beacon_rejects_malformed_solana_response(body):
    http_response = MagicMock()
    http_response.content = body

    with patch.object(beacon.requests, "post", return_value=http_response):
        response = decode_json(beacon.forward_plain_rpc({}, 1, 1, "getSlot"))

    assert response["error"]["message"] == "Solana RPC returned invalid JSON-RPC response"


@pytest.mark.parametrize("body", [
    b"not-json",
    b"[]",
    b"{}",
    b'{"result":1,"error":"bad"}',
])
def test_exit_node_rejects_malformed_solana_response(body):
    http_response = MagicMock()
    http_response.content = body

    with patch.object(exit_node.requests, "post", return_value=http_response):
        response, method, _ = exit_node.forward_rpc(build_rpc("getSlot"))

    assert decode_json(response)["error"]["message"] == "Solana RPC returned invalid JSON-RPC response"
    assert method == "getSlot"


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
