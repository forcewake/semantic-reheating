from __future__ import annotations

import os
import re
import stat
import string
import sys
import time
from collections import Counter
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
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MAX_PROMPT_BYTES = 64 * 1024
_PUNCTUATION = frozenset(string.punctuation)


def _malformed(detail: str) -> AssertionError:
    return AssertionError(f"malformed_markdown_link: {detail}")


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _mask_range(characters: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if characters[index] not in "\r\n":
            characters[index] = " "


def _mask_code(markdown: str) -> str:
    """Mask code while retaining offsets and line boundaries for the scanner."""
    characters = list(markdown)
    line_start = 0
    open_fence: tuple[str, int, int] | None = None
    while line_start < len(markdown):
        line_end = markdown.find("\n", line_start)
        if line_end == -1:
            line_end = len(markdown)
        line = markdown[line_start:line_end]
        leading = len(line) - len(line.lstrip(" "))
        marker_start = line_start + leading
        if (
            leading <= 3
            and marker_start < line_end
            and not _is_escaped(markdown, marker_start)
        ):
            marker = markdown[marker_start]
            run_end = marker_start
            while run_end < line_end and markdown[run_end] == marker:
                run_end += 1
            run_length = run_end - marker_start
            if marker in "`~" and run_length >= 3:
                if open_fence is None:
                    open_fence = (marker, run_length, line_start)
                elif (
                    marker == open_fence[0]
                    and run_length >= open_fence[1]
                    and not line[leading + run_length :].strip(" ")
                ):
                    _mask_range(characters, open_fence[2], line_end)
                    open_fence = None
        line_start = line_end + 1
    if open_fence is not None:
        raise _malformed("unclosed fenced code block")

    index = 0
    while index < len(markdown):
        if characters[index] != "`" or _is_escaped(markdown, index):
            index += 1
            continue
        end = index
        while end < len(markdown) and characters[end] == "`":
            end += 1
        run_length = end - index
        closing = markdown.find("`" * run_length, end)
        while closing != -1:
            closing_end = closing + run_length
            if (closing == 0 or markdown[closing - 1] != "`") and (
                closing_end == len(markdown) or markdown[closing_end] != "`"
            ):
                break
            closing = markdown.find("`" * run_length, closing + 1)
        if closing == -1:
            raise _malformed("unclosed inline code span")
        _mask_range(characters, index, closing + run_length)
        index = closing + run_length
    return "".join(characters)


def _parse_bracket(text: str, start: int) -> tuple[int, str]:
    """Return the exact end and content for an escape-aware bracket group."""
    assert text[start] == "["
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            if index + 1 < len(text) and text[index + 1] == "[":
                depth += 1
            index += 2
            continue
        if character == "\n":
            raise _malformed("newline in bracket label")
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index + 1, text[start + 1 : index]
        index += 1
    raise _malformed("unclosed bracket label")


def _reference_label(label: str) -> str:
    normalized: list[str] = []
    index = 0
    while index < len(label):
        if (
            index + 1 < len(label)
            and label[index] == "\\"
            and label[index + 1] in _PUNCTUATION
        ):
            normalized.append(label[index + 1])
            index += 2
        else:
            normalized.append(label[index])
            index += 1
    unescaped = "".join(normalized)
    return re.sub(r"[ \t\n\r\f\v]+", " ", unescaped).strip().casefold()


def _parse_bare_destination(text: str, start: int, limit: int) -> tuple[int, str]:
    index = start
    depth = 0
    while index < limit:
        character = text[index]
        if character == "\\":
            if index + 1 >= limit:
                raise _malformed("trailing destination escape")
            index += 2
            continue
        if character in " \t\r\n":
            break
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                break
            depth -= 1
        index += 1
    if index == start or depth:
        raise _malformed("unbalanced destination")
    return index, text[start:index]


def _parse_parenthesized_destination(text: str, start: int) -> tuple[int, str]:
    assert text[start] == "("
    index = start + 1
    while index < len(text) and text[index] in " \t":
        index += 1
    if index >= len(text):
        raise _malformed("unclosed inline destination")
    if text[index] == "<":
        destination_start = index + 1
        index = destination_start
        while index < len(text) and text[index] != ">":
            if text[index] == "\\":
                index += 2
            else:
                index += 1
        if index >= len(text) or index == destination_start:
            raise _malformed("malformed angle destination")
        destination = text[destination_start:index]
        index += 1
    else:
        index, destination = _parse_bare_destination(text, index, len(text))
    while index < len(text) and text[index] in " \t":
        index += 1
    if index >= len(text) or text[index] != ")":
        raise _malformed("unsupported inline destination tail")
    return index + 1, destination


def _parse_reference_destination(text: str, start: int, limit: int) -> tuple[int, str]:
    while start < limit and text[start] in " \t":
        start += 1
    if start >= limit:
        raise _malformed("missing reference destination")
    if text[start] == "<":
        end = text.find(">", start + 1, limit)
        if end == -1 or end == start + 1:
            raise _malformed("malformed reference angle destination")
        return end + 1, text[start + 1 : end]
    return _parse_bare_destination(text, start, limit)


def _consume_opaque_html_construct(text: str, start: int) -> int | None:
    """Return the exclusive end of a bounded opaque HTML construct."""
    assert text[start] == "<"
    if text.startswith("<!--", start):
        end = text.find("-->", start + 4)
        if end == -1:
            raise _malformed("unclosed HTML comment")
        return end + 3
    if text.startswith("<?", start):
        end = text.find("?>", start + 2)
        if end == -1:
            raise _malformed("unclosed HTML instruction")
        return end + 2
    if text.startswith("<![CDATA[", start):
        end = text.find("]]>", start + 9)
        if end == -1:
            raise _malformed("unclosed CDATA section")
        return end + 3
    if not text.startswith("<!", start):
        return None

    quote: str | None = None
    depth = 1
    index = start + 2
    limit = min(len(text), start + _MAX_PROMPT_BYTES)
    while index < limit:
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise _malformed("unclosed HTML declaration")


def _find_angle_end(text: str, start: int) -> int:
    """Consume one generic angle construct without retrying nested starts."""
    assert text[start] == "<"
    quote: str | None = None
    index = start + 1
    limit = min(len(text), start + _MAX_PROMPT_BYTES)
    while index < limit:
        character = text[index]
        if quote is not None:
            if character == quote and not _is_escaped(text, index):
                quote = None
        elif character in "\"'":
            quote = character
        elif character == "<" and not _is_escaped(text, index):
            raise _malformed("nested generic angle construct")
        elif character == ">":
            return index + 1
        index += 1
    raise _malformed("unclosed generic angle construct")


def _mask_opaque_html(text: str) -> str:
    """Mask opaque HTML constructs and recognized tags before link parsing."""
    characters = list(text)
    index = 0
    while index < len(text):
        if text[index] == "<" and not _is_escaped(text, index):
            end = _consume_opaque_html_construct(text, index)
            if end is None:
                end = _find_angle_end(text, index)
                if end is None or not _looks_like_html_tag(text[index + 1 : end - 1]):
                    index += 1
                    continue
            _mask_range(characters, index, end)
            index = end
            continue
        index += 1
    return "".join(characters)


def _looks_like_html_tag(candidate: str) -> bool:
    """Recognize bounded HTML-like angle syntax without swallowing paths."""
    if candidate.startswith(("!", "?")):
        return True

    index = 1 if candidate.startswith("/") else 0
    if index >= len(candidate) or candidate[index] not in string.ascii_letters:
        return False
    index += 1
    while (
        index < len(candidate)
        and candidate[index] in string.ascii_letters + string.digits + "-"
    ):
        index += 1

    if index == len(candidate) or candidate[index] == "/":
        return index == len(candidate) or index + 1 == len(candidate)
    if candidate[index] not in " \t\r\n":
        return False

    while index < len(candidate):
        while index < len(candidate) and candidate[index] in " \t\r\n":
            index += 1
        if index == len(candidate):
            return True
        if candidate[index] == "/":
            return index + 1 == len(candidate)

        attribute_start = index
        while (
            index < len(candidate)
            and candidate[index] in string.ascii_letters + string.digits + "-_:."
        ):
            index += 1
        if index == attribute_start:
            return False
        if index == len(candidate) or candidate[index] in " \t\r\n/":
            continue
        if candidate[index] != "=":
            return False

        index += 1
        if index == len(candidate):
            return False
        if candidate[index] in "\"'":
            quote = candidate[index]
            index += 1
            closing = candidate.find(quote, index)
            if closing == -1:
                return False
            index = closing + 1
        else:
            value_start = index
            while index < len(candidate) and candidate[index] not in " \t\r\n<>\"'":
                index += 1
            if index == value_start:
                return False

    return True


def _mask_definitions(visible: str) -> tuple[str, dict[str, str]]:
    characters = list(visible)
    definitions: dict[str, str] = {}
    line_start = 0
    while line_start < len(visible):
        line_end = visible.find("\n", line_start)
        if line_end == -1:
            line_end = len(visible)
        index = line_start
        while index < line_end and visible[index] == " " and index - line_start < 3:
            index += 1
        if (
            index < line_end
            and visible[index] == "["
            and not _is_escaped(visible, index)
        ):
            label_end, raw_label = _parse_bracket(visible, index)
            if label_end < line_end and visible[label_end] == ":":
                destination_end, destination = _parse_reference_destination(
                    visible, label_end + 1, line_end
                )
                if visible[destination_end:line_end].strip(" \t\r"):
                    raise _malformed("unsupported reference definition tail")
                label = _reference_label(raw_label)
                assert label, "malformed_markdown_link: empty reference label"
                assert label not in definitions, (
                    f"duplicate reference definition: {label!r}"
                )
                definitions[label] = destination
                _mask_range(characters, line_start, line_end)
        line_start = line_end + 1
    return "".join(characters), definitions


def _markdown_targets(markdown: str) -> list[str]:
    """Extract local targets with a bounded, escape-aware Markdown scanner."""
    assert len(markdown.encode("utf-8")) <= _MAX_PROMPT_BYTES, "prompt exceeds 64KiB"
    visible, definitions = _mask_definitions(_mask_opaque_html(_mask_code(markdown)))
    targets: list[str] = []
    index = 0
    while index < len(visible):
        character = visible[index]
        if character == "\\":
            index += 2
            continue
        image = (
            character == "!" and index + 1 < len(visible) and visible[index + 1] == "["
        )
        if character == "[" or image:
            start = index
            bracket_start = index + 1 if image else index
            label_end, raw_label = _parse_bracket(visible, bracket_start)
            if label_end < len(visible) and visible[label_end] == "(":
                index, destination = _parse_parenthesized_destination(
                    visible, label_end
                )
                targets.append(destination)
                continue
            if label_end < len(visible) and visible[label_end] == "[":
                reference_end, reference_label = _parse_bracket(visible, label_end)
                raw_reference = reference_label or raw_label
                index = reference_end
            else:
                raw_reference = raw_label
                index = label_end
            label = _reference_label(raw_reference)
            assert label, "malformed_markdown_link: empty reference label"
            assert label in definitions, (
                f"unresolved reference usage: {visible[start:index]!r}"
            )
            targets.append(definitions[label])
            continue
        if character == "]":
            raise _malformed("stray closing bracket")
        if character == "<":
            opaque_end = _consume_opaque_html_construct(visible, index)
            if opaque_end is not None:
                index = opaque_end
                continue
            end = _find_angle_end(visible, index)
            if end is not None:
                candidate = visible[index + 1 : end - 1]
                if _looks_like_html_tag(candidate):
                    index = end
                    continue
                parsed = urlsplit(candidate)
                if (
                    candidate
                    and not any(value in candidate for value in " \t\r\n=<")
                    and (
                        parsed.scheme
                        or candidate.startswith(("/", "."))
                        or "/" in candidate
                        or "." in candidate
                        or "#" in candidate
                    )
                ):
                    targets.append(candidate)
                    index = end
                    continue
        index += 1
    # Definitions are destinations too. Preserve them even when unused, so a
    # hidden unsafe destination cannot be silently ignored.
    targets.extend(definitions.values())
    return list(dict.fromkeys(targets))


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
            heading_counts = Counter(
                _slug(value) for value in HEADING.findall(target_text)
            )
            if heading_counts[fragment] == 0:
                raise AssertionError(f"missing fragment: {fragment!r}")
            if heading_counts[fragment] != 1:
                raise AssertionError(f"ambiguous fragment: {fragment!r}")


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


def test_link_parser_keeps_escaped_brackets_in_an_inline_label() -> None:
    assert _markdown_targets(
        "[" + "\\" + "[label]](../contracts/v1/evidence-record.schema.json)"
    ) == ["../contracts/v1/evidence-record.schema.json"]


def test_link_parser_keeps_nested_brackets_in_an_inline_label() -> None:
    assert _markdown_targets(
        "[one [two]](../contracts/v1/evidence-record.schema.json)"
    ) == ["../contracts/v1/evidence-record.schema.json"]


def test_link_parser_keeps_balanced_parentheses_in_destination() -> None:
    assert _markdown_targets(
        "[label](../contracts/v1/evidence-record.schema.json?x=(y))"
    ) == ["../contracts/v1/evidence-record.schema.json?x=(y)"]


def test_link_parser_rejects_an_unclosed_bracket() -> None:
    with pytest.raises(AssertionError, match="malformed_markdown_link"):
        _markdown_targets("before [unclosed after")


def test_link_parser_ignores_tilde_fenced_code_blocks() -> None:
    assert _markdown_targets("~~~\n[not-a-link](/outside.md)\n~~~\n") == []


def test_link_parser_resolves_nested_and_escaped_reference_labels_for_images() -> None:
    source = (
        "[ ID "
        + "\\"
        + "[Part]]: ../contracts/v1/evidence-record.schema.json\n"
        + "![alt][id [part]] [ ID [PART] ][] [id [part]]\n"
    )
    assert _markdown_targets(source) == ["../contracts/v1/evidence-record.schema.json"]


def test_link_parser_rejects_unbalanced_destinations_and_stray_closings() -> None:
    for source in (
        "[label](../contracts/v1/evidence-record.schema.json?x=(y)",
        "before ] after",
    ):
        with pytest.raises(AssertionError, match="malformed_markdown_link"):
            _markdown_targets(source)


def test_link_parser_distinguishes_html_tags_from_angle_targets() -> None:
    source = (
        "<br/>\n"
        "<br />\n"
        '<img src="x"/>\n'
        "<custom-element/>\n"
        "<span class=note>not a link</span>\n"
        "</span>\n"
        "<!-- comment -->\n"
        "<!DOCTYPE html>\n"
        "<?instruction?>\n"
        "<../contracts/v1/evidence-record.schema.json>\n"
        "<./relative.md>\n"
        "<contracts/v1/x.json>\n"
        "<https://example.invalid/x>"
    )
    assert _markdown_targets(source) == [
        "../contracts/v1/evidence-record.schema.json",
        "./relative.md",
        "contracts/v1/x.json",
        "https://example.invalid/x",
    ]


@pytest.mark.parametrize("source", ("<unterminated", "<unterminated <>"))
def test_link_parser_rejects_malformed_angle_input_deterministically(
    source: str,
) -> None:
    with pytest.raises(AssertionError, match="malformed_markdown_link"):
        _markdown_targets(source)


def test_link_parser_skips_escaped_angle_literals_without_rescanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    literal_count = (32 * 1024 - 2) // len(r"\<")
    source = "<" + r"\<" * literal_count + ">"
    assert len(source.encode("utf-8")) == 32 * 1024
    assert set(source) <= set(string.printable)

    calls = 0
    original = _find_angle_end

    def counted_angle_end(text: str, start: int) -> int:
        nonlocal calls
        calls += 1
        return original(text, start)

    monkeypatch.setattr(sys.modules[__name__], "_find_angle_end", counted_angle_end)
    started = time.perf_counter()
    assert _mask_opaque_html(source) == source
    elapsed = time.perf_counter() - started
    print(f"escaped-angle mask probe: calls={calls}, seconds={elapsed:.6f}")
    assert calls <= 1

    monkeypatch.setattr(sys.modules[__name__], "_find_angle_end", original)
    assert _markdown_targets(source) == []


def test_link_parser_preserves_escaped_angle_and_comment_literals() -> None:
    local = "../contracts/v1/evidence-record.schema.json"
    escaped_comment = r"\<!-- " + f"<{local}>" + " -->"
    mixed = r"\<literal> " + escaped_comment + f" <{local}>"

    assert _is_escaped(r"\<", 1)
    assert not _is_escaped(r"\\<../contracts/v1/evidence-record.schema.json>", 2)
    assert _mask_opaque_html(escaped_comment) == escaped_comment
    assert _markdown_targets(escaped_comment) == [local]
    assert _markdown_targets(mixed) == [local]
    assert _markdown_targets(r"\\<../contracts/v1/evidence-record.schema.json>") == [
        local
    ]


def test_link_parser_stops_after_one_generic_scan_for_malformed_nested_angle_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = _find_angle_end

    def counted_angle_end(text: str, start: int) -> int:
        nonlocal calls
        calls += 1
        return original(text, start)

    monkeypatch.setattr(sys.modules[__name__], "_find_angle_end", counted_angle_end)
    with pytest.raises(AssertionError, match="malformed_markdown_link"):
        _markdown_targets("<a<a>")
    assert calls <= 1


@pytest.mark.parametrize(
    "source",
    (
        "<" * 65535 + ">",
        "<a" * 32767 + "<>",
    ),
    ids=("run", "viable-looking-nested-starts"),
)
def test_link_parser_rejects_64kib_malformed_nested_angles_after_one_scan(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    calls = 0
    original = _find_angle_end

    def counted_angle_end(text: str, start: int) -> int:
        nonlocal calls
        calls += 1
        return original(text, start)

    monkeypatch.setattr(sys.modules[__name__], "_find_angle_end", counted_angle_end)
    with pytest.raises(AssertionError, match="malformed_markdown_link"):
        _markdown_targets(source)
    assert calls == 1


def test_link_parser_scans_many_short_valid_angle_constructs_linearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = _find_angle_end
    source = "<br/>" * (60 * 1024 // len("<br/>"))

    def counted_angle_end(text: str, start: int) -> int:
        nonlocal calls
        calls += 1
        return original(text, start)

    monkeypatch.setattr(sys.modules[__name__], "_find_angle_end", counted_angle_end)
    assert _markdown_targets(source) == []
    assert calls == source.count("<")


@pytest.mark.parametrize(
    "source",
    (
        "<!-- <file:///etc/passwd> -->",
        "<!DOCTYPE <file:///etc/passwd>>",
        "<?instruction <file:///etc/passwd>?>",
    ),
)
def test_link_parser_skips_opaque_html_constructs_without_reparsing_contents(
    source: str,
) -> None:
    assert _markdown_targets(source) == []


def test_link_parser_skips_opaque_html_comments_cdata_and_tags() -> None:
    local = "../contracts/v1/evidence-record.schema.json"
    source = (
        f"<!-- [ordinary]({local}) <{local}> -->\n"
        f"<!-- multiline\n[comment]({local})\n<{local}>\n-->\n"
        f"<![CDATA[[cdata]({local}) <{local}>]]>\n"
        f'<span title=">" data-link="[nested]({local}) <{local}>">'
    )
    assert _markdown_targets(source) == []


@pytest.mark.parametrize(
    "source",
    (
        "<!-- <file:///etc/passwd>",
        "<?instruction <file:///etc/passwd>",
        "<!DOCTYPE <file:///etc/passwd>",
    ),
)
def test_link_parser_rejects_unterminated_opaque_html_constructs(source: str) -> None:
    with pytest.raises(AssertionError, match="malformed_markdown_link"):
        _markdown_targets(source)


def test_current_prompt_assets_have_the_exact_seven_schema_targets() -> None:
    targets: list[str] = []
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        _validate_links(path, markdown)
        targets.extend(_markdown_targets(markdown))
    assert targets == [
        "../contracts/v1/recovery-instruction.schema.json",
        "../contracts/v1/decision-envelope.schema.json",
        "../contracts/v1/decision-envelope.schema.json",
        "../contracts/v1/recovery-instruction.schema.json",
        "../contracts/v1/recovery-instruction.schema.json",
        "../contracts/v1/recovery-outcome.schema.json",
        "../contracts/v1/evidence-record.schema.json",
    ]
    assert len(targets) == 7


def test_link_parser_resolves_full_collapsed_shortcut_and_image_references() -> None:
    source = (
        "[ Schema   ID ]: ../contracts/v1/evidence-record.schema.json\n"
        "[full][schema id] ![image][SCHEMA ID]\n"
        "[Schema ID][] ![Schema ID][]\n"
        "[schema id] ![schema id]\n"
    )
    assert _markdown_targets(source) == ["../contracts/v1/evidence-record.schema.json"]


def test_link_parser_rejects_missing_or_ambiguous_reference_labels() -> None:
    for source, reviewer_string in (
        ("[broken][missing]", "unresolved reference usage: '[broken][missing]'"),
        ("[broken][]", "unresolved reference usage: '[broken][]'"),
        ("[missing]", "unresolved reference usage: '[missing]'"),
        (
            "[duplicate]: safe.md\n[ DUPLICATE ]: other.md",
            "duplicate reference definition: 'duplicate'",
        ),
    ):
        with pytest.raises(AssertionError, match=re.escape(reviewer_string)):
            _markdown_targets(source)


def test_link_parser_does_not_reparse_definitions_or_inline_links_as_shortcuts() -> (
    None
):
    source = (
        "[definition]: ../contracts/v1/evidence-record.schema.json\n"
        "[inline](../contracts/v1/evidence-record.schema.json)\n"
        "![image](../contracts/v1/evidence-record.schema.json)\n"
        "`[missing]` and " + "\\" + "[ignored" + "\\" + "]\n"
        "```md\n[also missing][]\n```\n"
        "[definition][]\n"
    )
    assert _markdown_targets(source) == ["../contracts/v1/evidence-record.schema.json"]


def test_link_validation_rejects_unresolved_full_collapsed_and_shortcut_references() -> (
    None
):
    prompt = PROMPTS_DIR / "detection-notice.md"
    valid = prompt.read_text(encoding="utf-8")
    for usage, reviewer_string in (
        ("[broken][missing]", "unresolved reference usage: '[broken][missing]'"),
        ("[broken][]", "unresolved reference usage: '[broken][]'"),
        ("[missing]", "unresolved reference usage: '[missing]'"),
    ):
        with pytest.raises(AssertionError, match=re.escape(reviewer_string)):
            _validate_links(prompt, f"{valid}\n{usage}\n")


def test_link_validation_resolves_collapsed_and_shortcut_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir()
    target = contract_dir / "safe.json"
    target.write_text("# Contract\n", encoding="utf-8")
    prompt = prompt_dir / "notice.md"
    markdown = (
        "[ Safe   ID ]: ../contracts/safe.json#contract\n"
        "[full][safe id] [safe id][] [Safe ID]\n"
        "![alt][SAFE ID] ![Safe ID][] ![safe id]\n"
    )
    _validate_links(prompt, markdown, root)


def test_link_validation_accepts_unique_and_rejects_ambiguous_or_missing_fragments(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pack"
    prompt_dir = root / "prompts"
    contract_dir = root / "contracts"
    prompt_dir.mkdir(parents=True)
    contract_dir.mkdir()
    target = contract_dir / "safe.md"
    target.write_text("# Unique heading\n# Duplicate\n# duplicate\n", encoding="utf-8")
    prompt = prompt_dir / "notice.md"

    _validate_links(prompt, "[safe](../contracts/safe.md#unique-heading)\n", root)
    with pytest.raises(AssertionError, match="ambiguous fragment"):
        _validate_links(prompt, "[bad](../contracts/safe.md#duplicate)\n", root)
    with pytest.raises(AssertionError, match="missing fragment"):
        _validate_links(prompt, "[bad](../contracts/safe.md#absent)\n", root)


def test_link_validation_rejects_images_autolinks_and_escape_mutations() -> None:
    prompt = PROMPTS_DIR / "detection-notice.md"
    valid = prompt.read_text(encoding="utf-8")
    _validate_links(prompt, valid)
    for bad_markdown in (
        "![image](/outside.md)",
        "<../outside.md>",
        "<https://example.invalid/x>",
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
