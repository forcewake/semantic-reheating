# Detection notice

## Purpose
Turn verified recovery signals into a bounded advisory notice. It records why ordinary progress should pause without authorizing any action.

## Runtime form
Use this prompt only to summarize verified detector findings. You must produce an advisory notice, preserve uncertainty, and leave execution to the host.

## Operator form
Provide event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must reject missing authority and request escalation instead of action.

## Trigger
Use only when independent detector evidence shows repetition, no progress, risk, or a budget limit that requires an advisory decision.

## Non-trigger
Do not use for normal progress, a single uncorroborated signal, or a request to perform a change.

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
Interpret the input and advisory output against [DecisionEnvelope](../contracts/v1/decision-envelope.schema.json). Return only these sections: Detected signals; Independent no-progress evidence; Prohibited retry; Bounded next action; Host authority. Use only these JSON field names when a structured envelope is required: `contract_version`, `run_id`, `decision_id`, `decision`, `reason_codes`, `evidence_event_ids`, `recovery_budget`, `constraints`, `requires_host_action`, `human_summary`. Reject unlisted additions, an unknown major contract version, and fabricated IDs. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.
