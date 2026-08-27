"""RFC 8785 canonicalization and redacted action fingerprints.

The module accepts only exact built-in JSON values or JSON text/UTF-8 bytes.  It
validates values before passing them to :mod:`rfc8785`; this keeps error output
sanitized and makes the safe IEEE-754 integer boundary explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import rfc8785

from .validation import ContractValidationError, _ensure_json_value, load_public_json

_DEFAULT_EXCLUDED_PATHS = ("/event_id", "/timestamp", "/request_id")
_SAFE_INTEGER_MAX = 2**53 - 1


class CanonicalizationError(ValueError):
    """Sanitized failure from the canonical JSON boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid canonical JSON input")


def _is_non_root_json_pointer(path: str) -> bool:
    """Return whether an exact string is a non-root RFC-6901 pointer."""
    if not path or not path.startswith("/"):
        return False
    for raw_part in path[1:].split("/"):
        index = 0
        while index < len(raw_part):
            if raw_part[index] == "~":
                if index + 1 == len(raw_part) or raw_part[index + 1] not in "01":
                    return False
                index += 2
            else:
                index += 1
    return True


def _validate_fingerprint_record(digest: Any, excluded_fields: Any) -> None:
    if type(digest) is not str or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CanonicalizationError("invalid_fingerprint_record")
    if type(excluded_fields) is not tuple or any(type(path) is not str for path in excluded_fields):
        raise CanonicalizationError("invalid_fingerprint_record")
    if any(not _is_non_root_json_pointer(path) for path in excluded_fields):
        raise CanonicalizationError("invalid_fingerprint_record")
    if excluded_fields != tuple(sorted(excluded_fields)) or len(excluded_fields) != len(set(excluded_fields)):
        raise CanonicalizationError("invalid_fingerprint_record")


@dataclass(frozen=True, slots=True, init=False)
class FingerprintRecord:
    """Debug-safe action fingerprint metadata, with no source values."""

    digest: str
    excluded_fields: tuple[str, ...]

    def __init__(self, digest: str, excluded_fields: tuple[str, ...]) -> None:
        _validate_fingerprint_record(digest, excluded_fields)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "excluded_fields", excluded_fields)
        self.__post_init__()

    def __post_init__(self) -> None:
        _validate_fingerprint_record(self.digest, self.excluded_fields)

    def to_dict(self) -> dict[str, str | tuple[str, ...]]:
        """Return the deliberately redacted public debug representation."""
        self.__post_init__()
        return {"digest": self.digest, "excluded_fields": self.excluded_fields}


def _ensure_safe_integers(data: Any) -> None:
    """Reject every integer that cannot be represented exactly by IEEE-754."""
    stack = [data]
    while stack:
        value = stack.pop()
        if type(value) is int and not -_SAFE_INTEGER_MAX <= value <= _SAFE_INTEGER_MAX:
            raise CanonicalizationError("unsafe_json_integer")
        if type(value) is dict:
            stack.extend(value.values())
        elif type(value) is list:
            stack.extend(value)


def _json_input(data: Any) -> Any:
    code: str | None = None
    try:
        value = load_public_json(data) if type(data) in (str, bytes, bytearray) else data
        _ensure_json_value(value)
        _ensure_safe_integers(value)
        return value
    except CanonicalizationError as error:
        if error.__cause__ is None and error.__context__ is None:
            raise
        code = error.code
    except ContractValidationError as error:
        code = error.code
    except Exception:  # noqa: BLE001 - public boundary must sanitize native failures.
        code = "invalid_json_data"
    raise CanonicalizationError(code)


def _copy_json_tree(data: Any) -> Any:
    """Copy a validated JSON tree without preserving shared container aliases."""
    if type(data) is dict:
        return {key: _copy_json_tree(value) for key, value in data.items()}
    if type(data) is list:
        return [_copy_json_tree(value) for value in data]
    return data


def canonicalize_json(data: Any) -> bytes:
    """Return RFC 8785 bytes for exact JSON values or one raw JSON payload.

    Raw strings, bytes, and bytearrays are parsed as JSON with duplicate keys
    rejected. Direct values must be exact built-in JSON types and every integer
    must be inside ``[-(2**53 - 1), 2**53 - 1]``.
    """
    code: str | None = None
    try:
        return rfc8785.dumps(_json_input(data))
    except CanonicalizationError as error:
        if error.__cause__ is None and error.__context__ is None:
            raise
        code = error.code
    except Exception:  # noqa: BLE001 - public boundary must sanitize native failures.
        code = "invalid_json_data"
    raise CanonicalizationError(code)


def _pointer_parts(path: str) -> tuple[str, ...]:
    if not _is_non_root_json_pointer(path):
        raise CanonicalizationError("invalid_exclusion_path")
    parts: list[str] = []
    for raw_part in path[1:].split("/"):
        decoded: list[str] = []
        index = 0
        while index < len(raw_part):
            character = raw_part[index]
            if character == "~":
                if index + 1 == len(raw_part) or raw_part[index + 1] not in "01":
                    raise CanonicalizationError("invalid_exclusion_path")
                decoded.append("~" if raw_part[index + 1] == "0" else "/")
                index += 2
            else:
                decoded.append(character)
                index += 1
        parts.append("".join(decoded))
    return tuple(parts)


def _exclusion_paths(excluded_paths: Any) -> list[tuple[str, tuple[str, ...]]]:
    if type(excluded_paths) not in (tuple, list):
        raise CanonicalizationError("invalid_exclusion_path")
    raw_paths = [*_DEFAULT_EXCLUDED_PATHS, *excluded_paths]
    decoded: dict[str, tuple[str, ...]] = {}
    for path in raw_paths:
        if type(path) is not str:
            raise CanonicalizationError("invalid_exclusion_path")
        decoded.setdefault(path, _pointer_parts(path))
    return sorted(decoded.items(), key=lambda item: (-len(item[1]), item[0]))


def _remove_path(tree: Any, parts: tuple[str, ...]) -> bool:
    parent = tree
    for part in parts[:-1]:
        if type(parent) is list:
            raise CanonicalizationError("invalid_exclusion_path")
        if type(parent) is not dict or part not in parent:
            return False
        parent = parent[part]
    if type(parent) is list:
        raise CanonicalizationError("invalid_exclusion_path")
    if type(parent) is dict and parts[-1] in parent:
        del parent[parts[-1]]
        return True
    return False


def action_fingerprint(
    data: Any, *, excluded_paths: tuple[str, ...] | list[str] = ()
) -> FingerprintRecord:
    """Return a deterministic SHA-256 action digest with exact field exclusions.

    Default exact exclusions are ``/event_id``, ``/timestamp``, and
    ``/request_id``. Caller paths are additive RFC-6901 object-field pointers;
    ``~0`` and ``~1`` are decoded. The root is forbidden and encountering an
    array while resolving a path raises ``invalid_exclusion_path`` rather than
    deleting/reindexing array data. Processing is deepest-first, so overlapping
    parent and child paths have deterministic removal behavior. The input is
    parsed/validated then deeply copied; it is never modified.
    """
    code: str | None = None
    try:
        copied = _copy_json_tree(_json_input(data))
        removed = [
            path
            for path, parts in _exclusion_paths(excluded_paths)
            if _remove_path(copied, parts)
        ]
        return FingerprintRecord(
            digest=sha256(canonicalize_json(copied)).hexdigest(),
            excluded_fields=tuple(sorted(removed)),
        )
    except CanonicalizationError as error:
        if error.__cause__ is None and error.__context__ is None:
            raise
        code = error.code
    except Exception:  # noqa: BLE001 - public boundary must sanitize native failures.
        code = "invalid_json_data"
    raise CanonicalizationError(code)
