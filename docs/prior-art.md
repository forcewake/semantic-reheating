# Prior-art boundary

The word *reheating* is a deliberately bounded metaphor for proposal-policy/search breadth. This kit does not implement the acceptance schedule, energy function, stochastic transition rule, or convergence claims of strict simulated annealing. For the historical optimization reference, see Kirkpatrick, Gelatt, and Vecchi, “[Optimization by Simulated Annealing](https://doi.org/10.1126/science.220.4598.671)” (1983).

It is also not decoder-temperature control: it neither reads nor writes model sampling parameters. A host may use any model or no model at all; the public controller consumes trace evidence and policy, then produces advice.

The useful comparison is narrow: both phrases evoke limited exploration followed by constraints. In this kit, the constraints are explicit contracts, independent deterministic evidence, resource budgets, host approval, cooling, and hard stops. They are described in the [architecture](architecture.md), [detector](detectors.md), and [recovery-policy](recovery-policies.md) references.

No claim is made that the metaphor yields universal improvement, simulated-annealing guarantees, or a production deployment. The available evidence is the bounded synthetic corpus described in [evaluation](evaluation.md).
