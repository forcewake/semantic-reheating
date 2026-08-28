"""Task13 corpus assertions through the Task14 trusted replay path."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CORPUS = ROOT / "benchmark" / "corpus"
MANIFEST = ROOT / "benchmark" / "scenarios" / "manifest.json"

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


def test_replay_matches_every_ordered_manifest_decision_and_evidence_set() -> None:
    """Replay exposes per-scenario diagnostics rather than one artifact byte cmp."""
    from benchmark.replay import replay_result

    result = replay_result(CORPUS, MANIFEST)
    traces = result["traces"]
    actual_ids = tuple(trace["scenario_id"] for trace in traces)
    assert actual_ids == EXPECTED_SCENARIO_IDS
    assert len(traces) == 29

    mismatches: list[str] = []
    for trace in traces:
        scenario_id = trace["scenario_id"]
        actual_decision = trace["actual_decision"]
        expected_decision = trace["expected_decision"]
        actual_evidence = tuple(trace["actual_evidence_event_ids"])
        expected_evidence = tuple(trace["expected_evidence_event_ids"])
        if actual_decision != expected_decision or actual_evidence != expected_evidence:
            mismatches.append(
                f"{scenario_id}: decision expected={expected_decision!r} actual={actual_decision!r}; "
                f"evidence expected={expected_evidence!r} actual={actual_evidence!r}"
            )
    assert not mismatches, "\n".join(mismatches)
