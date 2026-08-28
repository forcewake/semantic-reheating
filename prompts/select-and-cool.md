# Select and cool

## Purpose
Select an evidence-supported branch for host consideration, then close exploration rather than extend it.

## Runtime form
Use this prompt only to compare bounded candidate branches after evidence changes. You must select only a branch with greater verified support or return no selection.

## Operator form
Provide candidate references, event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must treat a tie as a stop or escalation.

## Trigger
Use only when a bounded comparison has new verified evidence and one candidate may have greater support.

## Non-trigger
Do not use for normal progress, a tie, missing evidence, or a request to execute an unapproved action.

## Budget
Record limits and remaining capacity for turns, tool calls, tokens, elapsed time in seconds, and cost, both per-intervention and whole-run. Retries, handoffs, callbacks, and re-entry count against the budget. State maximum recovery episodes and maximum re-entry depth from host policy; there is no fixed universal iteration count. Stop at the first hard-limit breach.

## Tool restrictions
Use read-only or sandbox inspection by default. No writes or external side effects are allowed. Never repeat unknown, unconfirmed, or non-idempotent writes; do not use credentials. A named tool allowlist comes from host policy only. Output is advisory.

## Evidence
Cite event IDs and evidence IDs with fingerprints or digests. Separate observed facts, unknowns, and assumptions. Include no hidden reasoning, no raw transcript, and no unsupported invention.

## Cooling
End exploration when one branch has greater verified support and a concrete next action within remaining budget and authority. The host executes at most the selected authorized action and deterministic verification. Re-entry requires a new independent episode and a new budget.

## Stop conditions
For unsafe input, missing authority, non-idempotent uncertainty, no information gain, ambiguous acceptance, or a hard budget condition: stop, escalate, or block as appropriate.

## Output contract
The operator rendering is not JSON; prose fields are not inserted into closed records. Unlisted additions are rejected. Replace example values with trace-derived real values while preserving the exact closed shape; never fabricate IDs. Reject an unknown major contract version. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Operator Markdown rendering (not JSON)
Allowed headings, in order: Selected branch; Evidence delta; Rejected hypotheses; Next action; Remaining budget; Cooling status.

### Structured record: DecisionEnvelope
Emit one separate [DecisionEnvelope](../contracts/v1/decision-envelope.schema.json) record, never a union with an instruction. If evidence is tied, select no branch and stop or escalate.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-select",
  "decision_id": "decision-select-01",
  "decision": "reheat",
  "reason_codes": ["signals_agree", "repetition_detected", "no_progress_detected", "host_action_required"],
  "evidence_event_ids": ["event-select-01", "event-select-02"],
  "recovery_policy": "branch",
  "recovery_budget": {"turns": 2, "tool_calls": 2, "tokens": 240, "elapsed_seconds": 90, "cost": 2},
  "constraints": {"must_preserve_evidence": true, "no_non_idempotent_repeat": true, "require_host_confirmation": true, "allowed_effect_classes": ["read_only"]},
  "cooling_conditions": {"minimum_elapsed_seconds": 30, "require_new_evidence": true, "minimum_acceptance_gain": 0.1},
  "confidence": {"score": 0.85, "contributing_findings": [{"finding_id": "finding-select-01", "finding_class": "repetition", "matched": true, "score": 0.9, "weight": 0.5, "weighted_score": 0.45}, {"finding_id": "finding-select-02", "finding_class": "no_progress", "matched": true, "score": 0.8, "weight": 0.5, "weighted_score": 0.4}]},
  "requires_host_action": true,
  "human_summary": "Synthetic selected branch advisory.",
  "diagnosed_gaps": [],
  "rejected_hypothesis_refs": ["rejected-hypothesis-0123456789abcdef01234567"],
  "detector_notices": []
}
```

### Structured record: RecoveryInstruction
Emit one separate [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json) record for the host-considered next action; never merge it with the decision envelope.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-select",
  "instruction_id": "instruction-select-01",
  "selected_prompt_asset_id": "prompt-select-cool-v1",
  "variables": [{"name": "next_step", "value": "Perform one host-approved read-only comparison."}],
  "diagnosed_gaps": [{"kind": "stalled_progress", "description": "Synthetic branch evidence comparison."}],
  "recovery_budget": {"turns": 2, "tool_calls": 2, "tokens": 240, "elapsed_seconds": 90, "cost": 2},
  "allowed_tools": ["read_only", "validation"],
  "forbidden_actions": ["credential_access", "authority_grant", "network_publish", "non_idempotent_repeat"],
  "evidence_refs": ["evidence-select-01"],
  "rejected_hypothesis_refs": ["rejected-hypothesis-0123456789abcdef01234567"],
  "expected_output": {"kind": "plan", "required_sections": ["summary", "evidence", "next_steps", "stop_conditions"], "max_characters": 1200},
  "cooling_conditions": {"minimum_elapsed_seconds": 30, "require_new_evidence": true, "minimum_acceptance_gain": 0.1},
  "stop_conditions": ["budget_exhausted", "host_denial", "risk_detected", "cooling_required"],
  "advisory_only": true,
  "grants_authority": false
}
```
