from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _documents() -> tuple[dict[str, Any], dict[str, Any]]:
    campaign = json.loads(
        (PROJECT_ROOT / "benchmark/live/campaign.example.json").read_text(
            encoding="utf-8"
        )
    )
    stacks = json.loads(
        (PROJECT_ROOT / "benchmark/live/stacks.example.json").read_text(
            encoding="utf-8"
        )
    )
    return campaign, stacks


def _usage(*, tokens: int, tool_calls: int = 0, turns: int = 0) -> dict[str, object]:
    return {
        "turns": turns,
        "tokens": tokens,
        "tool_calls": tool_calls,
        "elapsed_seconds": 1.0,
        "cost_usd": 0.0,
    }


def test_executor_counts_nested_work_in_one_envelope_and_stops_on_first_run_cap(
    tmp_path: Path,
) -> None:
    from benchmark.live.executor import execute_campaign

    campaign, stacks = _documents()
    calls: list[dict[str, str]] = []
    persisted: list[dict[str, object]] = []

    def runner(command: tuple[str, ...], env: Mapping[str, str]) -> dict[str, object]:
        assert command
        calls.append(dict(env))
        return {
            "events": [
                {"usage": _usage(tokens=25_000, turns=15), "outcome": "accepted"},
                {"usage": _usage(tokens=25_000, turns=15), "outcome": "accepted"},
                {"usage": _usage(tokens=1, turns=1), "outcome": "accepted"},
            ],
            "raw_output": "Bearer private-value /home/operator/raw-transcript",
        }

    result, manifest = execute_campaign(
        campaign,
        stacks,
        command_runner=runner,
        clock=lambda: 10.0,
        result_sink=persisted.append,
        sandbox_root=tmp_path,
        limit_matrix=1,
    )

    assert len(calls) == 1
    assert set(calls[0]) == {
        "TASK_SANDBOX",
        "FIXTURE_PATH",
        "CAMPAIGN_ARM",
        "REPLICATE",
        "SYNTHETIC_TOOL_ALLOWLIST",
    }
    assert result["results"][0]["usage"] == {
        "tokens": 50_000,
        "tool_calls": 0,
        "elapsed_seconds": 2.0,
        "cost_usd": 0.0,
    }
    assert result["results"][0]["intervention"] == "stop"
    assert result["results"][0]["failure_kind"] == "controller_failure"
    assert manifest["recorded_run_count"] == 1
    assert persisted == [result]
    assert "private-value" not in json.dumps(result)
    assert "raw-transcript" not in json.dumps(manifest)


def test_executor_counts_turns_across_retries_handoffs_and_reentry_before_stopping(
    tmp_path: Path,
) -> None:
    from benchmark.live.executor import execute_campaign

    campaign, stacks = _documents()
    runner_calls = 0

    def runner(command: tuple[str, ...], env: Mapping[str, str]) -> dict[str, object]:
        nonlocal runner_calls
        runner_calls += 1
        return {
            "events": [
                {"usage": _usage(tokens=1, turns=10), "outcome": "not_accepted"},
                {"usage": _usage(tokens=1, turns=10), "outcome": "not_accepted"},
                {"usage": _usage(tokens=1, turns=11), "outcome": "accepted"},
            ]
        }

    result, manifest = execute_campaign(
        campaign,
        stacks,
        command_runner=runner,
        clock=lambda: 1.0,
        result_sink=lambda document: None,
        sandbox_root=tmp_path,
        limit_matrix=1,
    )

    assert runner_calls == 1
    assert result["source_kind"] == "partial_campaign"
    assert result["results"][0]["intervention"] == "stop"
    assert result["results"][0]["failure_kind"] == "controller_failure"
    assert result["results"][0]["usage"] == {
        "tokens": 3,
        "tool_calls": 0,
        "elapsed_seconds": 3.0,
        "cost_usd": 0.0,
    }
    assert "turns" not in result["results"][0]["usage"]
    assert manifest["blockers"] == []
    assert manifest["status"] == "partial"


def test_executor_stops_scheduling_at_first_campaign_cap_and_never_exceeds_matrix(
    tmp_path: Path,
) -> None:
    from benchmark.live.executor import execute_campaign

    campaign, stacks = _documents()
    call_count = 0

    def runner(command: tuple[str, ...], env: Mapping[str, str]) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {"events": [{"usage": _usage(tokens=50_000), "outcome": "accepted"}]}

    result, manifest = execute_campaign(
        campaign,
        stacks,
        command_runner=runner,
        clock=lambda: 1.0,
        result_sink=lambda document: None,
        sandbox_root=tmp_path,
    )

    assert call_count == 40
    assert len(result["results"]) == 40
    assert len(result["results"]) <= 108
    assert manifest["status"] == "partial"
    assert "campaign_tokens_cap_reached" in manifest["blockers"]


@pytest.mark.parametrize(
    ("outcome", "failure_kind"),
    [
        ("provider_error", "provider_error"),
        ("safety_refusal", "safety_refusal"),
        ("infrastructure_failure", "infrastructure_failure"),
    ],
)
def test_executor_keeps_failure_classification_separate(
    tmp_path: Path, outcome: str, failure_kind: str
) -> None:
    from benchmark.live.executor import execute_campaign

    campaign, stacks = _documents()
    result, _ = execute_campaign(
        campaign,
        stacks,
        command_runner=lambda command, env: {
            "events": [{"usage": _usage(tokens=1), "outcome": outcome}]
        },
        clock=lambda: 1.0,
        result_sink=lambda document: None,
        sandbox_root=tmp_path,
        limit_matrix=1,
    )

    record = result["results"][0]
    assert record["status"] == "failed"
    assert record["failure_kind"] == failure_kind


def test_blocked_no_call_path_never_invokes_runner_and_records_gate_reasons(
    tmp_path: Path,
) -> None:
    from benchmark.live.executor import create_blocked_artifacts

    campaign, stacks = _documents()
    called = False

    def forbidden_runner(command: tuple[str, ...], env: Mapping[str, str]) -> object:
        nonlocal called
        called = True
        raise AssertionError("blocked campaign must not invoke any stack")

    result, manifest = create_blocked_artifacts(
        campaign,
        stacks,
        blockers=[
            "second_selected_executable_stack_absent",
            "paid_execution_not_authorized",
        ],
        command_runner=forbidden_runner,
        output_path=tmp_path / "campaign.json",
        manifest_path=tmp_path / "campaign-manifest.json",
    )

    assert not called
    assert result["source_kind"] == "blocked_campaign"
    assert result["results"] == []
    assert manifest["status"] == "blocked"
    assert manifest["blockers"] == [
        "paid_execution_not_authorized",
        "second_selected_executable_stack_absent",
    ]
    assert json.loads((tmp_path / "campaign.json").read_text()) == result
    assert json.loads((tmp_path / "campaign-manifest.json").read_text()) == manifest
