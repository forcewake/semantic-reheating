# Semantic Reheating Reference Kit

A minimal, deterministic-first reference kit for expressing bounded, advisory recovery decisions from versioned agent traces and host policy.

> **Bounded terminology:** semantic reheating is not decoding temperature and does not change model sampling parameters. It is also not strict simulated annealing. The term describes a policy-guided way to recommend a limited change in recovery strategy when trace evidence indicates a stalled or degraded run.

The kit is designed so that a host retains authority over credentials, tools, retries, side effects, and escalation. It does not execute agent tools or make network requests as part of its command-line help path.

## Install and verify

Install the locked project dependencies, then run the local console entry point:

```bash
uv sync --all-groups
uv run reheat --help
```

The initial command only presents help. Later releases may add deterministic validation and decision capabilities while preserving host authority.

## Development

```bash
uv run pytest tests -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

This project is licensed under the [MIT License](LICENSE).
