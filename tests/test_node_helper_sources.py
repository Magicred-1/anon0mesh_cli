"""Source regressions for manual Node helper trust boundaries."""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_init_comp_def_sanitizes_and_bounds_simulation_logs():
    source = (PROJECT_ROOT / "scripts" / "init_comp_def_once.mjs").read_text()

    assert "terminalSafeText(line)" in source
    assert "logs.slice(0, MAX_RENDERED_LOG_LINES)" in source
    assert "more lines omitted" in source
