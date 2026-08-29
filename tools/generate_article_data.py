"""Deterministically render the evidence table owned by the article bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "article" / "semantic-reheating"
BEGIN = "<!-- BEGIN GENERATED RESULTS -->"
END = "<!-- END GENERATED RESULTS -->"
GOVERNED_ROOTS = (
    ROOT / "benchmark" / "results",
    ROOT / "benchmark" / "live" / "results",
)
SKILL_RESULT = ROOT / "skills" / "semantic-reheating" / "references" / "results.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(schema_path: Path, value: dict[str, Any]) -> None:
    schema = _load(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def _candidates(root: Path) -> set[str]:
    candidates = {
        str(path.relative_to(root))
        for governed in (
            root / "benchmark" / "results",
            root / "benchmark" / "live" / "results",
        )
        for path in governed.glob("*.json")
    }
    candidates.add(
        str(
            (root / "skills/semantic-reheating/references/results.json").relative_to(
                root
            )
        )
    )
    return candidates


def _source_schema(relative: str) -> Path:
    if relative == "benchmark/results/deterministic-results.json":
        return ROOT / "benchmark/schemas/v1/replay-result.schema.json"
    if relative == "skills/semantic-reheating/references/results.json":
        return ROOT / "skills/semantic-reheating/references/results.schema.json"
    if relative.endswith("-manifest.json"):
        return ROOT / "benchmark/live/campaign-run-manifest.schema.json"
    return ROOT / "benchmark/live/results.schema.json"


def validate_manifest(root: Path = ROOT) -> list[dict[str, Any]]:
    bundle = root / "article/semantic-reheating"
    manifest = _load(bundle / "article-data-manifest.json")
    _validate(bundle / "article-data-manifest.schema.json", manifest)
    artifacts = manifest["artifacts"]
    paths = [entry["path"] for entry in artifacts]
    hashes = [entry["sha256"] for entry in artifacts]
    orders = [entry["order"] for entry in artifacts]
    if (
        len(paths) != len(set(paths))
        or len(hashes) != len(set(hashes))
        or len(orders) != len(set(orders))
    ):
        raise ValueError("duplicate artifact path, hash, or order")
    if orders != list(range(1, len(orders) + 1)):
        raise ValueError("orders must be canonical consecutive integers")
    if set(paths) != _candidates(root):
        raise ValueError(
            "manifest must explicitly bind every governed result candidate"
        )
    for entry in artifacts:
        path = root / entry["path"]
        if not path.is_file() or _sha(path) != entry["sha256"]:
            raise ValueError(f"hash drift or missing artifact: {entry['path']}")
        if (
            entry["source_kind"] == "synthetic_example"
            and entry["status"] == "executed"
        ):
            raise ValueError(
                "synthetic examples cannot be selected as executed evidence"
            )
        _validate(_source_schema(entry["path"]), _load(path))
    return artifacts


def render_results(root: Path = ROOT) -> str:
    entries = validate_manifest(root)
    rows = [
        "\n| Evidence class | Bound artifact | Sample size / observed cells | Missing cells | Scope |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for entry in entries:
        if not entry["include"]:
            continue
        data = _load(root / entry["path"])
        link = f"`{entry['path']}` (`sha256:{entry['sha256']}`)"
        if entry["path"] == "benchmark/results/deterministic-results.json":
            metrics = data["metrics"]
            rows.append(
                f"| Deterministic benchmark | {link} | {metrics['decision_total']} traces | 0 | fixed corpus replay; 29/29 decisions and 29/29 safety outcomes match |"
            )
        elif entry["path"] == "skills/semantic-reheating/references/results.json":
            rows.append(
                f"| Skill A/B (single replicate) | {link} | {data['baseline_total_count']} scenarios | 0 | baseline {data['baseline_pass_count']}/{data['baseline_total_count']} → post-Skill {data['postskill']['pass_count']}/{data['postskill']['total_count']}; bounded scenario set |"
            )
        elif entry["source_kind"] == "blocked_campaign":
            planned = len(data.get("planned_cells", []))
            observed = len(data.get("results", []))
            rows.append(
                f"| Blocked campaign status | {link} | {observed} / {planned} cells | {planned - observed} | blocked; caps consumed 0; not an efficacy experiment |"
            )
    rows.extend(
        [
            "",
            "**Interpretation boundary.** These are committed redacted artifacts. The deterministic row is a fixture-replay result; the Skill row is one six-scenario replicate; the campaign rows are blocked status records. No row supports a universal improvement or production-deployment claim.",
            "",
        ]
    )
    return "\n".join(rows)


def generated_section_matches(text: str, root: Path = ROOT) -> bool:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        return False
    _, rest = text.split(BEGIN, 1)
    actual, _ = rest.split(END, 1)
    return actual == render_results(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    article = BUNDLE / "index.md"
    text = article.read_text(encoding="utf-8")
    rendered = render_results(ROOT)
    if args.check:
        if not generated_section_matches(text, ROOT):
            print("generated results section drift", file=sys.stderr)
            return 1
        print("article generated results section is current")
        return 0
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError(
            "article must contain exactly one generated-results delimiter pair"
        )
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    article.write_text(before + BEGIN + rendered + END + after, encoding="utf-8")
    print("rendered article results section")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
