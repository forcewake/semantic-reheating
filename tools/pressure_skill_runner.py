"""Private, offline-only runner for the Task 17 pressure baseline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

if __name__ != "__main__" or "--pressure-cgroup-helper" not in sys.argv:
    from jsonschema import Draft202012Validator

MAX_PUBLIC_BYTES = 64 * 1024
_MAX_HELPER_BYTES = 128 * 1024
MAX_CAPTURE_BYTES = 8 * 1024
WORKTREE_DISCOVERY_TIMEOUT_SECONDS = 2
MAX_COST_INTEGRAL_DIGITS = 12
MAX_COST_FRACTION_DIGITS = 6
MAX_COST = Decimal("999999999999.999999")
SCENARIO_ORDER = (
    "exact-retry-loop",
    "plan-oscillation",
    "productive-pagination",
    "blocked-authority",
    "unsafe-write",
    "exhausted-budget",
)
_PRIVATE_CONFIG_NAME = "pressure-stack.local.json"
_PROJECT_STATE_COMPONENTS = ("semantic-reheating", "pressure-baselines")
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MINIMAL_SELECTED_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONIOENCODING": "utf-8",
}
_FORBIDDEN_SELECTED_ENV = frozenset(
    {
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "NOTIFY_SOCKET",
        "LISTEN_FDS",
        "LISTEN_PID",
        "INVOCATION_ID",
        "JOURNAL_STREAM",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
    }
)
_SYSTEMD_RUN_PATH = Path("/usr/bin/systemd-run")
_SYSTEMCTL_PATH = Path("/usr/bin/systemctl")
_CGROUP_STOP_SECONDS = 0.4


@dataclass
class _StateRoot:
    """A private state directory held open as an authority capability."""

    fd: int
    path: Path
    children: list[int] = field(default_factory=list)

    def close(self) -> None:
        for child in reversed(self.children):
            os.close(child)
        self.children.clear()
        os.close(self.fd)

    def is_relative_to(self, other: Path) -> bool:
        return self.path.is_relative_to(other)


@dataclass(frozen=True)
class _CgroupContext:
    """Baseline-scoped, hash-pinned inputs for transient cgroup services."""

    systemd_run: str
    systemctl: str
    helper_path: str
    helper_sha256: str
    helper_identity: tuple[int, int]
    python_path: str
    python_sha256: str
    python_identity: tuple[int, int]
    client_environment: tuple[tuple[str, str], ...]


class PressureProtocolError(RuntimeError):
    """A stable fail-closed pressure-protocol error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _is_int(value: object) -> bool:
    return type(value) is int and value >= 0


_COST_PATTERN = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$")


def _decimal(value: object) -> Decimal:
    if type(value) is not str or _COST_PATTERN.fullmatch(value) is None:
        raise PressureProtocolError("pressure_invalid_decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise PressureProtocolError("pressure_invalid_decimal") from error
    if not parsed.is_finite() or parsed < 0 or parsed > MAX_COST:
        raise PressureProtocolError("pressure_invalid_decimal")
    return parsed


def _safe_regular_bytes(path: Path, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PressureProtocolError("pressure_file_missing") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PressureProtocolError("pressure_unsafe_file")
    if metadata.st_nlink != 1:
        raise PressureProtocolError("pressure_hardlink_forbidden")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        opened_snapshot = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened_snapshot
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        if opened.st_size > maximum:
            raise PressureProtocolError("pressure_file_too_large")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except FileNotFoundError as error:
            raise PressureProtocolError("pressure_unsafe_file") from error
        after_snapshot = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        path_snapshot = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_mode,
            path_after.st_nlink,
            path_after.st_size,
            path_after.st_mtime_ns,
            path_after.st_ctime_ns,
        )
        if (
            after_snapshot != opened_snapshot
            or path_snapshot != opened_snapshot
            or len(data) != opened.st_size
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        if len(data) > maximum:
            raise PressureProtocolError("pressure_file_too_large")
        return data
    finally:
        os.close(descriptor)


def _safe_regular_sha256(path: Path, maximum: int) -> str:
    """Hash a regular file from one descriptor without materializing its bytes."""
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PressureProtocolError("pressure_file_missing") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PressureProtocolError("pressure_unsafe_file")
    if metadata.st_nlink != 1:
        raise PressureProtocolError("pressure_hardlink_forbidden")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        opened_snapshot = _snapshot(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened_snapshot != _snapshot(metadata)
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        if opened.st_size > maximum:
            raise PressureProtocolError("pressure_file_too_large")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except FileNotFoundError as error:
            raise PressureProtocolError("pressure_unsafe_file") from error
        if (
            _snapshot(after) != opened_snapshot
            or _snapshot(path_after) != opened_snapshot
            or remaining != 0
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _private_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or "\x00" in name:
        raise PressureProtocolError("pressure_private_path_escape")
    return name


def _safe_regular_bytes_at(directory: int, name: str, maximum: int) -> bytes:
    """Read one non-link private leaf via an already-trusted directory FD."""
    name = _private_name(name)
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError as error:
        raise PressureProtocolError("pressure_file_missing") from error
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PressureProtocolError("pressure_unsafe_file")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_file") from error
    try:
        opened = os.fstat(fd)
        snapshot = _snapshot(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or snapshot != _snapshot(before)
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        if opened.st_size > maximum:
            raise PressureProtocolError("pressure_file_too_large")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            len(data) != opened.st_size
            or len(data) > maximum
            or _snapshot(after) != snapshot
            or _snapshot(current) != snapshot
        ):
            raise PressureProtocolError("pressure_unsafe_file")
        return data
    finally:
        os.close(fd)


def _load_json_at(
    directory: int, name: str, maximum: int = MAX_PUBLIC_BYTES
) -> tuple[dict[str, Any], bytes]:
    raw = _safe_regular_bytes_at(directory, name, maximum)
    value = _strict_json_loads(raw)
    if type(value) is not dict:
        raise PressureProtocolError("pressure_invalid_json")
    return value, raw


def _strict_json_loads(raw: bytes) -> object:
    """Parse one RFC JSON value without duplicate-key last-wins behavior."""

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    def no_constants(_value: str) -> object:
        raise ValueError("invalid JSON constant")

    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constants,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise PressureProtocolError("pressure_invalid_json") from error


def _load_json(
    path: Path, maximum: int = MAX_PUBLIC_BYTES
) -> tuple[dict[str, Any], bytes]:
    raw = _safe_regular_bytes(path, maximum)
    value = _strict_json_loads(raw)
    if type(value) is not dict:
        raise PressureProtocolError("pressure_invalid_json")
    return value, raw


def _validate_schema(schema: dict[str, Any], value: Any) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        error = next(Draft202012Validator(schema).iter_errors(value), None)
    except Exception as error:
        raise PressureProtocolError("pressure_invalid_public_contract") from error
    if error is not None:
        raise PressureProtocolError("pressure_invalid_public_contract")


def _baseline_summary_schema() -> tuple[dict[str, Any], dict[str, str]]:
    """Load the fixed, descriptor-read summary contract and its governing bindings."""
    paths = _public_paths(Path(__file__).resolve().parents[1])
    values: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for name in (
        "scenarios_schema",
        "rubric_schema",
        "baseline_summary_schema",
        "stack_receipt_schema",
    ):
        value, raw = _load_json(paths[name], MAX_PUBLIC_BYTES)
        values[name] = value
        hashes[name] = _sha256(raw)
    Draft202012Validator.check_schema(values["baseline_summary_schema"])
    return values["baseline_summary_schema"], hashes


def _repo_root(value: Path) -> Path:
    root = value.resolve(strict=True)
    if not (root / ".git").exists():
        raise PressureProtocolError("pressure_repository_missing")
    return root


def _worktree_roots(repo_root: Path) -> tuple[Path, ...]:
    """Read local Git worktree roots without consulting remotes."""
    git = shutil.which("git")
    if git is None:
        return (repo_root,)
    try:
        git_path = Path(git).resolve(strict=True)
        if not git_path.is_absolute() or not git_path.is_file():
            raise OSError("git executable unavailable")
        stdout, _stderr, returncode = _run_bounded(
            [str(git_path), "-C", str(repo_root), "worktree", "list", "--porcelain"],
            repo_root,
            time.monotonic() + WORKTREE_DISCOVERY_TIMEOUT_SECONDS,
            maximum=MAX_PUBLIC_BYTES,
            env=_safe_git_environment(),
        )
        if returncode != 0:
            raise PressureProtocolError("pressure_worktree_discovery_failed")
        lines = stdout.decode("utf-8", "strict").splitlines()
    except (OSError, UnicodeDecodeError, PressureProtocolError) as error:
        raise PressureProtocolError("pressure_worktree_discovery_failed") from error
    roots: list[Path] = []
    for line in lines:
        if not line:
            continue
        if line.startswith("worktree ") and len(line) > len("worktree "):
            try:
                root = Path(line.removeprefix("worktree ")).resolve(strict=True)
            except OSError as error:
                raise PressureProtocolError(
                    "pressure_worktree_discovery_failed"
                ) from error
            if not root.is_dir() or not (root / ".git").exists() or root in roots:
                raise PressureProtocolError("pressure_worktree_discovery_failed")
            roots.append(root)
            continue
        if line.startswith(("HEAD ", "branch ", "locked ", "prunable ")) or line in {
            "bare",
            "detached",
        }:
            continue
        raise PressureProtocolError("pressure_worktree_discovery_failed")
    if not roots or repo_root not in roots:
        raise PressureProtocolError("pressure_worktree_discovery_failed")
    return tuple(roots)


def _state_base_path() -> Path:
    """Implement the XDG shell contract without ever treating a relative value as CWD."""
    configured = os.environ.get("XDG_STATE_HOME")
    raw = configured if configured else os.environ.get("HOME", "") + "/.local/state"
    base = Path(raw)
    if not raw or not base.is_absolute():
        raise PressureProtocolError("pressure_unsafe_state_root")
    return base


def _assert_private_directory(fd: int, *, final: bool, created: bool = False) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise PressureProtocolError("pressure_unsafe_state_root")
    mode = stat.S_IMODE(metadata.st_mode)
    uid = os.getuid()
    if created:
        if metadata.st_uid != uid or mode != 0o700:
            raise PressureProtocolError("pressure_unsafe_state_root")
        return
    if final:
        if metadata.st_uid != uid or mode != 0o700:
            raise PressureProtocolError("pressure_unsafe_state_root")
        return
    # System-owned ancestors are only accepted when non-writable or sticky (e.g. /tmp).
    if metadata.st_uid == 0:
        if mode & 0o022 and not (mode & stat.S_ISVTX):
            raise PressureProtocolError("pressure_unsafe_state_root")
    elif metadata.st_uid != uid or mode & 0o022:
        raise PressureProtocolError("pressure_unsafe_state_root")


def _open_state_component(
    parent: int, name: str, *, final: bool = False
) -> tuple[int, bool]:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent,
        )
        _assert_private_directory(fd, final=final)
        return fd, False
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent,
            )
        except OSError as error:
            raise PressureProtocolError("pressure_unsafe_state_root") from error
        try:
            _assert_private_directory(fd, final=final, created=True)
        except BaseException:
            os.close(fd)
            raise
        return fd, True
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_state_root") from error


def _fd_path(fd: int) -> Path:
    try:
        resolved = Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_state_root") from error
    if not resolved.is_absolute():
        raise PressureProtocolError("pressure_unsafe_state_root")
    return resolved


def _state_root(repo_root: Path) -> _StateRoot:
    """Descriptor-walk the state path and retain the final directory capability."""
    base = _state_base_path()
    worktree_roots = _worktree_roots(repo_root)
    lexical_target = base.joinpath(*_PROJECT_STATE_COMPONENTS)
    if any(lexical_target.is_relative_to(root) for root in worktree_roots):
        raise PressureProtocolError("pressure_state_inside_repository")
    try:
        current = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_state_root") from error
    try:
        _assert_private_directory(current, final=False)
        for component in base.parts[1:]:
            next_fd, _created = _open_state_component(current, component)
            os.close(current)
            current = next_fd
        _assert_private_directory(current, final=False)
        base_path = _fd_path(current)
        target = base_path.joinpath(*_PROJECT_STATE_COMPONENTS)
        if any(target.is_relative_to(root) for root in worktree_roots):
            raise PressureProtocolError("pressure_state_inside_repository")
        for index, component in enumerate(_PROJECT_STATE_COMPONENTS):
            next_fd, _created = _open_state_component(
                current,
                component,
                final=index == len(_PROJECT_STATE_COMPONENTS) - 1,
            )
            os.close(current)
            current = next_fd
        final_path = _fd_path(current)
        if any(final_path.is_relative_to(root) for root in worktree_roots):
            raise PressureProtocolError("pressure_state_inside_repository")
        return _StateRoot(current, final_path)
    except BaseException:
        os.close(current)
        raise


def _validate_config(config: dict[str, Any], config_raw: bytes) -> dict[str, Any]:
    required = {
        "contract_version",
        "mode",
        "command_argv",
        "executable_sha256",
        "stack_metadata",
        "seed",
        "decoding",
        "caps",
        "enforcement",
        "usage_report",
        "skill_absent",
        "environment_allowlist",
    }
    if (
        set(config) != required
        or config.get("contract_version") != "1.0"
        or config.get("mode") != "baseline"
    ):
        raise PressureProtocolError("pressure_invalid_config")
    argv = config["command_argv"]
    if (
        type(argv) is not list
        or not argv
        or any(type(part) is not str or not part for part in argv)
    ):
        raise PressureProtocolError("pressure_invalid_config")
    allowed = {"{prompt}", "{usage_file}"}
    forbidden = set(";|&$`()<>\n\r")
    if (
        argv.count("{prompt}") != 1
        or argv.count("{usage_file}") != 1
        or any(("{" in part or "}" in part) and part not in allowed for part in argv)
    ):
        raise PressureProtocolError("pressure_invalid_config")
    if any(any(character in forbidden for character in part) for part in argv):
        raise PressureProtocolError("pressure_invalid_config")
    if (
        any("skill" in part.lower() for part in argv)
        or config["skill_absent"] is not True
    ):
        raise PressureProtocolError("pressure_skill_not_absent")
    environment_allowlist = config["environment_allowlist"]
    if (
        type(environment_allowlist) is not list
        or len(environment_allowlist) > 32
        or any(
            type(name) is not str
            or _ENV_NAME_PATTERN.fullmatch(name) is None
            or not name.isascii()
            for name in environment_allowlist
        )
        or len(set(environment_allowlist)) != len(environment_allowlist)
        or bool(set(environment_allowlist) & _FORBIDDEN_SELECTED_ENV)
    ):
        raise PressureProtocolError("pressure_invalid_config")
    metadata = config["stack_metadata"]
    if (
        type(metadata) is not dict
        or set(metadata) != {"cli", "framework", "model", "provider", "version"}
        or any(type(item) is not str for item in metadata.values())
    ):
        raise PressureProtocolError("pressure_invalid_config")
    if (
        type(config["executable_sha256"]) is not str
        or len(config["executable_sha256"]) != 64
    ):
        raise PressureProtocolError("pressure_invalid_config")
    if config["seed"] != "unsupported" and not _is_int(config["seed"]):
        raise PressureProtocolError("pressure_invalid_config")
    if config["decoding"] != "unsupported":
        raise PressureProtocolError("pressure_invalid_config")
    caps = config["caps"]
    if type(caps) is not dict or set(caps) != {
        "turns",
        "tools",
        "tokens",
        "elapsed_seconds",
        "cost",
    }:
        raise PressureProtocolError("pressure_invalid_config")
    if (
        not all(
            _is_int(caps[key])
            for key in ("turns", "tools", "tokens", "elapsed_seconds")
        )
        or caps["elapsed_seconds"] < 1
    ):
        raise PressureProtocolError("pressure_invalid_config")
    try:
        _decimal(caps["cost"])
    except PressureProtocolError as error:
        raise PressureProtocolError("pressure_invalid_config") from error
    enforcement = config["enforcement"]
    if (
        type(enforcement) is not dict
        or set(enforcement) != {"turns", "tools", "tokens", "elapsed_seconds", "cost"}
        or any(
            value not in {"hard", "reported", "unsupported"}
            for value in enforcement.values()
        )
    ):
        raise PressureProtocolError("pressure_invalid_config")
    usage = config["usage_report"]
    if (
        type(usage) is not dict
        or set(usage) != {"turns", "tools", "tokens", "elapsed_seconds", "cost"}
        or any(type(value) is not bool for value in usage.values())
    ):
        raise PressureProtocolError("pressure_invalid_config")
    for key in ("turns", "tools", "tokens", "cost"):
        if usage[key] is False and enforcement[key] != "unsupported":
            raise PressureProtocolError("pressure_invalid_config")
        if usage[key] is True and enforcement[key] != "reported":
            raise PressureProtocolError("pressure_invalid_config")
    if usage["elapsed_seconds"] is not True or enforcement["elapsed_seconds"] != "hard":
        raise PressureProtocolError("pressure_invalid_config")
    return {
        "config": config,
        "config_sha256": _sha256(config_raw),
    }


def _resolve_executable(argv: list[str], expected_hash: str) -> tuple[str, str]:
    selected = argv[0]
    resolved = (
        Path(selected)
        if os.path.isabs(selected)
        else Path(shutil.which(selected) or "")
    )
    if not resolved or not resolved.is_file():
        raise PressureProtocolError("pressure_executable_missing")
    binary = resolved.resolve(strict=True)
    digest = _sha256(_safe_regular_bytes(binary, 64 * 1024 * 1024))
    if digest != expected_hash:
        raise PressureProtocolError("pressure_executable_fingerprint_mismatch")
    return str(binary), digest


def _public_paths(repo_root: Path) -> dict[str, Path]:
    reference = repo_root / "skills" / "semantic-reheating" / "references"
    return {
        "scenarios": reference / "pressure-scenarios.json",
        "scenarios_schema": reference / "pressure-scenarios.schema.json",
        "rubric": reference / "rubric.json",
        "rubric_schema": reference / "rubric.schema.json",
        "baseline_summary_schema": reference / "baseline-summary.schema.json",
        "stack_receipt_schema": reference / "stack-receipt.schema.json",
    }


def load_public_protocol(repo: Path) -> dict[str, Any]:
    repo_root = _repo_root(repo)
    paths = _public_paths(repo_root)
    values: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        if not path.resolve(strict=True).is_relative_to(repo_root):
            raise PressureProtocolError("pressure_public_path_escape")
        value, raw = _load_json(path)
        values[name] = value
        hashes[name] = _sha256(raw)
    _validate_schema(values["scenarios_schema"], values["scenarios"])
    _validate_schema(values["rubric_schema"], values["rubric"])
    try:
        Draft202012Validator.check_schema(values["baseline_summary_schema"])
        Draft202012Validator.check_schema(values["stack_receipt_schema"])
    except Exception as error:
        raise PressureProtocolError("pressure_invalid_public_contract") from error
    _validate_schema(
        values["rubric"]["response_schema"],
        {
            "action": "stop",
            "authority_owner": "none",
            "would_execute_write": False,
            "repeat_unknown_write": False,
            "budget_state": "available",
            "evidence_ids": [],
            "reason_codes": [],
        },
    )
    scenario_ids = tuple(
        item["scenario_id"] for item in values["scenarios"]["scenarios"]
    )
    if scenario_ids != SCENARIO_ORDER:
        raise PressureProtocolError("pressure_scenario_order_invalid")
    return {
        "scenarios": values["scenarios"],
        "rubric": values["rubric"],
        "hashes": hashes,
    }


def _response_outcome(
    scenario: dict[str, Any], response: object, rubric: dict[str, Any]
) -> str:
    try:
        _validate_schema(rubric["response_schema"], response)
    except PressureProtocolError:
        return rubric["malformed_output_code"]
    expected_check_ids = scenario["expected_rubric_check_ids"]
    checks = {entry["check_id"]: entry for entry in rubric["checks"]}
    if (
        type(expected_check_ids) is not list
        or len(expected_check_ids) != 1
        or len(checks) != len(rubric["checks"])
        or expected_check_ids[0] not in checks
    ):
        raise PressureProtocolError("pressure_invalid_public_contract")
    check = checks[expected_check_ids[0]]
    assert type(response) is dict
    action = response["action"]
    if action not in check["pass_actions"]:
        return check["failure_code"]
    constraints = check["response_constraints"]
    if any(
        response[field] not in constraints[field]
        for field in (
            "authority_owner",
            "would_execute_write",
            "repeat_unknown_write",
            "budget_state",
        )
    ):
        return check["failure_code"]
    return "pass"


def _selected_environment(allowlist: list[str]) -> dict[str, str]:
    environment = dict(_MINIMAL_SELECTED_ENV)
    for name in allowlist:
        value = os.environ.get(name)
        if value is None:
            raise PressureProtocolError("pressure_environment_missing")
        environment[name] = value
    return environment


def _safe_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise OSError("short private write")
        offset += written


def _write_private(path: Path, data: bytes) -> str:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
    return _sha256(data)


def _write_private_atomic(path: Path, data: bytes) -> str:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    digest = _write_private(temporary, data)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return digest


def _write_private_at(
    directory: int, name: str, data: bytes, *, replace: bool = False
) -> str:
    name = _private_name(name)
    temporary = f".{name}.tmp-{os.getpid()}"
    target = temporary if replace else name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(target, flags, 0o600, dir_fd=directory)
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_file") from error
    try:
        _write_all(fd, data)
        os.fsync(fd)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise PressureProtocolError("pressure_unsafe_file")
    finally:
        os.close(fd)
    if replace:
        try:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
    return _sha256(data)


def _assert_run_directory(root_fd: int, name: str, run_fd: int) -> None:
    try:
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_state_root") from error
    opened = os.fstat(run_fd)
    if (
        not stat.S_ISDIR(named.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or named.st_dev != opened.st_dev
        or named.st_ino != opened.st_ino
        or named.st_uid != os.getuid()
        or stat.S_IMODE(named.st_mode) != 0o700
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        raise PressureProtocolError("pressure_unsafe_state_root")


def _create_run_directory(root_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=root_fd)
        run_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root_fd,
        )
    except FileExistsError as error:
        raise PressureProtocolError("pressure_run_exists") from error
    except OSError as error:
        raise PressureProtocolError("pressure_unsafe_state_root") from error
    try:
        _assert_run_directory(root_fd, name, run_fd)
        return run_fd
    except BaseException:
        os.close(run_fd)
        raise


def _usage(
    value: object, caps: dict[str, Any], usage_support: dict[str, bool]
) -> dict[str, Any]:
    supported = {
        key for key in ("turns", "tools", "tokens", "cost") if usage_support[key]
    }
    if type(value) is not dict or set(value) != supported:
        raise PressureProtocolError("pressure_invalid_usage")
    result: dict[str, Any] = {}
    for key in ("turns", "tools", "tokens"):
        if key not in supported:
            result[key] = "unsupported"
            continue
        if not _is_int(value[key]):
            raise PressureProtocolError("pressure_invalid_usage")
        if value[key] > caps[key]:
            raise PressureProtocolError("pressure_budget_exceeded")
        result[key] = value[key]
    if "cost" not in supported:
        result["cost"] = "unsupported"
    else:
        try:
            cost = _decimal(value["cost"])
        except PressureProtocolError as error:
            raise PressureProtocolError("pressure_invalid_usage") from error
        if cost > _decimal(caps["cost"]):
            raise PressureProtocolError("pressure_budget_exceeded")
        result["cost"] = value["cost"]
    return result


def _supports(config: dict[str, Any]) -> dict[str, Any]:
    usage = config["usage_report"]
    return {
        "seed": "unsupported" if config["seed"] == "unsupported" else "supported",
        "decoding": "unsupported"
        if config["decoding"] == "unsupported"
        else "supported",
        "usage_report": {
            key: "supported" if usage[key] else "unsupported"
            for key in ("turns", "tools", "tokens", "elapsed_seconds", "cost")
        },
        "enforcement": {
            key: config["enforcement"][key]
            for key in ("turns", "tools", "tokens", "elapsed_seconds", "cost")
        },
    }


def _validate_evidence_manifest(value: object) -> dict[str, Any]:
    """Keep the private transcript evidence closed and in public scenario order."""
    required = {
        "contract_version",
        "mode",
        "scenario_set_sha256",
        "rubric_sha256",
        "scenario_schema_sha256",
        "rubric_schema_sha256",
        "baseline_summary_schema_sha256",
        "stack_receipt_schema_sha256",
        "stack_config_sha256",
        "command_sha256",
        "entries",
    }
    if type(value) is not dict or set(value) != required:
        raise PressureProtocolError("pressure_evidence_manifest_invalid")
    if value["contract_version"] != "1.0" or value["mode"] != "baseline":
        raise PressureProtocolError("pressure_evidence_manifest_invalid")
    hash_names = required - {"contract_version", "mode", "entries"}
    if any(
        type(value[name]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
        for name in hash_names
    ):
        raise PressureProtocolError("pressure_evidence_manifest_invalid")
    entries = value["entries"]
    if type(entries) is not list or len(entries) != len(SCENARIO_ORDER):
        raise PressureProtocolError("pressure_evidence_manifest_invalid")
    for scenario_id, entry in zip(SCENARIO_ORDER, entries, strict=True):
        if (
            type(entry) is not dict
            or set(entry)
            != {"scenario_id", "stdout_sha256", "stderr_sha256", "usage_sha256"}
            or entry["scenario_id"] != scenario_id
            or any(
                type(entry[name]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", entry[name]) is None
                for name in ("stdout_sha256", "stderr_sha256", "usage_sha256")
            )
        ):
            raise PressureProtocolError("pressure_evidence_manifest_invalid")
    return value


def sanitize_projection(summary: dict[str, Any]) -> dict[str, Any]:
    """Return only the future public receipt shape; never include private receipts."""
    fields = {
        "contract_version",
        "mode",
        "scenario_set_sha256",
        "rubric_sha256",
        "scenario_schema_sha256",
        "rubric_schema_sha256",
        "baseline_summary_schema_sha256",
        "stack_receipt_schema_sha256",
        "stack_config_sha256",
        "command_sha256",
        "supports",
        "outcomes",
        "budget_consumption",
    }
    if set(summary) != fields | {"private_transcript_receipt"}:
        raise PressureProtocolError("pressure_invalid_public_contract")
    private_receipt = summary["private_transcript_receipt"]
    if (
        type(private_receipt) is not dict
        or set(private_receipt) != {"name", "sha256"}
        or type(private_receipt["name"]) is not str
        or "/" in private_receipt["name"]
        or "\\" in private_receipt["name"]
        or type(private_receipt["sha256"]) is not str
        or len(private_receipt["sha256"]) != 64
    ):
        raise PressureProtocolError("pressure_summary_private_receipt_invalid")
    try:
        schema, fixed_hashes = _baseline_summary_schema()
        public = {field: summary[field] for field in fields}
        expected = {
            "scenario_schema_sha256": fixed_hashes["scenarios_schema"],
            "rubric_schema_sha256": fixed_hashes["rubric_schema"],
            "baseline_summary_schema_sha256": fixed_hashes["baseline_summary_schema"],
            "stack_receipt_schema_sha256": fixed_hashes["stack_receipt_schema"],
        }
        if any(public[name] != digest for name, digest in expected.items()):
            raise PressureProtocolError("pressure_invalid_public_contract")
        _validate_schema(schema, public)
    except (KeyError, PressureProtocolError) as error:
        raise PressureProtocolError("pressure_invalid_public_contract") from error
    # Canonical serialize/parse detaches all nested public values from private state.
    detached_raw = _canonical_bytes(public)
    if len(detached_raw) > MAX_PUBLIC_BYTES:
        raise PressureProtocolError("pressure_invalid_public_contract")
    detached = _strict_json_loads(detached_raw)
    assert type(detached) is dict
    return detached


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_process_group(process_group: int, signal_value: signal.Signals) -> None:
    try:
        os.killpg(process_group, signal_value)
    except ProcessLookupError:
        pass


def _stop_process_group(process_group: int) -> None:
    """Begin termination of the fresh selected session before reaping its leader."""
    _signal_process_group(process_group, signal.SIGTERM)


def _cleanup_selected_process(
    process: subprocess.Popen[bytes], process_group: int
) -> None:
    """Stop the captured session once, then reap its direct leader."""
    _stop_process_group(process_group)
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired as error:
        _signal_process_group(process_group, signal.SIGKILL)
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        raise PressureProtocolError("pressure_process_group_cleanup_failed") from error
    grace_deadline = time.monotonic() + 0.2
    while _process_group_exists(process_group) and time.monotonic() < grace_deadline:
        time.sleep(0.01)
    if _process_group_exists(process_group):
        _signal_process_group(process_group, signal.SIGKILL)
    kill_deadline = time.monotonic() + 0.2
    while _process_group_exists(process_group) and time.monotonic() < kill_deadline:
        time.sleep(0.01)
    if _process_group_exists(process_group):
        raise PressureProtocolError("pressure_process_group_cleanup_failed")


def _direct_parent_exited(process_id: int) -> bool:
    """Observe the leader without reaping its PID before group cleanup."""
    try:
        return (
            os.waitid(
                os.P_PID,
                process_id,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            is not None
        )
    except ChildProcessError as error:
        raise PressureProtocolError("pressure_process_group_cleanup_failed") from error


def _read_ready_stream(
    selector: selectors.BaseSelector,
    stream: Any,
    streams: dict[Any, bytearray],
    maximum: int,
) -> str | None:
    chunk = os.read(stream.fileno(), maximum + 1 - len(streams[stream]))
    if not chunk:
        selector.unregister(stream)
        return None
    streams[stream].extend(chunk)
    if len(streams[stream]) > maximum:
        return "pressure_output_too_large"
    return None


def _drain_available_streams(
    selector: selectors.BaseSelector,
    streams: dict[Any, bytearray],
    maximum: int,
) -> str | None:
    """Capture every byte already available without waiting on descendants."""
    while selector.get_map():
        ready = selector.select(0)
        if not ready:
            return None
        for key, _ in ready:
            failure = _read_ready_stream(selector, key.fileobj, streams, maximum)
            if failure is not None:
                return failure
    return None


def _drain_closed_streams(
    selector: selectors.BaseSelector,
    streams: dict[Any, bytearray],
    maximum: int,
) -> str | None:
    """After session cleanup, drain EOF-bound pipes with a short fixed bound."""
    drain_deadline = time.monotonic() + 0.2
    while selector.get_map() and time.monotonic() < drain_deadline:
        for key, _ in selector.select(0.01):
            failure = _read_ready_stream(selector, key.fileobj, streams, maximum)
            if failure is not None:
                return failure
    if selector.get_map():
        raise PressureProtocolError("pressure_process_group_cleanup_failed")
    return None


def _trusted_system_binary(path: Path) -> str:
    """Accept only a non-link, root-owned, non-writable systemd client."""
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if (
        not path.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PressureProtocolError("pressure_cgroup_unavailable")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if resolved != path:
        raise PressureProtocolError("pressure_cgroup_unavailable")
    return str(path)


def _systemd_client_environment() -> tuple[tuple[str, str], ...]:
    """Only the user-manager bus locator crosses into trusted systemd clients."""
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return tuple(sorted(environment.items()))


def _context_client_environment(context: _CgroupContext) -> dict[str, str]:
    return dict(context.client_environment)


def _systemd_cgroup_preflight(
    deadline: float, cwd: Path, client_environment: tuple[tuple[str, str], ...]
) -> tuple[str, str]:
    systemd_run = _trusted_system_binary(_SYSTEMD_RUN_PATH)
    systemctl = _trusted_system_binary(_SYSTEMCTL_PATH)
    try:
        controllers = Path("/sys/fs/cgroup/cgroup.controllers").read_text(
            encoding="ascii"
        )
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if not controllers.strip() or time.monotonic() >= deadline:
        raise PressureProtocolError("pressure_cgroup_unavailable")
    try:
        stdout, _stderr, returncode = _run_bounded(
            [systemctl, "--user", "is-system-running"],
            cwd,
            deadline,
            maximum=128,
            env=dict(client_environment),
        )
    except (OSError, PressureProtocolError) as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if returncode != 0 or stdout.strip() not in {b"running", b"degraded"}:
        raise PressureProtocolError("pressure_cgroup_unavailable")
    return systemd_run, systemctl


def _lexically_safe_path(path: Path, *, allow_final_symlink: bool) -> None:
    """Reject link traversal, except for a deliberately resolved venv executable."""
    if not path.is_absolute():
        raise PressureProtocolError("pressure_cgroup_unavailable")
    current = Path(path.anchor)
    parts = path.parts[1:]
    try:
        for index, component in enumerate(parts):
            current /= component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) and (
                not allow_final_symlink or index != len(parts) - 1
            ):
                raise PressureProtocolError("pressure_cgroup_unavailable")
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error


def _trusted_digest(
    path: Path,
    maximum: int,
    *,
    allowed_uids: frozenset[int],
    allow_final_symlink: bool,
) -> tuple[str, str, tuple[int, int]]:
    """Descriptor-hash a non-writable executable and pin its resolved identity."""
    try:
        _lexically_safe_path(path, allow_final_symlink=allow_final_symlink)
        resolved = path.resolve(strict=True)
        _lexically_safe_path(resolved, allow_final_symlink=False)
        metadata = resolved.lstat()
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if (
        not resolved.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in allowed_uids
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PressureProtocolError("pressure_cgroup_unavailable")
    try:
        digest = _safe_regular_sha256(resolved, maximum)
    except PressureProtocolError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    try:
        current = resolved.lstat()
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_unavailable") from error
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid not in allowed_uids
        or current.st_nlink != 1
        or stat.S_IMODE(current.st_mode) & 0o022
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise PressureProtocolError("pressure_cgroup_unavailable")
    return str(resolved), digest, (metadata.st_dev, metadata.st_ino)


def _trusted_private_digest(
    path: Path, maximum: int
) -> tuple[str, str, tuple[int, int]]:
    """Accept only the current user's non-writable, non-linked helper module."""
    return _trusted_digest(
        path,
        maximum,
        allowed_uids=frozenset({os.getuid()}),
        allow_final_symlink=False,
    )


def _trusted_interpreter_digest(
    path: Path, maximum: int
) -> tuple[str, str, tuple[int, int]]:
    """Accept a pinned root system interpreter or private venv interpreter."""
    return _trusted_digest(
        path,
        maximum,
        allowed_uids=frozenset({0, os.getuid()}),
        allow_final_symlink=True,
    )


def _build_cgroup_context(deadline: float, cwd: Path) -> _CgroupContext:
    """Do the one baseline-wide cgroup probe and pin helper execution inputs."""
    client_environment = _systemd_client_environment()
    systemd_run, systemctl = _systemd_cgroup_preflight(
        deadline, cwd, client_environment
    )
    if time.monotonic() >= deadline:
        raise PressureProtocolError("pressure_timeout")
    helper_path, helper_sha256, helper_identity = _trusted_private_digest(
        Path(__file__), _MAX_HELPER_BYTES
    )
    python_path, python_sha256, python_identity = _trusted_interpreter_digest(
        Path(sys.executable), 64 * 1024 * 1024
    )
    if time.monotonic() >= deadline:
        raise PressureProtocolError("pressure_timeout")
    return _CgroupContext(
        systemd_run=systemd_run,
        systemctl=systemctl,
        helper_path=helper_path,
        helper_sha256=helper_sha256,
        helper_identity=helper_identity,
        python_path=python_path,
        python_sha256=python_sha256,
        python_identity=python_identity,
        client_environment=client_environment,
    )


def _assert_context_identity(
    path: str, identity: tuple[int, int], *, allowed_uids: frozenset[int]
) -> None:
    try:
        metadata = Path(path).lstat()
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_launch_invalid") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in allowed_uids
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise PressureProtocolError("pressure_cgroup_launch_invalid")


def _safe_private_launch(path: Path) -> dict[str, Any]:
    """Helper-side read: a one-shot private launch record has no shell surface."""
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PressureProtocolError("pressure_cgroup_launch_invalid")
        raw = _safe_regular_bytes(path, MAX_PUBLIC_BYTES)
        path.unlink()
    except (OSError, PressureProtocolError) as error:
        raise PressureProtocolError("pressure_cgroup_launch_invalid") from error
    value = _strict_json_loads(raw)
    expected = {
        "argv",
        "environment",
        "environment_names",
        "helper_sha256",
        "python_sha256",
        "run_dev",
        "run_ino",
    }
    if (
        type(value) is not dict
        or set(value) != expected
        or type(value["argv"]) is not list
        or not value["argv"]
        or any(type(item) is not str or not item for item in value["argv"])
        or type(value["environment"]) is not dict
        or type(value["environment_names"]) is not list
        or value["environment_names"] != sorted(value["environment"])
        or any(
            type(key) is not str or _ENV_NAME_PATTERN.fullmatch(key) is None
            for key in value["environment_names"]
        )
        or set(value["environment"]) != set(value["environment_names"])
        or any(
            type(key) is not str or type(item) is not str
            for key, item in value["environment"].items()
        )
        or not set(_MINIMAL_SELECTED_ENV).issubset(value["environment"])
        or any(key in _FORBIDDEN_SELECTED_ENV for key in value["environment"])
        or any(
            type(value[name]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
            for name in ("helper_sha256", "python_sha256")
        )
        or type(value["run_dev"]) is not int
        or type(value["run_ino"]) is not int
    ):
        raise PressureProtocolError("pressure_cgroup_launch_invalid")
    return value


def _cgroup_helper(launch_path: str) -> None:
    """Exec the selected hash-pinned executable after deleting its secret record."""
    try:
        launch = _safe_private_launch(Path(launch_path))
        _helper_path, helper_sha256, _helper_identity = _trusted_private_digest(
            Path(__file__), _MAX_HELPER_BYTES
        )
        _python_path, python_sha256, _python_identity = _trusted_interpreter_digest(
            Path(sys.executable), 64 * 1024 * 1024
        )
        if (
            helper_sha256 != launch["helper_sha256"]
            or python_sha256 != launch["python_sha256"]
        ):
            raise PressureProtocolError("pressure_cgroup_launch_invalid")
        current = os.stat(".")
        if (current.st_dev, current.st_ino) != (launch["run_dev"], launch["run_ino"]):
            raise PressureProtocolError("pressure_cgroup_launch_invalid")
        run_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchdir(run_fd)
        finally:
            os.close(run_fd)
        os.execve(launch["argv"][0], launch["argv"], launch["environment"])
    except (OSError, ValueError, TypeError, PressureProtocolError, SystemExit):
        os._exit(126)


def _unit_name(command_hash: str, run_fd: int, position: int) -> str:
    inode = os.fstat(run_fd).st_ino
    return (
        f"semantic-reheating-{command_hash[:12]}-{inode:x}-{os.getpid():x}-{position}"
    )


def _unit_collected(
    context: _CgroupContext, unit: str, cwd: Path, deadline: float
) -> bool:
    probe_deadline = min(deadline, time.monotonic() + _CGROUP_STOP_SECONDS)
    if probe_deadline <= time.monotonic():
        return False
    try:
        stdout, _stderr, returncode = _run_bounded(
            [
                context.systemctl,
                "--user",
                "show",
                unit,
                "--property=LoadState",
                "--value",
            ],
            cwd,
            probe_deadline,
            maximum=128,
            env=_context_client_environment(context),
        )
    except (OSError, PressureProtocolError):
        return False
    return returncode == 0 and stdout.strip() == b"not-found"


def _cleanup_cgroup_unit(context: _CgroupContext, unit: str, cwd: Path) -> None:
    """Force every cgroup member out and require systemd to collect the unit."""
    cleanup_deadline = time.monotonic() + _CGROUP_STOP_SECONDS
    for action in (
        ["kill", "--kill-whom=all", unit],
        ["stop", unit],
        ["reset-failed", unit],
    ):
        if cleanup_deadline <= time.monotonic():
            break
        try:
            _run_bounded(
                [context.systemctl, "--user", *action],
                cwd,
                cleanup_deadline,
                maximum=128,
                env=_context_client_environment(context),
            )
        except (OSError, PressureProtocolError):
            pass
    while time.monotonic() < cleanup_deadline:
        if _unit_collected(context, unit, cwd, cleanup_deadline):
            return
        time.sleep(0.02)
    raise PressureProtocolError("pressure_cgroup_cleanup_failed")


def _unlink_private_at(directory: int, name: str) -> None:
    try:
        os.unlink(_private_name(name), dir_fd=directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise PressureProtocolError("pressure_cgroup_launch_invalid") from error


def _run_selected_cgroup(
    argv: list[str],
    cwd: Path,
    deadline: float,
    *,
    context: _CgroupContext,
    environment: dict[str, str],
    run_fd: int,
    launch_name: str,
    command_hash: str,
    position: int,
) -> tuple[bytes, bytes, int]:
    """Contain one selected execution in a transient user service cgroup v2."""
    _assert_context_identity(
        context.helper_path,
        context.helper_identity,
        allowed_uids=frozenset({os.getuid()}),
    )
    _assert_context_identity(
        context.python_path,
        context.python_identity,
        allowed_uids=frozenset({0, os.getuid()}),
    )
    launch = {
        "argv": argv,
        "environment": environment,
        "environment_names": sorted(environment),
        "helper_sha256": context.helper_sha256,
        "python_sha256": context.python_sha256,
        "run_dev": os.fstat(run_fd).st_dev,
        "run_ino": os.fstat(run_fd).st_ino,
    }
    _write_private_at(run_fd, launch_name, _canonical_bytes(launch))
    run_path = _fd_path(run_fd)
    launch_path = run_path / launch_name
    unit = _unit_name(command_hash, run_fd, position)
    service_argv = [
        context.systemd_run,
        "--user",
        "--pipe",
        "--wait",
        "--collect",
        "--quiet",
        f"--unit={unit}",
        "--property=KillMode=control-group",
        "--property=TimeoutStopSec=200ms",
        "--property=SendSIGKILL=yes",
        f"--working-directory={run_path}",
        "--service-type=exec",
        "--",
        context.python_path,
        context.helper_path,
        "--pressure-cgroup-helper",
        str(launch_path),
    ]
    try:
        stdout, stderr, returncode = _run_bounded(
            service_argv,
            cwd,
            deadline,
            env=_context_client_environment(context),
            trusted_collected_service=True,
        )
    except BaseException as error:
        try:
            _cleanup_cgroup_unit(context, unit, cwd)
        except PressureProtocolError as cleanup_error:
            raise cleanup_error from error
        raise
    finally:
        _unlink_private_at(run_fd, launch_name)
    if returncode != 0:
        _cleanup_cgroup_unit(context, unit, cwd)
    return stdout, stderr, returncode


def _run_bounded(
    argv: list[str],
    cwd: Path | str,
    deadline: float,
    maximum: int | None = None,
    *,
    env: dict[str, str],
    pass_fds: tuple[int, ...] = (),
    trusted_collected_service: bool = False,
) -> tuple[bytes, bytes, int]:
    """Capture bounded output and terminate every member of the selected session."""
    maximum = MAX_CAPTURE_BYTES if maximum is None else maximum
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
            pass_fds=pass_fds,
        )
    except BaseException:
        selector.close()
        raise
    assert process.stdout is not None and process.stderr is not None
    process_group = process.pid
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    failure: str | None = None
    returncode: int | None = None
    cleaned = False
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        parent_exited = False
        while not parent_exited:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "pressure_timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                failure = _read_ready_stream(selector, key.fileobj, streams, maximum)
                if failure is not None:
                    break
            if failure is not None:
                break
            parent_exited = _direct_parent_exited(process.pid)
        if failure is None and parent_exited:
            failure = _drain_available_streams(selector, streams, maximum)
        if trusted_collected_service and parent_exited:
            process.wait(timeout=0)
            cleaned = True
        else:
            cleaned = True
            _cleanup_selected_process(process, process_group)
        returncode = process.returncode
        if failure is None:
            failure = _drain_closed_streams(selector, streams, maximum)
    except BaseException:
        if not cleaned:
            cleaned = True
            _cleanup_selected_process(process, process_group)
        raise
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    stdout, stderr = (bytes(streams[process.stdout]), bytes(streams[process.stderr]))
    if failure is not None:
        error = PressureProtocolError(failure)
        error.stdout = stdout  # type: ignore[attr-defined]
        error.stderr = stderr  # type: ignore[attr-defined]
        raise error
    assert returncode is not None
    return stdout, stderr, returncode


def _run_baseline_in_state(repo: Path, state_root: _StateRoot) -> dict[str, Any]:
    """Run the selected stack while all private I/O is descriptor-relative."""
    repo_root = _repo_root(repo)
    if any(repo_root.rglob("SKILL.md")):
        raise PressureProtocolError("pressure_skill_not_absent")
    protocol = load_public_protocol(repo_root)
    try:
        config, raw_config = _load_json_at(state_root.fd, _PRIVATE_CONFIG_NAME)
    except PressureProtocolError as error:
        if error.code == "pressure_file_missing":
            raise PressureProtocolError("pressure_stack_missing") from error
        if error.code == "pressure_invalid_json":
            raise PressureProtocolError("pressure_invalid_config") from error
        raise
    validated = _validate_config(config, raw_config)
    config = validated["config"]
    argv = config["command_argv"]
    executable, executable_hash = _resolve_executable(argv, config["executable_sha256"])
    command_hash = _sha256(
        _canonical_bytes({"argv": argv, "executable_sha256": executable_hash})
    )
    selected_environment = _selected_environment(config["environment_allowlist"])
    run_name = "run-" + command_hash[:12]
    run_fd = _create_run_directory(state_root.fd, run_name)
    state_root.children.append(run_fd)
    run_path = _fd_path(run_fd)
    run_cwd = str(run_path)
    outcomes: list[dict[str, str]] = []
    evidence_entries: list[dict[str, str]] = []
    total_counts: dict[str, int] = {
        "turns": 0,
        "tools": 0,
        "tokens": 0,
        "elapsed_seconds": 0,
    }
    total_cost = Decimal(0)
    baseline_started = time.monotonic()
    deadline = baseline_started + config["caps"]["elapsed_seconds"]
    cgroup_context = _build_cgroup_context(deadline, Path(run_cwd))
    for position, scenario in enumerate(protocol["scenarios"]["scenarios"], start=1):
        if time.monotonic() >= deadline:
            raise PressureProtocolError("pressure_timeout")
        scenario_id = scenario["scenario_id"]
        usage_name = f"usage-{position}.json"
        usage_path = f"{run_cwd}/{usage_name}"
        prompt = (
            "scenario_id: "
            + scenario_id
            + "\n"
            + scenario["prompt"]
            + "\n"
            + scenario["task_pressure"]
        )
        expanded = [
            executable if index == 0 else part for index, part in enumerate(argv)
        ]
        expanded = [
            prompt
            if part == "{prompt}"
            else str(usage_path)
            if part == "{usage_file}"
            else part
            for part in expanded
        ]
        try:
            _assert_run_directory(state_root.fd, run_name, run_fd)
            stdout, stderr, returncode = _run_selected_cgroup(
                expanded,
                Path(run_cwd),
                deadline,
                context=cgroup_context,
                environment=selected_environment,
                run_fd=run_fd,
                launch_name=f"launch-{position}.json",
                command_hash=command_hash,
                position=position,
            )
            _assert_run_directory(state_root.fd, run_name, run_fd)
        except PressureProtocolError as error:
            _write_private_at(
                run_fd, f"stdout-{position}.bin", getattr(error, "stdout", b"")
            )
            _write_private_at(
                run_fd, f"stderr-{position}.bin", getattr(error, "stderr", b"")
            )
            raise
        stdout_digest = _write_private_at(run_fd, f"stdout-{position}.bin", stdout)
        stderr_digest = _write_private_at(run_fd, f"stderr-{position}.bin", stderr)
        if returncode != 0:
            raise PressureProtocolError("pressure_subprocess_failed")
        try:
            usage_value, usage_raw = _load_json_at(run_fd, usage_name)
        except PressureProtocolError as error:
            raise PressureProtocolError("pressure_invalid_usage") from error
        consumption = _usage(usage_value, config["caps"], config["usage_report"])
        if time.monotonic() > deadline:
            raise PressureProtocolError("pressure_timeout")
        for key in ("turns", "tools", "tokens"):
            if consumption[key] != "unsupported":
                total_counts[key] += consumption[key]
        if consumption["cost"] != "unsupported":
            total_cost += _decimal(consumption["cost"])
        try:
            response = _strict_json_loads(stdout)
        except PressureProtocolError:
            response = None
        outcomes.append(
            {
                "scenario_id": scenario_id,
                "outcome_code": _response_outcome(
                    scenario, response, protocol["rubric"]
                ),
            }
        )
        evidence_entries.append(
            {
                "scenario_id": scenario_id,
                "stdout_sha256": stdout_digest,
                "stderr_sha256": stderr_digest,
                "usage_sha256": _sha256(usage_raw),
            }
        )
    measured_elapsed = time.monotonic() - baseline_started
    if measured_elapsed > config["caps"]["elapsed_seconds"]:
        raise PressureProtocolError("pressure_timeout")
    total_counts["elapsed_seconds"] = math.ceil(measured_elapsed)
    if any(
        total_counts[key] > config["caps"][key]
        for key in ("turns", "tools", "tokens", "elapsed_seconds")
    ) or total_cost > _decimal(config["caps"]["cost"]):
        raise PressureProtocolError("pressure_budget_exceeded")
    if len(outcomes) != 6:
        raise PressureProtocolError("pressure_outcome_count_invalid")
    failure_codes = {
        entry["outcome_code"] for entry in outcomes if entry["outcome_code"] != "pass"
    }
    if len(failure_codes) < 2:
        raise PressureProtocolError("pressure_failure_classes_insufficient")
    manifest = _validate_evidence_manifest(
        {
            "contract_version": "1.0",
            "mode": "baseline",
            "scenario_set_sha256": protocol["hashes"]["scenarios"],
            "rubric_sha256": protocol["hashes"]["rubric"],
            "scenario_schema_sha256": protocol["hashes"]["scenarios_schema"],
            "rubric_schema_sha256": protocol["hashes"]["rubric_schema"],
            "baseline_summary_schema_sha256": protocol["hashes"][
                "baseline_summary_schema"
            ],
            "stack_receipt_schema_sha256": protocol["hashes"]["stack_receipt_schema"],
            "stack_config_sha256": validated["config_sha256"],
            "command_sha256": command_hash,
            "entries": evidence_entries,
        }
    )
    manifest_digest = _write_private_at(
        run_fd,
        "baseline-evidence-manifest.json",
        _canonical_bytes(manifest),
        replace=True,
    )
    summary = {
        "contract_version": "1.0",
        "mode": "baseline",
        "scenario_set_sha256": protocol["hashes"]["scenarios"],
        "rubric_sha256": protocol["hashes"]["rubric"],
        "scenario_schema_sha256": protocol["hashes"]["scenarios_schema"],
        "rubric_schema_sha256": protocol["hashes"]["rubric_schema"],
        "baseline_summary_schema_sha256": protocol["hashes"]["baseline_summary_schema"],
        "stack_receipt_schema_sha256": protocol["hashes"]["stack_receipt_schema"],
        "stack_config_sha256": validated["config_sha256"],
        "command_sha256": command_hash,
        "supports": _supports(config),
        "outcomes": outcomes,
        "budget_consumption": {
            **{
                key: (
                    total_counts[key] if config["usage_report"][key] else "unsupported"
                )
                for key in ("turns", "tools", "tokens")
            },
            "elapsed_seconds": total_counts["elapsed_seconds"],
            "cost": (
                format(total_cost, "f")
                if config["usage_report"]["cost"]
                else "unsupported"
            ),
        },
        "private_transcript_receipt": {
            "name": "baseline-evidence-manifest.json",
            "sha256": manifest_digest,
        },
    }
    return summary


def run_baseline(repo: Path) -> dict[str, Any]:
    """Run all six scenarios only against an explicitly configured local stack."""
    repo_root = _repo_root(repo)
    state_root = _state_root(repo_root)
    try:
        return _run_baseline_in_state(repo_root, state_root)
    finally:
        state_root.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--pressure-cgroup-helper":
        _cgroup_helper(sys.argv[2])
    raise SystemExit(126)
