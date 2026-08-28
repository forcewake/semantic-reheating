from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_reheating import cli

ROOT = Path(__file__).resolve().parents[2]


def _policy() -> dict[str, object]:
    return json.loads(
        (ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_text()
    )


def _missing_authority_event() -> dict[str, object]:
    event = json.loads(
        (ROOT / "tests/fixtures/contracts/minimal-trace-event.json").read_text()
    )
    event.update(
        {"kind": "error", "payload": {"diagnostic_cause": "missing_authority"}}
    )
    return event


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps(_missing_authority_event()) + "\n", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    return trace, policy


def test_analyze_json_is_canonical_and_missing_authority_escalates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace, policy = _inputs(tmp_path)
    args = ["analyze", str(trace), "--policy", str(policy), "--format", "json"]

    assert cli.main(args) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert first.out.endswith("\n")
    decision = json.loads(first.out)
    assert decision["decision"] == "escalate"
    assert decision["requires_host_action"] is True
    assert first.out == cli._canonical_line(decision)

    assert cli.main(args) == 0
    assert capsys.readouterr().out == first.out


def test_analyze_text_is_stable_redacted_and_escalates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace, policy = _inputs(tmp_path)

    assert (
        cli.main(["analyze", str(trace), "--policy", str(policy), "--format", "text"])
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[:3] == [
        "decision: escalate",
        "confidence: 0.0",
        "requires_host_action: true",
    ]
    assert "diagnostic_cause" not in captured.out
    assert "missing_authority" not in captured.out


def test_analyze_maps_required_detector_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace, policy = _inputs(tmp_path)
    policy_data = _policy()
    semantic = policy_data["detectors"]["semantic_detector"]  # type: ignore[index]
    semantic.update({"enabled": True, "required": True})  # type: ignore[union-attr]
    policy.write_text(json.dumps(policy_data), encoding="utf-8")

    assert cli.main(["analyze", str(trace), "--policy", str(policy)]) == 7
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: required_detector_unavailable\n"
