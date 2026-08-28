# Verify or stop

## Purpose
Report deterministic host verification results and close the recovery path when acceptance is not established.

## Runtime form
Use this prompt only to compare an expected state with an observed state after an authorized host action. You must report the result and never create a retry.

## Operator form
Provide decision IDs, evidence IDs, expected and observed fingerprints or digests, consumed counters, and host policy. The operator must block absent authority before verification is requested.

## Trigger
Use only when the host reports one authorized action, evidence IDs are available, and a deterministic acceptance check can compare expected and observed state.

## Non-trigger
Do not use for normal progress, a proposed action, a non-deterministic check, or a request for a blind retry.

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
Report against [RecoveryOutcome](../contracts/v1/recovery-outcome.schema.json) and [EvidenceRecord](../contracts/v1/evidence-record.schema.json). Return only these sections: Deterministic acceptance result; Expected state fingerprints; Observed state fingerprints; Outcome/stop code; Decision IDs; Evidence IDs; Blind retry prohibition. A failure does not trigger a blind retry. Use only these JSON field names when structured output is required: `contract_version`, `run_id`, `outcome_id`, `instruction_id`, `host_result`, `consumed_counters`, `evidence_gained`, `acceptance_delta`, `state_delta`, `error_class`, `host_denial`, `human_escalation`, `evidence_id`, `final_status`. Reject unlisted additions, an unknown major contract version, and fabricated IDs. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.
