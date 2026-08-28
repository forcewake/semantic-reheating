# Bounded reheating

## Purpose
Generate a small, falsifiable advisory comparison when evidence-supported recovery branches remain unresolved. Semantic reheating is not decoding-temperature control and is not simulated annealing in the strict mathematical sense. Exploration applies only to hypotheses and read-only tests, never production side effects.

## Runtime form
Use this prompt only to compare supplied evidence under a bounded hypothesis contract. You must produce exactly three alternatives and no action beyond the host allowlist. Semantic reheating is not decoding-temperature control and is not simulated annealing in the strict mathematical sense.

## Operator form
Provide redacted context, event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must reject any missing authority and submit an advisory only.

## Trigger
Use only when verified evidence supports a bounded recovery comparison and ordinary diagnosis cannot distinguish competing explanations.

## Non-trigger
Do not use for normal progress, a settled explanation, open-ended research, or any request to write or explore production side effects.

## Budget
Record limits and remaining capacity for turns, tool calls, tokens, elapsed time in seconds, and cost, both per-intervention and whole-run. Retries, handoffs, callbacks, and re-entry count against the budget. State maximum recovery episodes and maximum re-entry depth from host policy; there is no fixed universal iteration count. Stop at the first hard-limit breach.

## Tool restrictions
Use read-only or sandbox inspection by default. No writes or external side effects are allowed. Never repeat unknown, unconfirmed, or non-idempotent writes; do not use credentials. A named tool allowlist comes from host policy only. Output is advisory.

## Evidence
Cite event IDs and evidence IDs with fingerprints or digests. Separate observed facts, unknowns, and assumptions. Include no hidden reasoning, no raw transcript, and no unsupported invention.

## Cooling
End branch exploration when one branch has greater verified support and a concrete next action within remaining budget and authority. The host executes at most the selected authorized action and deterministic verification. Re-entry requires a new independent episode and a new budget.

## Stop conditions
For unsafe input, missing authority, non-idempotent uncertainty, no information gain, ambiguous acceptance, or a hard budget condition: stop, escalate, or block as appropriate.

## Output contract
The operator rendering is not JSON; prose fields are not inserted into closed records. Unlisted additions are rejected. Replace example values with trace-derived real values while preserving the exact closed shape; never fabricate IDs. Reject an unknown major contract version. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Operator Markdown rendering (not JSON)
Allowed headings, in order: Hypotheses; Evidence comparison; Read-only tests; Stop decision.

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

### Structured record: RecoveryInstruction
Emit one separate [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json) record with the exact conditional hypothesis contract; do not merge the operator hypotheses into JSON.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-reheat",
  "instruction_id": "instruction-reheat-01",
  "selected_prompt_asset_id": "prompt-reheat-v1",
  "variables": [{"name": "current_goal", "value": "Compare three synthetic explanations."}],
  "diagnosed_gaps": [{"kind": "missing_evidence", "description": "Synthetic bounded comparison gap."}],
  "recovery_budget": {"turns": 2, "tool_calls": 2, "tokens": 240, "elapsed_seconds": 90, "cost": 2},
  "allowed_tools": ["read_only", "analysis", "validation"],
  "forbidden_actions": ["credential_access", "authority_grant", "network_publish", "non_idempotent_repeat"],
  "evidence_refs": ["evidence-reheat-01"],
  "rejected_hypothesis_refs": [],
  "expected_output": {"kind": "diagnosis", "required_sections": ["summary", "evidence", "constraints", "next_steps", "stop_conditions"], "max_characters": 1800, "hypothesis_contract": {"exact_hypotheses": 3, "mutually_exclusive": true, "falsifiable": true, "discriminating_tests_per_hypothesis": 1, "allowed_test_effect_classes": ["read_only"]}},
  "cooling_conditions": {"minimum_elapsed_seconds": 30, "require_new_evidence": true, "minimum_acceptance_gain": 0.1},
  "stop_conditions": ["budget_exhausted", "host_denial", "risk_detected", "cooling_required"],
  "advisory_only": true,
  "grants_authority": false
}
```
