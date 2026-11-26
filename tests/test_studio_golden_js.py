"""Run the editor's pure-JS golden-state tests (ultraai/studio/web/golden.test.mjs)
with node --test; skipped when node is not installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "ultraai" / "studio" / "web"


def test_golden_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    r = subprocess.run([node, "--test", str(WEB / "golden.test.mjs")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
