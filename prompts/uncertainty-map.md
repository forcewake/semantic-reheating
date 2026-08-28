# Uncertainty map

## Purpose
Classify each recovery unknown once so the host can decide whether bounded verification is useful or escalation is required.

## Runtime form
Use this prompt only to map supplied unknowns to a closed disposition. You must retain evidence references, name an owner, and make no operational change.

## Operator form
Provide redacted context, event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must supply authority before any host action is considered.

## Trigger
Use only when verified evidence leaves one or more material recovery unknowns that block a bounded decision.

## Non-trigger
Do not use for normal progress, settled facts, or a request to fill gaps by invention.

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
Produce a [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json)-compatible advisory. Return only these sections: Unknown; Disposition; Evidence refs; Test; Owner; Assumption boundary. Map every unknown to exactly one of `verify`, `assume`, `escalate`, or `block`. An assumption is policy-authorized and bounded, or it must be escalated. Use only these JSON field names when structured output is required: `contract_version`, `run_id`, `instruction_id`, `selected_prompt_asset_id`, `variables`, `diagnosed_gaps`, `recovery_budget`, `evidence_refs`, `allowed_tools`, `forbidden_actions`, `stop_conditions`, `advisory_only`, `grants_authority`. Reject unlisted additions, an unknown major contract version, and fabricated IDs. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.
