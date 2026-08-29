# Synthetic corpus and bounded evaluation

The committed [corpus manifest](../benchmark/scenarios/manifest.json) indexes redacted synthetic JSONL traces and their expected detector, decision, evidence, and safety outcomes. It includes pathological cases (repetition, cycles, unchanged state, repeated errors, budget burn, blocked authority, and unsafe writes) as well as productive controls such as pagination, batching, changed hypotheses, handoffs, verification reruns, and eventual consistency.

The replay and metric artifacts are deterministic local evidence for this reference kit. They check whether the supplied contracts, policy, and trace produce the declared result; they are not a claim that semantic reheating universally improves agent performance, generalizes to production workloads, or has been production deployed.

## Read results narrowly

Use the corpus to inspect false interventions, missed signals, stop/restart choices, consumed public counters, and evidence gained under a fixed policy. Keep synthetic controls when changing a detector, and compare the exact manifest and result artifacts rather than inferring success from prose. A passing corpus does not authorize a host to run tools or relax its safety policy.

The [detector reference](detectors.md) explains the tested signals, while [recovery policies](recovery-policies.md) explains the bounded gate, hard stops, and cooling. The [Python example](../examples/python-generic-agent/README.md) and [TypeScript example](../examples/typescript-middleware/README.md) show host-owned integrations without a live provider.
