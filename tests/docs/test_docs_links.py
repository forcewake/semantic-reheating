"""The public documentation fan-in must keep local contract links usable."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PUBLIC_DOCS = (
    ROOT / "README.md",
    DOCS / "architecture.md",
    DOCS / "trace-contract.md",
    DOCS / "detectors.md",
    DOCS / "recovery-policies.md",
    DOCS / "evaluation.md",
    DOCS / "prior-art.md",
)
CONTRACTS = (
    "trace-event.schema.json",
    "run-policy.schema.json",
    "detector-finding.schema.json",
    "decision-envelope.schema.json",
    "recovery-instruction.schema.json",
    "recovery-outcome.schema.json",
    "evidence-record.schema.json",
)
LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")


def test_public_documentation_files_exist_and_local_links_resolve() -> None:
    for document in PUBLIC_DOCS:
        assert document.is_file(), (
            f"missing public document: {document.relative_to(ROOT)}"
        )
        for raw_target in LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.split("#", maxsplit=1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            assert (document.parent / target).resolve().is_file(), (
                f"unresolved local link in {document.relative_to(ROOT)}: {raw_target}"
            )


def test_readme_links_the_canonical_public_documentation() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for name in (
        "architecture",
        "trace-contract",
        "detectors",
        "recovery-policies",
        "evaluation",
        "prior-art",
    ):
        assert f"docs/{name}.md" in text


def test_every_public_contract_has_an_explained_trace_contract_link() -> None:
    text = (DOCS / "trace-contract.md").read_text(encoding="utf-8")

    for contract in CONTRACTS:
        target = f"../contracts/v1/{contract}"
        assert target in text, f"missing public contract link: {target}"
        assert re.search(rf"\[[^]\n]+\]\({re.escape(target)}\)", text), (
            f"contract must have an explanatory Markdown label: {contract}"
        )


def test_reference_docs_cover_safety_boundaries_and_evidence() -> None:
    expected_terms = {
        "architecture.md": ("host", "controller", "authority"),
        "detectors.md": ("false-positive", "deterministic", "semantic"),
        "recovery-policies.md": ("cooling", "host", "stop"),
        "evaluation.md": ("synthetic", "corpus", "not"),
        "prior-art.md": ("simulated annealing", "decoder"),
    }

    for name, terms in expected_terms.items():
        text = (DOCS / name).read_text(encoding="utf-8").lower()
        for term in terms:
            assert term in text, f"{name} must explain {term!r}"
