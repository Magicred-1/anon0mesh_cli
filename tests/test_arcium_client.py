"""
tests/test_arcium_client.py — unit tests for arcium_client.py
Shim subprocess calls are mocked; no Node.js required.
"""

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import arcium_client


# ── helpers ────────────────────────────────────────────────────────────────────

def _proc(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout     = stdout
    m.stderr     = stderr
    m.returncode = returncode
    return m


def _encrypt_payload() -> dict:
    return {
        "ok": True,
        "ciphertexts": [[1] * 32],
        "pubkey_hex": "ab" * 32,
        "nonce_hex": "cd" * 16,
        "nonce_bn": "123",
        "shared_secret_hex": "ef" * 32,
    }


VALID_HEX_32 = "ab" * 32
VALID_HEX_16 = "cd" * 16
VALID_CIPHERTEXT = [1] * 32
VALID_PUBKEY = "11111111111111111111111111111111"


def _payment_client(rpc_url: str = "https://rpc.example.test"):
    client = object.__new__(arcium_client.ArciumBeaconClient)
    client.rpc_url = rpc_url
    client.program_id = arcium_client.MXE_PROGRAM_ID
    client._payer_hex = "00"
    client._payer_b58 = VALID_PUBKEY
    client.mxe_pubkey_hex = VALID_HEX_32
    client.cluster_offset = 456
    return client


def _clear_optional_payment_accounts(monkeypatch):
    monkeypatch.delenv("ARCIUM_BROADCASTER_TOKEN_ACCOUNT", raising=False)
    monkeypatch.delenv("ARCIUM_TREASURY_TOKEN_ACCOUNT", raising=False)


# ── _run_shim ─────────────────────────────────────────────────────────────────

def test_run_shim_missing_file(tmp_path):
    with patch.object(arcium_client, "SHIM_PATH", tmp_path / "missing.mjs"):
        with pytest.raises(FileNotFoundError, match="rescue_shim.mjs not found"):
            arcium_client._run_shim("keygen")


@patch("subprocess.run")
def test_run_shim_success(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc(json.dumps({"ok": True, "result": 42}))
        data = arcium_client._run_shim("keygen")
    assert data["result"] == 42


@patch("subprocess.run")
def test_run_shim_ok_false_raises(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc(json.dumps({"ok": False, "error": "bad input"}))
        with pytest.raises(RuntimeError, match="bad input"):
            arcium_client._run_shim("encrypt", "arg1")


@patch("subprocess.run")
def test_run_shim_nonzero_exit_rejects_ok_response(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc(json.dumps({"ok": True}), returncode=1)
        with pytest.raises(RuntimeError, match=r"shim error \(exit 1\)"):
            arcium_client._run_shim("keygen")


@patch("subprocess.run")
def test_run_shim_non_json_stdout_raises(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc("this is not json", returncode=1, stderr="stderr msg")
        with pytest.raises(RuntimeError, match="shim non-JSON output"):
            arcium_client._run_shim("broken")


@patch("subprocess.run")
def test_run_shim_non_object_json_raises(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc("[]")
        with pytest.raises(RuntimeError, match="shim returned non-object JSON"):
            arcium_client._run_shim("broken")


@patch("subprocess.run")
def test_run_shim_falls_back_to_stderr_in_error_msg(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc("", returncode=1, stderr="stderr detail")
        with pytest.raises(RuntimeError, match="stderr detail"):
            arcium_client._run_shim("cmd")


@patch("subprocess.run")
def test_run_shim_redacts_url_in_non_json_error(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    secret_url = "https://user:pass@rpc.example.test/private?api-key=secret"
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc("", returncode=1, stderr=f"request to {secret_url} failed")
        with pytest.raises(RuntimeError) as exc_info:
            arcium_client._run_shim("cmd")
    assert "https://rpc.example.test/..." in str(exc_info.value)
    assert "user:pass" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


@patch("subprocess.run")
def test_run_shim_passes_stdin_data(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc(json.dumps({"ok": True}))
        arcium_client._run_shim("decrypt", "arg1", stdin_data="my_secret")
    assert mock_run.call_args[1]["input"] == "my_secret"


@patch("subprocess.run")
def test_run_shim_no_stdin_when_not_provided(mock_run, tmp_path):
    shim = tmp_path / "rescue_shim.mjs"
    shim.touch()
    with patch.object(arcium_client, "SHIM_PATH", shim):
        mock_run.return_value = _proc(json.dumps({"ok": True}))
        arcium_client._run_shim("keygen")
    assert mock_run.call_args[1]["input"] is None


# ── rescue_keygen ─────────────────────────────────────────────────────────────

@patch("arcium_client._run_shim")
def test_rescue_keygen_calls_keygen(mock_shim):
    mock_shim.return_value = {"ok": True, "privkey_hex": "aa" * 32, "pubkey_hex": "cc" * 32}
    priv, pub = arcium_client.rescue_keygen()
    mock_shim.assert_called_once_with("keygen")
    assert priv == "aa" * 32
    assert pub == "cc" * 32


@patch("arcium_client._run_shim")
def test_rescue_keygen_rejects_invalid_keys(mock_shim):
    mock_shim.return_value = {"ok": True, "privkey_hex": [], "pubkey_hex": "ccdd"}
    with pytest.raises(ValueError, match="invalid keys"):
        arcium_client.rescue_keygen()


# ── rescue_encrypt ────────────────────────────────────────────────────────────

@patch("arcium_client._run_shim")
def test_rescue_encrypt_passes_mxe_pubkey_and_values_via_stdin(mock_shim):
    mock_shim.return_value = _encrypt_payload()
    arcium_client.rescue_encrypt(VALID_HEX_32, [999])
    args = mock_shim.call_args[0]
    assert args[0] == "encrypt"
    assert args[1] == VALID_HEX_32
    assert len(args) == 2
    assert json.loads(mock_shim.call_args.kwargs["stdin_data"]) == [999]


@patch("arcium_client._run_shim")
def test_rescue_encrypt_with_nonce_appends_arg(mock_shim):
    mock_shim.return_value = _encrypt_payload()
    arcium_client.rescue_encrypt(VALID_HEX_32, [1], nonce_hex=VALID_HEX_16)
    args = mock_shim.call_args[0]
    assert args == ("encrypt", VALID_HEX_32, VALID_HEX_16)
    assert json.loads(mock_shim.call_args.kwargs["stdin_data"]) == [1]


@patch("arcium_client._run_shim")
def test_rescue_encrypt_without_nonce_omits_arg(mock_shim):
    mock_shim.return_value = _encrypt_payload()
    arcium_client.rescue_encrypt(VALID_HEX_32, [1])
    args = mock_shim.call_args[0]
    assert args == ("encrypt", VALID_HEX_32)
    assert json.loads(mock_shim.call_args.kwargs["stdin_data"]) == [1]


@pytest.mark.parametrize("field, value", [
    ("ciphertexts", [[1] * 31]),
    ("pubkey_hex", "ab" * 31),
    ("nonce_hex", "cd" * 15),
    ("nonce_bn", "9" * 100_000),
    ("nonce_bn", "١"),
    ("shared_secret_hex", "ef" * 31),
])
@patch("arcium_client._run_shim")
def test_rescue_encrypt_rejects_invalid_payload(mock_shim, field, value):
    payload = _encrypt_payload()
    payload[field] = value
    mock_shim.return_value = payload
    with pytest.raises(ValueError, match="invalid payload"):
        arcium_client.rescue_encrypt(VALID_HEX_32, [1])


@pytest.mark.parametrize("mxe_pubkey, values, nonce", [
    ("bad", [1], None),
    (VALID_HEX_32, [], None),
    (VALID_HEX_32, [True], None),
    (VALID_HEX_32, [-1], None),
    (VALID_HEX_32, [1 << 256], None),
    (VALID_HEX_32, [1] * 101, None),
    (VALID_HEX_32, [1], "bad"),
])
@patch("arcium_client._run_shim")
def test_rescue_encrypt_rejects_invalid_request_without_running_shim(mock_shim, mxe_pubkey, values, nonce):
    with pytest.raises(ValueError, match="invalid encrypt request"):
        arcium_client.rescue_encrypt(mxe_pubkey, values, nonce)
    mock_shim.assert_not_called()


# ── rescue_decrypt ────────────────────────────────────────────────────────────

@patch("arcium_client._run_shim")
def test_rescue_decrypt_passes_secret_via_stdin(mock_shim):
    mock_shim.return_value = {"ok": True, "values": ["100", "200"]}
    arcium_client.rescue_decrypt(VALID_HEX_32, [VALID_CIPHERTEXT] * 2, VALID_HEX_16)
    assert mock_shim.call_args[1]["stdin_data"] == VALID_HEX_32


@patch("arcium_client._run_shim")
def test_rescue_decrypt_returns_ints(mock_shim):
    mock_shim.return_value = {"ok": True, "values": ["42", "0", "999"]}
    result = arcium_client.rescue_decrypt(VALID_HEX_32, [VALID_CIPHERTEXT] * 3, VALID_HEX_16)
    assert result == [42, 0, 999]
    assert all(isinstance(v, int) for v in result)


@patch("arcium_client._run_shim")
def test_rescue_decrypt_rejects_empty_ciphertexts_without_running_shim(mock_shim):
    ciphertexts = []
    with pytest.raises(ValueError, match="invalid decrypt request"):
        arcium_client.rescue_decrypt(VALID_HEX_32, ciphertexts, VALID_HEX_16)
    mock_shim.assert_not_called()


@patch("arcium_client._run_shim")
def test_rescue_decrypt_passes_valid_ciphertexts_and_nonce_as_args(mock_shim):
    mock_shim.return_value = {"ok": True, "values": ["1", "2"]}
    ciphertexts = [VALID_CIPHERTEXT, [2] * 32]
    arcium_client.rescue_decrypt(VALID_HEX_32, ciphertexts, VALID_HEX_16)
    args = mock_shim.call_args[0]
    assert args[0] == "decrypt"
    assert json.loads(args[1]) == ciphertexts
    assert args[2] == VALID_HEX_16


@pytest.mark.parametrize("values", [None, "42", [True], ["-1"], [{}], ["9" * 100_000], ["١"]])
@patch("arcium_client._run_shim")
def test_rescue_decrypt_rejects_invalid_values(mock_shim, values):
    mock_shim.return_value = {"ok": True, "values": values}
    with pytest.raises(ValueError, match="invalid values"):
        arcium_client.rescue_decrypt(VALID_HEX_32, [VALID_CIPHERTEXT], VALID_HEX_16)


@pytest.mark.parametrize("secret, ciphertexts, nonce", [
    ("bad", [VALID_CIPHERTEXT], VALID_HEX_16),
    (VALID_HEX_32, [], VALID_HEX_16),
    (VALID_HEX_32, [[1] * 31], VALID_HEX_16),
    (VALID_HEX_32, [[256] * 32], VALID_HEX_16),
    (VALID_HEX_32, [VALID_CIPHERTEXT] * 101, VALID_HEX_16),
    (VALID_HEX_32, [VALID_CIPHERTEXT], "bad"),
])
@patch("arcium_client._run_shim")
def test_rescue_decrypt_rejects_invalid_request_without_running_shim(mock_shim, secret, ciphertexts, nonce):
    with pytest.raises(ValueError, match="invalid decrypt request"):
        arcium_client.rescue_decrypt(secret, ciphertexts, nonce)
    mock_shim.assert_not_called()


# ── rescue_shared_secret ──────────────────────────────────────────────────────

@patch("arcium_client._run_shim")
def test_rescue_shared_secret_passes_privkey_via_stdin(mock_shim):
    mock_shim.return_value = {"ok": True, "shared_secret_hex": "de" * 32}
    arcium_client.rescue_shared_secret(VALID_HEX_32, VALID_HEX_32)
    assert mock_shim.call_args[1]["stdin_data"] == VALID_HEX_32


@patch("arcium_client._run_shim")
def test_rescue_shared_secret_passes_mxe_pubkey_as_arg(mock_shim):
    mock_shim.return_value = {"ok": True, "shared_secret_hex": "de" * 32}
    arcium_client.rescue_shared_secret(VALID_HEX_32, VALID_HEX_32)
    args = mock_shim.call_args[0]
    assert args[0] == "shared_secret"
    assert args[1] == VALID_HEX_32


@patch("arcium_client._run_shim")
def test_rescue_shared_secret_returns_hex(mock_shim):
    mock_shim.return_value = {"ok": True, "shared_secret_hex": "ca" * 32}
    result = arcium_client.rescue_shared_secret(VALID_HEX_32, VALID_HEX_32)
    assert result == "ca" * 32


@patch("arcium_client._run_shim")
def test_rescue_shared_secret_rejects_invalid_key(mock_shim):
    mock_shim.return_value = {"ok": True, "shared_secret_hex": []}
    with pytest.raises(ValueError, match="invalid key"):
        arcium_client.rescue_shared_secret(VALID_HEX_32, VALID_HEX_32)


@patch("arcium_client._run_shim")
def test_rescue_shared_secret_rejects_invalid_request_without_running_shim(mock_shim):
    with pytest.raises(ValueError, match="invalid shared_secret request"):
        arcium_client.rescue_shared_secret("bad", VALID_HEX_32)
    mock_shim.assert_not_called()


@patch("arcium_client._run_shim")
def test_log_payment_stats_redacts_shim_error(mock_shim, monkeypatch, capsys):
    secret_url = "https://user:pass@rpc.example.test/private?api-key=secret"
    mock_shim.side_effect = RuntimeError(f"request to {secret_url} failed")
    _clear_optional_payment_accounts(monkeypatch)
    client = _payment_client(secret_url)

    result = asyncio.run(client.log_payment_stats(
        1, VALID_PUBKEY, VALID_PUBKEY, VALID_PUBKEY, VALID_PUBKEY,
    ))

    assert result["message"] == "request to https://rpc.example.test/... failed"
    assert "user:pass" not in capsys.readouterr().out
    assert "secret" not in result["message"]


@pytest.mark.parametrize("signature", [None, "", [], True])
@patch("arcium_client._run_shim")
def test_log_payment_stats_rejects_invalid_shim_signature(mock_shim, signature, monkeypatch):
    mock_shim.return_value = {"signature": signature}
    _clear_optional_payment_accounts(monkeypatch)
    client = _payment_client()

    result = asyncio.run(client.log_payment_stats(
        1, VALID_PUBKEY, VALID_PUBKEY, VALID_PUBKEY, VALID_PUBKEY,
    ))

    assert result == {
        "status": "error",
        "message": "shim execute_payment returned an invalid signature",
    }


@patch("arcium_client._run_shim")
def test_log_payment_stats_rejects_invalid_metadata_without_running_shim(mock_shim, monkeypatch):
    _clear_optional_payment_accounts(monkeypatch)
    client = _payment_client()

    result = asyncio.run(client.log_payment_stats(
        1, "not-a-pubkey", VALID_PUBKEY, VALID_PUBKEY, VALID_PUBKEY,
    ))

    assert result == {"status": "error", "message": "invalid Arcium payment metadata"}
    mock_shim.assert_not_called()


# ── ArciumBeacon (disabled path) ──────────────────────────────────────────────

def test_arcium_beacon_disabled_when_env_not_set(monkeypatch):
    monkeypatch.setenv("ARCIUM_ENABLED", "0")
    beacon = arcium_client.ArciumBeacon.from_env()
    assert not beacon.enabled


def test_arcium_beacon_none_client_is_disabled():
    beacon = arcium_client.ArciumBeacon(None)
    assert not beacon.enabled
    assert beacon._loop is None
    assert beacon._thread is None


def test_arcium_beacon_failed_connect_closes_client_and_stops_loop():
    class FailingClient:
        closed = False

        async def connect(self):
            raise RuntimeError("rpc unavailable")

        async def close(self):
            self.closed = True

    client = FailingClient()
    beacon = arcium_client.ArciumBeacon(client)

    assert not beacon.enabled
    assert client.closed
    assert not beacon._thread.is_alive()
    assert beacon._loop.is_closed()


def test_arcium_beacon_log_payment_stats_returns_none_when_disabled():
    beacon = arcium_client.ArciumBeacon(None)
    result = beacon.log_payment_stats(
        amount=1000,
        payer_token_account="pTA",
        recipient="recip",
        recipient_token_account="rTA",
        mint="mint",
    )
    assert result is None


def test_arcium_beacon_from_env_no_solana_package_disables(monkeypatch):
    """When solana package is missing, Arcium must disable even if ARCIUM_ENABLED=1."""
    monkeypatch.setenv("ARCIUM_ENABLED", "1")
    with patch.object(arcium_client, "HAS_SOLANA", False):
        beacon = arcium_client.ArciumBeacon.from_env()
    assert not beacon.enabled


def test_arcium_beacon_from_env_repairs_payer_permissions(tmp_path, monkeypatch):
    path = tmp_path / "payer.json"
    path.write_text("[1, 2, 3]")
    path.chmod(0o666)
    keypair_type = MagicMock()
    keypair_type.from_bytes.return_value = object()
    monkeypatch.setenv("ARCIUM_ENABLED", "1")
    monkeypatch.setenv("ARCIUM_PAYER_KEYPAIR", str(path))
    monkeypatch.setenv("ARCIUM_MXE_PUBKEY_HEX", "ab")
    monkeypatch.setenv("ARCIUM_CLUSTER_OFFSET", "456")
    monkeypatch.setattr(arcium_client, "HAS_SOLANA", True)
    monkeypatch.setattr(arcium_client, "Keypair", keypair_type)

    with (
        patch.object(arcium_client, "ArciumBeaconClient", return_value=object()),
        patch.object(arcium_client.ArciumBeacon, "__init__", return_value=None),
    ):
        arcium_client.ArciumBeacon.from_env()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    keypair_type.from_bytes.assert_called_once_with(bytes([1, 2, 3]))


def test_arcium_beacon_from_env_refuses_symlinked_payer_without_chmod_target(
    tmp_path, monkeypatch, capsys,
):
    target = tmp_path / "payer.json"
    target.write_text("[1, 2, 3]")
    target.chmod(0o666)
    path = tmp_path / "payer-link.json"
    path.symlink_to(target)
    monkeypatch.setenv("ARCIUM_ENABLED", "1")
    monkeypatch.setenv("ARCIUM_PAYER_KEYPAIR", str(path))
    monkeypatch.setenv("ARCIUM_MXE_PUBKEY_HEX", "ab")
    monkeypatch.setattr(arcium_client, "HAS_SOLANA", True)

    beacon = arcium_client.ArciumBeacon.from_env()

    assert not beacon.enabled
    assert stat.S_IMODE(target.stat().st_mode) == 0o666
    assert "Arcium env error" in capsys.readouterr().out


@pytest.mark.parametrize("offset", ["not-an-int", "-1", str(1 << 32)])
def test_arcium_beacon_from_env_invalid_cluster_offset_disables(tmp_path, monkeypatch, offset, capsys):
    path = tmp_path / "payer.json"
    path.write_text("[1, 2, 3]")
    keypair_type = MagicMock()
    keypair_type.from_bytes.return_value = object()
    monkeypatch.setenv("ARCIUM_ENABLED", "1")
    monkeypatch.setenv("ARCIUM_PAYER_KEYPAIR", str(path))
    monkeypatch.setenv("ARCIUM_MXE_PUBKEY_HEX", "ab")
    monkeypatch.setenv("ARCIUM_CLUSTER_OFFSET", offset)
    monkeypatch.setattr(arcium_client, "HAS_SOLANA", True)
    monkeypatch.setattr(arcium_client, "Keypair", keypair_type)

    beacon = arcium_client.ArciumBeacon.from_env()

    assert not beacon.enabled
    assert "Arcium env error" in capsys.readouterr().out


# ── constants ─────────────────────────────────────────────────────────────────

def test_mxe_program_id_is_idl_address():
    # Must match declare_id! in programs/ble-revshare/src/lib.rs
    assert arcium_client.MXE_PROGRAM_ID == "7xeQNUggKc2e5q6AQxsFBLBkXGg2p54kSx11zVainMks"


def test_arcium_signer_pda_format():
    assert len(arcium_client.ARCIUM_SIGNER_PDA) >= 32
