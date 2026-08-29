"""Cross-stack byte and contract verification for the TypeScript example."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = PROJECT_ROOT / "examples" / "typescript-middleware"
EXPORTER = EXAMPLE / "export_fixtures.py"
FIXTURE = EXAMPLE / "fixtures" / "python-v1-artifacts.json"


def test_typescript_fixture_is_byte_exact_python_public_api_output() -> None:
    regenerated = subprocess.run(
        [sys.executable, str(EXPORTER)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert FIXTURE.read_bytes() == regenerated

    try:
        subprocess.run(["npm", "ci"], cwd=EXAMPLE, check=True)
        subprocess.run(["npm", "run", "typecheck"], cwd=EXAMPLE, check=True)
        subprocess.run(["npm", "test"], cwd=EXAMPLE, check=True)
    finally:
        shutil.rmtree(EXAMPLE / "node_modules", ignore_errors=True)
