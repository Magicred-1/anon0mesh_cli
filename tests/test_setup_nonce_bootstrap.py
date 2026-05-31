"""Regression tests for the nonce bootstrap embedded in setup.sh."""

import json
from pathlib import Path

import pytest


SETUP = Path(__file__).parents[1] / "setup.sh"


def _nonce_bootstrap() -> str:
    setup = SETUP.read_text()
    marker = '          ANON0MESH_KP_PATH="$WALLET_KEYPAIR_PATH" \\\n'
    nonce_setup = setup.split(marker, 1)[1]
    return nonce_setup.split("python << 'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]


def test_nonce_bootstrap_compiles():
    compile(_nonce_bootstrap(), "<setup nonce bootstrap>", "exec")


class _Response:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        yield from self.chunks

    def close(self):
        self.closed = True


class _Requests:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def post(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.response


def _rpc_with(response, monkeypatch, tmp_path):
    monkeypatch.setenv("ANON0MESH_RPC", "https://example.invalid")
    monkeypatch.setenv("ANON0MESH_DIR", str(tmp_path))
    namespace = {}
    bootstrap = _nonce_bootstrap()
    exec(bootstrap.split("\ndef require_u64", 1)[0], namespace)
    requests = _Requests(response)
    namespace["requests"] = requests
    return namespace["rpc"], requests


def test_nonce_bootstrap_rpc_streams_bounded_response_and_closes(monkeypatch, tmp_path):
    response = _Response([json.dumps({"result": "ok"}).encode()])
    rpc, requests = _rpc_with(response, monkeypatch, tmp_path)

    assert rpc("getHealth", []) == "ok"
    assert requests.kwargs["stream"] is True
    assert response.closed is True


def test_nonce_bootstrap_rpc_rejects_oversized_response_and_closes(monkeypatch, tmp_path):
    response = _Response([b"x" * (1024 * 1024 + 1)])
    rpc, _requests = _rpc_with(response, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="response exceeds size limit"):
        rpc("getHealth", [])
    assert response.closed is True


def test_nonce_bootstrap_preserves_keypair_after_uncertain_submission():
    bootstrap = _nonce_bootstrap()

    assert bootstrap.index("submitted = False") < bootstrap.index("\ntry:\n    payer =")
    assert bootstrap.index("submitted = True") < bootstrap.index('rpc("sendTransaction"')
    assert "if submitted:" in bootstrap
    assert "transaction submission status is unknown; preserving nonce keypair" in bootstrap
    assert "elif nonce_path is not None:" in bootstrap
