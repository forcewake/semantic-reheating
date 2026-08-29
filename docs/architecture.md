# Architecture and authority boundary

Semantic reheating is a deterministic-first, **host-advisory** controller. It turns a redacted, versioned trace plus a validated run policy into a closed decision envelope; it does not operate the agent.

```text
host-owned tools -> redacted TraceEvent records -> analyze(trace, policy)
                                                -> DecisionEnvelope -> host-owned switch
                                                -> RecoveryInstruction / RecoveryOutcome
```

The host creates trace events after it observes work, supplies policy, chooses whether to act, performs every tool call, and records the result. The controller owns no credentials, tool handles, approval channel, retry loop, network client, or side-effect authority. `restart` and `escalate` are advisory decisions that explicitly require host action; a host may deny any recommendation.

## Controller boundary

`analyze` aggregates deterministic detector findings and policy constraints into a [decision envelope](../contracts/v1/decision-envelope.schema.json). The closed decision vocabulary is `continue`, `nudge`, `diagnose`, `reheat`, `restart`, `escalate`, or `stop`. A hard risk or budget stop dominates a recovery recommendation.

A host may then request a [recovery instruction](../contracts/v1/recovery-instruction.schema.json), execute only a policy-approved action, and report a [recovery outcome](../contracts/v1/recovery-outcome.schema.json). Evidence is recorded separately through the [evidence record](../contracts/v1/evidence-record.schema.json), keeping facts distinct from advice.

## Inputs and outputs

The public boundary is the set of [closed v1 contracts](trace-contract.md), not Python internals. Contract validation rejects unknown fields and incompatible major versions. The [detector reference](detectors.md) describes evidence production; the [recovery-policy reference](recovery-policies.md) describes how a host constrains the recommendation.

For executable, host-owned integrations see the [generic Python example](../examples/python-generic-agent/README.md) and [TypeScript AJV middleware](../examples/typescript-middleware/README.md). Both are synthetic demonstrations, not deployment templates.
