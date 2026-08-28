from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_reheating import cli

ROOT = Path(__file__).resolve().parents[2]


def _decision() -> dict[str, object]:
    return json.loads(
        (ROOT / "tests/fixtures/contracts/minimal-decision-envelope.json").read_text()
    )


def test_explain_renders_only_stable_public_text_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(_decision()), encoding="utf-8")

    assert cli.main(["explain", str(decision)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "decision: escalate\n"
        "confidence: 0.8\n"
        "requires_host_action: true\n"
        "reason_codes: host_action_required\n"
        "evidence_event_ids: event-001\n"
        'summary: "Redacted host action request."\n'
    )


def test_explain_escapes_control_characters_in_human_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _decision()
    data["human_summary"] = "safe\n\x1b[31mnot-a-line"
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(data), encoding="utf-8")

    assert cli.main(["explain", str(decision)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "decision: escalate",
        "confidence: 0.8",
        "requires_host_action: true",
        "reason_codes: host_action_required",
        "evidence_event_ids: event-001",
        'summary: "safe\\n\\u001b[31mnot-a-line"',
    ]
    assert "\x1b" not in captured.out


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 5), ("2.0", 5)],
)
def test_explain_preflights_version_before_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object, expected: int
) -> None:
    data = _decision()
    data["contract_version"] = value
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(data), encoding="utf-8")

    assert cli.main(["explain", str(decision)]) == expected
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
