# Detectors and false-positive protection

Detectors are pure functions over validated trace windows and policy thresholds. They return [detector findings](../contracts/v1/detector-finding.schema.json), not tool commands or recovery decisions. Explanations expose redacted reason codes and event IDs rather than payload content.

## Deterministic signals

The deterministic set covers exact repetition, repeated error, no-net-state cycle, unchanged expected state, acceptance stall, and budget burn. These provide repetition, no-progress, or budget evidence from declared event identities, state fingerprints, acceptance observations, and public counters.

False-positive protection is deliberate:

- equivalent repetition uses canonical action fingerprints or declared digests, not changed prose alone;
- unchanged-state checks require an expected state change, so a polling trace that changes state is not a match;
- acceptance checks distinguish an observed verification from an unchanged result;
- budget evidence is useful for diagnosis but cannot substitute for the independent repetition-plus-no-progress gate required for `reheat`;
- a hard risk or hard budget finding produces a deterministic stop before an optional recommendation can matter.

The exact data shape and windows come from the [trace contracts](trace-contract.md) and the [run-policy contract](../contracts/v1/run-policy.schema.json). The [controller boundary](architecture.md) aggregates findings; it does not execute a detector's suggested action.

## Optional semantic detection

The optional semantic detector is disabled unless the host explicitly enables it in `RunPolicy`. When enabled, the host must separately meter its use and retain that accounting alongside the ordinary run budget. It is injected at the controller boundary, may return only repetition or no-progress evidence, and its unavailable or degraded status becomes an advisory notice when it is not required.

It cannot weaken a deterministic stop: the policy requires `can_relax_hard_stops: false`, and hard risk or budget stops still dominate. Optional semantic evidence therefore supplements, rather than replaces, deterministic evidence or host judgment. See [recovery policies](recovery-policies.md) for the gating and cooling rules.
