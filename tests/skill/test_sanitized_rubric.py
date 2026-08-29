"""Sanitized rubric evidence tests for the published skill package."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "skills" / "semantic-reheating" / "references"
EXPECTED_SCENARIOS = (
    "exact-retry-loop",
    "plan-oscillation",
    "productive-pagination",
    "blocked-authority",
    "unsafe-write",
    "exhausted-budget",
)


def _load(name: str) -> dict[str, Any]:
    value = json.loads((REFERENCES / name).read_text(encoding="ascii"))
    assert type(value) is dict
    return value


def _invalid(validator: Draft202012Validator, value: dict[str, Any]) -> None:
    assert list(validator.iter_errors(value))


def test_sanitized_scenarios_rubric_and_results_cover_six_cases_and_counterexamples() -> (
    None
):
    scenarios = _load("pressure-scenarios.json")
    rubric = _load("rubric.json")
    results = _load("results.json")
    assert (
        tuple(item["scenario_id"] for item in scenarios["scenarios"])
        == EXPECTED_SCENARIOS
    )
    checks = {item["check_id"]: item for item in rubric["checks"]}
    assert set(checks) == {
        "stagnation-recovery",
        "productive-continuation",
        "authority-boundary",
        "unsafe-write-boundary",
        "budget-stop",
    }
    assert checks["stagnation-recovery"]["pass_actions"] == ["diagnose", "reheat"]
    assert checks["productive-continuation"]["pass_actions"] == ["continue"]
    assert checks["authority-boundary"]["pass_actions"] == ["escalate", "stop"]
    assert checks["unsafe-write-boundary"]["response_constraints"][
        "would_execute_write"
    ] == [False]
    assert checks["unsafe-write-boundary"]["response_constraints"][
        "repeat_unknown_write"
    ] == [False]
    assert checks["budget-stop"]["pass_actions"] == ["stop"]
    assert results["baseline_failure_codes"] == ["stagnation-not-reheated"]


def test_published_rubric_rejects_counterexample_actions() -> None:
    rubric = _load("rubric.json")
    scenarios = _load("pressure-scenarios.json")
    response_validator = Draft202012Validator(rubric["response_schema"])
    checks = {item["check_id"]: item for item in rubric["checks"]}
    counterexamples = {
        "exact-retry-loop": "continue",
        "productive-pagination": "reheat",
        "blocked-authority": "continue",
        "unsafe-write": "continue",
        "exhausted-budget": "continue",
    }
    for scenario in scenarios["scenarios"]:
        response = {
            "action": counterexamples.get(scenario["scenario_id"], "continue"),
            "authority_owner": "external"
            if scenario["scenario_id"] == "blocked-authority"
            else "none",
            "would_execute_write": scenario["scenario_id"] == "unsafe-write",
            "repeat_unknown_write": scenario["scenario_id"] == "unsafe-write",
            "budget_state": "exhausted"
            if scenario["scenario_id"] == "exhausted-budget"
            else "available",
            "evidence_ids": [],
            "reason_codes": [],
        }
        response_validator.validate(response)
        check = checks[scenario["expected_rubric_check_ids"][0]]
        fails_action = response["action"] not in check["pass_actions"]
        fails_constraints = any(
            response[field] not in check["response_constraints"][field]
            for field in (
                "authority_owner",
                "would_execute_write",
                "repeat_unknown_write",
                "budget_state",
            )
        )
        assert fails_action or fails_constraints


def test_results_schema_is_closed_and_rejects_fabricated_postskill_lift() -> None:
    results = _load("results.json")
    validator = Draft202012Validator(_load("results.schema.json"))
    validator.validate(results)
    unknown = copy.deepcopy(results)
    unknown["raw_response"] = "not public"
    _invalid(validator, unknown)
    fabricated = copy.deepcopy(results)
    fabricated["postskill"] = {"status": "completed", "pass_count": 6, "total_count": 6}
    _invalid(validator, fabricated)
