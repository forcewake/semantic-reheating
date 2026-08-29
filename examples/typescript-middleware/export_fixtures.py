"""Emit the immutable TypeScript fixture using only public Python APIs."""

from __future__ import annotations

import hashlib
import sys
from typing import Any

from semantic_reheating import (
    DecisionEnvelope,
    RecoveryInstruction,
    RecoveryOutcome,
    RunPolicy,
    TraceEvent,
    record_outcome,
)
from semantic_reheating.canonical import canonicalize_json


def policy_source() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "policy_id": "policy-standard",
        "detectors": {
            "windows": {"repetition_events": 2, "no_progress_events": 2},
            "thresholds": {
                "repetition_score": 0.7,
                "no_progress_score": 0.7,
                "risk_score": 0.8,
                "budget_score": 0.9,
            },
            "weights": {
                "repetition": 0.4,
                "no_progress": 0.4,
                "risk": 0.1,
                "budget": 0.1,
            },
        },
        "agreeing_signals": {
            "required_classes": ["repetition", "no_progress"],
            "minimum_count": 2,
            "budget_can_substitute": False,
        },
        "recovery_ladder": {
            "nudge": {"permitted": True, "requires_host_action": False},
            "diagnose": {"permitted": True, "requires_host_action": False},
            "reheat": {"permitted": True, "requires_host_action": False},
            "restart": {"permitted": True, "requires_host_action": True},
            "escalate": {"permitted": True, "requires_host_action": True},
            "stop": {"permitted": True, "requires_host_action": False},
        },
        "budgets": {
            "per_intervention": {
                "turns": 1,
                "tool_calls": 1,
                "tokens": 100,
                "elapsed_seconds": 60,
                "cost": 1,
            },
            "whole_run": {
                "turns": 5,
                "tool_calls": 5,
                "tokens": 500,
                "elapsed_seconds": 300,
                "cost": 5,
            },
        },
        "max_recovery_episodes": 1,
        "max_reentry_depth": 1,
        "side_effect_rules": {
            "automatic_repeat_allowed_effect_classes": [
                "read_only",
                "idempotent_write",
            ],
            "automatic_unconfirmed_non_idempotent_repeat": False,
            "unknown_treated_as_repeatable": False,
        },
        "cooling_conditions": {
            "minimum_elapsed_seconds": 30,
            "require_new_evidence": True,
            "minimum_acceptance_gain": 0.1,
        },
    }


def counter_source() -> dict[str, int]:
    return {"turns": 1, "tool_calls": 0, "tokens": 0, "elapsed_seconds": 1, "cost": 0}


def decision_source() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "run_id": "run-typescript-v1",
        "decision_id": "decision-escalate-v1",
        "decision": "escalate",
        "reason_codes": ["risk_detected", "host_action_required"],
        "evidence_event_ids": ["event-1"],
        "recovery_policy": None,
        "recovery_budget": counter_source(),
        "constraints": {
            "must_preserve_evidence": True,
            "no_non_idempotent_repeat": True,
            "require_host_confirmation": True,
            "allowed_effect_classes": ["read_only"],
        },
        "cooling_conditions": {
            "minimum_elapsed_seconds": 30,
            "require_new_evidence": True,
            "minimum_acceptance_gain": 0.1,
        },
        "confidence": {
            "score": 1,
            "contributing_findings": [
                {
                    "finding_id": "finding-risk-v1",
                    "finding_class": "risk",
                    "matched": True,
                    "score": 1,
                    "weight": 0.1,
                    "weighted_score": 0.1,
                }
            ],
        },
        "requires_host_action": True,
        "human_summary": "Escalate to the host; no automatic action is granted.",
        "diagnosed_gaps": [
            {"kind": "risk_blocker", "description": "Host review required."}
        ],
        "rejected_hypothesis_refs": [],
        "detector_notices": [],
    }


def instruction_source() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "run_id": "run-typescript-v1",
        "instruction_id": "instruction-v1",
        "selected_prompt_asset_id": "prompt-diagnose-v1",
        "variables": [],
        "diagnosed_gaps": [
            {"kind": "risk_blocker", "description": "Host review required."}
        ],
        "recovery_budget": counter_source(),
        "allowed_tools": ["read_only", "analysis"],
        "forbidden_actions": ["authority_grant", "non_idempotent_repeat"],
        "evidence_refs": ["event-1"],
        "rejected_hypothesis_refs": [],
        "expected_output": {
            "kind": "handoff",
            "required_sections": ["summary", "evidence", "next_steps"],
            "max_characters": 400,
        },
        "cooling_conditions": {
            "minimum_elapsed_seconds": 30,
            "require_new_evidence": True,
            "minimum_acceptance_gain": 0.1,
        },
        "stop_conditions": ["risk_detected", "host_denial"],
        "advisory_only": True,
        "grants_authority": False,
    }


def outcome_source() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "run_id": "run-typescript-v1",
        "outcome_id": "outcome-v1",
        "instruction_id": "instruction-v1",
        "host_result": {"status": "escalated", "summary": "Host accepted escalation."},
        "consumed_counters": counter_source(),
        "evidence_gained": ["evidence-host-review"],
        "acceptance_delta": {"status": "unknown", "summary": "Awaiting host review."},
        "state_delta": {"observed": False, "summary": "No automatic state change."},
        "error_class": "risk_blocker",
        "host_denial": {"denied": False, "reason_code": None},
        "human_escalation": True,
    }


def bundle() -> dict[str, Any]:
    trace = TraceEvent.from_dict(
        {
            "contract_version": "1.0",
            "run_id": "run-typescript-v1",
            "event_id": "event-1",
            "sequence": 1,
            "kind": "state_observation",
            "actor": "host",
            "effect_class": "read_only",
            "payload": {"status": "observed"},
        }
    )
    policy = RunPolicy.from_dict(policy_source())
    decision = DecisionEnvelope.from_dict(decision_source())
    instruction = RecoveryInstruction.from_dict(instruction_source())
    outcome = RecoveryOutcome.from_dict(outcome_source())
    evidence = record_outcome(decision, outcome)
    canonical_value: dict[str, Any] = {
        "z": 0,
        "text": "e\u0301",
        "nested": {"b": True, "a": "Å"},
    }
    canonical_utf8 = canonicalize_json(canonical_value)
    artifacts = {
        "trace_event": trace.to_dict(),
        "run_policy": policy.to_dict(),
        "detector_finding": {
            "contract_version": "1.0",
            "run_id": "run-typescript-v1",
            "finding_id": "finding-risk-v1",
            "detector_name": "host-risk-detector",
            "detector_version": "1.0",
            "matched": True,
            "score": 1,
            "finding_class": "risk",
            "event_ids": ["event-1"],
            "reason_code": "risk_detected",
            "explanation": "A host-owned risk detector requested review.",
            "availability": {
                "status": "available",
                "notice": "Deterministic detector available.",
            },
        },
        "decision_envelope": decision.to_dict(),
        "recovery_instruction": instruction.to_dict(),
        "recovery_outcome": outcome.to_dict(),
        "evidence_record": evidence.to_dict(),
    }
    return {
        "fixture_version": "1.0",
        "artifacts": artifacts,
        "canonical_i_json": {
            "value": canonical_value,
            "utf8": canonical_utf8.decode("utf-8"),
            "sha256": hashlib.sha256(canonical_utf8).hexdigest(),
        },
    }


def main() -> None:
    sys.stdout.buffer.write(canonicalize_json(bundle()))


if __name__ == "__main__":
    main()
