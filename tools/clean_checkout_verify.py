"""Reproduce a committed candidate from a caller-owned clean checkout.

The explicit mode deliberately writes its closed receipt outside both the source and
clone.  It never invokes pressure/live runners or provider-backed executors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.public_hygiene import scan_repository


class CleanCheckoutError(RuntimeError):
    """A closed clean-checkout verification failure with no command output."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""


GateRunner = Callable[[list[str], Path, dict[str, str]], CommandResult]

COMMAND_NAMES = (
    "clone",
    "checkout_exact_commit",
    "clone_head",
    "clone_clean",
    "uv_sync_offline",
    "reheat_help",
    "fixture_validate",
    "benchmark_replay_byte_compare",
    "python_generic_example",
    "typescript_npm_ci_offline",
    "typescript_typecheck",
    "typescript_test",
    "article_npm_ci_offline",
    "article_generate_check",
    "article_validate",
    "article_asset_check",
    "article_node_modules_cleanup",
    "domain_json_registry",
    "python_suite",
    "hygiene_tracked",
    "hygiene_history",
    "forbidden_artifact_paths",
    "diff_check",
    "status_clean",
)
_STATUS = frozenset({"pass", "fail", "not_run"})
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_FORBIDDEN_SEGMENTS = frozenset({".hermes", "rdd", "private"})


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise CleanCheckoutError(f"git {' '.join(args[:2])} failed")
    return completed.stdout.strip()


def _subprocess_gate_runner(
    args: list[str], root: Path, environment: dict[str, str]
) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=root,
        env={**os.environ, **environment},
        capture_output=True,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _require_clean_source(root: Path) -> None:
    if _git(root, "status", "--porcelain"):
        raise CleanCheckoutError(
            "explicit verification requires a clean source worktree"
        )


def _resolve_commit(root: Path, requested: str | None) -> str:
    if requested is not None and not _SHA.fullmatch(requested):
        raise CleanCheckoutError("--commit must be an exact 40-character SHA")
    candidate = requested if requested is not None else _git(root, "rev-parse", "HEAD")
    sha = _git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
    reachable = set(_git(root, "rev-list", "--all").splitlines())
    if sha not in reachable:
        raise CleanCheckoutError("--commit must be reachable from a source ref")
    return sha


def _safe_clone_target(root: Path, clone_dir: Path) -> Path:
    if not clone_dir.is_absolute() or ".." in clone_dir.parts:
        raise CleanCheckoutError("--clone-dir must be an absolute safe temporary path")
    target = clone_dir.resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if target.parent != temp_root or target == temp_root or not target.name:
        raise CleanCheckoutError(
            "--clone-dir must be one safe leaf below the system temp root"
        )
    if _is_within(target, root) or _is_within(root, target):
        raise CleanCheckoutError("--clone-dir must not overlap the source repository")
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise CleanCheckoutError(
            "--clone-dir must be a non-symlink directory or absent"
        )
    return target


def _ensure_receipt_parent(output: Path) -> Path:
    if not output.is_absolute() or ".." in output.parts or not output.name:
        raise CleanCheckoutError("--receipt must be an absolute safe file path")
    current = Path(output.anchor)
    for part in output.parts[1:-1]:
        current /= part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass
        mode = os.lstat(current).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CleanCheckoutError("--receipt parent contains an unsafe path")
    try:
        mode = os.lstat(output).st_mode
    except FileNotFoundError:
        return output.parent
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise CleanCheckoutError("--receipt must be a regular file or absent")
    return output.parent


def _safe_receipt_target(root: Path, clone_dir: Path, receipt: Path) -> Path:
    parent = _ensure_receipt_parent(receipt)
    target = (parent / receipt.name).resolve(strict=False)
    if _is_within(target, root) or _is_within(target, clone_dir):
        raise CleanCheckoutError("--receipt must be outside the source and clone")
    return target


def _archive(root: Path, commit: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise CleanCheckoutError("unable to archive exact commit")
    return {
        "sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "size_bytes": len(completed.stdout),
    }


def _initial_outcomes() -> list[dict[str, str]]:
    return [{"name": name, "status": "not_run"} for name in COMMAND_NAMES]


def _set_outcome(outcomes: list[dict[str, str]], name: str, status: str) -> None:
    if name not in COMMAND_NAMES or status not in _STATUS:
        raise CleanCheckoutError("internal closed outcome contract failure")
    for outcome in outcomes:
        if outcome["name"] == name:
            outcome["status"] = status
            return
    raise CleanCheckoutError("internal missing outcome")


def validate_receipt(receipt: object, *, expected_commit: str | None = None) -> None:
    """Reject any receipt shape, gate order, or digest contract drift."""
    if not isinstance(receipt, dict) or set(receipt) != {
        "archive",
        "command_outcomes",
        "commit_sha",
        "receipt_version",
        "status",
    }:
        raise CleanCheckoutError("receipt has unknown or missing fields")
    commit = receipt["commit_sha"]
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise CleanCheckoutError("receipt commit SHA is invalid")
    if expected_commit is not None and commit != expected_commit:
        raise CleanCheckoutError("receipt commit SHA does not match requested commit")
    archive = receipt["archive"]
    if (
        not isinstance(archive, dict)
        or set(archive) != {"sha256", "size_bytes"}
        or not isinstance(archive["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
        or type(archive["size_bytes"]) is not int
        or archive["size_bytes"] < 0
    ):
        raise CleanCheckoutError("receipt archive is invalid")
    outcomes = receipt["command_outcomes"]
    if not isinstance(outcomes, list) or len(outcomes) != len(COMMAND_NAMES):
        raise CleanCheckoutError("receipt command outcomes are incomplete")
    for name, outcome in zip(COMMAND_NAMES, outcomes, strict=True):
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"name", "status"}
            or outcome["name"] != name
            or outcome["status"] not in _STATUS
        ):
            raise CleanCheckoutError(
                "receipt command outcome order or vocabulary is invalid"
            )
    status = receipt["status"]
    if receipt["receipt_version"] != "1.0" or status not in {"pass", "fail"}:
        raise CleanCheckoutError("receipt version or status is invalid")
    outcome_statuses = [outcome["status"] for outcome in outcomes]
    if status == "pass" and any(item != "pass" for item in outcome_statuses):
        raise CleanCheckoutError("pass receipt contains a non-pass outcome")
    if status == "fail" and "fail" not in outcome_statuses:
        raise CleanCheckoutError("fail receipt has no failed outcome")


def _write_receipt(receipt: Path, payload: dict[str, Any]) -> None:
    validate_receipt(payload)
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    temporary = receipt.with_name(f".{receipt.name}.tmp-{os.getpid()}")
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt)
        if receipt.read_bytes() != encoded:
            raise CleanCheckoutError("receipt byte readback failed")
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _forbidden_artifact_paths(root: Path) -> bool:
    tracked = _git(root, "ls-files", "-z").split("\0")
    objects = _git(root, "rev-list", "--objects", "--all").splitlines()
    paths = [path for path in tracked if path]
    paths.extend(line.split(" ", 1)[1] for line in objects if " " in line)
    return any(bool(set(Path(path).parts) & _FORBIDDEN_SEGMENTS) for path in paths)


def _remove_article_node_modules(clone: Path) -> None:
    target = clone / "tools" / "assets" / "node_modules"
    if not target.exists():
        return
    mode = os.lstat(target).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise CleanCheckoutError("article node_modules cleanup target is unsafe")
    shutil.rmtree(target)


def _run_gate(
    outcomes: list[dict[str, str]],
    name: str,
    args: list[str],
    clone: Path,
    runner: GateRunner,
    environment: dict[str, str],
) -> CommandResult:
    result = runner(args, clone, environment)
    _set_outcome(outcomes, name, "pass" if result.returncode == 0 else "fail")
    if result.returncode:
        raise CleanCheckoutError(f"gate failed: {name}")
    return result


def _run_clone_steps(
    outcomes: list[dict[str, str]], root: Path, clone: Path, commit: str
) -> None:
    if clone.exists():
        shutil.rmtree(clone)
    cloned = subprocess.run(
        [
            "git",
            "clone",
            "--no-local",
            "--quiet",
            "--no-checkout",
            str(root),
            str(clone),
        ],
        capture_output=True,
        check=False,
    )
    _set_outcome(outcomes, "clone", "pass" if cloned.returncode == 0 else "fail")
    if cloned.returncode:
        raise CleanCheckoutError("unable to create clean checkout")
    checkout = subprocess.run(
        ["git", "checkout", "--detach", "--quiet", commit],
        cwd=clone,
        capture_output=True,
        check=False,
    )
    _set_outcome(
        outcomes,
        "checkout_exact_commit",
        "pass" if checkout.returncode == 0 else "fail",
    )
    if checkout.returncode:
        raise CleanCheckoutError("unable to checkout exact commit")
    head_matches = _git(clone, "rev-parse", "HEAD") == commit
    _set_outcome(outcomes, "clone_head", "pass" if head_matches else "fail")
    if not head_matches:
        raise CleanCheckoutError("clean checkout HEAD does not match requested commit")
    clean = not _git(clone, "status", "--porcelain")
    _set_outcome(outcomes, "clone_clean", "pass" if clean else "fail")
    if not clean:
        raise CleanCheckoutError("clean checkout is dirty")


def verify_clean_checkout(
    root: Path,
    clone_dir: Path,
    receipt: Path,
    *,
    commit: str | None = None,
    gate_runner: GateRunner | None = None,
) -> dict[str, Any]:
    """Run deterministic, offline gates in a detached clone and write a closed receipt."""
    source = root.resolve()
    _require_clean_source(source)
    exact_commit = _resolve_commit(source, commit)
    clone = _safe_clone_target(source, clone_dir)
    output = _safe_receipt_target(source, clone, receipt)
    archive = _archive(source, exact_commit)
    outcomes = _initial_outcomes()
    runner = _subprocess_gate_runner if gate_runner is None else gate_runner
    environment = {"UV_OFFLINE": "1"}
    failure: CleanCheckoutError | None = None
    try:
        _run_clone_steps(outcomes, source, clone, exact_commit)
        _run_gate(
            outcomes,
            "uv_sync_offline",
            ["uv", "sync", "--frozen", "--offline", "--all-groups"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "reheat_help",
            [".venv/bin/reheat", "--help"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "fixture_validate",
            [
                ".venv/bin/reheat",
                "validate",
                "benchmark/corpus/exact-repetition-stall.jsonl",
                "--policy",
                "tests/fixtures/contracts/minimal-run-policy.json",
            ],
            clone,
            runner,
            environment,
        )
        first = _run_gate(
            outcomes,
            "benchmark_replay_byte_compare",
            [
                ".venv/bin/reheat",
                "benchmark",
                "benchmark/corpus",
                "--manifest",
                "benchmark/scenarios/manifest.json",
                "--format",
                "json",
            ],
            clone,
            runner,
            environment,
        )
        second = runner(
            [
                ".venv/bin/reheat",
                "benchmark",
                "benchmark/corpus",
                "--manifest",
                "benchmark/scenarios/manifest.json",
                "--format",
                "json",
            ],
            clone,
            environment,
        )
        if second.returncode or first.stdout != second.stdout:
            _set_outcome(outcomes, "benchmark_replay_byte_compare", "fail")
            raise CleanCheckoutError("gate failed: benchmark_replay_byte_compare")
        _run_gate(
            outcomes,
            "python_generic_example",
            [
                ".venv/bin/python",
                "examples/python-generic-agent/main.py",
                "--scenario",
                "productive",
            ],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "typescript_npm_ci_offline",
            ["npm", "ci", "--offline", "--prefix", "examples/typescript-middleware"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "typescript_typecheck",
            ["npm", "run", "typecheck", "--prefix", "examples/typescript-middleware"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "typescript_test",
            ["npm", "test", "--prefix", "examples/typescript-middleware"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "article_npm_ci_offline",
            ["npm", "ci", "--offline", "--prefix", "tools/assets"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "article_generate_check",
            [".venv/bin/python", "tools/generate_article_data.py", "--check"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "article_validate",
            [
                ".venv/bin/python",
                "tools/validate_article.py",
                "article/semantic-reheating",
            ],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "article_asset_check",
            [".venv/bin/python", "tools/render_assets.py", "--check"],
            clone,
            runner,
            environment,
        )
        try:
            _remove_article_node_modules(clone)
        except CleanCheckoutError:
            _set_outcome(outcomes, "article_node_modules_cleanup", "fail")
            raise
        _set_outcome(outcomes, "article_node_modules_cleanup", "pass")
        _run_gate(
            outcomes,
            "domain_json_registry",
            [".venv/bin/python", "tools/domain_json_registry.py"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "python_suite",
            [
                ".venv/bin/python",
                "-m",
                "pytest",
                "tests",
                "-q",
                "-m",
                "not pressure_live",
            ],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "hygiene_tracked",
            [".venv/bin/python", "tools/public_hygiene.py", "--tracked-only"],
            clone,
            runner,
            environment,
        )
        _run_gate(
            outcomes,
            "hygiene_history",
            [".venv/bin/python", "tools/public_hygiene.py", "--history"],
            clone,
            runner,
            environment,
        )
        forbidden = _forbidden_artifact_paths(clone)
        _set_outcome(
            outcomes, "forbidden_artifact_paths", "fail" if forbidden else "pass"
        )
        if forbidden:
            raise CleanCheckoutError(
                "forbidden artifact path in tracked tree or reachable history"
            )
        _run_gate(
            outcomes,
            "diff_check",
            ["git", "diff", "--check"],
            clone,
            runner,
            environment,
        )
        clean = not _git(clone, "status", "--porcelain")
        _set_outcome(outcomes, "status_clean", "pass" if clean else "fail")
        if not clean:
            raise CleanCheckoutError("clean checkout became dirty")
    except CleanCheckoutError as error:
        failure = error
    payload: dict[str, Any] = {
        "archive": archive,
        "command_outcomes": outcomes,
        "commit_sha": exact_commit,
        "receipt_version": "1.0",
        "status": "fail" if failure else "pass",
    }
    _write_receipt(output, payload)
    if failure:
        raise failure
    return payload


def verify_local(root: Path) -> bool:
    """Preserve the small legacy local-only cleanliness/hygiene check."""
    root = root.resolve()
    _require_clean_source(root)
    with tempfile.TemporaryDirectory(prefix="semantic-reheating-clean-") as temporary:
        checkout = Path(temporary) / "checkout"
        clone = subprocess.run(
            ["git", "clone", "--no-local", "--quiet", str(root), str(checkout)],
            capture_output=True,
            check=False,
        )
        if clone.returncode:
            raise CleanCheckoutError("unable to create clean checkout")
        if _git(checkout, "status", "--porcelain"):
            raise CleanCheckoutError("clean checkout is dirty")
        findings = scan_repository(checkout, tracked_only=True)
        if findings:
            raise CleanCheckoutError("clean checkout hygiene failed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local", action="store_true", help="verify current local committed checkout"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--commit", help="exact reachable 40-character source commit SHA"
    )
    parser.add_argument("--clone-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.local:
            if args.commit or args.clone_dir or args.receipt:
                parser.error(
                    "--local cannot be combined with explicit clone/receipt options"
                )
            verify_local(args.root)
            print("clean checkout verification valid")
            return 0
        if args.clone_dir is None or args.receipt is None:
            parser.error(
                "explicit verification requires both --clone-dir and --receipt"
            )
        receipt = verify_clean_checkout(
            args.root, args.clone_dir, args.receipt, commit=args.commit
        )
    except CleanCheckoutError as error:
        print(f"clean checkout verification failed: {error}", file=sys.stderr)
        return 1
    print(f"clean checkout verification passed for {receipt['commit_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
