# TypeScript middleware interoperability example

This example is a small **host-owned** TypeScript boundary for Semantic Reheating
v1 JSON artifacts. It does not port the Python controller, invoke tools, grant
authority, or adapt an agent framework.

## What it demonstrates

- reads the immutable Python-emitted fixture and its closed, versioned aggregate
  wrapper;
- loads the authoritative schemas directly from [`../../contracts/v1`](../../contracts/v1)
  and validates them with AJV Draft 2020-12 — the schemas are never copied or
  translated;
- rejects unknown aggregate/artifact fields and unknown `2.x` versions;
- compares RFC 8785 bytes and SHA-256 for a deliberately non-normalized Unicode
  I-JSON value emitted by Python; and
- runs a generic async host loop whose `analyze` and `execute` callbacks stay
  owned by the embedding host. The sample scenarios cover normal progress,
  stagnation, bounded recovery, cooling, and a safe stop.

The fixture's `decision_envelope` deliberately uses `"decision": "escalate"`
with `"requires_host_action": true`. Validation proves the wire contract; the
host callback remains the only execution authority.

## Run

```sh
npm ci
npm run typecheck
npm test
```

From the repository root, the cross-stack test regenerates the fixture through
public Python models, byte-compares it before Node setup, then runs the Node
suite:

```sh
uv run pytest tests/integration/test_typescript_fixture_contract.py -q
```

## Fixture maintenance

`export_fixtures.py` writes canonical RFC 8785 bytes to standard output. It
uses public model constructors/serializers and `record_outcome`; it does not
read private controller state. Refresh the committed fixture only after a
contract change has been intentionally reviewed:

```sh
uv run examples/typescript-middleware/export_fixtures.py \
  > examples/typescript-middleware/fixtures/python-v1-artifacts.json
```
