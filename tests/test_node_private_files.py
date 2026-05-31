"""Regression tests for Node helper credential-file reads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_FILE_HELPER = PROJECT_ROOT / "scripts" / "private_file.mjs"


def _read_private_text(path: Path) -> subprocess.CompletedProcess:
    script = (
        f'import {{ readPrivateTextFileSync }} from {json.dumps(PRIVATE_FILE_HELPER.as_uri())};'
        'process.stdout.write(readPrivateTextFileSync(process.env.DEST));'
    )
    return subprocess.run(
        ["node", "--input-type=module", "-e", script],
        env={**os.environ, "DEST": str(path)},
        text=True,
        capture_output=True,
    )


def test_node_private_reader_repairs_regular_file_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text("SECRET=value\n")
    path.chmod(0o666)

    result = _read_private_text(path)

    assert result.returncode == 0
    assert result.stdout == "SECRET=value\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_node_private_reader_refuses_symlink_without_chmod_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("SECRET=value\n")
    target.chmod(0o666)
    path = tmp_path / ".env"
    path.symlink_to(target)

    result = _read_private_text(path)

    assert result.returncode != 0
    assert target.stat().st_mode & 0o777 == 0o666


def test_node_private_reader_refuses_non_regular_file(tmp_path):
    result = _read_private_text(tmp_path)

    assert result.returncode != 0


def test_node_private_reader_rejects_oversized_file(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"x" * (1024 * 1024 + 1))

    result = _read_private_text(path)

    assert result.returncode != 0


def test_node_private_reader_rejects_invalid_utf8(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"\xff")

    result = _read_private_text(path)

    assert result.returncode != 0
