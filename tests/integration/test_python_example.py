"""End-to-end contract for the generic Python host example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = PROJECT_ROOT / "examples" / "python-generic-agent" / "main.py"


@pytest.mark.parametrize(
    ("scenario", "expected_decision", "expected_host_action"),
    (
        ("productive", "continue", "continue"),
        ("exact_repetition", "nudge", "nudge"),
        ("bounded_recovery", "reheat", "reheat"),
        ("cooling", "reheat", "cool"),
        ("unsafe_write", "stop", "stop"),
    ),
)
def test_generic_python_host_returns_one_named_result_per_scenario(
    scenario: str, expected_decision: str, expected_host_action: str
) -> None:
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE), "--scenario", scenario],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)

    assert result["result"] == scenario
    assert result["advisory_decision"] == expected_decision
    assert result["host_switch_decisions"] == [expected_decision]
    assert result["host_action"] == expected_host_action
    assert result["outcome_recorded"] is True
    assert result["controller_tool_invocations"] == 0
    assert all(call["actor"] == "host" for call in result["tool_invocations"])


def test_unsafe_write_records_absent_confirmation_and_stops() -> None:
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE), "--scenario", "unsafe_write"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)

    assert result["host_confirmation"] == "absent"
    assert result["host_action"] == "stop"
    assert result["evidence_final_status"] == "blocked"
    assert result["tool_invocations"] == []
