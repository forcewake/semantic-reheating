"""Strict scoped citation-ledger tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "article/semantic-reheating"


def test_ledger_is_closed_and_matches_article_citation_keys_exactly() -> None:
    schema = json.loads((BUNDLE / "sources-ledger.schema.json").read_text())
    ledger = json.loads((BUNDLE / "sources-ledger.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(ledger)
    text = (BUNDLE / "index.md").read_text()
    cited = re.findall(r"\[\^([a-z0-9-]+)\]", text)
    assert cited
    keys = [record["citation_key"] for record in ledger["sources"]]
    assert len(keys) == len(set(keys))
    assert set(cited) == set(keys)
    assert all(cited.count(key) >= 1 for key in keys)
    assert all(record["accessed"] == "2026-08-29" for record in ledger["sources"])


def test_ledger_rejects_unknown_and_incomplete_records() -> None:
    schema = json.loads((BUNDLE / "sources-ledger.schema.json").read_text())
    ledger = json.loads((BUNDLE / "sources-ledger.json").read_text())
    validator = Draft202012Validator(schema)
    for mutation in (
        lambda value: value.update({"unknown": True}),
        lambda value: value["sources"][0].update({"unknown": True}),
        lambda value: value["sources"][0].pop("url"),
        lambda value: value.update({"schema_version": "2.0"}),
    ):
        value = json.loads(json.dumps(ledger))
        mutation(value)
        with pytest.raises(ValidationError):
            validator.validate(value)
