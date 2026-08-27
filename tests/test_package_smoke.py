from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_console_help_is_available_offline() -> None:
    project_root = Path(__file__).resolve().parents[1]
    executable = (
        project_root / ".venv" / "Scripts" / "reheat.exe"
        if os.name == "nt"
        else project_root / ".venv" / "bin" / "reheat"
    )
    assert executable.is_file(), f"project-local console executable is missing: {executable}"

    result = subprocess.run(
        [str(executable), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: reheat" in result.stdout
