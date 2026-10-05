"""Use an optional local JS runtime for browser-free module contract tests."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_navigation_contract():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is optional; no JavaScript runtime is installed")
    result = subprocess.run([executable, str(Path(__file__).with_name("frontend_contract.mjs"))],
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
