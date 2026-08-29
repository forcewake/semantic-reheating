"""Focused contract tests for the external clean-checkout receipt."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from tools.clean_checkout_verify import (
    COMMAND_NAMES,
    CleanCheckoutError,
    CommandResult,
    validate_receipt,
    verify_clean_checkout,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("public source\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "initial")
    return root


def test_explicit_clone_mode_writes_canonical_external_pass_receipt(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    clone = Path(tempfile.gettempdir()) / f"semantic-reheating-test-{uuid.uuid4().hex}"
    receipt = tmp_path / "receipts" / "result.json"
    seen: list[tuple[list[str], Path, dict[str, str]]] = []

    def passing_runner(
        args: list[str], cwd: Path, environment: dict[str, str]
    ) -> CommandResult:
        seen.append((args, cwd, environment))
        article_modules = cwd / "tools" / "assets" / "node_modules"
        if args == ["npm", "ci", "--offline", "--prefix", "tools/assets"]:
            article_modules.mkdir(parents=True)
            (article_modules / "generated.txt").write_text(
                "generated\n", encoding="utf-8"
            )
        if args == [".venv/bin/python", "tools/domain_json_registry.py"]:
            assert not article_modules.exists()
        return CommandResult(0, b"deterministic benchmark\n")

    try:
        result = verify_clean_checkout(root, clone, receipt, gate_runner=passing_runner)
    finally:
        shutil.rmtree(clone, ignore_errors=True)

    assert result["status"] == "pass"
    assert result["commit_sha"] == git(root, "rev-parse", "HEAD")
    assert [outcome["name"] for outcome in result["command_outcomes"]] == list(
        COMMAND_NAMES
    )
    assert {outcome["status"] for outcome in result["command_outcomes"]} == {"pass"}
    assert "article_node_modules_cleanup" in COMMAND_NAMES
    assert (
        result["archive"]["sha256"]
        == hashlib.sha256(
            subprocess.run(
                ["git", "archive", "--format=tar", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
            ).stdout
        ).hexdigest()
    )
    assert json.loads(receipt.read_text(encoding="utf-8")) == result
    assert receipt.read_bytes().endswith(b"\n")
    assert all(cwd.name == clone.name for _, cwd, _ in seen)
    assert all(environment.get("UV_OFFLINE") == "1" for _, _, environment in seen)
    fixture_commands = [
        args for args, _, _ in seen if args[:2] == [".venv/bin/reheat", "validate"]
    ]
    assert fixture_commands == [
        [
            ".venv/bin/reheat",
            "validate",
            "benchmark/corpus/exact-repetition-stall.jsonl",
            "--policy",
            "tests/fixtures/contracts/minimal-run-policy.json",
        ]
    ]
    assert not any(
        args[0].endswith("pressure_skill_runner.py")
        or "benchmark/live" in " ".join(args)
        for args, _, _ in seen
    )


def test_receipt_validator_rejects_closed_contract_drift() -> None:
    receipt = {
        "archive": {"sha256": "a" * 64, "size_bytes": 1},
        "command_outcomes": [
            {"name": name, "status": "pass"} for name in COMMAND_NAMES
        ],
        "commit_sha": "b" * 40,
        "receipt_version": "1.0",
        "status": "pass",
    }
    validate_receipt(receipt, expected_commit="b" * 40)

    unknown = {**receipt, "local_path": "/tmp/forbidden"}
    missing = {key: value for key, value in receipt.items() if key != "archive"}
    reordered = {
        **receipt,
        "command_outcomes": list(reversed(receipt["command_outcomes"])),
    }
    wrong_sha = {**receipt, "commit_sha": "c" * 40}
    for invalid in (unknown, missing, reordered, wrong_sha):
        with pytest.raises(CleanCheckoutError):
            validate_receipt(invalid, expected_commit="b" * 40)


def test_safe_failure_writes_typed_fail_receipt_without_source_writes(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    clone = Path(tempfile.gettempdir()) / f"semantic-reheating-test-{uuid.uuid4().hex}"
    receipt = tmp_path / "receipts" / "failed.json"

    def failing_runner(
        args: list[str], cwd: Path, environment: dict[str, str]
    ) -> CommandResult:
        return CommandResult(1 if args[:2] == ["uv", "sync"] else 0)

    try:
        with pytest.raises(CleanCheckoutError, match="uv_sync_offline"):
            verify_clean_checkout(root, clone, receipt, gate_runner=failing_runner)
    finally:
        shutil.rmtree(clone, ignore_errors=True)

    result = json.loads(receipt.read_text(encoding="utf-8"))
    assert result["status"] == "fail"
    assert result["command_outcomes"][4] == {
        "name": "uv_sync_offline",
        "status": "fail",
    }
    assert all(item["status"] == "not_run" for item in result["command_outcomes"][5:])
    assert not git(root, "status", "--porcelain")


def test_unsafe_receipt_path_is_rejected_before_clone_or_source_write(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    clone = Path(tempfile.gettempdir()) / f"semantic-reheating-test-{uuid.uuid4().hex}"

    with pytest.raises(CleanCheckoutError, match="outside the source"):
        verify_clean_checkout(root, clone, root / "receipt.json")

    assert not clone.exists()
    assert not (root / "receipt.json").exists()
    assert not git(root, "status", "--porcelain")
