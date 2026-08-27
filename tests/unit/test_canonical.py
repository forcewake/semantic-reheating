"""RFC 8785 canonical action fingerprint behavior."""

from __future__ import annotations

import pytest

_SECRET_TEXT = "SECRET-CHAIN-SENTINEL-MUST-NOT-LEAK"
_SECRET_BYTES = _SECRET_TEXT.encode()


class _HostileInput:
    def __repr__(self) -> str:
        raise RuntimeError(_SECRET_TEXT)


class _StringSubclass(str):
    pass


class _TupleSubclass(tuple[str, ...]):
    pass


def _assert_clean_public_error(error: BaseException) -> None:
    """Require a public canonical error graph to contain no caller secret."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert _SECRET_TEXT not in str(current)
        assert _SECRET_TEXT not in repr(current)
        assert _SECRET_TEXT not in repr(current.args)
        for attribute in ("object", "reason", "doc", "msg"):
            value = getattr(current, attribute, None)
            if type(value) is str:
                assert _SECRET_TEXT not in value
            elif isinstance(value, (bytes, bytearray)):
                assert _SECRET_BYTES not in bytes(value)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)


def test_canonicalize_json_sorts_object_keys() -> None:
    from semantic_reheating.canonical import canonicalize_json

    assert canonicalize_json({"z": 1, "a": "value"}) == b'{"a":"value","z":1}'


def test_action_fingerprint_ignores_default_and_configured_secret_fields() -> None:
    from semantic_reheating.canonical import action_fingerprint

    first = {
        "tool": "fetch", "args": {"query": "safe", "authorization": "secret-one"},
        "event_id": "event-one", "timestamp": "2026-01-01T00:00:00Z", "request_id": "req-one",
    }
    second = {
        "request_id": "req-two", "timestamp": "2027-01-01T00:00:00Z", "event_id": "event-two",
        "args": {"authorization": "secret-two", "query": "safe"}, "tool": "fetch",
    }

    first_record = action_fingerprint(first, excluded_paths=("/args/authorization",))
    second_record = action_fingerprint(second, excluded_paths=["/args/authorization"])
    raw_record = action_fingerprint(
        '{"request_id":"req-three","args":{"authorization":"secret-three","query":"safe"},"tool":"fetch","event_id":"event-three","timestamp":"2028-01-01T00:00:00Z"}',
        excluded_paths=("/args/authorization",),
    )

    assert first_record.digest == second_record.digest == raw_record.digest
    assert len(first_record.digest) == 64
    assert first_record.digest.isascii() and first_record.digest.islower()
    assert action_fingerprint(
        {**first, "args": {"query": "changed", "authorization": "secret-one"}},
        excluded_paths=("/args/authorization",),
    ).digest != first_record.digest
    assert action_fingerprint({"tool": "fetch", "args": {"event_id": "one"}}).digest != action_fingerprint(
        {"tool": "fetch", "args": {"event_id": "two"}}
    ).digest


def test_action_fingerprint_removes_escaped_nested_paths_without_mutating_source() -> None:
    from semantic_reheating.canonical import action_fingerprint

    source = {
        "event_id": "volatile",
        "a/b": {"~token": "secret", "material": 1},
        "payload": {"token": "remove-with-parent", "material": "kept-until-parent-removal"},
    }
    original = {
        "event_id": "volatile",
        "a/b": {"~token": "secret", "material": 1},
        "payload": {"token": "remove-with-parent", "material": "kept-until-parent-removal"},
    }

    record = action_fingerprint(
        source,
        excluded_paths=["/a~1b/~0token", "/missing", "/event_id", "/payload", "/payload/token"],
    )

    assert source == original
    assert record.excluded_fields == (
        "/a~1b/~0token",
        "/event_id",
        "/payload",
        "/payload/token",
    )


def test_action_fingerprint_copies_shared_json_aliases_per_occurrence() -> None:
    from semantic_reheating.canonical import action_fingerprint

    shared = {"authorization": "secret", "material": "kept"}
    aliased_source = {"left": shared, "right": shared}
    independent_source = {
        "left": {"material": "kept"},
        "right": {"authorization": "secret", "material": "kept"},
    }

    aliased = action_fingerprint(aliased_source, excluded_paths=("/left/authorization",))
    independent = action_fingerprint(independent_source)

    assert aliased.digest == independent.digest
    assert aliased_source == {
        "left": {"authorization": "secret", "material": "kept"},
        "right": {"authorization": "secret", "material": "kept"},
    }
    assert aliased_source["left"] is aliased_source["right"] is shared


def test_fingerprint_record_debug_output_exposes_only_digest_and_paths() -> None:
    from semantic_reheating.canonical import action_fingerprint

    secret = "SECRET-SENTINEL-MUST-NOT-LEAK"
    record = action_fingerprint({"authorization": secret, "tool": "fetch"}, excluded_paths=["/authorization"])

    assert record.to_dict() == {"digest": record.digest, "excluded_fields": ("/authorization",)}
    assert "SECRET-SENTINEL-MUST-NOT-LEAK" not in repr(record)
    assert "SECRET-SENTINEL-MUST-NOT-LEAK" not in str(record.to_dict())


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ('{"tool":"fetch","tool":"SECRET-SENTINEL-MUST-NOT-LEAK"}', "duplicate_key"),
        ('{"value":NaN}', "invalid_json_number"),
        ('{"value":Infinity}', "invalid_json_number"),
        ('{"value":-Infinity}', "invalid_json_number"),
    ],
)
def test_canonicalize_json_rejects_unsafe_raw_json_tokens(raw: str, code: str) -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    with pytest.raises(CanonicalizationError) as caught:
        canonicalize_json(raw)

    assert caught.value.code == code
    assert "SECRET-SENTINEL-MUST-NOT-LEAK" not in str(caught.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonicalize_json_rejects_direct_nonfinite_numbers(value: float) -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    with pytest.raises(CanonicalizationError) as caught:
        canonicalize_json(value)

    assert caught.value.code == "nonfinite_json_number"


@pytest.mark.parametrize("value", [2**53 - 1, -(2**53 - 1)])
def test_canonicalize_json_accepts_ieee754_safe_integer_boundaries(value: int) -> None:
    from semantic_reheating.canonical import canonicalize_json

    assert canonicalize_json(value) == str(value).encode()


@pytest.mark.parametrize("value", [2**53, -(2**53)])
def test_canonicalize_json_rejects_unsafe_integer_boundaries_in_direct_and_raw_input(value: int) -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    for input_value in (value, str(value)):
        with pytest.raises(CanonicalizationError) as caught:
            canonicalize_json(input_value)
        assert caught.value.code == "unsafe_json_integer"


def test_canonicalize_json_preserves_boolean_json_type() -> None:
    from semantic_reheating.canonical import canonicalize_json

    assert canonicalize_json(True) == b"true"


@pytest.mark.parametrize("raw", [b'{"tool":"fetch"}', bytearray(b'{"tool":"fetch"}')])
def test_canonicalize_json_accepts_utf8_bytes_and_bytearray(raw: bytes | bytearray) -> None:
    from semantic_reheating.canonical import canonicalize_json

    assert canonicalize_json(raw) == b'{"tool":"fetch"}'


@pytest.mark.parametrize("raw", [b"\xff", bytearray(b"\xff")])
def test_canonicalize_json_rejects_invalid_utf8_bytes_and_bytearray(raw: bytes | bytearray) -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    with pytest.raises(CanonicalizationError) as caught:
        canonicalize_json(raw)

    assert caught.value.code == "invalid_json_encoding"


@pytest.mark.parametrize("public_api", ["canonicalize_json", "action_fingerprint"])
@pytest.mark.parametrize(
    "data",
    [
        b'{"value":"SECRET-CHAIN-SENTINEL-MUST-NOT-LEAK"}\xff',
        bytearray(b'{"value":"SECRET-CHAIN-SENTINEL-MUST-NOT-LEAK"}\xff'),
        '{"value":"safe","value":"SECRET-CHAIN-SENTINEL-MUST-NOT-LEAK"}',
        '{"value":"\ud800SECRET-CHAIN-SENTINEL-MUST-NOT-LEAK"}',
        _HostileInput(),
    ],
)
def test_public_canonical_errors_do_not_retain_input_exception_chains(public_api: str, data: object) -> None:
    from semantic_reheating.canonical import (
        CanonicalizationError,
        action_fingerprint,
        canonicalize_json,
    )

    public_call = canonicalize_json if public_api == "canonicalize_json" else action_fingerprint

    with pytest.raises(CanonicalizationError) as caught:
        public_call(data)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    _assert_clean_public_error(caught.value)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (object(), "non_json_data"),
        (["cycle"], "json_cycle"),
        ("not JSON", "invalid_json"),
    ],
)
def test_canonicalize_json_translates_hostile_or_invalid_input(value: object, code: str) -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    if value == ["cycle"]:
        value.append(value)
    with pytest.raises(CanonicalizationError) as caught:
        canonicalize_json(value)

    assert caught.value.code == code


def test_canonicalize_json_translates_direct_depth_and_node_limits() -> None:
    from semantic_reheating.canonical import CanonicalizationError, canonicalize_json

    nested: object = None
    for _ in range(65):
        nested = [nested]

    for value, code in ((nested, "json_depth_exceeded"), ([None] * 10_000, "json_node_limit_exceeded")):
        with pytest.raises(CanonicalizationError) as caught:
            canonicalize_json(value)
        assert caught.value.code == code


def test_action_fingerprint_rejects_non_exact_excluded_path_containers() -> None:
    from semantic_reheating.canonical import CanonicalizationError, action_fingerprint

    class ListSubclass(list[str]):
        pass

    class TupleSubclass(tuple[str, ...]):
        pass

    for excluded_paths in (
        "/authorization",
        {"/authorization"},
        {"path": "/authorization"},
        ListSubclass(["/authorization"]),
        TupleSubclass(("/authorization",)),
    ):
        with pytest.raises(CanonicalizationError) as caught:
            action_fingerprint({"authorization": "secret"}, excluded_paths=excluded_paths)  # type: ignore[arg-type]
        assert caught.value.code == "invalid_exclusion_path"
        assert "secret" not in str(caught.value)


@pytest.mark.parametrize("excluded_paths", [("",), ["authorization"], ("/bad~2escape",)])
def test_action_fingerprint_rejects_malformed_pointer_entries(excluded_paths: tuple[str] | list[str]) -> None:
    from semantic_reheating.canonical import CanonicalizationError, action_fingerprint

    with pytest.raises(CanonicalizationError) as caught:
        action_fingerprint({"authorization": "secret"}, excluded_paths=excluded_paths)

    assert caught.value.code == "invalid_exclusion_path"
    assert "secret" not in str(caught.value)


def test_action_fingerprint_rejects_array_pointer_traversal() -> None:
    from semantic_reheating.canonical import CanonicalizationError, action_fingerprint

    with pytest.raises(CanonicalizationError) as caught:
        action_fingerprint({"items": [{"token": "secret"}]}, excluded_paths=("/items/0/token",))

    assert caught.value.code == "invalid_exclusion_path"
    assert "secret" not in str(caught.value)


def test_action_fingerprint_removes_empty_string_object_key_with_root_pointer() -> None:
    from semantic_reheating.canonical import action_fingerprint

    record = action_fingerprint({"": "remove", "material": "kept"}, excluded_paths=("/",))

    assert record.digest == action_fingerprint({"material": "kept"}).digest
    assert record.excluded_fields == ("/",)


def test_action_fingerprint_deduplicates_default_and_missing_excluded_paths() -> None:
    from semantic_reheating.canonical import action_fingerprint

    record = action_fingerprint(
        {"event_id": "volatile", "material": "kept"},
        excluded_paths=("/event_id", "/event_id", "/missing"),
    )

    assert record.excluded_fields == ("/event_id",)


@pytest.mark.parametrize(
    ("digest", "excluded_fields"),
    [
        ("d", ["/initial"]),
        (b"d" * 64, ("/initial",)),
        ("d" * 63, ("/initial",)),
        ("D" * 64, ("/initial",)),
        ("g" * 64, ("/initial",)),
        (_StringSubclass("d" * 64), ("/initial",)),
        ("d" * 64, ["/initial"]),
        ("d" * 64, _TupleSubclass(("/initial",))),
        ("d" * 64, (_StringSubclass("/initial"),)),
        ("d" * 64, ("",)),
        ("d" * 64, ("initial",)),
        ("d" * 64, ("/bad~2escape",)),
        ("d" * 64, ("/z", "/a")),
        ("d" * 64, ("/initial", "/initial")),
    ],
)
def test_fingerprint_record_rejects_noncanonical_runtime_state(digest: object, excluded_fields: object) -> None:
    from semantic_reheating.canonical import CanonicalizationError, FingerprintRecord

    with pytest.raises(CanonicalizationError) as caught:
        FingerprintRecord(digest, excluded_fields)  # type: ignore[arg-type]

    assert caught.value.code == "invalid_fingerprint_record"
    assert str(caught.value) == "Invalid canonical JSON input"


def test_fingerprint_record_revalidates_tampered_frozen_state_without_leaking_values() -> None:
    from semantic_reheating.canonical import (
        CanonicalizationError,
        FingerprintRecord,
        action_fingerprint,
    )

    generated = action_fingerprint({"tool": "fetch", "authorization": _SECRET_TEXT}, excluded_paths=("/authorization",))
    record = FingerprintRecord(generated.digest, generated.excluded_fields)
    first = record.to_dict()
    second = record.to_dict()

    assert first == second == {"digest": generated.digest, "excluded_fields": ("/authorization",)}
    assert first is not second
    assert first["excluded_fields"] is record.excluded_fields
    object.__setattr__(record, "excluded_fields", [_SECRET_TEXT])

    with pytest.raises(CanonicalizationError) as caught:
        record.to_dict()

    assert caught.value.code == "invalid_fingerprint_record"
    assert _SECRET_TEXT not in str(caught.value)
    _assert_clean_public_error(caught.value)


def test_canonicalization_preserves_unicode_code_points_without_normalization() -> None:
    from semantic_reheating.canonical import action_fingerprint, canonicalize_json

    composed = {"label": "é", "tool": "fetch"}
    decomposed = {"tool": "fetch", "label": "e\u0301"}

    assert canonicalize_json('{"tool":"fetch","label":"é"}') == canonicalize_json(composed)
    assert canonicalize_json(composed) != canonicalize_json(decomposed)
    assert action_fingerprint(composed).digest != action_fingerprint(decomposed).digest
