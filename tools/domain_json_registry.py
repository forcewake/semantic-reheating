"""Explicit, closed ownership registry for every public domain JSON artifact."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.validators import validator_for


class RegistryError(ValueError):
    """A public JSON artifact has no declared, valid ownership."""


@dataclass(frozen=True)
class RegistryEntry:
    path: str
    schema: str | None
    mode: str  # schema, json, jsonl


_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
_ECOSYSTEM_METADATA = frozenset(
    {
        "examples/typescript-middleware/package.json",
        "examples/typescript-middleware/package-lock.json",
        "examples/typescript-middleware/tsconfig.json",
        "tools/assets/package.json",
        "tools/assets/package-lock.json",
    }
)
_INTENTIONAL_INVALID_FIXTURES = frozenset(
    {"tests/fixtures/contracts/unknown-trace-field.json"}
)


def _entries() -> tuple[RegistryEntry, ...]:
    contracts = (
        "trace-event",
        "run-policy",
        "detector-finding",
        "decision-envelope",
        "recovery-instruction",
        "recovery-outcome",
        "evidence-record",
    )
    entries: list[RegistryEntry] = [
        RegistryEntry(f"contracts/v1/{name}.schema.json", None, "schema")
        for name in contracts
    ]
    entries += [
        RegistryEntry(
            "benchmark/schemas/v1/corpus-manifest.schema.json", None, "schema"
        ),
        RegistryEntry("benchmark/schemas/v1/replay-result.schema.json", None, "schema"),
        RegistryEntry("benchmark/live/campaign.schema.json", None, "schema"),
        RegistryEntry("benchmark/live/stacks.schema.json", None, "schema"),
        RegistryEntry(
            "benchmark/live/campaign-run-manifest.schema.json", None, "schema"
        ),
        RegistryEntry("benchmark/live/results.schema.json", None, "schema"),
        RegistryEntry(
            "examples/typescript-middleware/fixtures/python-v1-artifacts.schema.json",
            None,
            "schema",
        ),
    ]
    for name in (
        "pressure-scenarios",
        "baseline-summary",
        "rubric",
        "stack-receipt",
        "results",
    ):
        entries.append(
            RegistryEntry(
                f"skills/semantic-reheating/references/{name}.schema.json",
                None,
                "schema",
            )
        )
    for name in ("article-data-manifest", "sources-ledger"):
        entries.append(
            RegistryEntry(
                f"article/semantic-reheating/{name}.schema.json", None, "schema"
            )
        )
    entries += [
        RegistryEntry(
            "benchmark/scenarios/manifest.json",
            "benchmark/schemas/v1/corpus-manifest.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/results/deterministic-results.json",
            "benchmark/schemas/v1/replay-result.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/campaign.example.json",
            "benchmark/live/campaign.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/stacks.example.json",
            "benchmark/live/stacks.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/stacks.selected.json",
            "benchmark/live/stacks.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/results/example-redacted-results.json",
            "benchmark/live/results.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/results/campaign-2026-08-29.json",
            "benchmark/live/results.schema.json",
            "json",
        ),
        RegistryEntry(
            "benchmark/live/results/campaign-2026-08-29-manifest.json",
            "benchmark/live/campaign-run-manifest.schema.json",
            "json",
        ),
        RegistryEntry(
            "examples/typescript-middleware/fixtures/python-v1-artifacts.json",
            "examples/typescript-middleware/fixtures/python-v1-artifacts.schema.json",
            "json",
        ),
    ]
    for name in (
        "pressure-scenarios",
        "baseline-summary",
        "rubric",
        "stack-receipt",
        "results",
    ):
        entries.append(
            RegistryEntry(
                f"skills/semantic-reheating/references/{name}.json",
                f"skills/semantic-reheating/references/{name}.schema.json",
                "json",
            )
        )
    for name in ("article-data-manifest", "sources-ledger"):
        entries.append(
            RegistryEntry(
                f"article/semantic-reheating/{name}.json",
                f"article/semantic-reheating/{name}.schema.json",
                "json",
            )
        )
    corpus = [
        "batching-a",
        "batching-b",
        "blocked-authority",
        "budget-burn-cost",
        "budget-burn-elapsed-seconds",
        "budget-burn-tokens",
        "budget-burn-tool-calls",
        "budget-burn-turns",
        "changed-hypothesis-a",
        "changed-hypothesis-b",
        "context-restart",
        "cycle-five-step",
        "cycle-four-step",
        "cycle-three-step",
        "cycle-two-step",
        "eventual-consistency-a",
        "eventual-consistency-b",
        "exact-repetition-stall",
        "handoff-a",
        "handoff-b",
        "pagination-a",
        "pagination-b",
        "repeated-error",
        "state-changing-poll-a",
        "state-changing-poll-b",
        "unchanged-state",
        "unsafe-write-repetition",
        "verification-rerun-a",
        "verification-rerun-b",
    ]
    entries += [
        RegistryEntry(
            f"benchmark/corpus/{name}.jsonl",
            "contracts/v1/trace-event.schema.json",
            "jsonl",
        )
        for name in corpus
    ]
    for name in contracts:
        entries.append(
            RegistryEntry(
                f"tests/fixtures/contracts/minimal-{name}.json",
                f"contracts/v1/{name}.schema.json",
                "json",
            )
        )
    return tuple(entries)


REGISTRY = _entries()


def registry_entries(root: Path | None = None) -> tuple[RegistryEntry, ...]:
    """Return the static registry; root is accepted only for a uniform tool API."""
    del root
    return REGISTRY


def _domain_candidate(path: str) -> bool:
    return path.endswith((".json", ".jsonl")) and path.startswith(
        (
            "contracts/",
            "benchmark/",
            "examples/typescript-middleware/fixtures/",
            "skills/semantic-reheating/references/",
            "article/semantic-reheating/",
            "tests/fixtures/contracts/",
        )
    )


def discover_domain_json(root: Path) -> set[str]:
    """Discover candidates only under declared public domain roots, never by extension globally."""
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if (
                _domain_candidate(relative)
                and relative not in _ECOSYSTEM_METADATA | _INTENTIONAL_INVALID_FIXTURES
            ):
                result.add(relative)
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryError(f"invalid JSON {path}: {error}") from error


def _check_schema(path: Path) -> None:
    data = _load_json(path)
    if not isinstance(data, Mapping) or data.get("$schema") != _SCHEMA:
        raise RegistryError(f"schema is not Draft 2020-12: {path}")
    try:
        Draft202012Validator.check_schema(data)
    except Exception as error:  # jsonschema exposes version-specific schema errors
        raise RegistryError(f"invalid schema {path}: {error}") from error


def validate_registry(
    root: Path,
    entries: Iterable[RegistryEntry | Mapping[str, str | None]] | None = None,
) -> None:
    """Fail closed on missing, duplicate, undeclared, or invalid registered content."""
    raw_entries = list(REGISTRY if entries is None else entries)
    normalized: list[RegistryEntry] = [
        e if isinstance(e, RegistryEntry) else RegistryEntry(**e) for e in raw_entries
    ]
    seen: set[str] = set()
    for entry in normalized:
        if entry.path in seen:
            raise RegistryError(f"duplicate registry mapping: {entry.path}")
        seen.add(entry.path)
        if entry.mode not in {"schema", "json", "jsonl"}:
            raise RegistryError(f"unknown validation mode: {entry.mode}")
        if not (root / entry.path).is_file():
            raise RegistryError(f"missing registered artifact: {entry.path}")
        if entry.mode != "schema" and (
            not entry.schema or not (root / entry.schema).is_file()
        ):
            raise RegistryError(f"absent schema for {entry.path}")
    undeclared = discover_domain_json(root) - seen
    if undeclared:
        raise RegistryError(f"undeclared public domain JSON: {sorted(undeclared)}")
    schema_store: dict[str, Any] = {}
    for entry in normalized:
        if entry.mode == "schema":
            schema_data = _load_json(root / entry.path)
            schema_id = (
                schema_data.get("$id") if isinstance(schema_data, Mapping) else None
            )
            if isinstance(schema_id, str):
                schema_store[schema_id] = schema_data
    for entry in normalized:
        artifact = root / entry.path
        if entry.mode == "schema":
            _check_schema(artifact)
            continue
        schema = _load_json(root / str(entry.schema))
        schema_id = schema.get("$id", "") if isinstance(schema, Mapping) else ""
        validator_class = validator_for(schema)
        resolver = RefResolver(
            base_uri=str(schema_id), referrer=schema, store=schema_store
        )
        validator = validator_class(schema, resolver=resolver)
        documents = (
            [_load_json(artifact)]
            if entry.mode == "json"
            else [
                json.loads(line)
                for line in artifact.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        for document in documents:
            errors = sorted(
                validator.iter_errors(document), key=lambda err: list(err.path)
            )
            if errors:
                raise RegistryError(
                    f"artifact fails schema {entry.path}: {errors[0].message}"
                )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    validate_registry(args.root.resolve())
    print("domain JSON registry valid")
