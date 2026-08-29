"""A stdlib-only host loop around the semantic reheating public API."""

from __future__ import annotations

import argparse
import json
from typing import Any

from semantic_reheating import (
    RecoveryOutcome,
    RunPolicy,
    TraceEvent,
    analyze,
    record_outcome,
)


class SyntheticTool:
    """An in-memory tool owned and invoked only by the host."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, str]] = []

    def run(self, action: str) -> dict[str, str]:
        self.invocations.append({"actor": "host", "action": action})
        return {
            "action": action,
            "result": "unchanged" if action == "lookup" else "new",
        }


class Host:
    """A small host-owned loop; analysis observes events but executes nothing."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.tool = SyntheticTool()
        self.trace: list[TraceEvent] = []
        self.host_switch_decisions: list[str] = []
        self.host_action = "continue"
        self.host_confirmation = "present"

    def _event(self, kind: str, payload: dict[str, str], **extra: Any) -> None:
        sequence = len(self.trace) + 1
        self.trace.append(
            TraceEvent.from_dict(
                {
                    "contract_version": "1.0",
                    "run_id": f"run-{self.scenario}",
                    "event_id": f"event-{sequence}",
                    "sequence": sequence,
                    "kind": kind,
                    "actor": "host",
                    "effect_class": "read_only",
                    "payload": payload,
                    **extra,
                }
            )
        )

    def run_tool(self, action: str) -> None:
        result = self.tool.run(action)
        self._event("tool_call", {"action": action})
        result_fields: dict[str, Any] = {"parent_event_id": f"event-{len(self.trace)}"}
        if action == "advance":
            result_fields["evidence_refs"] = ["evidence-progress"]
        self._event("tool_result", result, **result_fields)

    def add_stagnation(self) -> None:
        self.run_tool("lookup")
        self.run_tool("lookup")
        self._event(
            "acceptance_check",
            {"criterion": "target"},
            acceptance_delta="unchanged",
        )
        self._event(
            "acceptance_check",
            {"criterion": "target"},
            acceptance_delta="unchanged",
        )

    def add_unconfirmed_write_requests(self) -> None:
        self.host_confirmation = "absent"
        for _ in range(2):
            self._event(
                "tool_call",
                {"action": "transfer"},
                effect_class="non_idempotent_write",
            )
            self._event(
                "tool_result",
                {"status": "confirmation_absent"},
                parent_event_id=f"event-{len(self.trace)}",
                effect_class="non_idempotent_write",
            )

    def apply_decision(self, decision: Any) -> None:
        """The host's explicit switch is the only action authority."""
        advisory = decision.decision.value
        self.host_switch_decisions.append(advisory)
        if advisory == "reheat" and self.scenario == "cooling":
            self.host_action = "cool"
        elif advisory == "reheat":
            self.host_action = "reheat"
            self.run_tool("research")
        elif advisory == "stop":
            self.host_action = "stop"
        elif advisory == "nudge":
            self.host_action = "nudge"
        else:
            self.host_action = "continue"

    def outcome(self, decision: Any) -> RecoveryOutcome:
        denied = self.scenario == "unsafe_write"
        return RecoveryOutcome.from_dict(
            {
                "contract_version": "1.0",
                "run_id": decision.run_id,
                "outcome_id": f"outcome-{self.scenario}",
                "instruction_id": f"instruction-{decision.decision_id}",
                "host_result": {
                    "status": "denied" if denied else "completed",
                    "summary": (
                        "Host confirmation was absent; no write ran."
                        if denied
                        else f"Host applied {self.host_action}."
                    ),
                },
                "consumed_counters": {
                    "turns": 1,
                    "tool_calls": len(self.tool.invocations),
                    "tokens": 0,
                    "elapsed_seconds": 1,
                    "cost": 0,
                },
                "evidence_gained": [f"evidence-{self.scenario}"],
                "acceptance_delta": {
                    "status": "unknown" if denied else "improved",
                    "summary": "Host-reported synthetic outcome.",
                },
                "state_delta": {
                    "observed": not denied,
                    "summary": "In-memory state only.",
                },
                "error_class": "host_denial" if denied else None,
                "host_denial": {
                    "denied": denied,
                    "reason_code": "not_confirmed" if denied else None,
                },
                "human_escalation": False,
            }
        )


def policy_for(scenario: str) -> RunPolicy:
    source: dict[str, Any] = {
        "contract_version": "1.0",
        "policy_id": "policy-standard",
        "detectors": {
            "windows": {"repetition_events": 8, "no_progress_events": 8},
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
            "semantic_detector": {
                "enabled": False,
                "metered": False,
                "weight": 0,
                "required": False,
                "can_relax_hard_stops": False,
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
    if scenario in {"bounded_recovery", "cooling"}:
        source["recovery_ladder"]["nudge"]["permitted"] = False
        source["recovery_ladder"]["diagnose"]["permitted"] = False
    return RunPolicy.from_dict(source)


def run(scenario: str) -> dict[str, Any]:
    host = Host(scenario)
    if scenario == "productive":
        host.run_tool("lookup")
        host.run_tool("lookup")
        host._event(
            "acceptance_check",
            {"criterion": "target"},
            acceptance_delta="unchanged",
        )
        host.run_tool("advance")
        host._event(
            "acceptance_check",
            {"criterion": "target"},
            acceptance_delta="unchanged",
        )
    elif scenario in {"exact_repetition", "bounded_recovery", "cooling"}:
        host.add_stagnation()
    else:
        host.add_unconfirmed_write_requests()

    decision = analyze(host.trace, policy_for(scenario))
    host.apply_decision(decision)
    evidence = record_outcome(decision, host.outcome(decision))
    return {
        "result": scenario,
        "advisory_decision": decision.decision.value,
        "host_switch_decisions": host.host_switch_decisions,
        "host_action": host.host_action,
        "host_confirmation": host.host_confirmation,
        "outcome_recorded": True,
        "evidence_final_status": evidence.final_status,
        "tool_invocations": host.tool.invocations,
        "controller_tool_invocations": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=(
            "productive",
            "exact_repetition",
            "bounded_recovery",
            "cooling",
            "unsafe_write",
        ),
        default="productive",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.scenario), sort_keys=True))


if __name__ == "__main__":
    main()
