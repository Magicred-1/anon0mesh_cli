"""
tests/test_wallet.py — unit tests for wallet.py
Requires: pip install solders
"""

import json
import base64
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import beacon as beacon_module
import state
import wallet

pytestmark = pytest.mark.skipif(
    not wallet.HAS_SOLDERS, reason="solders not installed"
)

from solders.keypair import Keypair
from solders.transaction import Transaction

# A known valid base58 Hash string (all 1s — the default/zero hash)
ZERO_HASH = "11111111111111111111111111111111"
MXE_PUBKEY_HEX = "00" * 32


def _write_keypair(path: Path) -> Keypair:
    kp = Keypair()
    path.write_text(json.dumps(list(bytes(kp))))
    return kp


def _arcium_accounts() -> dict[str, str]:
    return {
        name: str(Keypair().pubkey())
        for name in (
            "mxeAccount",
            "compDefAccount",
            "mempoolAccount",
            "executingPool",
            "computationAccount",
            "clusterAccount",
            "poolAccount",
            "clockAccount",
        )
    }


def test_load_private_keypair_repairs_permissions(tmp_path):
    path = tmp_path / "legacy.json"
    expected = _write_keypair(path)
    path.chmod(0o666)

    loaded = wallet._load_private_keypair(path)

    assert loaded.pubkey() == expected.pubkey()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── generate_wallet ───────────────────────────────────────────────────────────

def test_generate_wallet_creates_file(tmp_path):
    path = str(tmp_path / "w.json")
    result = wallet.generate_wallet(path)
    assert result == path
    assert Path(path).exists()
    data = json.loads(Path(path).read_text())
    assert isinstance(data, list)
    assert len(data) == 64


def test_generate_wallet_sets_active_wallet(tmp_path):
    path = str(tmp_path / "w.json")
    wallet.generate_wallet(path)
    assert state.active_wallet is not None
    assert state.active_wallet["path"] == path
    assert len(state.active_wallet["pubkey"]) >= 32


def test_generate_wallet_default_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = wallet.generate_wallet(None)
    assert result is not None
    assert Path(result).exists()


def test_generate_wallet_keypair_is_valid(tmp_path):
    path = str(tmp_path / "w.json")
    wallet.generate_wallet(path)
    kp = Keypair.from_bytes(bytes(json.loads(Path(path).read_text())))
    assert str(kp.pubkey()) == state.active_wallet["pubkey"]


def test_generate_wallet_permissions_owner_only(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("existing file with permissive mode")
    path.chmod(0o666)
    wallet.generate_wallet(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_generate_wallet_bad_path(capsys):
    result = wallet.generate_wallet("/nonexistent_dir/wallet.json")
    assert result is None
    assert "Failed to save" in capsys.readouterr().out


def test_generate_wallet_refuses_symlink_target(tmp_path, capsys):
    target = tmp_path / "keep.txt"
    target.write_text("keep")
    path = tmp_path / "wallet.json"
    path.symlink_to(target)

    result = wallet.generate_wallet(str(path))

    assert result is None
    assert target.read_text() == "keep"
    assert "Failed to save" in capsys.readouterr().out


# ── import_wallet ─────────────────────────────────────────────────────────────

def test_import_wallet_hex64(tmp_path):
    kp = Keypair()
    path = str(tmp_path / "imported.json")
    result = wallet.import_wallet(bytes(kp).hex(), path)
    assert result == str(kp.pubkey())
    assert Path(path).exists()


def test_import_wallet_hex64_sets_active_wallet(tmp_path):
    kp = Keypair()
    path = str(tmp_path / "imported.json")
    wallet.import_wallet(bytes(kp).hex(), path)
    assert state.active_wallet["pubkey"] == str(kp.pubkey())
    assert state.active_wallet["path"] == path


def test_import_wallet_json_array(tmp_path):
    kp = Keypair()
    path = str(tmp_path / "imported.json")
    result = wallet.import_wallet(json.dumps(list(bytes(kp))), path)
    assert result == str(kp.pubkey())


def test_import_wallet_json_array_saves_file(tmp_path):
    kp = Keypair()
    path = str(tmp_path / "imported.json")
    wallet.import_wallet(json.dumps(list(bytes(kp))), path)
    saved = json.loads(Path(path).read_text())
    assert saved == list(bytes(kp))


def test_import_wallet_permissions_owner_only(tmp_path):
    kp = Keypair()
    path = tmp_path / "imported.json"
    wallet.import_wallet(json.dumps(list(bytes(kp))), str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_import_wallet_invalid_json_array(tmp_path, capsys):
    path = str(tmp_path / "bad.json")
    result = wallet.import_wallet("[not valid json}", path)
    assert result is None
    assert "failed" in capsys.readouterr().out.lower()


def test_import_wallet_hex_wrong_length(tmp_path, capsys):
    path = str(tmp_path / "bad.json")
    result = wallet.import_wallet("deadbeef", path)  # 4 bytes — not 32 or 64
    assert result is None
    assert "must be 64 or 128" in capsys.readouterr().out


def test_import_wallet_invalid_base58(tmp_path, capsys):
    path = str(tmp_path / "bad.json")
    result = wallet.import_wallet("not_a_valid_base58_keypair!", path)
    assert result is None


# ── scan_nonce_accounts ───────────────────────────────────────────────────────

def test_scan_nonce_accounts_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert wallet.scan_nonce_accounts() == []


def test_scan_nonce_accounts_finds_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp1 = Keypair()
    kp2 = Keypair()
    (tmp_path / "nonce_aaaaaaaa.json").write_text(json.dumps(list(bytes(kp1))))
    (tmp_path / "nonce_bbbbbbbb.json").write_text(json.dumps(list(bytes(kp2))))
    result = wallet.scan_nonce_accounts()
    assert len(result) == 2
    pubkeys = {r["pubkey"] for r in result}
    assert str(kp1.pubkey()) in pubkeys
    assert str(kp2.pubkey()) in pubkeys


def test_scan_nonce_accounts_skips_corrupt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp = Keypair()
    (tmp_path / "nonce_good.json").write_text(json.dumps(list(bytes(kp))))
    (tmp_path / "nonce_bad.json").write_text("not json at all!")
    result = wallet.scan_nonce_accounts()
    assert len(result) == 1
    assert result[0]["pubkey"] == str(kp.pubkey())


def test_scan_nonce_accounts_ignores_non_nonce_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp = Keypair()
    (tmp_path / "wallet.json").write_text(json.dumps(list(bytes(kp))))
    (tmp_path / "wallet_abc.json").write_text(json.dumps(list(bytes(kp))))
    assert wallet.scan_nonce_accounts() == []


def test_scan_nonce_accounts_returns_path_and_pubkey(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp = Keypair()
    (tmp_path / "nonce_test1234.json").write_text(json.dumps(list(bytes(kp))))
    result = wallet.scan_nonce_accounts()
    assert "path" in result[0]
    assert "pubkey" in result[0]
    assert result[0]["pubkey"] == str(kp.pubkey())


def test_scan_nonce_accounts_repairs_legacy_permissions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "nonce_test1234.json"
    path.write_text(json.dumps(list(bytes(Keypair()))))
    path.chmod(0o666)
    wallet.scan_nonce_accounts()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── auto_load_wallet ──────────────────────────────────────────────────────────

def test_auto_load_finds_wallet_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp = Keypair()
    (tmp_path / "wallet.json").write_text(json.dumps(list(bytes(kp))))
    state.active_wallet = None
    wallet.auto_load_wallet()
    assert state.active_wallet is not None
    assert state.active_wallet["pubkey"] == str(kp.pubkey())
    assert "wallet.json" in state.active_wallet["path"]


def test_auto_load_repairs_legacy_wallet_permissions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "wallet.json"
    path.write_text(json.dumps(list(bytes(Keypair()))))
    path.chmod(0o666)
    wallet.auto_load_wallet()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_auto_load_finds_wallet_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp = Keypair()
    (tmp_path / "wallet_abc12345.json").write_text(json.dumps(list(bytes(kp))))
    state.active_wallet = None
    wallet.auto_load_wallet()
    assert state.active_wallet is not None
    assert state.active_wallet["pubkey"] == str(kp.pubkey())


def test_auto_load_prefers_wallet_json_over_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kp_main   = Keypair()
    kp_prefix = Keypair()
    (tmp_path / "wallet.json").write_text(json.dumps(list(bytes(kp_main))))
    (tmp_path / "wallet_abc.json").write_text(json.dumps(list(bytes(kp_prefix))))
    state.active_wallet = None
    wallet.auto_load_wallet()
    assert state.active_wallet["pubkey"] == str(kp_main.pubkey())


def test_auto_load_skips_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wallet.json").write_text("not valid json")
    state.active_wallet = None
    wallet.auto_load_wallet()
    assert state.active_wallet is None


def test_auto_load_nothing_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.active_wallet = None
    wallet.auto_load_wallet()
    assert state.active_wallet is None


# ── offline_sign_transfer ─────────────────────────────────────────────────────

def test_offline_sign_transfer_invalid_recipient(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.offline_sign_transfer(
        str(tmp_path / "payer.json"),
        "not-a-valid-pubkey",
        1,
        ZERO_HASH,
    )
    assert result is None
    assert "Invalid recipient address" in capsys.readouterr().out


def test_offline_sign_transfer_invalid_blockhash(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.offline_sign_transfer(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        1,
        "not-a-valid-blockhash",
    )
    assert result is None
    assert "Invalid blockhash" in capsys.readouterr().out


@pytest.mark.parametrize("lamports", [-1, 1 << 64])
def test_offline_sign_transfer_rejects_out_of_range_lamports(capsys, lamports):
    result = wallet.offline_sign_transfer("unused.json", str(Keypair().pubkey()), lamports, ZERO_HASH)
    assert result is None
    assert "Lamports must be an integer between" in capsys.readouterr().out


# ── offline_sign_nonce_transfer ───────────────────────────────────────────────

def test_offline_sign_nonce_transfer_returns_base64(tmp_path):
    payer    = _write_keypair(tmp_path / "payer.json")
    auth     = _write_keypair(tmp_path / "auth.json")
    nonce_kp = Keypair()
    to       = Keypair()

    tx = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "payer.json"),
        str(nonce_kp.pubkey()),
        str(tmp_path / "auth.json"),
        str(to.pubkey()),
        100_000,
        ZERO_HASH,
    )
    assert tx is not None
    decoded = base64.b64decode(tx)
    assert len(decoded) > 0


def test_offline_sign_nonce_transfer_same_payer_and_auth(tmp_path):
    payer = _write_keypair(tmp_path / "payer.json")
    to    = Keypair()
    tx = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(tmp_path / "payer.json"),   # authority == payer
        str(to.pubkey()),
        1,
        ZERO_HASH,
    )
    assert tx is not None


def test_offline_sign_nonce_transfer_missing_keypair(tmp_path, capsys):
    result = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "nonexistent.json"),
        str(Keypair().pubkey()),
        str(tmp_path / "nonexistent.json"),
        str(Keypair().pubkey()),
        100,
        ZERO_HASH,
    )
    assert result is None
    assert "Failed to load" in capsys.readouterr().out


def test_offline_sign_nonce_transfer_zero_lamports_still_signs(tmp_path):
    payer = _write_keypair(tmp_path / "payer.json")
    to    = Keypair()
    tx = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(tmp_path / "payer.json"),
        str(to.pubkey()),
        0,
        ZERO_HASH,
    )
    # 0 lamports is valid for signing (the tx is legal, Solana may reject it)
    assert tx is not None


@pytest.mark.parametrize("nonce_account,to_address", [
    ("not-a-valid-pubkey", str(Keypair().pubkey())),
    (str(Keypair().pubkey()), "not-a-valid-pubkey"),
])
def test_offline_sign_nonce_transfer_invalid_address(tmp_path, capsys, nonce_account, to_address):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "payer.json"),
        nonce_account,
        str(tmp_path / "payer.json"),
        to_address,
        1,
        ZERO_HASH,
    )
    assert result is None
    assert "Invalid address" in capsys.readouterr().out


def test_offline_sign_nonce_transfer_invalid_nonce_value(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.offline_sign_nonce_transfer(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        1,
        "not-a-valid-blockhash",
    )
    assert result is None
    assert "Invalid nonce value" in capsys.readouterr().out


@pytest.mark.parametrize("lamports", [-1, 1 << 64])
def test_offline_sign_nonce_transfer_rejects_out_of_range_lamports(capsys, lamports):
    result = wallet.offline_sign_nonce_transfer(
        "unused-payer.json",
        str(Keypair().pubkey()),
        "unused-auth.json",
        str(Keypair().pubkey()),
        lamports,
        ZERO_HASH,
    )
    assert result is None
    assert "Lamports must be an integer between" in capsys.readouterr().out


# ── partial_sign_execute_payment ──────────────────────────────────────────────

@patch("wallet._account_exists", return_value=True)
@patch("arcium_client._run_shim")
@patch("arcium_client.rescue_encrypt")
def test_partial_sign_execute_payment_returns_base64(
    mock_encrypt, mock_shim, _mock_exists, tmp_path, monkeypatch,
):
    payer   = _write_keypair(tmp_path / "payer.json")
    beacon  = Keypair()
    to      = Keypair()
    nonce   = Keypair()
    mint    = Keypair()
    mock_encrypt.return_value = {
        "ciphertexts": [[0] * 32],
        "pubkey_hex": "00" * 32,
        "nonce_bn": "0",
    }
    mock_shim.return_value = _arcium_accounts()

    tx = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(beacon.pubkey()),
        str(nonce.pubkey()),
        str(to.pubkey()),
        500_000,
        MXE_PUBKEY_HEX,
        str(mint.pubkey()),
        nonce_value=ZERO_HASH,
    )
    assert tx is not None
    decoded = base64.b64decode(tx)
    assert len(decoded) > 0
    monkeypatch.setattr(beacon_module, "beacon_cosign_keypair", beacon)
    assert beacon_module._validate_cosign_transaction(Transaction.from_bytes(decoded)) is None


def test_partial_sign_execute_payment_missing_keypair(tmp_path, capsys):
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "missing.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
    )
    assert result is None
    assert "Failed to load" in capsys.readouterr().out


def test_partial_sign_execute_payment_invalid_address(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        "not-a-valid-pubkey",
        "also-invalid",
        "still-invalid",
        1_000,
        MXE_PUBKEY_HEX,
        "invalid-mint",
    )
    assert result is None
    assert "Invalid address" in capsys.readouterr().out


def test_partial_sign_execute_payment_invalid_broadcaster_token_account(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
        broadcaster_token_account_str="not-a-valid-pubkey",
    )
    assert result is None
    assert "Invalid address" in capsys.readouterr().out


@pytest.mark.parametrize("amount", [-1, 1 << 64])
def test_partial_sign_execute_payment_rejects_out_of_range_amount(capsys, amount):
    result = wallet.partial_sign_execute_payment(
        "unused.json",
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        amount,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
    )
    assert result is None
    assert "Amount must be an integer between" in capsys.readouterr().out


@pytest.mark.parametrize("cluster_offset", [-1, True, 1 << 32])
def test_partial_sign_execute_payment_rejects_out_of_range_cluster_offset(capsys, cluster_offset):
    result = wallet.partial_sign_execute_payment(
        "unused.json",
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
        cluster_offset=cluster_offset,
    )
    assert result is None
    assert "Cluster offset must be an integer between" in capsys.readouterr().out


@patch("wallet._account_exists", return_value=True)
@patch("arcium_client._run_shim")
@patch("arcium_client.rescue_encrypt")
def test_partial_sign_execute_payment_invalid_nonce_value(mock_encrypt, mock_shim, _mock_exists, tmp_path, capsys):
    mock_encrypt.return_value = {
        "ciphertexts": [[0] * 32],
        "pubkey_hex": "00" * 32,
        "nonce_bn": "0",
    }
    mock_shim.return_value = _arcium_accounts()
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
        nonce_value="not-a-valid-blockhash",
    )
    assert result is None
    assert "Invalid nonce value" in capsys.readouterr().out


@patch("arcium_client._run_shim")
@patch("arcium_client.rescue_encrypt")
def test_partial_sign_execute_payment_invalid_encryption_payload(mock_encrypt, mock_shim, tmp_path, capsys):
    mock_encrypt.return_value = {}
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
    )
    assert result is None
    assert "Invalid Arcium encryption payload" in capsys.readouterr().out
    mock_shim.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"ciphertexts": [[0] * 31], "pubkey_hex": "00" * 32, "nonce_bn": "0"},
    {"ciphertexts": [[0] * 32], "pubkey_hex": "00" * 31, "nonce_bn": "0"},
    {"ciphertexts": [[0] * 32], "pubkey_hex": "00" * 32, "nonce_bn": True},
    {"ciphertexts": [[0] * 32], "pubkey_hex": "00" * 32, "nonce_bn": "-1"},
    {"ciphertexts": [[0] * 32], "pubkey_hex": "00" * 32, "nonce_bn": "9" * 100_000},
    {"ciphertexts": [[0] * 32], "pubkey_hex": "00" * 32, "nonce_bn": "١"},
])
@patch("arcium_client._run_shim")
@patch("arcium_client.rescue_encrypt")
def test_partial_sign_execute_payment_rejects_malformed_encryption_fields(
    mock_encrypt, mock_shim, tmp_path, capsys, payload,
):
    mock_encrypt.return_value = payload
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
    )
    assert result is None
    assert "Invalid Arcium encryption payload" in capsys.readouterr().out
    mock_shim.assert_not_called()


@patch("arcium_client._run_shim")
@patch("arcium_client.rescue_encrypt")
def test_partial_sign_execute_payment_invalid_account_metadata(mock_encrypt, mock_shim, tmp_path, capsys):
    mock_encrypt.return_value = {
        "ciphertexts": [[0] * 32],
        "pubkey_hex": "00" * 32,
        "nonce_bn": "0",
    }
    mock_shim.return_value = {"mxeAccount": "not-a-valid-pubkey"}
    _write_keypair(tmp_path / "payer.json")
    result = wallet.partial_sign_execute_payment(
        str(tmp_path / "payer.json"),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        str(Keypair().pubkey()),
        1_000,
        MXE_PUBKEY_HEX,
        str(Keypair().pubkey()),
    )
    assert result is None
    assert "Invalid Arcium account metadata" in capsys.readouterr().out


# ── create_nonce_account (instruction build) ──────────────────────────────────

def test_create_nonce_account_authority_param_name(tmp_path):
    """
    Regression: InitializeNonceAccountParams uses 'authority', not 'authorized_pubkey'.
    Mock out RPC calls and verify the instruction builds without ValueError.
    """
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")

    mock_blockhash = MagicMock(return_value="4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi")

    with patch("rpc.rpc_call", side_effect=[{"result": 1_447_680}, {"result": "SIG"}]), \
         patch("rpc.get_recent_blockhash", mock_blockhash):
        # The call must not raise ValueError: Missing required key: authority
        try:
            result = wallet.create_nonce_account(
                str(tmp_path / "payer.json"),
                str(tmp_path / "nonce.json"),
                None,
            )
        except ValueError as e:
            pytest.fail(f"InitializeNonceAccountParams raised ValueError: {e}")
    assert result is not None


def test_create_nonce_account_generated_key_permissions(tmp_path, monkeypatch):
    _write_keypair(tmp_path / "payer.json")
    monkeypatch.chdir(tmp_path)
    mock_blockhash = MagicMock(return_value="4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi")

    with patch("rpc.rpc_call", side_effect=[{"result": 1_447_680}, {"result": "SIG"}]), \
         patch("rpc.get_recent_blockhash", mock_blockhash):
        result = wallet.create_nonce_account(str(tmp_path / "payer.json"))

    assert result is not None
    nonce_paths = list(tmp_path.glob("nonce_*.json"))
    assert len(nonce_paths) == 1
    assert stat.S_IMODE(nonce_paths[0].stat().st_mode) == 0o600


def test_create_nonce_account_generated_key_write_failure(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")

    with patch("wallet._save_private_keypair", side_effect=OSError("refused")):
        result = wallet.create_nonce_account(str(tmp_path / "payer.json"))

    assert result is None
    assert "Failed to save nonce keypair: refused" in capsys.readouterr().out


def test_create_nonce_account_invalid_authority(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")
    result = wallet.create_nonce_account(
        str(tmp_path / "payer.json"),
        str(tmp_path / "nonce.json"),
        "not-a-valid-pubkey",
    )
    assert result is None
    assert "Invalid authority address" in capsys.readouterr().out


def test_create_nonce_account_invalid_blockhash(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")

    with patch("rpc.rpc_call", return_value={"result": 1_447_680}), \
         patch("rpc.get_recent_blockhash", return_value="not-a-valid-blockhash"):
        result = wallet.create_nonce_account(
            str(tmp_path / "payer.json"),
            str(tmp_path / "nonce.json"),
            None,
        )

    assert result is None
    assert "Invalid blockhash" in capsys.readouterr().out


def test_create_nonce_account_scalar_send_error(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")
    blockhash = "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi"

    with patch("rpc.rpc_call", side_effect=[{"result": 1_447_680}, {"error": "busy"}]), \
         patch("rpc.get_recent_blockhash", return_value=blockhash):
        result = wallet.create_nonce_account(
            str(tmp_path / "payer.json"),
            str(tmp_path / "nonce.json"),
            None,
        )

    assert result is None
    assert "busy" in capsys.readouterr().out


def test_create_nonce_account_rejects_boolean_rent(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")

    with patch("rpc.rpc_call", return_value={"result": True}):
        result = wallet.create_nonce_account(
            str(tmp_path / "payer.json"),
            str(tmp_path / "nonce.json"),
            None,
        )

    assert result is None
    assert "Unexpected getMinimumBalanceForRentExemption response" in capsys.readouterr().out


def test_create_nonce_account_rejects_missing_signature(tmp_path, capsys):
    _write_keypair(tmp_path / "payer.json")
    _write_keypair(tmp_path / "nonce.json")
    blockhash = "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi"

    with patch("rpc.rpc_call", side_effect=[{"result": 1_447_680}, {"result": None}]), \
         patch("rpc.get_recent_blockhash", return_value=blockhash):
        result = wallet.create_nonce_account(
            str(tmp_path / "payer.json"),
            str(tmp_path / "nonce.json"),
            None,
        )

    assert result is None
    assert "Unexpected sendTransaction response" in capsys.readouterr().out
