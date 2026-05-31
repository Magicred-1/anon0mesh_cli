"""
tests/test_shared.py — unit tests for shared.py
"""

import json
import stat
import zlib
from pathlib import Path

import pytest

import shared


# ── build_rpc ─────────────────────────────────────────────────────────────────

def test_build_rpc_structure():
    payload = json.loads(shared.build_rpc("getSlot"))
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "getSlot"
    assert payload["params"] == []
    assert payload["id"] == 1


def test_build_rpc_with_params():
    payload = json.loads(shared.build_rpc("getBalance", ["addr123"], req_id=7))
    assert payload["params"] == ["addr123"]
    assert payload["id"] == 7


def test_build_rpc_none_params_becomes_empty_list():
    payload = json.loads(shared.build_rpc("getSlot", None))
    assert payload["params"] == []


def test_build_rpc_returns_bytes():
    result = shared.build_rpc("getSlot")
    assert isinstance(result, bytes)


# ── build_response ─────────────────────────────────────────────────────────────

def test_build_response_result():
    payload = json.loads(shared.build_response(result=42))
    assert payload["result"] == 42
    assert "error" not in payload


def test_build_response_result_dict():
    payload = json.loads(shared.build_response(result={"value": 99}))
    assert payload["result"] == {"value": 99}


def test_build_response_error():
    payload = json.loads(shared.build_response(error="something went wrong"))
    assert payload["error"]["message"] == "something went wrong"
    assert payload["error"]["code"] == -32000
    assert "result" not in payload


def test_build_response_req_id():
    payload = json.loads(shared.build_response(result=1, req_id=99))
    assert payload["id"] == 99


def test_build_response_returns_bytes():
    assert isinstance(shared.build_response(result=1), bytes)


# ── decode_json ────────────────────────────────────────────────────────────────

def test_decode_json_roundtrip():
    original = {"method": "getSlot", "params": [1, 2, 3]}
    assert shared.decode_json(json.dumps(original).encode()) == original


def test_decode_json_nested():
    data = {"result": {"value": {"blockhash": "abc"}}}
    assert shared.decode_json(json.dumps(data).encode()) == data


# ── redact_url ────────────────────────────────────────────────────────────────

def test_redact_url_hides_query_credentials_and_path_tokens():
    url = "https://user:pass@rpc.example.test/private-token?api-key=secret#fragment"
    assert shared.redact_url(url) == "https://rpc.example.test/..."


def test_redact_url_preserves_safe_host_and_port():
    assert shared.redact_url("https://rpc.example.test:8899") == "https://rpc.example.test:8899"


def test_redact_url_formats_ipv6_host():
    assert shared.redact_url("http://[::1]:8899/token") == "http://[::1]:8899/..."


def test_redact_url_rejects_non_url_values():
    assert shared.redact_url("not-a-url") == "<redacted-rpc-url>"


def test_redact_urls_hides_embedded_credentials_and_path_tokens():
    text = "request to https://user:pass@rpc.example.test/private?api-key=secret failed"
    assert shared.redact_urls(text) == "request to https://rpc.example.test/... failed"


# ── rpc_error_message ─────────────────────────────────────────────────────────

def test_rpc_error_message_extracts_object_message():
    assert shared.rpc_error_message({"message": "busy"}) == "busy"


def test_rpc_error_message_accepts_scalar_error():
    assert shared.rpc_error_message("busy") == "busy"


# ── private local files ───────────────────────────────────────────────────────

def test_restrict_private_file_permissions_repairs_existing_file(tmp_path):
    path = tmp_path / "identity"
    path.write_text("secret")
    path.chmod(0o666)
    shared.restrict_private_file_permissions(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_private_identity_uses_owner_only_permissions(tmp_path):
    path = tmp_path / "identity"

    class FakeIdentity:
        def to_file(self, output_path):
            Path(output_path).write_text("secret")

    shared.save_private_identity(FakeIdentity(), str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── response compression ──────────────────────────────────────────────────────

def test_compressed_response_roundtrip():
    raw = b"repeated payload " * 100
    assert shared.decompress_response(shared.compress_response(raw)) == raw


def test_decompress_response_rejects_expansion_over_limit():
    raw = b"x" * (shared._MAX_DECOMPRESSED_RESPONSE_BYTES + 1)
    compressed = shared._COMPRESS_MAGIC + zlib.compress(raw)
    with pytest.raises(ValueError, match="expanded size limit"):
        shared.decompress_response(compressed)


# ── quiet mode ─────────────────────────────────────────────────────────────────

def test_log_info_visible_by_default(capsys):
    shared.set_quiet(False)
    shared.log_info("hello from log_info")
    assert "hello from log_info" in capsys.readouterr().out


def test_log_info_suppressed_when_quiet(capsys):
    shared.set_quiet(True)
    shared.log_info("should be hidden")
    assert "should be hidden" not in capsys.readouterr().out


def test_log_tx_suppressed_when_quiet(capsys):
    shared.set_quiet(True)
    shared.log_tx("tx hidden")
    assert "tx hidden" not in capsys.readouterr().out


def test_log_tx_visible_when_not_quiet(capsys):
    shared.set_quiet(False)
    shared.log_tx("tx visible")
    assert "tx visible" in capsys.readouterr().out


def test_log_ok_always_visible_even_when_quiet(capsys):
    shared.set_quiet(True)
    shared.log_ok("ok message")
    assert "ok message" in capsys.readouterr().out


def test_log_warn_always_visible_even_when_quiet(capsys):
    shared.set_quiet(True)
    shared.log_warn("warn message")
    assert "warn message" in capsys.readouterr().out


def test_log_err_always_visible_even_when_quiet(capsys):
    shared.set_quiet(True)
    shared.log_err("err message")
    assert "err message" in capsys.readouterr().out


def test_set_quiet_false_restores_log_info(capsys):
    shared.set_quiet(True)
    shared.set_quiet(False)
    shared.log_info("restored")
    assert "restored" in capsys.readouterr().out


def test_log_ok_contains_checkmark(capsys):
    shared.log_ok("done")
    assert "✔" in capsys.readouterr().out


def test_log_err_contains_cross(capsys):
    shared.log_err("failed")
    assert "✘" in capsys.readouterr().out


def test_log_warn_contains_warning_symbol(capsys):
    shared.log_warn("watch out")
    assert "⚠" in capsys.readouterr().out
