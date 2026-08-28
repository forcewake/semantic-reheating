"""Public-hygiene tests for the committed synthetic benchmark assets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from corpus_support import CorpusBudget, read_corpus, read_small_public_file

from semantic_reheating.validation import load_public_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "benchmark/scenarios/manifest.json"
SCHEMA_PATH = PROJECT_ROOT / "benchmark/schemas/v1/corpus-manifest.schema.json"
RESULT_SCHEMA_PATH = PROJECT_ROOT / "benchmark/schemas/v1/replay-result.schema.json"
RESULT_PATH = PROJECT_ROOT / "benchmark/results/deterministic-results.json"

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
_EXACT_HTTPS_URL = re.compile(r"https://[^\s]+", re.IGNORECASE)
_PATH_TRAVERSAL = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_SCHEMA_URL_POINTERS = frozenset(("$.$id", "$.$schema"))
_HASH_BACKED_FINDING_PREFIXES = frozenset(
    (
        "exact-repetition",
        "cycle",
        "repeated-error",
        "unchanged-state",
        "acceptance-stall",
        "budget-burn",
        "hard-budget",
        "repeated-risky-call",
    )
)
_HASH_BACKED_FINDING_ID = re.compile(
    rf"(?:{'|'.join(_HASH_BACKED_FINDING_PREFIXES)})-[0-9a-f]{{64}}$"
)
_DECISION_ID = re.compile(r"decision-[0-9a-f]{24}(?:[0-9a-f]{40})?$")


def _is_safe_hash_identifier(value: str, pointer: str) -> bool:
    """Allow only contract-bound hashes, never identifier-shaped free text."""
    if pointer.endswith(".finding_id"):
        return _HASH_BACKED_FINDING_ID.fullmatch(value) is not None
    if pointer.endswith(".decision_id"):
        return _DECISION_ID.fullmatch(value) is not None
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None and pointer.endswith(
        ("_sha256", ".corpus_revision", ".policy_sha256")
    )


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
            assert key.isascii(), location
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
        assert value.isascii(), location
        assert not value.startswith("/"), location
        assert _PATH_TRAVERSAL.search(value) is None, location
        if _is_safe_hash_identifier(value, pointer):
            return
        assert _FORBIDDEN_TEXT.search(value) is None, location
        if _URL.search(value) is not None:
            assert allow_schema_urls and pointer in _SCHEMA_URL_POINTERS, location
            assert _EXACT_HTTPS_URL.fullmatch(value) is not None, location


def _public_paths() -> list[Path]:
    manifest = load_public_json(read_small_public_file(MANIFEST_PATH))
    assert type(manifest) is dict
    return [
        SCHEMA_PATH,
        RESULT_SCHEMA_PATH,
        MANIFEST_PATH,
        RESULT_PATH,
        *(PROJECT_ROOT / entry["trace_path"] for entry in manifest["entries"]),
    ]


def _assert_public_bytes(path: Path, raw: bytes) -> None:
    assert raw.startswith(b"\xef\xbb\xbf") is False
    assert b"\r" not in raw
    raw.decode("utf-8", errors="strict")
    assert raw.isascii(), str(path)


def test_public_corpus_bytes_and_json_values_are_redacted_and_deterministic() -> None:
    paths = _public_paths()
    corpus = read_corpus(
        (
            str(path.relative_to(PROJECT_ROOT))
            for path in paths
            if path.suffix == ".jsonl"
        ),
        budget=CorpusBudget(),
    )
    traces = {trace.trace_path: trace for trace in corpus.traces}
    assert len(paths) == 33
    assert len(traces) == 29
    assert set(paths) == {
        path
        for path in (PROJECT_ROOT / "benchmark").rglob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    }
    for path in paths:
        if path.suffix == ".jsonl":
            trace = traces[str(path.relative_to(PROJECT_ROOT))]
            assert trace.lines and all(line.strip() for line in trace.lines)
            for line in trace.lines:
                _assert_public_bytes(path, line)
                assert_public_json(load_public_json(line[:-1]), str(path))
        else:
            raw = read_small_public_file(path)
            _assert_public_bytes(path, raw)
            assert_public_json(
                load_public_json(raw),
                str(path),
                allow_schema_urls=path in {SCHEMA_PATH, RESULT_SCHEMA_PATH},
            )


def test_benchmark_source_has_no_private_runtime_strings() -> None:
    for path in (
        PROJECT_ROOT / "benchmark/replay.py",
        PROJECT_ROOT / "benchmark/metrics.py",
        PROJECT_ROOT / "src/semantic_reheating/cli.py",
    ):
        raw = path.read_bytes()
        _assert_public_bytes(path, raw)
        assert _FORBIDDEN_TEXT.search(raw.decode("ascii")) is None


def test_public_hygiene_hash_identifier_exemptions_are_closed_to_pointer_and_prefix() -> (
    None
):
    digest = "a" * 55 + "123456789"
    assert_public_json({"finding_id": f"cycle-{digest}"})
    assert_public_json({"decision_id": f"decision-{digest}"})
    assert_public_json({"corpus_revision": digest})
    assert_public_json({"trace_sha256": digest})

    for value in (
        {"finding_id": f"unknown-{digest}"},
        {"note": f"finding-{digest}"},
        {"note": digest},
        {"note": "finding-password:redacted"},
    ):
        with pytest.raises(AssertionError):
            assert_public_json(value)


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


@pytest.mark.parametrize(
    "value",
    (
        {"path": "/etc/passwd"},
        {"path": "/var/lib/private/item"},
        {"path": "/root/x"},
        {"path": "/tmp/x"},
        {"path": "../private"},
    ),
)
def test_public_hygiene_helper_rejects_absolute_posix_and_traversal_paths(
    value: dict[str, str],
) -> None:
    with pytest.raises(AssertionError):
        assert_public_json(value)


@pytest.mark.parametrize(
    "value",
    (
        {"note": "\u200bsecret"},
        {"\u200ckey": "safe"},
        {"note": "\u200dsecret"},
        {"note": "\u2060secret"},
        {"note": "\ufeffsecret"},
        {"note": "ｓｅｃｒｅｔ"},
    ),
)
def test_public_hygiene_helper_rejects_direct_non_ascii_keys_and_values(
    value: dict[str, str],
) -> None:
    with pytest.raises(AssertionError):
        assert_public_json(value)


@pytest.mark.parametrize(
    "raw",
    (
        b'{"note":"\\u200bsecret"}',
        b'{"\\u200ckey":"safe"}',
        b'{"note":"\\u200dsecret"}',
        b'{"note":"\\u2060secret"}',
        b'{"note":"\\ufeffsecret"}',
        b'{"note":"\\uff53\\uff45\\uff43\\uff52\\uff45\\uff54"}',
    ),
)
def test_public_hygiene_helper_rejects_json_escaped_non_ascii_keys_and_values(
    raw: bytes,
) -> None:
    with pytest.raises(AssertionError):
        assert_public_json(load_public_json(raw))
