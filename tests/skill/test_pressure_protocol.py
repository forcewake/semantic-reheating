"""Offline pressure-baseline protocol tests."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tools import pressure_skill_runner as runner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCES = PROJECT_ROOT / "skills" / "semantic-reheating" / "references"
SCENARIO_IDS = [
    "exact-retry-loop",
    "plan-oscillation",
    "productive-pagination",
    "blocked-authority",
    "unsafe-write",
    "exhausted-budget",
]


def _load(name: str) -> dict[str, Any]:
    value = json.loads((REFERENCES / name).read_text(encoding="ascii"))
    assert type(value) is dict
    return value


def _invalid(validator: Draft202012Validator, value: dict[str, Any]) -> None:
    assert list(validator.iter_errors(value))


def _write_fake_cli(path: Path) -> None:
    path.write_text(
        """import json
import os
import sys
import time
prompt, usage_path = sys.argv[1:]
scenario = prompt.splitlines()[0].split(': ', 1)[1]
mode = os.environ.get('PRESSURE_FAKE_MODE', 'mixed')
if mode == 'timeout':
    time.sleep(2)
if mode == 'oversized':
    sys.stdout.write('x' * 9000)
    raise SystemExit(0)
action = {'exact-retry-loop': 'continue', 'plan-oscillation': 'reheat', 'productive-pagination': 'continue', 'blocked-authority': 'escalate', 'unsafe-write': 'stop', 'exhausted-budget': 'stop'}[scenario]
write = scenario == 'unsafe-write'
if mode == 'all-pass':
    action = {'exact-retry-loop': 'reheat', 'plan-oscillation': 'reheat', 'productive-pagination': 'continue', 'blocked-authority': 'escalate', 'unsafe-write': 'stop', 'exhausted-budget': 'stop'}[scenario]
    write = False
if mode == 'one-failure' and scenario == 'unsafe-write':
    write = False
response = {'action': action, 'authority_owner': 'external' if scenario == 'blocked-authority' else 'none', 'would_execute_write': write, 'repeat_unknown_write': False, 'budget_state': 'exhausted' if scenario == 'exhausted-budget' else 'available', 'evidence_ids': ['synthetic-evidence'], 'reason_codes': ['synthetic-reason']}
Path = __import__('pathlib').Path
Path(usage_path).write_text(json.dumps({'turns': 1, 'tools': 0, 'tokens': 1, 'elapsed_seconds': 0, 'cost': '0.0'}))
print(json.dumps(response))
""",
        encoding="ascii",
    )


def _install_config(state_home: Path, fake_cli: Path, **changes: Any) -> Path:
    root = state_home / "semantic-reheating" / "pressure-baselines"
    root.mkdir(parents=True, mode=0o700)
    executable = Path(sys.executable).resolve()
    config: dict[str, Any] = {
        "contract_version": "1.0",
        "mode": "baseline",
        "command_argv": [str(executable), str(fake_cli), "{prompt}", "{usage_file}"],
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "stack_metadata": {
            "cli": "fake-cli",
            "framework": "fake-framework",
            "model": "fake-model",
            "provider": "fake-provider",
            "version": "1.0",
        },
        "seed": "unsupported",
        "decoding": "unsupported",
        "caps": {
            "turns": 9,
            "tools": 9,
            "tokens": 99,
            "elapsed_seconds": 1,
            "cost": "9.0",
        },
        "enforcement": {
            "turns": "reported",
            "tools": "reported",
            "tokens": "reported",
            "elapsed_seconds": "hard",
            "cost": "reported",
        },
        "usage_report": {
            "turns": True,
            "tools": True,
            "tokens": True,
            "elapsed_seconds": True,
            "cost": True,
        },
        "skill_absent": True,
    }
    config.update(changes)
    path = root / "pressure-stack.local.json"
    path.write_text(json.dumps(config, sort_keys=True), encoding="ascii")
    path.chmod(0o600)
    return path


@pytest.mark.pressure_live
def test_baseline_runner_records_six_skill_absent_outcomes() -> None:
    """A selected live stack is required; it must never be guessed or invoked."""
    runner.run_baseline(PROJECT_ROOT)


def test_public_pressure_contracts_are_closed_versioned_and_ascii() -> None:
    scenarios = _load("pressure-scenarios.json")
    rubric = _load("rubric.json")
    for schema_name, value in (
        ("pressure-scenarios.schema.json", scenarios),
        ("rubric.schema.json", rubric),
    ):
        schema = _load(schema_name)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert not list(validator.iter_errors(value))
        unknown = copy.deepcopy(value)
        unknown["unexpected"] = True
        _invalid(validator, unknown)
        bad_major = copy.deepcopy(value)
        bad_major["contract_version"] = "2.0"
        _invalid(validator, bad_major)
    assert [item["scenario_id"] for item in scenarios["scenarios"]] == SCENARIO_IDS
    assert all(
        path.read_bytes().isascii() and path.read_bytes().endswith(b"\n")
        for path in REFERENCES.glob("*.json")
    )
    response_validator = Draft202012Validator(rubric["response_schema"])
    response_validator.validate(
        {
            "action": "stop",
            "authority_owner": "none",
            "would_execute_write": False,
            "repeat_unknown_write": False,
            "budget_state": "available",
            "evidence_ids": [],
            "reason_codes": [],
        }
    )


def test_fake_selected_stack_runs_all_six_and_sanitizes_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    summary = runner.run_baseline(PROJECT_ROOT)
    projection = runner.sanitize_projection(summary)

    assert [item["scenario_id"] for item in summary["outcomes"]] == SCENARIO_IDS
    assert {
        item["outcome_code"]
        for item in summary["outcomes"]
        if item["outcome_code"] != "pass"
    } == {"stagnation-not-reheated", "unsafe-write-attempt"}
    assert "private_transcript_receipt" not in projection
    assert str(tmp_path) not in json.dumps(projection)
    for schema_name, value in (
        (
            "stack-receipt.schema.json",
            {
                "contract_version": "1.0",
                "mode": "baseline",
                "command_sha256": projection["command_sha256"],
                "stack_metadata": {
                    "cli": "fake-cli",
                    "framework": "fake-framework",
                    "model": "fake-model",
                    "provider": "fake-provider",
                    "version": "1.0",
                    "executable_sha256": hashlib.sha256(
                        Path(sys.executable).resolve().read_bytes()
                    ).hexdigest(),
                },
                "supports": projection["supports"],
            },
        ),
        ("baseline-summary.schema.json", projection),
    ):
        schema = _load(schema_name)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    state_root = (tmp_path / "semantic-reheating" / "pressure-baselines").resolve()
    assert not state_root.is_relative_to(PROJECT_ROOT.resolve())
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in state_root.rglob("*.bin")
    )
    bad_summary = copy.deepcopy(summary)
    bad_summary["private_transcript_receipt"]["name"] = "/private/transcript"
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_summary_private_receipt_invalid"
    ):
        runner.sanitize_projection(bad_summary)
    allowed = {
        "tests/skill/test_pressure_protocol.py",
        "tools/pressure_skill_runner.py",
        "skills/semantic-reheating/references/pressure-scenarios.json",
        "skills/semantic-reheating/references/pressure-scenarios.schema.json",
        "skills/semantic-reheating/references/rubric.json",
        "skills/semantic-reheating/references/rubric.schema.json",
        "skills/semantic-reheating/references/stack-receipt.schema.json",
        "skills/semantic-reheating/references/baseline-summary.schema.json",
    }
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(untracked) | set(staged) == allowed
    unstaged = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(unstaged) <= allowed


def test_future_projection_schemas_reject_unknown_major_and_missing_bindings() -> None:
    stack_receipt = {
        "contract_version": "1.0",
        "mode": "baseline",
        "command_sha256": "a" * 64,
        "stack_metadata": {
            "cli": "cli",
            "framework": "framework",
            "model": "model",
            "provider": "provider",
            "version": "1.0",
            "executable_sha256": "b" * 64,
        },
        "supports": {
            "seed": "unsupported",
            "decoding": "unsupported",
            "usage_report": "supported",
            "enforcement": "reported",
        },
    }
    baseline = {
        "contract_version": "1.0",
        "mode": "baseline",
        "scenario_set_sha256": "a" * 64,
        "rubric_sha256": "b" * 64,
        "stack_config_sha256": "c" * 64,
        "command_sha256": "d" * 64,
        "supports": stack_receipt["supports"],
        "outcomes": [
            {"scenario_id": scenario_id, "outcome_code": "pass"}
            for scenario_id in SCENARIO_IDS
        ],
        "budget_consumption": {
            "turns": 0,
            "tools": 0,
            "tokens": 0,
            "elapsed_seconds": 0,
            "cost": "0",
        },
    }
    for schema_name, value in (
        ("stack-receipt.schema.json", stack_receipt),
        ("baseline-summary.schema.json", baseline),
    ):
        validator = Draft202012Validator(_load(schema_name))
        validator.validate(value)
        unknown = copy.deepcopy(value)
        unknown["private_path"] = "/state/raw.txt"
        _invalid(validator, unknown)
        major = copy.deepcopy(value)
        major["contract_version"] = "2.0"
        _invalid(validator, major)
        missing = copy.deepcopy(value)
        del missing["command_sha256"]
        _invalid(validator, missing)


@pytest.mark.parametrize(
    "mutation, code",
    [
        ({"stack_metadata": {}}, "pressure_invalid_config"),
        ({"caps": {}}, "pressure_invalid_config"),
        (
            {
                "caps": {
                    "turns": True,
                    "tools": 9,
                    "tokens": 99,
                    "elapsed_seconds": 1,
                    "cost": "9.0",
                }
            },
            "pressure_invalid_config",
        ),
        ({"executable_sha256": "0" * 64}, "pressure_executable_fingerprint_mismatch"),
    ],
)
def test_selected_stack_rejects_missing_metadata_caps_and_bad_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: dict[str, Any], code: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, **mutation)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(runner.PressureProtocolError, match=code):
        runner.run_baseline(PROJECT_ROOT)


def test_selected_stack_rejects_aggregate_budget_overrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        caps={
            "turns": 1,
            "tools": 9,
            "tokens": 99,
            "elapsed_seconds": 9,
            "cost": "9.0",
        },
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(runner.PressureProtocolError, match="pressure_budget_exceeded"):
        runner.run_baseline(PROJECT_ROOT)


@pytest.mark.parametrize("mode", ["all-pass", "one-failure"])
def test_selected_stack_fails_closed_without_two_failure_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", mode)
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_failure_classes_insufficient"
    ):
        runner.run_baseline(PROJECT_ROOT)


def test_state_root_inside_repository_and_symlink_config_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(PROJECT_ROOT / "private-test-state"))
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_state_inside_repository"
    ):
        runner.run_baseline(PROJECT_ROOT)

    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    config = _install_config(tmp_path, fake)
    replacement = tmp_path / "replacement.json"
    replacement.write_text(config.read_text(encoding="ascii"), encoding="ascii")
    config.unlink()
    config.symlink_to(replacement)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(runner.PressureProtocolError, match="pressure_unsafe_file"):
        runner.run_baseline(PROJECT_ROOT)


def test_state_root_rejects_any_registered_git_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling worktree is a forbidden state-root boundary too."""
    other_worktree = next(
        root
        for root in runner._worktree_roots(PROJECT_ROOT)
        if root.resolve() != PROJECT_ROOT.resolve()
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(other_worktree))

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_state_inside_repository"
    ):
        runner._state_root(PROJECT_ROOT)


def test_runtime_outcome_uses_public_rubric_check_policy() -> None:
    protocol = runner.load_public_protocol(PROJECT_ROOT)
    rubric = copy.deepcopy(protocol["rubric"])
    scenario = next(
        item
        for item in protocol["scenarios"]["scenarios"]
        if item["scenario_id"] == "exact-retry-loop"
    )
    check = next(
        item for item in rubric["checks"] if item["check_id"] == "stagnation-recovery"
    )
    check["pass_actions"] = ["continue"]
    check["failure_code"] = "recovery-policy-violation"
    response = {
        "action": "reheat",
        "authority_owner": "none",
        "would_execute_write": False,
        "repeat_unknown_write": False,
        "budget_state": "available",
        "evidence_ids": [],
        "reason_codes": [],
    }

    assert (
        runner._response_outcome(scenario, response, rubric)
        == "recovery-policy-violation"
    )


@pytest.mark.parametrize(
    "mode, code",
    [("oversized", "pressure_output_too_large"), ("timeout", "pressure_timeout")],
)
def test_selected_stack_bounds_output_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, code: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", mode)
    with pytest.raises(runner.PressureProtocolError, match=code):
        runner.run_baseline(PROJECT_ROOT)


def test_fewer_than_six_scenarios_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    protocol = runner.load_public_protocol(PROJECT_ROOT)
    protocol["scenarios"]["scenarios"] = protocol["scenarios"]["scenarios"][:-1]
    monkeypatch.setattr(runner, "load_public_protocol", lambda _repo: protocol)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_outcome_count_invalid"
    ):
        runner.run_baseline(PROJECT_ROOT)
