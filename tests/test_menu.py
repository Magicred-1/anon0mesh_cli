"""Focused tests for interactive menu helpers."""

import menu


def test_fetch_balance_sol_accepts_wrapped_lamports(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": 1_000_000_000}})
    assert menu._fetch_balance_sol("address") == 1.0


def test_fetch_balance_sol_rejects_malformed_result(monkeypatch):
    monkeypatch.setattr(menu, "rpc_call", lambda *_: {"result": {"value": "not-lamports"}})
    assert menu._fetch_balance_sol("address") is None
