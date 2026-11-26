"""Runs the editor keymap unit tests (ultraai/studio/web/keys.test.mjs) with `node --test`.

Skipped gracefully when no node binary is available; the tests themselves use synthetic
data only (an in-memory localStorage stub), no songs/, no network."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT / "ultraai" / "studio" / "web" / "keys.test.mjs"
NODE_FALLBACKS = [Path("/c/Program Files/nodejs/node.exe"), Path("C:/Program Files/nodejs/node.exe")]


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    for cand in NODE_FALLBACKS:
        if cand.exists():
            return str(cand)
    return None


def test_editor_keymap_unit_tests():
    node = _node()
    if not node:
        pytest.skip("node is not installed – skipping web/keys.test.mjs")
    assert TEST_FILE.exists(), f"missing {TEST_FILE}"
    r = subprocess.run([node, "--test", str(TEST_FILE)], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"node --test failed:\n{r.stdout}\n{r.stderr}"
    assert "fail 0" in r.stdout or "# fail 0" in r.stdout, r.stdout
