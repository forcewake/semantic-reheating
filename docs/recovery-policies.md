# Recovery policies, ladder, and cooling

A [run policy](../contracts/v1/run-policy.schema.json) is validated before analysis. It bounds detector windows and thresholds, five resource dimensions (turns, tool calls, tokens, elapsed seconds, and cost), recovery episodes, re-entry depth, repeatable effect classes, and the recovery ladder. It constrains a host; it cannot grant authority to a controller.

## Ladder and gate

The closed ladder is `nudge`, `diagnose`, `reheat`, `restart`, `escalate`, and `stop`. The controller chooses the lowest allowed advisory stage from the policy. `reheat` needs independent repetition and no-progress evidence; budget findings cannot replace either class. `restart` and `escalate` require host action, and a host can always stop or deny work.

A reheat instruction contains bounded, falsifiable hypotheses and read-only discriminating tests. It must preserve evidence, disallow an unconfirmed non-idempotent repeat, and pass the host's own tool/permission checks. The [instruction contract](../contracts/v1/recovery-instruction.schema.json) records those limits; the [outcome contract](../contracts/v1/recovery-outcome.schema.json) records what the host actually did.

## Cooling and stop conditions

Cooling prevents a recommendation from becoming an uncontrolled retry loop. Policy can require elapsed time, new evidence, and a minimum acceptance gain before another recovery episode. It also limits episodes and re-entry depth; retries, handoffs, callbacks, and re-entry count against the same counters.

The controller emits a deterministic stop when a hard risk or budget limit is reached. Cooling, host denial, missing authority, unsafe writes, and exhausted budgets are stop/escalation conditions—not invitations to retry. Optional semantic detection cannot relax these conditions. See [detectors](detectors.md) for the evidence rules and [architecture](architecture.md) for the host controller boundary.
