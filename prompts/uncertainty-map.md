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
Cooling ends when all unknowns classified exactly once and verify items have bounded host-approved checks. It makes no branch selection claim. Any follow-up needs remaining budget and host authority; re-entry requires a new independent episode and a new budget.

## Stop conditions
For unsafe input, missing authority, non-idempotent uncertainty, no information gain, ambiguous acceptance, or a hard budget condition: stop, escalate, or block as appropriate.

## Output contract
The operator rendering is not JSON; prose fields are not inserted into closed records. Unlisted additions are rejected. Replace example values with trace-derived real values while preserving the exact closed shape; never fabricate IDs. Reject an unknown major contract version. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Operator Markdown rendering (not JSON)
Allowed headings, in order: Unknown; Disposition; Evidence refs; Test; Owner; Assumption boundary.

### Structured record: RecoveryInstruction
Emit one separate [RecoveryInstruction](../contracts/v1/recovery-instruction.schema.json) record. Map every unknown to exactly one of verify, assume, escalate, or block in the operator rendering; assumptions require policy authorization and a bound.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-uncertainty",
  "instruction_id": "instruction-uncertainty-01",
  "selected_prompt_asset_id": "prompt-uncertainty-v1",
  "variables": [{"name": "current_goal", "value": "Classify a synthetic evidence gap."}],
  "diagnosed_gaps": [{"kind": "missing_evidence", "description": "Synthetic unknown needs host review."}],
  "recovery_budget": {"turns": 1, "tool_calls": 1, "tokens": 120, "elapsed_seconds": 45, "cost": 1},
  "allowed_tools": ["read_only", "validation"],
  "forbidden_actions": ["credential_access", "authority_grant", "network_publish", "non_idempotent_repeat"],
  "evidence_refs": ["evidence-unknown-01"],
  "rejected_hypothesis_refs": [],
  "expected_output": {"kind": "diagnosis", "required_sections": ["summary", "evidence", "constraints"], "max_characters": 1200},
  "cooling_conditions": {"minimum_elapsed_seconds": 30, "require_new_evidence": true, "minimum_acceptance_gain": 0.1},
  "stop_conditions": ["budget_exhausted", "host_denial", "risk_detected", "cooling_required"],
  "advisory_only": true,
  "grants_authority": false
}
```
