from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
EXPECTED_PROMPTS = {
    "detection-notice.md",
    "uncertainty-map.md",
    "bounded-reheating.md",
    "select-and-cool.md",
    "verify-or-stop.md",
}
INLINE_LINK = re.compile(r"(?<!!)\[[^]\n]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^]\n]+\]:\s*(\S+)(?:\s+.*)?$", re.MULTILINE)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _without_code_spans(markdown: str) -> str:
    without_fences = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", without_fences)


def _markdown_targets(markdown: str) -> list[str]:
    visible = _without_code_spans(markdown)
    return INLINE_LINK.findall(visible) + REFERENCE_LINK.findall(visible)


def _slug(heading: str) -> str:
    normalized = heading.strip().lower()
    normalized = re.sub(r"[^a-z0-9 _-]", "", normalized)
    return re.sub(r"[ _]+", "-", normalized).strip("-")


def _validate_links(path: Path, markdown: str) -> None:
    targets = _markdown_targets(markdown)
    assert targets, f"{path.name} must contain a Markdown schema link"
    for target in targets:
        decoded = unquote(target)
        assert not any(ord(character) < 32 for character in decoded), target
        assert "\\" not in decoded, target
        parsed = urlsplit(decoded)
        assert parsed.scheme != "file", target
        assert not parsed.username and not parsed.password, target
        if parsed.scheme in {"http", "https"}:
            continue
        assert not parsed.scheme, target
        assert not decoded.startswith("/"), target
        link_path, separator, fragment = decoded.partition("#")
        assert link_path, target
        target_path = (path.parent / link_path).resolve()
        assert target_path.is_relative_to(PROJECT_ROOT.resolve()), target
        assert (
            target_path.exists()
            and target_path.is_file()
            and not target_path.is_symlink()
        ), target
        if separator:
            target_text = target_path.read_text(encoding="utf-8")
            headings = {_slug(value) for value in HEADING.findall(target_text)}
            assert fragment in headings, target


def test_every_prompt_link_is_local_resolved_and_safe() -> None:
    paths = sorted(PROMPTS_DIR.glob("*.md"))
    assert {path.name for path in paths} == EXPECTED_PROMPTS
    for path in paths:
        _validate_links(path, path.read_text(encoding="utf-8"))


def test_link_parser_ignores_code_spans_and_rejects_escape_mutations() -> None:
    source = "`[not a link](/outside.md)`\n[local](../contracts/v1/evidence-record.schema.json)\n"
    assert _markdown_targets(source) == ["../contracts/v1/evidence-record.schema.json"]
    prompt = PROMPTS_DIR / "detection-notice.md"
    valid = prompt.read_text(encoding="utf-8")
    _validate_links(prompt, valid)
    for bad_target in (
        "/outside.md",
        "file:///outside.md",
        "../outside.md",
        "../contracts/v1/evidence-record.schema.json#no-such-heading",
        "..\\contracts\\v1\\evidence-record.schema.json",
    ):
        mutated = valid + f"\n[bad]({bad_target})\n"
        with pytest.raises(AssertionError):
            _validate_links(prompt, mutated)
