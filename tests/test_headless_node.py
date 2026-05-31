"""Regression tests for the headless-node launcher."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "scripts" / "headless-node.sh"


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
