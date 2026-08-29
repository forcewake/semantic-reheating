# Generic Python Host Example

This is a standalone, stdlib-only synthetic host loop. It demonstrates using
`semantic_reheating` as an advisory controller while the host retains all tool,
confirmation, recovery, cooling, and stop authority.

Run a named scenario from the repository root:

```bash
uv run python examples/python-generic-agent/main.py --scenario productive
uv run python examples/python-generic-agent/main.py --scenario unsafe_write
```

The program writes one JSON result. Available scenarios are:

- `productive` — a normal read-only result and improved acceptance check;
- `exact_repetition` — repeated read-only work plus an unchanged acceptance check;
- `bounded_recovery` — the policy permits one host-owned recovery action;
- `cooling` — the host receives a reheat recommendation and chooses its bounded cooling branch;
- `unsafe_write` — confirmation is absent, the host executes no write, records the denial, and stops.

`main.py` creates public `TraceEvent` objects only after its in-memory synthetic
tool returns. It passes the trace to public `analyze`, then applies the returned
advisory decision in `Host.apply_decision` with an explicit `if` switch. Finally,
it reports the host outcome with public `record_outcome`. The controller has no
reference to the synthetic tool and cannot invoke it.
