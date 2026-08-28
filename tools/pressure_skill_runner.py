"""Private, offline-only runner for the Task 17 pressure baseline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import stat
import subprocess
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

MAX_PUBLIC_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 8 * 1024
WORKTREE_DISCOVERY_TIMEOUT_SECONDS = 2
SCENARIO_ORDER = (
    "exact-retry-loop",
    "plan-oscillation",
    "productive-pagination",
    "blocked-authority",
    "unsafe-write",
    "exhausted-budget",
)
_PRIVATE_CONFIG_NAME = "pressure-stack.local.json"


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


def _decimal(value: object) -> Decimal:
    if type(value) is not str:
        raise PressureProtocolError("pressure_invalid_decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise PressureProtocolError("pressure_invalid_decimal") from error
    if not parsed.is_finite() or parsed < 0:
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


def _load_json(
    path: Path, maximum: int = MAX_PUBLIC_BYTES
) -> tuple[dict[str, Any], bytes]:
    raw = _safe_regular_bytes(path, maximum)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PressureProtocolError("pressure_invalid_json") from error
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


def _baseline_summary_schema() -> dict[str, Any]:
    """Load only the committed, bounded public projection contract."""
    path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "semantic-reheating"
        / "references"
        / "baseline-summary.schema.json"
    )
    schema, _ = _load_json(path, MAX_PUBLIC_BYTES)
    Draft202012Validator.check_schema(schema)
    return schema


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
        stdout, _stderr, returncode = _run_bounded(
            [git, "-C", str(repo_root), "worktree", "list", "--porcelain"],
            repo_root,
            time.monotonic() + WORKTREE_DISCOVERY_TIMEOUT_SECONDS,
            maximum=MAX_PUBLIC_BYTES,
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


def _state_root(repo_root: Path) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    candidate = base / "semantic-reheating" / "pressure-baselines"
    worktree_roots = _worktree_roots(repo_root)
    precreation_target = candidate.resolve(strict=False)
    if any(precreation_target.is_relative_to(root) for root in worktree_roots):
        raise PressureProtocolError("pressure_state_inside_repository")
    if candidate.exists():
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PressureProtocolError("pressure_unsafe_state_root")
    else:
        candidate.mkdir(mode=0o700, parents=True)
    os.chmod(candidate, 0o700)
    resolved = candidate.resolve(strict=True)
    if any(resolved.is_relative_to(root) for root in worktree_roots):
        raise PressureProtocolError("pressure_state_inside_repository")
    return resolved


def _private_file(path: Path, root: Path) -> Path:
    if not path.absolute().is_relative_to(root):
        raise PressureProtocolError("pressure_private_path_escape")
    return path


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
        "config_sha256": _sha256(_canonical_bytes(json.loads(config_raw))),
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


def _write_private(path: Path, data: bytes) -> str:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
    return _sha256(data)


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


def sanitize_projection(summary: dict[str, Any]) -> dict[str, Any]:
    """Return only the future public receipt shape; never include private receipts."""
    fields = {
        "contract_version",
        "mode",
        "scenario_set_sha256",
        "rubric_sha256",
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
        _validate_schema(
            _baseline_summary_schema(), {field: summary[field] for field in fields}
        )
    except (KeyError, PressureProtocolError) as error:
        raise PressureProtocolError("pressure_invalid_public_contract") from error
    # Canonical serialize/parse detaches all nested public values from private state.
    return json.loads(_canonical_bytes({field: summary[field] for field in fields}))


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


def _stop_process_group(process_group: int, *, term_sent: bool = False) -> None:
    """Terminate the fresh selected session even after its leader exits."""
    if not term_sent:
        _signal_process_group(process_group, signal.SIGTERM)
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


def _cleanup_selected_process(
    process: subprocess.Popen[bytes], process_group: int
) -> None:
    _signal_process_group(process_group, signal.SIGTERM)
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired as error:
        raise PressureProtocolError("pressure_process_group_cleanup_failed") from error
    _stop_process_group(process_group, term_sent=True)


def _run_bounded(
    argv: list[str], cwd: Path, deadline: float, maximum: int | None = None
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
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "pressure_timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                ready_stream: Any = key.fileobj
                assert hasattr(ready_stream, "read")
                chunk = os.read(
                    ready_stream.fileno(),
                    maximum + 1 - len(streams[ready_stream]),
                )
                if not chunk:
                    selector.unregister(ready_stream)
                    continue
                streams[ready_stream].extend(chunk)
                if len(streams[ready_stream]) > maximum:
                    failure = "pressure_output_too_large"
                    break
            if failure is not None:
                break
        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "pressure_timeout"
            else:
                try:
                    process.wait(timeout=remaining)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    failure = "pressure_timeout"
        if failure is not None:
            _cleanup_selected_process(process, process_group)
            cleaned = True
        else:
            # Output and the direct return status are complete before normal cleanup.
            _cleanup_selected_process(process, process_group)
            cleaned = True
    except BaseException:
        if not cleaned:
            _cleanup_selected_process(process, process_group)
            cleaned = True
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


def run_baseline(repo: Path) -> dict[str, Any]:
    """Run all six scenarios only against an explicitly configured local stack."""
    repo_root = _repo_root(repo)
    if any(repo_root.rglob("SKILL.md")):
        raise PressureProtocolError("pressure_skill_not_absent")
    protocol = load_public_protocol(repo_root)
    state_root = _state_root(repo_root)
    config_path = _private_file(state_root / _PRIVATE_CONFIG_NAME, state_root)
    if not config_path.exists():
        raise PressureProtocolError("pressure_stack_missing")
    config, raw_config = _load_json(config_path)
    validated = _validate_config(config, raw_config)
    config = validated["config"]
    argv = config["command_argv"]
    executable, executable_hash = _resolve_executable(argv, config["executable_sha256"])
    command_hash = _sha256(
        _canonical_bytes({"argv": argv, "executable_sha256": executable_hash})
    )
    run_dir = state_root / ("run-" + command_hash[:12])
    if run_dir.exists():
        raise PressureProtocolError("pressure_run_exists")
    run_dir.mkdir(mode=0o700)
    outcomes: list[dict[str, str]] = []
    total_counts: dict[str, int] = {
        "turns": 0,
        "tools": 0,
        "tokens": 0,
        "elapsed_seconds": 0,
    }
    total_cost = Decimal(0)
    baseline_started = time.monotonic()
    deadline = baseline_started + config["caps"]["elapsed_seconds"]
    for position, scenario in enumerate(protocol["scenarios"]["scenarios"], start=1):
        if time.monotonic() >= deadline:
            raise PressureProtocolError("pressure_timeout")
        scenario_id = scenario["scenario_id"]
        usage_path = run_dir / f"usage-{position}.json"
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
            stdout, stderr, returncode = _run_bounded(expanded, run_dir, deadline)
        except PressureProtocolError as error:
            _write_private(
                run_dir / f"stdout-{position}.bin", getattr(error, "stdout", b"")
            )
            _write_private(
                run_dir / f"stderr-{position}.bin", getattr(error, "stderr", b"")
            )
            raise
        stdout_digest = _write_private(run_dir / f"stdout-{position}.bin", stdout)
        _write_private(run_dir / f"stderr-{position}.bin", stderr)
        if returncode != 0:
            raise PressureProtocolError("pressure_subprocess_failed")
        usage_value, _ = _load_json(usage_path)
        consumption = _usage(usage_value, config["caps"], config["usage_report"])
        if time.monotonic() > deadline:
            raise PressureProtocolError("pressure_timeout")
        for key in ("turns", "tools", "tokens"):
            if consumption[key] != "unsupported":
                total_counts[key] += consumption[key]
        if consumption["cost"] != "unsupported":
            total_cost += _decimal(consumption["cost"])
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = None
        outcomes.append(
            {
                "scenario_id": scenario_id,
                "outcome_code": _response_outcome(
                    scenario, response, protocol["rubric"]
                ),
            }
        )
        _write_private(
            run_dir / f"receipt-{position}.json",
            _canonical_bytes({"stdout_sha256": stdout_digest}),
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
    summary = {
        "contract_version": "1.0",
        "mode": "baseline",
        "scenario_set_sha256": protocol["hashes"]["scenarios"],
        "rubric_sha256": protocol["hashes"]["rubric"],
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
            "name": "receipt-1.json",
            "sha256": _sha256(
                _safe_regular_bytes(run_dir / "receipt-1.json", MAX_CAPTURE_BYTES)
            ),
        },
    }
    return summary
