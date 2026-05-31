"""Regression tests for the nonce bootstrap embedded in setup.sh."""

from pathlib import Path


SETUP = Path(__file__).parents[1] / "setup.sh"


def _nonce_bootstrap() -> str:
    setup = SETUP.read_text()
    marker = '          ANON0MESH_KP_PATH="$WALLET_KEYPAIR_PATH" \\\n'
    nonce_setup = setup.split(marker, 1)[1]
    return nonce_setup.split("python << 'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]


def test_nonce_bootstrap_compiles():
    compile(_nonce_bootstrap(), "<setup nonce bootstrap>", "exec")


def test_nonce_bootstrap_preserves_keypair_after_uncertain_submission():
    bootstrap = _nonce_bootstrap()

    assert bootstrap.index("submitted = False") < bootstrap.index("try:")
    assert bootstrap.index("submitted = True") < bootstrap.index('rpc("sendTransaction"')
    assert "if submitted:" in bootstrap
    assert "transaction submission status is unknown; preserving nonce keypair" in bootstrap
    assert "elif nonce_path is not None:" in bootstrap
