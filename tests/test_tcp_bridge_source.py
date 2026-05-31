"""Regression tests for the generated TCP bridge client program."""
from pathlib import Path


TCP_BRIDGE = Path(__file__).parents[1] / "tests" / "test_tcp_bridge.py"


def test_generated_client_embeds_paths_as_python_literals():
    source = TCP_BRIDGE.read_text()

    assert "sys.path.insert(0, {os.path.realpath(PROJECT_ROOT)!r})" in source
    assert "r = RNS.Reticulum({RELAY_CONFIG!r})" in source
    assert 'sys.path.insert(0, "{os.path.realpath(PROJECT_ROOT)}")' not in source
    assert 'r = RNS.Reticulum("{RELAY_CONFIG}")' not in source
