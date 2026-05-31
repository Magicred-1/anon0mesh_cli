"""
tests/test_shared.py — unit tests for shared.py
"""

import json
import os
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


def test_terminal_safe_text_escapes_control_bytes():
    assert shared.terminal_safe_text("before\x1b[2J\nafter") == r"before\x1b[2J\x0aafter"


# ── numeric parsing ───────────────────────────────────────────────────────────

def test_positive_int_accepts_positive_value():
    assert shared.positive_int("15") == 15


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_positive_int_rejects_non_positive_or_invalid_value(value):
    with pytest.raises(ValueError):
        shared.positive_int(value)


@pytest.mark.parametrize("value", [0, (1 << 64) - 1])
def test_is_u64_accepts_bounds(value):
    assert shared.is_u64(value)


@pytest.mark.parametrize("value", [-1, 1 << 64, True, 1.0, "1"])
def test_is_u64_rejects_out_of_range_or_non_integer_values(value):
    assert not shared.is_u64(value)


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


def test_load_dotenv_private_repairs_permissions_and_preserves_existing_env(
    tmp_path, monkeypatch,
):
    path = tmp_path / ".env"
    path.write_text("NEW_TEST_VALUE=loaded\nEXISTING_TEST_VALUE=from-file\n")
    path.chmod(0o666)
    monkeypatch.delenv("NEW_TEST_VALUE", raising=False)
    monkeypatch.setenv("EXISTING_TEST_VALUE", "from-process")

    shared.load_dotenv_private(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.environ["NEW_TEST_VALUE"] == "loaded"
    assert os.environ["EXISTING_TEST_VALUE"] == "from-process"


# ── response compression ──────────────────────────────────────────────────────

def test_compressed_response_roundtrip():
    raw = b"repeated payload " * 100
    assert shared.decompress_response(shared.compress_response(raw)) == raw


def test_decompress_response_rejects_expansion_over_limit():
    raw = b"x" * (shared.MAX_MESH_RESPONSE_BYTES + 1)
    compressed = shared._COMPRESS_MAGIC + zlib.compress(raw)
    with pytest.raises(ValueError, match="expanded size limit"):
        shared.decompress_response(compressed)


def test_decompress_response_rejects_oversized_uncompressed_payload():
    with pytest.raises(ValueError, match="Response exceeds size limit"):
        shared.decompress_response(b"x" * (shared.MAX_MESH_RESPONSE_BYTES + 1))


def test_decompress_response_rejects_truncated_compressed_payload():
    compressed = shared._COMPRESS_MAGIC + zlib.compress(b"payload" * 100)
    with pytest.raises(ValueError, match="malformed"):
        shared.decompress_response(compressed[:-1])


def test_decompress_response_rejects_trailing_compressed_payload():
    compressed = shared._COMPRESS_MAGIC + zlib.compress(b"payload") + b"trailing"
    with pytest.raises(ValueError, match="malformed"):
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


def test_log_warn_escapes_terminal_control_bytes(capsys):
    shared.log_warn("before\x1b[2Jafter")
    output = capsys.readouterr().out
    assert r"before\x1b[2Jafter" in output
    assert "\x1b[2J" not in output
