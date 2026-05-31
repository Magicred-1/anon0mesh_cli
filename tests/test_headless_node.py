"""Regression tests for the headless-node launcher."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "scripts" / "headless-node.sh"


def test_launcher_uses_restrictive_umask(tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "mkdir -p \"$ANONMESH_STATE_DIR\"\n"
        "umask > \"$ANONMESH_STATE_DIR/observed-umask\"\n"
    )
    fake_python.chmod(0o755)
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "ANONMESH_PYTHON": str(fake_python),
        "ANONMESH_STATE_DIR": str(state_dir),
    }

    result = subprocess.run(
        [str(LAUNCHER), "preflight"],
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert int((state_dir / "observed-umask").read_text().strip(), 8) == 0o77


def test_stop_clears_stale_pid_without_signalling_unrelated_process(tmp_path):
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (tmp_path / "headless-node.pid").write_text(f"{sleeper.pid}\n")
        env = {**os.environ, "ANONMESH_STATE_DIR": str(tmp_path)}

        result = subprocess.run(
            [str(LAUNCHER), "stop"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        assert "already stopped" in result.stdout
        assert sleeper.poll() is None
        assert not (tmp_path / "headless-node.pid").exists()
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=3)


def test_start_failure_clears_pid_file(tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "[[ \"$1\" == *preflight.py ]] && exit 0\n"
        "exit 1\n"
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "ANONMESH_PYTHON": str(fake_python),
        "ANONMESH_STATE_DIR": str(tmp_path / "state"),
    }

    result = subprocess.run(
        [str(LAUNCHER), "start"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "failed to start" in result.stderr
    assert not (tmp_path / "state" / "headless-node.pid").exists()


def test_start_keeps_rpc_url_out_of_process_args(tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "[[ \"$1\" == *preflight.py ]] && exit 0\n"
        "while true; do sleep 1; done\n"
    )
    fake_python.chmod(0o755)
    state_dir = tmp_path / "state"
    secret_url = "https://rpc.example.test/private-token?api-key=secret"
    env = {
        **os.environ,
        "ANONMESH_PYTHON": str(fake_python),
        "ANONMESH_RPC_URL": secret_url,
        "ANONMESH_STATE_DIR": str(state_dir),
    }

    try:
        start = subprocess.run(
            [str(LAUNCHER), "start"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert start.returncode == 0
        pid = (state_dir / "headless-node.pid").read_text().strip()
        args = subprocess.run(
            ["ps", "-ww", "-p", pid, "-o", "args="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert secret_url not in args
        assert "api-key" not in args
    finally:
        subprocess.run([str(LAUNCHER), "stop"], check=False, env=env)
