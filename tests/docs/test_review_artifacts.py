"""Fail-closed contract for sanitized independent review summaries."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVIEW_ARTIFACTS = (
    "2026-08-27-spec-compliance.md",
    "2026-08-27-quality-security.md",
    "2026-08-27-publication-readiness.md",
)
REQUIRED_HEADINGS = (
    "Review metadata",
    "Commands and output summary",
    "Acceptance criteria checklist",
    "Findings",
    "Verdict",
)
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
FIELD_LINE = r"(?mi)^\s*(?:[-*]\s+)?{label}:\s*(?P<value>\S.*?)\s*$"

# Keep scanner-sensitive examples assembled at runtime.  # hygiene: test-runtime-fragment
FORBIDDEN_MARKERS = (
    "private" + " prompt",
    "raw" + " transcript",
    "internal" + " orchestration",
)
USER_LOCAL_PATH = re.compile(
    r"(?:/" + "home" + r"/[^/\s]+|/" + "Users" + r"/[^/\s]+|[A-Za-z]:\\" + "Users" + r"\\[^\\\s]+)"
)
PROVIDER_IDENTITY_FIELD = re.compile(
    r"(?mi)^\s*(?:[-*]\s+)?(?:provider|model|reviewer identity)\s*(?:name|id|identity)?\s*:"
)


def _section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        markdown,
    )
    assert match is not None, f"missing required heading: {heading}"
    return match.group("body")


def _field(section: str, label: str) -> str:
    match = re.search(FIELD_LINE.format(label=re.escape(label)), section)
    assert match is not None, f"missing required field: {label}"
    return match.group("value").strip().strip("`")


def _assert_no_disallowed_content(markdown: str) -> None:
    lowered = markdown.casefold()
    assert not any(marker in lowered for marker in FORBIDDEN_MARKERS)
    assert USER_LOCAL_PATH.search(markdown) is None
    assert PROVIDER_IDENTITY_FIELD.search(markdown) is None


def _assert_review_contract(path: Path) -> None:
    assert path.is_file(), f"missing public review summary: {path.relative_to(ROOT)}"
    markdown = path.read_text(encoding="utf-8")
    _assert_no_disallowed_content(markdown)

    sections = {heading: _section(markdown, heading) for heading in REQUIRED_HEADINGS}

    assert _field(sections["Review metadata"], "Reviewer role")
    independence = _field(sections["Review metadata"], "Fresh-context independence")
    assert "fresh" in independence.casefold() and "independent" in independence.casefold()
    commit = _field(sections["Review metadata"], "Commit under review")
    assert COMMIT_SHA.fullmatch(commit), "commit under review must be one immutable 40-hex SHA"

    commands = re.findall(
        FIELD_LINE.format(label=re.escape("Command")),
        sections["Commands and output summary"],
    )
    output_summaries = re.findall(
        FIELD_LINE.format(label=re.escape("Output summary")),
        sections["Commands and output summary"],
    )
    assert commands, "at least one exact command is required"
    assert len(commands) == len(output_summaries), (
        "every recorded command requires one output summary"
    )

    checklist = re.findall(
        r"(?m)^\s*[-*]\s+\[[ xX]\]\s+\S.+$",
        sections["Acceptance criteria checklist"],
    )
    assert checklist, "acceptance criteria checklist must contain checkbox entries"

    severity = _field(sections["Findings"], "Severity").casefold()
    assert severity in {"none", "informational", "minor", "important", "critical"}

    verdicts = re.findall(r"(?m)^\s*(PASS|REQUEST_CHANGES)\s*$", sections["Verdict"])
    assert len(verdicts) == 1, "verdict must be exactly PASS or REQUEST_CHANGES"


@pytest.mark.parametrize("name", REVIEW_ARTIFACTS)
def test_public_review_summary_is_sanitized_and_complete(name: str) -> None:
    _assert_review_contract(ROOT / "docs" / "reviews" / name)
