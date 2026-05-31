"""Focused tests for interactive menu helpers."""

import menu


def test_fetch_balance_sol_accepts_wrapped_lamports(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": 1_000_000_000}})
    assert menu._fetch_balance_sol("address") == 1.0


def test_fetch_balance_sol_rejects_malformed_result(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": "not-lamports"}})
    assert menu._fetch_balance_sol("address") is None


def test_broadcast_with_retry_accepts_string_signature(monkeypatch, capsys):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": "signature"})
    menu._broadcast_with_retry("transaction")
    assert "Signature:" in capsys.readouterr().out


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
