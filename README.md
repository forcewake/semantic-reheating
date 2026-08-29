# Semantic Reheating Reference Kit

A minimal, deterministic-first reference kit for expressing bounded, advisory recovery decisions from versioned agent traces and host policy.

> **Bounded terminology:** semantic reheating is a **proposal-policy/search-breadth metaphor**. It is **not decoder-temperature control**, does not change model sampling parameters, and is **not strict simulated annealing**. The term describes a policy-guided proposal to try a limited, evidence-backed recovery strategy when a trace indicates a stalled or degraded run.

The controller is advisory. The host retains authority over credentials, tools, retries, side effects, confirmation, cooling, and escalation. It does not execute a host tool, make a network request, does not grant permission, or replace the host's safety policy.

## Documentation

- [Architecture and authority boundary](docs/architecture.md)
- [Trace and public-contract reference](docs/trace-contract.md)
- [Detectors and false-positive protection](docs/detectors.md)
- [Recovery policies, ladder, and cooling](docs/recovery-policies.md)
- [Synthetic corpus and bounded evaluation](docs/evaluation.md)
- [Prior-art boundary](docs/prior-art.md)
- [Evidence-led article](article/semantic-reheating/index.md)

## Non-goals

This is not a decoder control, an autonomous tool runner, a general agent framework, or a production-deployment claim. It makes no universal improvement claim: an advisory recommendation is useful only when the host validates it against its own acceptance and safety criteria.

## Install and verify

Install the locked project dependencies, then run the local console entry point:

```bash
uv sync --all-groups
uv run reheat --help
```

The current CLI provides local, advisory operations: `reheat validate` validates a trace and policy, `reheat analyze` analyzes them, `reheat explain` renders a validated decision, and `reheat benchmark` replays the committed synthetic benchmark corpus. These commands do not grant host authority or execute host tools.

## Examples

- [Generic Python host loop](examples/python-generic-agent/README.md) records synthetic trace events after its in-memory tools return, applies the advisory decision in host code, and records the outcome.
- [TypeScript AJV middleware](examples/typescript-middleware/README.md) validates the unchanged v1 artifacts without porting the Python controller or granting tool authority.

## Development

```bash
uv run pytest tests -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

This project is licensed under the [MIT License](LICENSE).
