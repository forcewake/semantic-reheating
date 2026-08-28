from __future__ import annotations

import os
import re
import stat
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
INLINE_LINK = re.compile(
    r"!?\[[^]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)"
)
REFERENCE_DEFINITION = re.compile(
    r"^\s*\[([^]\n]+)\]:\s*(?:<([^>\n]+)>|(\S+))(?:\s+.*)?$", re.MULTILINE
)
ANGLE_AUTOLINK = re.compile(r"<([^ <>\n]+)>")
FENCED_CODE = re.compile(r"^```[^\n]*\n.*?^```\s*$", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]*`")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _without_code_spans(markdown: str) -> str:
    return INLINE_CODE.sub("", FENCED_CODE.sub("", markdown))


def _markdown_targets(markdown: str) -> list[str]:
    """Extract every local Markdown destination after excluding code samples."""
    visible = _without_code_spans(markdown)
    definitions = {
        match.group(1).strip().casefold(): match.group(2) or match.group(3)
        for match in REFERENCE_DEFINITION.finditer(visible)
    }
    targets: list[str] = []
    for match in INLINE_LINK.finditer(visible):
        targets.append(match.group(1) or match.group(2))
    targets.extend(ANGLE_AUTOLINK.findall(visible))
    # Definitions are destinations too. Preserve source order, including unused
    # definitions, so a hidden unsafe destination cannot be silently ignored.
    targets.extend(
        match.group(2) or match.group(3)
        for match in REFERENCE_DEFINITION.finditer(visible)
    )
    for usage in re.finditer(r"(?<!!)\[[^]\n]+\]\[([^]\n]+)\]", visible):
        label = usage.group(1).strip().casefold()
        assert label in definitions, f"unresolved reference usage: {usage.group(0)!r}"
    return targets


def _slug(heading: str) -> str:
    normalized = heading.strip().lower()
    normalized = re.sub(r"[^a-z0-9 _-]", "", normalized)
    return re.sub(r"[ _]+", "-", normalized).strip("-")


def _validate_lexical_link_components(root: Path, lexical_target: Path) -> None:
    """Validate currently-existing lexical components before resolution."""
    root_stat = root.lstat()
    assert stat.S_ISDIR(root_stat.st_mode) and not stat.S_ISLNK(root_stat.st_mode)
    try:
        relative = lexical_target.relative_to(root)
    except ValueError as error:
        raise AssertionError(
            f"path escapes project root before resolution: {lexical_target}"
        ) from error
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            component_stat = current.lstat()
        except FileNotFoundError:
            continue
        assert not stat.S_ISLNK(component_stat.st_mode), f"symlink component: {current}"
        if index == len(relative.parts) - 1:
            assert stat.S_ISREG(component_stat.st_mode), (
                f"non-regular final component: {current}"
            )
        else:
            assert stat.S_ISDIR(component_stat.st_mode), (
                f"non-directory intermediate component: {current}"
            )


def _validate_links(
    path: Path, markdown: str, project_root: Path = PROJECT_ROOT
) -> None:
    root = project_root.absolute()
    targets = _markdown_targets(markdown)
    assert targets, f"{path.name} must contain a Markdown schema link"
    for target in targets:
        decoded = unquote(target)
        assert not any(ord(character) < 32 for character in decoded), target
        assert "\\" not in decoded, target
        parsed = urlsplit(decoded)
        assert not parsed.scheme, (
            target
        )  # This self-contained pack has no external links.
        assert not parsed.username and not parsed.password, target
        assert not decoded.startswith("/"), target
        link_path, separator, fragment = decoded.partition("#")
        assert link_path, target
        lexical_target = (path.parent / link_path).absolute()
        _validate_lexical_link_components(root, lexical_target)
        try:
            target_path = lexical_target.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise AssertionError(f"unresolved local target: {target}") from error
        assert target_path.is_relative_to(root.resolve()), target
        target_stat = target_path.stat()
        assert stat.S_ISREG(target_stat.st_mode), target
        if separator:
            target_text = target_path.read_text(encoding="utf-8")
            headings = {_slug(value) for value in HEADING.findall(target_text)}
            assert fragment in headings, target


def _reject_fifo_consumption(monkeypatch: pytest.MonkeyPatch, fifo: Path) -> None:
    original_resolve = Path.resolve
    original_stat = Path.stat
    original_read_text = Path.read_text

    def contains_fifo(candidate: Path) -> bool:
        normalized = Path(os.path.normpath(candidate))
        return normalized == fifo or fifo in normalized.parents

    def reject_fifo_resolve(candidate: Path, strict: bool = False) -> Path:
        if contains_fifo(candidate):
            raise AssertionError("FIFO must be rejected before stat, open, or resolve")
        return original_resolve(candidate, strict=strict)

    def reject_fifo_stat(
        candidate: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        if follow_symlinks and contains_fifo(candidate):
            raise AssertionError("FIFO must be rejected before stat, open, or resolve")
        return original_stat(candidate, follow_symlinks=follow_symlinks)

    def reject_fifo_read_text(
        candidate: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        if contains_fifo(candidate):
            raise AssertionError("FIFO must be rejected before stat, open, or resolve")
        return original_read_text(candidate, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "resolve", reject_fifo_resolve)
    monkeypatch.setattr(Path, "stat", reject_fifo_stat)
    monkeypatch.setattr(Path, "read_text", reject_fifo_read_text)


def test_every_prompt_link_is_local_resolved_and_safe() -> None:
    paths = sorted(PROMPTS_DIR.glob("*.md"))
    assert {path.name for path in paths} == EXPECTED_PROMPTS
    for path in paths:
        _validate_links(path, path.read_text(encoding="utf-8"))


def test_link_parser_ignores_code_spans_and_captures_all_markdown_link_forms() -> None:
    source = (
        "`[not a link](/outside.md)`\n"
        "```md\n![not an image](/also-outside.md)\n```\n"
        "[local](../contracts/v1/evidence-record.schema.json)\n"
        "![image](unsafe-image.md)\n"
        "<unsafe-autolink.md>\n"
        "[reference]: unsafe-reference.md\n"
        "[use][reference]\n"
    )
    assert _markdown_targets(source) == [
        "../contracts/v1/evidence-record.schema.json",
        "unsafe-image.md",
        "unsafe-autolink.md",
        "unsafe-reference.md",
    ]


def test_link_validation_rejects_images_autolinks_and_escape_mutations() -> None:
    prompt = PROMPTS_DIR / "detection-notice.md"
    valid = prompt.read_text(encoding="utf-8")
    _validate_links(prompt, valid)
    for bad_markdown in (
        "![image](/outside.md)",
        "<../outside.md>",
        "[bad](file:///outside.md)",
        "[bad](https://example.test/contract.json)",
        "[bad](https://user:password@example.test/contract.json)",
        "[bad](..\\contracts\\v1\\evidence-record.schema.json)",
        "[bad](../contracts/v1/evidence-record.schema.json%00)",
        "[bad](../contracts/v1/evidence-record.schema.json#no-such-heading)",
    ):
        with pytest.raises(AssertionError):
            _validate_links(prompt, f"{valid}\n{bad_markdown}\n")


def test_link_validation_rejects_symlinks_before_resolution(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir()
    target = contract_dir / "safe.json"
    target.write_text("# Contract\n", encoding="utf-8")
    prompt = prompt_dir / "notice.md"
    prompt.write_text("[safe](../contracts/safe.json#contract)\n", encoding="utf-8")
    _validate_links(prompt, prompt.read_text(encoding="utf-8"), root)
    link = contract_dir / "linked.json"
    os.symlink(target, link)
    with pytest.raises(AssertionError, match="symlink component"):
        _validate_links(prompt, "[bad](../contracts/linked.json)\n", root)
    nested = contract_dir / "nested"
    os.symlink(contract_dir, nested)
    with pytest.raises(AssertionError, match="symlink component"):
        _validate_links(prompt, "[bad](../contracts/nested/safe.json)\n", root)


def test_link_validation_rejects_fifo_leaf_before_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable")
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir()
    fifo = contract_dir / "target.fifo"
    os.mkfifo(fifo)
    prompt = prompt_dir / "notice.md"

    _reject_fifo_consumption(monkeypatch, fifo)
    with pytest.raises(AssertionError, match="non-regular final component"):
        _validate_links(prompt, "[bad](../contracts/target.fifo)\n", root)


def test_link_validation_rejects_fifo_intermediate_before_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable")
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir()
    fifo = contract_dir / "nested.fifo"
    os.mkfifo(fifo)
    prompt = prompt_dir / "notice.md"

    _reject_fifo_consumption(monkeypatch, fifo)
    with pytest.raises(AssertionError, match="non-directory intermediate component"):
        _validate_links(prompt, "[bad](../contracts/nested.fifo/child.json)\n", root)


def test_link_validation_accepts_regular_leaf_through_regular_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts" / "nested"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir(parents=True)
    target = contract_dir / "safe.json"
    target.write_text("# Contract\n", encoding="utf-8")
    prompt = prompt_dir / "notice.md"

    _validate_links(prompt, "[safe](../contracts/nested/safe.json#contract)\n", root)
