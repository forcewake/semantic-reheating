# Verify or stop

## Purpose
Report deterministic host verification results and close the recovery path when acceptance is not established.

## Runtime form
Use this prompt only to compare an expected state with an observed state after an authorized host action. You must report the result and never create a retry.

## Operator form
Provide decision IDs, evidence IDs, expected and observed fingerprints or digests, consumed counters, and host policy. The operator must block absent authority before verification is requested.

## Trigger
Use only when, after an authorized host action, its outcome is reported, evidence IDs are available, and a deterministic acceptance check can compare expected and observed state.

## Non-trigger
Do not use for normal progress, before action, with no expected state, for a proposed action, a non-deterministic check, or a request for a blind retry.

## Budget
Record limits and remaining capacity for turns, tool calls, tokens, elapsed time in seconds, and cost, both per-intervention and whole-run. Retries, handoffs, callbacks, and re-entry count against the budget. State maximum recovery episodes and maximum re-entry depth from host policy; there is no fixed universal iteration count. Stop at the first hard-limit breach.

## Tool restrictions
Use read-only or sandbox inspection by default. No writes or external side effects are allowed. Never repeat unknown, unconfirmed, or non-idempotent writes; do not use credentials. A named tool allowlist comes from host policy only. Output is advisory.

## Evidence
Cite event IDs and evidence IDs with fingerprints or digests. Separate observed facts, unknowns, and assumptions. Include no hidden reasoning, no raw transcript, and no unsupported invention.

## Cooling
Close after deterministic expected-vs-observed comparison and an outcome/evidence record. There is no blind retry/research. New work needs a new host-authorized independent episode and a new budget; the host retains authority for every action.

## Stop conditions
For unsafe input, missing authority, non-idempotent uncertainty, no information gain, ambiguous acceptance, or a hard budget condition: stop, escalate, or block as appropriate.

## Output contract
The operator rendering is not JSON; prose fields are not inserted into closed records. Unlisted additions are rejected. Replace example values with trace-derived real values while preserving the exact closed shape; never fabricate IDs. Reject an unknown major contract version. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Operator Markdown rendering (not JSON)
Allowed headings, in order: Deterministic acceptance result; Expected state fingerprints; Observed state fingerprints; Outcome/stop code; Decision IDs; Evidence IDs; Blind retry prohibition.

### Structured record: RecoveryOutcome
Emit one separate [RecoveryOutcome](../contracts/v1/recovery-outcome.schema.json) record; do not merge outcome facts with evidence.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-verify",
  "outcome_id": "outcome-verify-01",
  "instruction_id": "instruction-verify-01",
  "host_result": {"status": "completed", "summary": "Synthetic host action completed."},
  "consumed_counters": {"turns": 1, "tool_calls": 1, "tokens": 120, "elapsed_seconds": 45, "cost": 1},
  "evidence_gained": ["evidence-verify-01"],
  "acceptance_delta": {"status": "improved", "summary": "Synthetic expected state observed."},
  "state_delta": {"observed": true, "summary": "Synthetic state fingerprint changed as expected."},
  "error_class": null,
  "host_denial": {"denied": false, "reason_code": null},
  "human_escalation": false
}
```

### Structured record: EvidenceRecord
Emit one separate [EvidenceRecord](../contracts/v1/evidence-record.schema.json) record; do not merge evidence facts with the outcome.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-verify",
  "evidence_id": "evidence-record-verify-01",
  "trigger": {"finding_ids": ["finding-verify-01"], "reason_code": "signals_agree"},
  "chosen_policy": "policy-standard",
  "actual_counters": {"turns": 1, "tool_calls": 1, "tokens": 120, "elapsed_seconds": 45, "cost": 1},
  "new_evidence_refs": ["evidence-verify-01"],
  "acceptance_delta": {"status": "improved", "summary": "Synthetic expected state observed."},
  "repeated_side_effects_avoided": true,
  "final_status": "recovered"
}
```
