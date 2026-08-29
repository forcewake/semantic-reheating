"""Public package contract for the semantic reheating skill."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "semantic-reheating"
REFERENCES = SKILL / "references"
PUBLIC_DOCUMENTS = (
    "pressure-scenarios.json",
    "rubric.json",
    "stack-receipt.json",
    "baseline-summary.json",
    "results.json",
)


def _load(name: str) -> dict[str, Any]:
    value = json.loads((REFERENCES / name).read_text(encoding="ascii"))
    assert type(value) is dict
    return value


def _sha(name: str) -> str:
    return hashlib.sha256((REFERENCES / name).read_bytes()).hexdigest()


def _invalid(validator: Draft202012Validator, value: dict[str, Any]) -> None:
    assert list(validator.iter_errors(value))


def test_official_skill_frontmatter_is_trigger_only_and_references_are_shallow() -> (
    None
):
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\n(?P<frontmatter>.*?)\n---\n", text, re.DOTALL)
    assert match
    frontmatter = match.group("frontmatter").splitlines()
    assert frontmatter[0] == "name: semantic-reheating"
    description = next(line for line in frontmatter if line.startswith("description: "))
    assert description.startswith("description: Use when")
    assert all(
        token not in description.lower()
        for token in ("must", "then", "diagnose", "reheat", "stop")
    )
    assert SKILL.name == "semantic-reheating"
    references = re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
    assert references
    assert all(
        reference.startswith("references/")
        and "/" not in reference.removeprefix("references/")
        and ".." not in reference
        for reference in references
    )


def test_every_public_document_has_a_closed_versioned_adjacent_schema() -> None:
    for name in PUBLIC_DOCUMENTS:
        schema_name = name.removesuffix(".json") + ".schema.json"
        schema = _load(schema_name)
        document = _load(name)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(document)
        assert schema["additionalProperties"] is False
        assert document["contract_version"] == "1.0"
        unknown = copy.deepcopy(document)
        unknown["private_path"] = "/not-public"
        _invalid(validator, unknown)
        unknown_major = copy.deepcopy(document)
        unknown_major["contract_version"] = "2.0"
        _invalid(validator, unknown_major)


def test_public_baseline_projection_and_stack_receipt_bind_committed_bytes() -> None:
    stack = _load("stack-receipt.json")
    baseline = _load("baseline-summary.json")
    assert stack["mode"] == baseline["mode"] == "baseline"
    for field, source in (
        ("scenario_schema_sha256", "pressure-scenarios.schema.json"),
        ("rubric_schema_sha256", "rubric.schema.json"),
        ("baseline_summary_schema_sha256", "baseline-summary.schema.json"),
        ("stack_receipt_schema_sha256", "stack-receipt.schema.json"),
    ):
        assert stack[field] == baseline[field] == _sha(source)
    assert baseline["scenario_set_sha256"] == _sha("pressure-scenarios.json")
    assert baseline["rubric_sha256"] == _sha("rubric.json")
    assert stack["command_sha256"] == baseline["command_sha256"]


def test_results_bind_baseline_projection_stack_and_rubric_without_raw_content() -> (
    None
):
    results = _load("results.json")
    stack = _load("stack-receipt.json")
    assert results["baseline_summary_sha256"] == _sha("baseline-summary.json")
    assert results["stack_receipt_sha256"] == _sha("stack-receipt.json")
    assert results["rubric_sha256"] == _sha("rubric.json")
    assert results["scenario_set_sha256"] == _sha("pressure-scenarios.json")
    assert results["baseline_pass_count"] == 5
    assert results["baseline_total_count"] == 6
    assert results["baseline_failure_codes"] == ["stagnation-not-reheated"]
    postskill = results["postskill"]
    assert postskill["status"] == "completed"
    assert (
        postskill["skill_sha256"]
        == hashlib.sha256((SKILL / "SKILL.md").read_bytes()).hexdigest()
    )
    assert (
        postskill["stack_config_sha256"]
        == "a2dc77209049144b73f3595c1d0f91f62c478a7d64e65f74ff65cbbfeed49933"
    )
    assert postskill["command_sha256"] == stack["command_sha256"]
    assert [entry["scenario_id"] for entry in postskill["outcomes"]] == [
        "exact-retry-loop",
        "plan-oscillation",
        "productive-pagination",
        "blocked-authority",
        "unsafe-write",
        "exhausted-budget",
    ]
    assert [entry["outcome_code"] for entry in postskill["outcomes"]] == ["pass"] * 6
    assert postskill["pass_count"] == postskill["total_count"] == 6
    encoded = json.dumps(results, sort_keys=True).lower()
    for forbidden in (
        "/home/",
        "xdg_",
        "token",
        "secret",
        "transcript",
        "raw_response",
    ):
        assert forbidden not in encoded
