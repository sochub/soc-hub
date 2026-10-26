"""The slack-socket process imports task modules directly (no app.worker first); that order must work."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", [f"app.tasks.{m.stem}" for m in sorted(
    (Path(__file__).parents[1] / "app" / "tasks").glob("*.py")) if m.stem != "__init__"])
def test_task_module_imports_cold(module):
    r = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
