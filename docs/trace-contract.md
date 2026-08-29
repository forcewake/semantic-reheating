# Trace and public-contract reference

All public artifacts are closed Draft 2020-12 v1 JSON contracts. They carry redacted identifiers, declared effect classes, counters, and evidence references; they are not a place for credentials, private transcripts, or hidden reasoning. Consumers should validate an artifact before constructing a model or taking host action.

| Contract | Purpose |
| --- | --- |
| [Trace event](../contracts/v1/trace-event.schema.json) | A redacted, ordered observation from a run, with one payload representation and a declared effect class. |
| [Run policy](../contracts/v1/run-policy.schema.json) | The host's bounded detector, ladder, budget, side-effect, cooling, and optional-detector configuration. |
| [Detector finding](../contracts/v1/detector-finding.schema.json) | A detector's versioned, redacted match status, score, reason code, and supporting event IDs. |
| [Decision envelope](../contracts/v1/decision-envelope.schema.json) | The controller's closed advisory decision, constraints, evidence IDs, confidence inputs, and host-action flag. |
| [Recovery instruction](../contracts/v1/recovery-instruction.schema.json) | A host-considered, bounded instruction with gaps, limits, cooling, and stop conditions; it grants no authority. |
| [Recovery outcome](../contracts/v1/recovery-outcome.schema.json) | The host-reported completed, partial, failed, denied, or escalated result and consumed counters. |
| [Evidence record](../contracts/v1/evidence-record.schema.json) | A separately fingerprinted record that binds redacted evidence to a decision or outcome. |

## Trace discipline

A `TraceEvent` has contiguous sequence numbers within a run. It labels an observed effect as `read_only`, `idempotent_write`, `non_idempotent_write`, or `unknown`; the latter two are not silently replayed. Payloads are intentionally opaque at this boundary. Prefer a public digest or reference when the original content is not appropriate to publish.

The [architecture boundary](architecture.md) keeps these artifacts separate from execution. The [detectors](detectors.md) consume typed trace windows, and [recovery policies](recovery-policies.md) constrain the result. The [TypeScript example](../examples/typescript-middleware/README.md) validates the same artifacts with AJV without field translation.
