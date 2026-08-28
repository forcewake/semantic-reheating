# Detection notice

## Purpose
Turn verified recovery signals into a bounded advisory notice. It records why ordinary progress should pause without authorizing any action.

## Runtime form
Use this prompt only to summarize verified detector findings. You must produce an advisory notice, preserve uncertainty, and leave execution to the host.

## Operator form
Provide event IDs, evidence IDs, fingerprints or digests, remaining counters, and host policy. The operator must reject missing authority and request escalation instead of action.

## Trigger
Use only when the reheat gate has at least one repetition-class finding AND one independent measurable no-progress-class finding. Risk or a hard budget can independently force STOP or ESCALATE, but never reheat. Budget evidence alone never counts as either class.

## Non-trigger
Do not use for normal progress, a single class, or two same-class findings. Productive controls are non-trigger evidence: changed pagination cursor or batch item; changed hypothesis or tool input; new evidence or error fingerprint; acceptance-required verification rerun; productive handoff; or a converging state poll.

## Budget
Record limits and remaining capacity for turns, tool calls, tokens, elapsed time in seconds, and cost, both per-intervention and whole-run. Retries, handoffs, callbacks, and re-entry count against the budget. State maximum recovery episodes and maximum re-entry depth from host policy; there is no fixed universal iteration count. Stop at the first hard-limit breach.

## Tool restrictions
Use read-only or sandbox inspection by default. No writes or external side effects are allowed. Never repeat unknown, unconfirmed, or non-idempotent writes; do not use credentials. A named tool allowlist comes from host policy only. Output is advisory.

## Evidence
Cite event IDs and evidence IDs with fingerprints or digests. Separate observed facts, unknowns, and assumptions. Include no hidden reasoning, no raw transcript, and no unsupported invention.

## Cooling
The notice closes after evidence is recorded and the route is diagnose/continue/stop/escalate. It does not select or execute a branch. Any later intervention needs remaining budget and host authority; re-entry requires a new independent episode and a new budget.

## Stop conditions
For unsafe input, missing authority, non-idempotent uncertainty, no information gain, ambiguous acceptance, or a hard budget condition: stop, escalate, or block as appropriate.

## Output contract
The operator rendering is not JSON; prose fields are not inserted into closed records. Unlisted additions are rejected. Replace example values with trace-derived real values while preserving the exact closed shape; never fabricate IDs. Reject an unknown major contract version. The controller/prompt does not grant tools, credentials, approvals, permissions, or side-effect authority; host remains the sole authority.

### Operator Markdown rendering (not JSON)
Allowed headings, in order: Detected signals; Independent no-progress evidence; Prohibited retry; Bounded next action; Host authority.

### Structured record: DecisionEnvelope
Emit one separate [DecisionEnvelope](../contracts/v1/decision-envelope.schema.json) record; do not merge prose or another record into it.

```json
{
  "contract_version": "1.0",
  "run_id": "run-synthetic-notice",
  "decision_id": "decision-notice-01",
  "decision": "diagnose",
  "reason_codes": ["repetition_detected", "no_progress_detected", "host_action_required"],
  "evidence_event_ids": ["event-repeat-01", "event-stall-01"],
  "recovery_policy": null,
  "recovery_budget": {"turns": 2, "tool_calls": 2, "tokens": 240, "elapsed_seconds": 90, "cost": 2},
  "constraints": {"must_preserve_evidence": true, "no_non_idempotent_repeat": true, "require_host_confirmation": true, "allowed_effect_classes": ["read_only"]},
  "cooling_conditions": {"minimum_elapsed_seconds": 30, "require_new_evidence": true, "minimum_acceptance_gain": 0.1},
  "confidence": {"score": 0.8, "contributing_findings": [{"finding_id": "finding-repeat-01", "finding_class": "repetition", "matched": true, "score": 0.9, "weight": 0.5, "weighted_score": 0.45}, {"finding_id": "finding-stall-01", "finding_class": "no_progress", "matched": true, "score": 0.8, "weight": 0.5, "weighted_score": 0.4}]},
  "requires_host_action": true,
  "human_summary": "Synthetic advisory diagnosis request.",
  "diagnosed_gaps": [{"kind": "stalled_progress", "description": "Synthetic independent progress gap."}],
  "rejected_hypothesis_refs": [],
  "detector_notices": []
}
```
