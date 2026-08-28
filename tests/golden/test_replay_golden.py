"""Independent manifest oracle for the trusted deterministic replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "benchmark" / "corpus"
MANIFEST = ROOT / "benchmark" / "scenarios" / "manifest.json"
MANIFEST_SCHEMA = ROOT / "benchmark" / "schemas" / "v1" / "corpus-manifest.schema.json"

# A curated, literal oracle: changing corpus order must be reviewed deliberately.
EXPECTED_SCENARIO_IDS = (
    "exact-repetition-stall",
    "cycle-two-step",
    "cycle-three-step",
    "cycle-four-step",
    "cycle-five-step",
    "unchanged-state",
    "repeated-error",
    "budget-burn-turns",
    "budget-burn-tool-calls",
    "budget-burn-tokens",
    "budget-burn-elapsed-seconds",
    "budget-burn-cost",
    "blocked-authority",
    "context-restart",
    "unsafe-write-repetition",
    "batching-a",
    "batching-b",
    "changed-hypothesis-a",
    "changed-hypothesis-b",
    "eventual-consistency-a",
    "eventual-consistency-b",
    "handoff-a",
    "handoff-b",
    "pagination-a",
    "pagination-b",
    "state-changing-poll-a",
    "state-changing-poll-b",
    "verification-rerun-a",
    "verification-rerun-b",
)


def _manifest() -> dict[str, Any]:
    """Read committed bytes directly; replay output is never an expected-value source."""
    from semantic_reheating.validation import load_public_json

    payload = load_public_json(MANIFEST.read_bytes())
    schema = json.loads(MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    assert type(payload) is dict
    return payload


def test_replay_matches_direct_ordered_manifest_oracle() -> None:
    """Each replay actual is compared to its corresponding committed manifest entry."""
    from benchmark.replay import replay_result

    manifest = _manifest()
    entries = manifest["entries"]
    assert type(entries) is list
    manifest_ids = tuple(entry["scenario_id"] for entry in entries)
    manifest_paths = tuple(entry["trace_path"] for entry in entries)
    assert len(entries) == 29
    assert len(set(manifest_ids)) == 29
    assert len(set(manifest_paths)) == 29
    assert manifest_ids == EXPECTED_SCENARIO_IDS

    result = replay_result(CORPUS, MANIFEST)
    traces = result["traces"]
    assert type(traces) is list
    result_ids = tuple(trace["scenario_id"] for trace in traces)
    assert len(traces) == 29
    assert len(set(result_ids)) == 29
    assert result_ids == EXPECTED_SCENARIO_IDS

    mismatches: list[str] = []
    for entry, trace in zip(entries, traces, strict=True):
        scenario_id = entry["scenario_id"]
        assert trace["scenario_id"] == scenario_id, scenario_id
        # The copied expected fields remain an embedding-integrity assertion only.
        assert trace["expected_decision"] == entry["expected_decision"], scenario_id
        assert (
            trace["expected_evidence_event_ids"] == entry["expected_evidence_event_ids"]
        ), scenario_id
        if trace["actual_decision"] != entry["expected_decision"] or (
            trace["actual_evidence_event_ids"] != entry["expected_evidence_event_ids"]
        ):
            mismatches.append(
                f"{scenario_id}: decision expected={entry['expected_decision']!r} "
                f"actual={trace['actual_decision']!r}; evidence "
                f"expected={entry['expected_evidence_event_ids']!r} "
                f"actual={trace['actual_evidence_event_ids']!r}"
            )
    assert not mismatches, "\n".join(mismatches)
