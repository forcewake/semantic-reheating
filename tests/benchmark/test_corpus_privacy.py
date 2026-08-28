"""Public-hygiene tests for the committed synthetic benchmark assets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from semantic_reheating.validation import load_public_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "benchmark/scenarios/manifest.json"
SCHEMA_PATH = PROJECT_ROOT / "benchmark/schemas/v1/corpus-manifest.schema.json"

_FORBIDDEN_TEXT = re.compile(
    r"(?:\.hermes|/home/|/Users/|(?:^|[\s\"'])[A-Za-z]:\\|\\\\[A-Za-z0-9_.-]+\\|"
    r"(?:api[_-]?key|secret|token|private[_-]?key|password)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:^|[^@\w])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?:\+?\d[\d .()/-]{7,}\d)|"
    r"\b(?:epam|internal_monologue|chain_of_thought|hidden_reasoning|scratchpad|thoughts)\b|"
    r"\bchain of thought\b|\bhidden reasoning\b|\binternal monologue\b)",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = re.compile(
    r"(?:chain_of_thought|hidden_reasoning|scratchpad|internal_monologue|thoughts|(?:api[_-]?key|secret|token|private[_-]?key|password))",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://", re.IGNORECASE)
_SCHEMA_URL_POINTERS = frozenset(("$.$id", "$.$schema"))


def assert_public_json(
    value: Any,
    location: str = "$",
    *,
    pointer: str = "$",
    allow_schema_urls: bool = False,
) -> None:
    if type(value) is dict:
        for key, item in value.items():
            assert type(key) is str
            assert _FORBIDDEN_KEYS.fullmatch(key) is None, location
            assert_public_json(
                item,
                f"{location}.{key}",
                pointer=f"{pointer}.{key}",
                allow_schema_urls=allow_schema_urls,
            )
    elif type(value) is list:
        for index, item in enumerate(value):
            assert_public_json(
                item,
                f"{location}[{index}]",
                pointer=f"{pointer}[{index}]",
                allow_schema_urls=allow_schema_urls,
            )
    elif type(value) is str:
        assert _FORBIDDEN_TEXT.search(value) is None, location
        assert _URL.search(value) is None or (
            allow_schema_urls and pointer in _SCHEMA_URL_POINTERS
        ), location


def _public_paths() -> list[Path]:
    manifest = load_public_json(MANIFEST_PATH.read_bytes())
    assert type(manifest) is dict
    return [
        SCHEMA_PATH,
        MANIFEST_PATH,
        *(PROJECT_ROOT / entry["trace_path"] for entry in manifest["entries"]),
    ]


def _assert_public_bytes(path: Path, raw: bytes) -> None:
    assert raw.startswith(b"\xef\xbb\xbf") is False
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert _FORBIDDEN_TEXT.search(text) is None, str(path)


def test_public_corpus_bytes_and_json_values_are_redacted_and_deterministic() -> None:
    paths = _public_paths()
    assert len(paths) == 31
    assert set(paths) == {
        path for path in (PROJECT_ROOT / "benchmark").rglob("*") if path.is_file()
    }
    for path in paths:
        raw = path.read_bytes()
        _assert_public_bytes(path, raw)
        if path.suffix == ".jsonl":
            lines = raw.splitlines()
            assert lines and all(line.strip() for line in lines)
            for line in lines:
                assert_public_json(load_public_json(line), str(path))
        else:
            assert_public_json(
                load_public_json(raw), str(path), allow_schema_urls=path == SCHEMA_PATH
            )


@pytest.mark.parametrize(
    "value",
    [
        {"path": "/home/example/item"},
        {"path": ".hermes/session"},
        {"api_key": "redacted"},
        {"note": "token=redacted"},
        {"endpoint": "https://example.test"},
        {"contact": "person@example.test"},
        {"scratchpad": "private draft"},
    ],
)
def test_public_hygiene_helper_rejects_representative_mutations(
    value: dict[str, str],
) -> None:
    with pytest.raises(AssertionError):
        assert_public_json(value)


def test_public_hygiene_helper_allows_legitimate_budget_tokens() -> None:
    assert_public_json({"budget_counters": {"tokens": 1}})
