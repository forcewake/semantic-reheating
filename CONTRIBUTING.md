# Contributing

Thank you for improving the Semantic Reheating Reference Kit.

## Before opening a change

- Keep examples, traces, and test data synthetic and redacted.
- Do not commit credentials, provider details, local transcripts, `.hermes/` data, or private benchmark material.
- Preserve the host's authority over tools, side effects, retries, and escalation.
- Use focused RED→GREEN tests for behavioral changes.

## Local checks

```bash
uv sync --all-groups
uv run pytest tests -q
```

Please explain the behavior being changed, add or update focused tests, and keep documentation clear that semantic reheating is an advisory recovery concept rather than decoding-temperature control or strict simulated annealing.
