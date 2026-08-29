"""Contract tests for the Task 24 evidence-led article bundle."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "article" / "semantic-reheating"


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_article_frontmatter_sections_and_evidence_boundary() -> None:
    text = (BUNDLE / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert "title: Semantic Reheating for LLM Agents" in frontmatter
    assert "draft: false" in frontmatter
    assert "TocOpen: false" in frontmatter
    assert len(re.findall(r"^## [1-8]\. ", text, flags=re.MULTILINE)) == 8
    assert "not decoder temperature" in text.lower()
    assert "not strict simulated annealing" in text.lower()
    for label in (
        "Documented fact",
        "Repository observation",
        "Experiment result",
        "Recommendation",
    ):
        assert label in text
    assert "0 / 108" in text
    assert "missing cells: 108" in text


def test_data_manifest_is_closed_and_binds_all_governed_candidates() -> None:
    schema = load(BUNDLE / "article-data-manifest.schema.json")
    manifest = load(BUNDLE / "article-data-manifest.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    assert manifest["schema_version"] == "1.0"
    entries = manifest["artifacts"]
    assert [entry["order"] for entry in entries] == list(range(1, len(entries) + 1))
    assert len({entry["path"] for entry in entries}) == len(entries)
    assert len({entry["sha256"] for entry in entries}) == len(entries)
    governed = [
        ROOT / "benchmark" / "results" / "deterministic-results.json",
        *(ROOT / "benchmark" / "live" / "results").glob("*.json"),
        ROOT / "skills" / "semantic-reheating" / "references" / "results.json",
    ]
    assert {entry["path"] for entry in entries} == {
        str(path.relative_to(ROOT)) for path in governed
    }
    for entry in entries:
        assert entry["sha256"] == sha256(ROOT / entry["path"])
    assert any(
        entry["source_kind"] == "blocked_campaign" and entry["include"]
        for entry in entries
    )
    assert all(
        not (
            entry["source_kind"] == "synthetic_example"
            and entry["status"] == "executed"
        )
        for entry in entries
    )
    invalid = dict(manifest)
    invalid["unexpected"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


def test_manifest_rejects_adversarial_bindings() -> None:
    schema = load(BUNDLE / "article-data-manifest.schema.json")
    manifest = load(BUNDLE / "article-data-manifest.json")
    validator = Draft202012Validator(schema)
    for mutate in (
        lambda value: value["artifacts"][0].update({"unknown": True}),
        lambda value: value["artifacts"][0].pop("sha256"),
        lambda value: value.update({"schema_version": "2.0"}),
        lambda value: value["artifacts"][0].update({"source_kind": "invented"}),
        lambda value: value["artifacts"][0].update({"status": "invented"}),
    ):
        value = json.loads(json.dumps(manifest))
        mutate(value)
        with pytest.raises(ValidationError):
            validator.validate(value)
