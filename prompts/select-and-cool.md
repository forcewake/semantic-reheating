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
Interpret the advisory against [DecisionEnvelope](../contracts/v1/decision-envelope.schema.json) and, where needed, [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json). Return only these sections: Selected branch; Evidence delta; Rejected hypotheses; Next action; Remaining budget; Cooling status. If there is a tie or no greater evidence, return no selection and stop or escalate. Use only these JSON field names when structured output is required: `contract_version`, `run_id`, `decision_id`, `decision`, `evidence_event_ids`, `recovery_budget`, `cooling_conditions`, `rejected_hypothesis_refs`, `instruction_id`, `selected_prompt_asset_id`, `advisory_only`, `grants_authority`. Reject unlisted additions, an unknown major contract version, and fabricated IDs. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.
