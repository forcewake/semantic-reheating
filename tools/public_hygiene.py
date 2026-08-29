"""Fail-closed public repository hygiene scanner; never prints matched secret values."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.domain_json_registry import (
    REGISTRY,
    RegistryError,
    discover_domain_json,
    validate_registry,
)


class HygieneError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    path: str
    message: str


_SECRET = re.compile(
    r"(?i)(?:ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|sk-[a-z0-9]{20,}|aws.{0,20}?(?:secret|access).{0,10}[=:])"  # hygiene: scanner-pattern
)
_USER_PATH = re.compile(
    r"(?:/home/[A-Za-z0-9._-]+(?:/|\\b)|/Users/[A-Za-z0-9._-]+(?:/|\\b)|[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._-]+(?:\\\\|\\b))"  # hygiene: scanner-pattern
)
_PRIVATE = re.compile(
    r"(?i)(?:raw[ _-]?reasoning|private[ _-]?(?:transcript|trace|prompt|content)|internal[ _-]?orchestration)"  # hygiene: scanner-pattern
)
_LINK = re.compile(r"(?<!!)\[[^]]*\]\(([^)#?]+)(?:#[^)]*)?\)")
_ASSET_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp"}


def _run(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise HygieneError(f"git {' '.join(args)} failed")
    return completed.stdout


def _tracked_paths(root: Path) -> list[str]:
    return [path for path in _run(root, "ls-files", "-z").split("\0") if path]


_POLICY_PATH = frozenset({"SECURITY.md", "CONTRIBUTING.md"})
_POLICY_WORDING = re.compile(
    r"(?i)\b(?:do not|don't|never|must not|forbidden|prohibited|reject(?:ed)?|avoid|no|detects|publishing|not a place)\b"
)
_SCANNER_PATTERN_FRAGMENT = "# hygiene: scanner-pattern"
_TEST_RUNTIME_FRAGMENT = "# hygiene: test-runtime-fragment"
_KNOWN_TEST_RUNTIME_PATHS = {
    ("tests/benchmark/test_corpus_privacy.py", "/home/" + "example/item"),
    ("tests/live/test_campaign_executor.py", "/home/" + "operator/raw-transcript"),
}
_PUBLIC_PRIVATE_FIELD = f"{'private'}_{'transcript'}_{'receipt'}"
_PUBLIC_POLICY_CLASS = re.compile(
    r"(?i)\b" + "private" + r" transcript evidence closed\b"
)


def _matching_line(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:] if end == -1 else text[start:end]


def _is_scanner_pattern_fragment(path: str, line: str) -> bool:
    return path == "tools/public_hygiene.py" and _SCANNER_PATTERN_FRAGMENT in line


def _is_explicit_test_runtime_fragment(path: str, line: str) -> bool:
    return path.startswith("tests/") and _TEST_RUNTIME_FRAGMENT in line


def _is_known_test_runtime_path(path: str, line: str) -> bool:
    return any(
        expected_path == path and expected_value in line
        for expected_path, expected_value in _KNOWN_TEST_RUNTIME_PATHS
    )


def _is_public_private_field(line: str) -> bool:
    return _PUBLIC_PRIVATE_FIELD in line or bool(_PUBLIC_POLICY_CLASS.search(line))


def _find_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    policy_source = path.startswith("docs/") or path in _POLICY_PATH
    if any(
        not _is_scanner_pattern_fragment(path, _matching_line(text, match.start()))
        for match in _SECRET.finditer(text)
    ):
        findings.append(Finding(path, "credential-shaped content"))
    if any(
        not (
            _is_scanner_pattern_fragment(
                path, line := _matching_line(text, match.start())
            )
            or _is_explicit_test_runtime_fragment(path, line)
            or _is_known_test_runtime_path(path, line)
        )
        for match in _USER_PATH.finditer(text)
    ):
        findings.append(Finding(path, "user-specific absolute path"))
    if any(
        not (
            _is_scanner_pattern_fragment(
                path, line := _matching_line(text, match.start())
            )
            or _is_explicit_test_runtime_fragment(path, line)
            or _is_public_private_field(line)
            or (policy_source and _POLICY_WORDING.search(line))
        )
        for match in _PRIVATE.finditer(text)
    ):
        findings.append(Finding(path, "private" + " content marker"))
    return findings


def _relative_link_findings(root: Path, paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in paths:
        if not relative.endswith(".md"):
            continue
        source = root / relative
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(relative, "non-UTF-8 Markdown"))
            continue
        for target in _LINK.findall(text):
            if "://" in target or target.startswith(("/", "mailto:")):
                continue
            resolved = (source.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                findings.append(
                    Finding(relative, "bad relative link escapes repository")
                )
                continue
            if not resolved.exists():
                findings.append(Finding(relative, f"bad relative link: {target}"))
    return findings


def _article_asset_findings(root: Path, paths: Iterable[str]) -> list[Finding]:
    article_root = root / "article" / "semantic-reheating"
    if not article_root.is_dir():
        return []
    tracked = set(paths)
    markdown = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in tracked
        if relative.startswith("article/semantic-reheating/")
        and relative.endswith(".md")
    )
    findings: list[Finding] = []
    for asset in article_root.iterdir():
        relative = asset.relative_to(root).as_posix()
        if (
            asset.is_file()
            and asset.suffix.lower() in _ASSET_SUFFIXES
            and relative in tracked
            and asset.name not in markdown
        ):
            findings.append(Finding(relative, "unused article asset"))
    return findings


def _package_and_frontmatter_findings(
    root: Path, paths: Iterable[str]
) -> list[Finding]:
    findings: list[Finding] = []
    skill = "skills/semantic-reheating/SKILL.md"
    if skill in set(paths):
        content = (root / skill).read_text(encoding="utf-8")
        if (
            not content.startswith("---\n")
            or "\nname:" not in content
            or "\ndescription:" not in content
        ):
            findings.append(Finding(skill, "invalid skill frontmatter"))
    for path in paths:
        if path.endswith("package.json"):
            try:
                json.loads((root / path).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                findings.append(Finding(path, "invalid package metadata"))
    return findings


def _current_findings(root: Path) -> list[Finding]:
    paths = _tracked_paths(root)
    findings: list[Finding] = []
    registered = {entry.path for entry in REGISTRY}
    for relative in paths:
        if relative == ".hermes" or relative.startswith(".hermes/"):
            findings.append(Finding(relative, "forbidden tracked .hermes path"))
        if relative.startswith(("benchmark/live/private/",)) or relative.endswith(
            ".transcript.jsonl"
        ):
            findings.append(
                Finding(relative, "forbidden tracked private artifact path")
            )
        candidate = root / relative
        if candidate.is_file():
            try:
                findings.extend(
                    _find_text(relative, candidate.read_text(encoding="utf-8"))
                )
            except UnicodeDecodeError:
                pass
    undeclared = discover_domain_json(root) - registered
    findings.extend(
        Finding(path, "undeclared public domain JSON") for path in sorted(undeclared)
    )
    findings.extend(_relative_link_findings(root, paths))
    findings.extend(_article_asset_findings(root, paths))
    findings.extend(_package_and_frontmatter_findings(root, paths))
    diff = subprocess.run(
        ["git", "diff", "--check"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode:
        findings.append(Finding("working-tree", "git diff --check failed"))
    if any(path in registered for path in paths):
        try:
            validate_registry(root)
        except RegistryError as error:
            findings.append(
                Finding("domain-json-registry", f"registry validation failed: {error}")
            )
    return findings


def _history_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    objects = _run(root, "rev-list", "--objects", "--all").splitlines()
    for line in objects:
        parts = line.split(maxsplit=1)
        sha = parts[0]
        path = parts[1] if len(parts) == 2 else f"history blob {sha[:12]}"
        if _run(root, "cat-file", "-t", sha).strip() != "blob":
            continue
        raw = subprocess.run(
            ["git", "cat-file", "-p", sha], cwd=root, capture_output=True, check=True
        ).stdout
        text = raw.decode("utf-8", errors="replace")
        findings.extend(_find_text(path, text))
        if path == ".hermes" or path.startswith(
            (".hermes/", "benchmark/live/private/")
        ):
            findings.append(Finding(path, "forbidden artifact path"))
    return findings


def scan_repository(
    root: Path,
    *,
    tracked_only: bool = False,
    history: bool = False,
    raise_on_findings: bool = False,
) -> list[Finding]:
    if tracked_only == history:
        raise HygieneError("choose exactly one of tracked_only or history")
    findings = _current_findings(root) if tracked_only else _history_findings(root)
    if findings and raise_on_findings:
        raise HygieneError(
            "; ".join(f"{item.path}: {item.message}" for item in findings)
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tracked-only", action="store_true")
    mode.add_argument("--history", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    findings = scan_repository(
        args.root.resolve(), tracked_only=args.tracked_only, history=args.history
    )
    if findings:
        for finding in findings:
            print(f"{finding.path}: {finding.message}")
        return 1
    print("public hygiene valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
