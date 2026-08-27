from __future__ import annotations

import os
from pathlib import Path
import subprocess


def test_console_help_is_available_offline() -> None:
    executable = (
        Path(".venv") / "Scripts" / "reheat.exe"
        if os.name == "nt"
        else Path(".venv") / "bin" / "reheat"
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
