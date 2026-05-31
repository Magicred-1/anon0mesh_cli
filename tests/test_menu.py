"""Focused tests for interactive menu helpers."""

import pytest

import menu


@pytest.mark.parametrize("raw", [
    "not-a-number", "nan", "inf", "-inf", "0", "-1",
    "0.0000000001", "1e1000000000", "1e-1000000000",
])
def test_parse_positive_units_rejects_invalid_amount(raw):
    assert menu._parse_positive_units(raw, 1_000_000_000) is None


def test_parse_positive_units_converts_decimal_amount():
    assert menu._parse_positive_units("1.5", 1_000_000_000) == 1_500_000_000


def test_parse_positive_units_preserves_exact_u64_boundary():
    assert menu._parse_positive_units("18446744073.709551615", 1_000_000_000) == (1 << 64) - 1


def test_parse_positive_units_rejects_amount_above_u64_boundary():
    assert menu._parse_positive_units("18446744073.709551616", 1_000_000_000) is None


def test_parse_positive_units_rejects_fraction_hidden_beyond_decimal_precision():
    assert menu._parse_positive_units("0.0000000010000000000000000000000000001", 1_000_000_000) is None


@pytest.mark.parametrize("scale", [0, -1, True, 1.5])
def test_parse_positive_units_rejects_invalid_scale(scale):
    assert menu._parse_positive_units("1", scale) is None


@pytest.mark.parametrize("raw", ["not-an-int", "-1", str(1 << 32)])
def test_bounded_env_int_rejects_invalid_value(monkeypatch, raw):
    monkeypatch.setenv("TEST_OFFSET", raw)
    assert menu._bounded_env_int("TEST_OFFSET", "1", (1 << 32) - 1) is None


def test_bounded_env_int_accepts_valid_value(monkeypatch):
    monkeypatch.setenv("TEST_OFFSET", "456")
    assert menu._bounded_env_int("TEST_OFFSET", "1", (1 << 32) - 1) == 456


def test_fetch_balance_sol_accepts_wrapped_lamports(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": 1_000_000_000}})
    assert menu._fetch_balance_sol("address") == 1.0


def test_fetch_balance_sol_rejects_malformed_result(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": "not-lamports"}})
    assert menu._fetch_balance_sol("address") is None


@pytest.mark.parametrize("lamports", [-1, 1 << 64])
def test_fetch_balance_sol_rejects_out_of_range_lamports(monkeypatch, lamports):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": lamports}})
    assert menu._fetch_balance_sol("address") is None


def test_broadcast_with_retry_accepts_string_signature(monkeypatch, capsys):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": "signature"})
    menu._broadcast_with_retry("transaction")
    assert "Signature:" in capsys.readouterr().out


def test_broadcast_with_retry_escapes_terminal_control_bytes(monkeypatch, capsys):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": "before\x1b[2Jafter"})
    menu._broadcast_with_retry("transaction")
    output = capsys.readouterr().out
    assert r"before\x1b[2Jafter" in output
    assert "\x1b[2J" not in output


def test_broadcast_with_retry_handles_scalar_error(monkeypatch, capsys):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"error": "busy"})
    menu._broadcast_with_retry("transaction")
    output = capsys.readouterr().out
    assert "busy" in output
    assert "All broadcast attempts failed" in output


def test_broadcast_with_retry_rejects_malformed_signature(monkeypatch, capsys):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"unexpected": "object"}})
    menu._broadcast_with_retry("transaction")
    assert "All broadcast attempts failed" in capsys.readouterr().out


def test_select_nonce_account_escapes_terminal_control_bytes_in_path(monkeypatch):
    labels = []
    pubkey = "a" * 44
    monkeypatch.setattr(menu, "scan_nonce_accounts", lambda: [
        {"pubkey": pubkey, "path": "before\x1b[2Jafter"},
    ])
    monkeypatch.setattr(menu, "_fetch_balance_sol", lambda *_: 1.0)
    monkeypatch.setattr(menu, "_pick", lambda _prompt, options: labels.extend(options) or 0)

    assert menu._select_nonce_account() == pubkey
    assert r"before\x1b[2Jafter" in labels[0]
    assert "\x1b[2J" not in labels[0]


def test_render_header_escapes_terminal_control_bytes_in_wallet_path(monkeypatch, capsys):
    class Pool:
        strategy = "race"

        def active_links(self):
            return []

        def pending_count(self):
            return 0

    menu.state.pool = Pool()
    menu.state.active_wallet = {"pubkey": "a" * 44, "path": "before\x1b[2Jafter"}
    monkeypatch.setattr(menu, "_wallet_qr_lines", lambda _pubkey: [])

    menu._render_header()

    output = capsys.readouterr().out
    assert r"before\x1b[2Jafter" in output
    assert "\x1b[2J" not in output
