# Bounded reheating

## Purpose
Generate a small, falsifiable advisory comparison when evidence-supported recovery branches remain unresolved. Semantic reheating is not decoding temperature and is not side-effect exploration.

## Runtime form
Use this prompt only to compare supplied evidence under a bounded hypothesis contract. You must produce exactly three alternatives and no action beyond the host allowlist.

## Operator form
Provide redacted context, event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must reject any missing authority and submit an advisory only.

## Trigger
Use only when verified evidence supports a bounded recovery comparison and ordinary diagnosis cannot distinguish competing explanations.

## Non-trigger
Do not use for normal progress, a settled explanation, open-ended research, or any request to write or explore side effects.

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
Produce a [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json)-compatible advisory. Semantic reheating is not decoding temperature or side-effect exploration. Return only these sections: Hypotheses; Evidence comparison; Read-only tests; Stop decision. The output must contain exactly three mutually exclusive and falsifiable hypotheses. Each has exactly one read-only discriminating test, selected only from the host allowlist; no research, write, or tool action beyond that allowlist. Use only these JSON field names when structured output is required: `contract_version`, `run_id`, `instruction_id`, `selected_prompt_asset_id`, `expected_output`, `hypothesis_contract`, `allowed_test_effect_classes`, `evidence_refs`, `recovery_budget`, `stop_conditions`, `advisory_only`, `grants_authority`. Reject unlisted additions, an unknown major contract version, and fabricated IDs. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Hypothesis 1
**Claim**: State one candidate explanation. It must be mutually exclusive with Hypotheses 2 and 3 and falsifiable.

**Falsifier**: State the observable result that disproves this claim.

**Supporting evidence**: Cite only supplied evidence IDs and fingerprints.

**Refuting evidence**: Cite only supplied evidence IDs and fingerprints.

**Discriminating read-only test**: Name one test, allowed by host policy, that distinguishes this claim from both alternatives. There is exactly one test.

### Hypothesis 2
**Claim**: State one candidate explanation. It must be mutually exclusive with Hypotheses 1 and 3 and falsifiable.

**Falsifier**: State the observable result that disproves this claim.

**Supporting evidence**: Cite only supplied evidence IDs and fingerprints.

**Refuting evidence**: Cite only supplied evidence IDs and fingerprints.

**Discriminating read-only test**: Name one test, allowed by host policy, that distinguishes this claim from both alternatives. There is exactly one test.

### Hypothesis 3
**Claim**: State one candidate explanation. It must be mutually exclusive with Hypotheses 1 and 2 and falsifiable.

**Falsifier**: State the observable result that disproves this claim.

**Supporting evidence**: Cite only supplied evidence IDs and fingerprints.

**Refuting evidence**: Cite only supplied evidence IDs and fingerprints.

**Discriminating read-only test**: Name one test, allowed by host policy, that distinguishes this claim from both alternatives. There is exactly one test.
