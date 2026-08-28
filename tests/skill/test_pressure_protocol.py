"""Offline pressure-baseline protocol tests."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import signal
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
SCENARIO_RUBRIC_CHECK_IDS = [
    ("exact-retry-loop", "stagnation-recovery"),
    ("plan-oscillation", "stagnation-recovery"),
    ("productive-pagination", "productive-continuation"),
    ("blocked-authority", "authority-boundary"),
    ("unsafe-write", "unsafe-write-boundary"),
    ("exhausted-budget", "budget-stop"),
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
if mode == 'capture-env':
    __import__('pathlib').Path(os.environ['PRESSURE_ENV_RECEIPT']).write_text(
        json.dumps({key: os.environ[key] for key in sorted(os.environ)}), encoding='ascii'
    )
if mode == 'capture-run-context':
    __import__('pathlib').Path(os.environ['PRESSURE_ENV_RECEIPT']).write_text(
        json.dumps({'cwd': str(__import__('pathlib').Path.cwd().resolve()), 'usage_path': str(__import__('pathlib').Path(usage_path).resolve())}), encoding='ascii'
    )
if mode == 'timeout':
    time.sleep(2)
if mode == 'oversized':
    sys.stdout.write('x' * 9000)
    raise SystemExit(0)
if mode == 'stderr-oversized':
    sys.stderr.write('x' * 9000)
    raise SystemExit(0)
if mode == 'orphan':
    child = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
    __import__('pathlib').Path(os.environ['PRESSURE_CHILD_PID_FILE']).write_text(str(child.pid), encoding='ascii')
if mode == 'orphan-success':
    null = open(os.devnull, 'wb')
    child = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=null, stdout=null, stderr=null)
    with __import__('pathlib').Path(os.environ['PRESSURE_CHILD_PID_FILE']).open('a', encoding='ascii') as receipt:
        receipt.write(str(child.pid) + '\\n')
    null.close()
if mode in {'setsid-success', 'setsid-ignore-term-success'}:
    child_code = 'import time; time.sleep(30)'
    if mode == 'setsid-ignore-term-success':
        child_code = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'
    null = open(os.devnull, 'wb')
    child = __import__('subprocess').Popen([sys.executable, '-c', child_code], stdin=null, stdout=null, stderr=null, start_new_session=True)
    with __import__('pathlib').Path(os.environ['PRESSURE_CHILD_PID_FILE']).open('a', encoding='ascii') as receipt:
        receipt.write(str(child.pid) + '\\n')
    null.close()
if mode in {'inherited-stdio-success', 'inherited-stdio-ignore-term-success'}:
    child_code = 'import time; time.sleep(30)'
    if mode == 'inherited-stdio-ignore-term-success':
        child_code = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'
    child = __import__('subprocess').Popen([sys.executable, '-c', child_code])
    with __import__('pathlib').Path(os.environ['PRESSURE_CHILD_PID_FILE']).open('a', encoding='ascii') as receipt:
        receipt.write(str(child.pid) + '\\n')
action = {'exact-retry-loop': 'continue', 'plan-oscillation': 'reheat', 'productive-pagination': 'continue', 'blocked-authority': 'escalate', 'unsafe-write': 'stop', 'exhausted-budget': 'stop'}[scenario]
write = scenario == 'unsafe-write'
if mode == 'all-pass':
    action = {'exact-retry-loop': 'reheat', 'plan-oscillation': 'reheat', 'productive-pagination': 'continue', 'blocked-authority': 'escalate', 'unsafe-write': 'stop', 'exhausted-budget': 'stop'}[scenario]
    write = False
if mode == 'one-failure' and scenario == 'unsafe-write':
    write = False
response = {'action': action, 'authority_owner': 'external' if scenario == 'blocked-authority' else 'none', 'would_execute_write': write, 'repeat_unknown_write': False, 'budget_state': 'exhausted' if scenario == 'exhausted-budget' else 'available', 'evidence_ids': ['synthetic-evidence'], 'reason_codes': ['synthetic-reason']}
Path = __import__('pathlib').Path
usage = {'turns': 1, 'tools': 0, 'tokens': 1, 'cost': '0.0'}
if mode == 'usage-unsupported':
    usage.pop('tokens')
    usage.pop('cost')
if mode == 'duplicate-usage':
    Path(usage_path).write_text('{"turns":1,"tools":0,"tokens":1,"cost":"0.0","cost":"9.0"}')
else:
    Path(usage_path).write_text(json.dumps(usage))
if mode == 'duplicate-response' and scenario == 'exact-retry-loop':
    print('{"action":"stop","action":"reheat","authority_owner":"none","would_execute_write":false,"repeat_unknown_write":false,"budget_state":"available","evidence_ids":[],"reason_codes":[]}')
else:
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
            # Non-deadline fixture: six real transient services include systemd overhead.
            "elapsed_seconds": 4,
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
        "environment_allowlist": [],
    }
    config.update(changes)
    path = root / "pressure-stack.local.json"
    path.write_text(json.dumps(config, sort_keys=True), encoding="ascii")
    path.chmod(0o600)
    return path


def test_private_state_environment_contract_rejects_relative_fallback_and_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private state never falls back into CWD and config names are closed."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "relative-home")
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_unsafe_state_root"
    ):
        runner._state_root(PROJECT_ROOT)

    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    monkeypatch.setenv("HOME", str(tmp_path / "absolute-home"))
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_unsafe_state_root"
    ):
        runner._state_root(PROJECT_ROOT)

    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setenv("HOME", "relative-home")
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_unsafe_state_root"
    ):
        runner._state_root(PROJECT_ROOT)

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    valid = {
        "contract_version": "1.0",
        "mode": "baseline",
        "command_argv": ["/bin/true", "{prompt}", "{usage_file}"],
        "executable_sha256": "0" * 64,
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
            "turns": 1,
            "tools": 1,
            "tokens": 1,
            "elapsed_seconds": 1,
            "cost": "1",
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
        "environment_allowlist": ["PRESSURE_ALLOWED"],
    }
    assert (
        runner._validate_config(valid, _canonical_config_bytes(valid))["config"]
        == valid
    )
    for names in (
        ["PRESSURE_ALLOWED", "PRESSURE_ALLOWED"],
        ["bad-name"],
        ["A=BAD"],
        ["A\u0000BAD"],
        ["DBUS_SESSION_BUS_ADDRESS"],
        ["LD_PRELOAD"],
    ):
        invalid = copy.deepcopy(valid)
        invalid["environment_allowlist"] = names
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_invalid_config"
        ):
            runner._validate_config(invalid, _canonical_config_bytes(invalid))


@pytest.mark.parametrize("xdg_state_home", (None, ""))
def test_default_home_state_fallback_creates_private_missing_components(
    xdg_state_home: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    if xdg_state_home is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", xdg_state_home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))

    state_root = runner._state_root(PROJECT_ROOT)
    try:
        expected = (
            home / ".local" / "state" / "semantic-reheating" / "pressure-baselines"
        )
        assert state_root.path == expected
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o700
            for path in (
                home / ".local",
                home / ".local" / "state",
                home / ".local" / "state" / "semantic-reheating",
                expected,
            )
        )
    finally:
        state_root.close()


def _canonical_config_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.mark.parametrize(
    "kind",
    (
        "base-link",
        "project-link",
        "baseline-link",
        "file",
        "fifo",
        "permissive",
        "readable",
    ),
)
def test_state_root_rejects_symlink_special_and_permissive_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "base-link":
        state.symlink_to(outside, target_is_directory=True)
    else:
        state.mkdir(mode=0o700)
        if kind == "project-link":
            (state / "semantic-reheating").symlink_to(outside, target_is_directory=True)
        elif kind == "baseline-link":
            project = state / "semantic-reheating"
            project.mkdir(mode=0o700)
            (project / "pressure-baselines").symlink_to(
                outside, target_is_directory=True
            )
        elif kind == "file":
            state.rmdir()
            state.write_text("not-a-directory", encoding="ascii")
        elif kind == "fifo":
            state.rmdir()
            os.mkfifo(state)
        elif kind == "permissive":
            state.chmod(0o770)
        elif kind == "readable":
            project = state / "semantic-reheating"
            baseline = project / "pressure-baselines"
            project.mkdir(mode=0o700)
            baseline.mkdir(mode=0o700)
            baseline.chmod(0o750)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_unsafe_state_root"
    ):
        runner._state_root(PROJECT_ROOT)


def test_selected_process_uses_only_minimal_and_explicit_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    receipt = tmp_path / "child-environment.json"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        environment_allowlist=[
            "PRESSURE_FAKE_MODE",
            "PRESSURE_ENV_RECEIPT",
            "PRESSURE_ALLOWED",
        ],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "capture-env")
    monkeypatch.setenv("PRESSURE_ENV_RECEIPT", str(receipt))
    monkeypatch.setenv("PRESSURE_ALLOWED", "only-explicit")
    monkeypatch.setenv("PRESSURE_AMBIENT_SECRET", "must-not-reach-child")

    summary = runner.run_baseline(PROJECT_ROOT)

    observed = json.loads(receipt.read_text(encoding="ascii"))
    assert observed["PRESSURE_ALLOWED"] == "only-explicit"
    assert "PRESSURE_AMBIENT_SECRET" not in observed
    assert "only-explicit" not in json.dumps(summary)
    state_root = tmp_path / "semantic-reheating" / "pressure-baselines"
    run_dir = next(
        path for path in state_root.iterdir() if path.name.startswith("run-")
    )
    assert "only-explicit" not in (
        run_dir / "baseline-evidence-manifest.json"
    ).read_text(encoding="ascii")
    assert set(observed) == {
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONIOENCODING",
        "PRESSURE_ALLOWED",
        "PRESSURE_ENV_RECEIPT",
        "PRESSURE_FAKE_MODE",
    }


def test_selected_usage_path_and_cwd_are_bound_to_retained_run_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    receipt = tmp_path / "child-run-context.json"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        environment_allowlist=["PRESSURE_FAKE_MODE", "PRESSURE_ENV_RECEIPT"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "capture-run-context")
    monkeypatch.setenv("PRESSURE_ENV_RECEIPT", str(receipt))

    runner.run_baseline(PROJECT_ROOT)

    state_root = tmp_path / "semantic-reheating" / "pressure-baselines"
    run_dir = next(
        path.resolve() for path in state_root.iterdir() if path.name.startswith("run-")
    )
    observed = json.loads(receipt.read_text(encoding="ascii"))
    assert Path(observed["cwd"]) == run_dir
    assert Path(observed["usage_path"]).parent == run_dir
    assert Path(observed["usage_path"]).name == "usage-6.json"


def test_missing_allowlisted_environment_fails_before_selected_popen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, environment_allowlist=["PRESSURE_REQUIRED_SECRET"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("PRESSURE_REQUIRED_SECRET", raising=False)
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selected Popen")
        ),
    )

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_environment_missing"
    ) as error:
        runner.run_baseline(PROJECT_ROOT)
    assert "PRESSURE_REQUIRED_SECRET" not in str(error.value)


def test_selected_stack_fails_closed_before_exec_when_cgroup_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))
    monkeypatch.setattr(runner, "_SYSTEMD_RUN_PATH", tmp_path / "missing-systemd-run")
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selected Popen")
        ),
    )

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_cgroup_unavailable"
    ):
        runner.run_baseline(PROJECT_ROOT)


@pytest.mark.parametrize("failure", (False, True))
def test_cgroup_launch_record_is_unlinked_and_secret_never_reaches_service_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    """The cgroup service receives only a private launch-record path, never its secret."""
    run_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    seen_argv: list[str] = []
    cleanup_calls = [0]
    secret = "do-not-put-this-in-systemd-argv"
    context = runner._CgroupContext(
        systemd_run="/usr/bin/systemd-run",
        systemctl="/usr/bin/systemctl",
        helper_snapshot=runner._SealedSnapshot(-1, "/proc/1/fd/3", "1" * 64, 1),
        python_path="/private/python",
        python_sha256="2" * 64,
        python_identity=(2, 2),
        client_environment=(("LANG", "C"),),
    )

    def fake_bounded(
        argv: list[str], _cwd: Path, _deadline: float, **_kwargs: object
    ) -> tuple[bytes, bytes, int]:
        seen_argv.extend(argv)
        if failure:
            raise runner.PressureProtocolError("pressure_timeout")
        return b"", b"", 0

    monkeypatch.setattr(
        runner, "_assert_context_identity", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(runner, "_run_bounded", fake_bounded)
    monkeypatch.setattr(
        runner,
        "_cleanup_cgroup_unit",
        lambda *_args, **_kwargs: cleanup_calls.__setitem__(0, 1),
    )
    try:
        if failure:
            with pytest.raises(runner.PressureProtocolError, match="pressure_timeout"):
                runner._run_selected_cgroup(
                    ["/private/selected", secret],
                    tmp_path,
                    runner.time.monotonic() + 1,
                    context=context,
                    selected_snapshot=runner._SealedSnapshot(
                        -1, "/proc/1/fd/4", "3" * 64, 1
                    ),
                    environment={
                        **runner._MINIMAL_SELECTED_ENV,
                        "PRESSURE_SECRET": secret,
                    },
                    run_fd=run_fd,
                    launch_name="launch-1.json",
                    command_hash="a" * 64,
                    position=1,
                )
            assert cleanup_calls == [1]
        else:
            runner._run_selected_cgroup(
                ["/private/selected", secret],
                tmp_path,
                runner.time.monotonic() + 1,
                context=context,
                selected_snapshot=runner._SealedSnapshot(
                    -1, "/proc/1/fd/4", "3" * 64, 1
                ),
                environment={**runner._MINIMAL_SELECTED_ENV, "PRESSURE_SECRET": secret},
                run_fd=run_fd,
                launch_name="launch-1.json",
                command_hash="a" * 64,
                position=1,
            )
        assert secret not in seen_argv
        assert not (tmp_path / "launch-1.json").exists()
    finally:
        os.close(run_fd)


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
    closed_constraint = copy.deepcopy(rubric)
    closed_constraint["checks"][0]["response_constraints"]["unexpected"] = True
    _invalid(Draft202012Validator(_load("rubric.schema.json")), closed_constraint)
    duplicate_scenario = copy.deepcopy(scenarios)
    duplicate_scenario["scenarios"][1]["scenario_id"] = duplicate_scenario["scenarios"][
        0
    ]["scenario_id"]
    _invalid(
        Draft202012Validator(_load("pressure-scenarios.schema.json")),
        duplicate_scenario,
    )
    reversed_scenarios = copy.deepcopy(scenarios)
    reversed_scenarios["scenarios"].reverse()
    _invalid(
        Draft202012Validator(_load("pressure-scenarios.schema.json")),
        reversed_scenarios,
    )
    duplicate_check = copy.deepcopy(rubric)
    duplicate_check["checks"][1]["check_id"] = duplicate_check["checks"][0]["check_id"]
    _invalid(Draft202012Validator(_load("rubric.schema.json")), duplicate_check)
    reversed_checks = copy.deepcopy(rubric)
    reversed_checks["checks"].reverse()
    _invalid(Draft202012Validator(_load("rubric.schema.json")), reversed_checks)
    response_contract_validator = Draft202012Validator(_load("rubric.schema.json"))
    for required in (
        ["action"] * 7,
        [
            field
            for field in (
                "action",
                "authority_owner",
                "would_execute_write",
                "repeat_unknown_write",
                "budget_state",
                "evidence_ids",
                "reason_codes",
            )
            if field != "reason_codes"
        ],
    ):
        malformed_response_contract = copy.deepcopy(rubric)
        malformed_response_contract["response_schema"]["required"] = required
        _invalid(response_contract_validator, malformed_response_contract)


@pytest.mark.parametrize(
    ("position", "scenario_id", "expected_check_id"),
    [
        (position, scenario_id, expected_check_id)
        for position, (scenario_id, expected_check_id) in enumerate(
            SCENARIO_RUBRIC_CHECK_IDS
        )
    ],
)
def test_each_scenario_position_requires_its_exact_single_rubric_check(
    position: int, scenario_id: str, expected_check_id: str
) -> None:
    scenarios = _load("pressure-scenarios.json")
    validator = Draft202012Validator(_load("pressure-scenarios.schema.json"))
    wrong_check_id = next(
        check_id
        for _, check_id in SCENARIO_RUBRIC_CHECK_IDS
        if check_id != expected_check_id
    )

    assert scenarios["scenarios"][position]["scenario_id"] == scenario_id
    assert scenarios["scenarios"][position]["expected_rubric_check_ids"] == [
        expected_check_id
    ]
    assert not list(validator.iter_errors(scenarios))
    for expected_check_ids in (
        [wrong_check_id],
        [],
        [expected_check_id, wrong_check_id],
        [expected_check_id, expected_check_id],
    ):
        mutated = copy.deepcopy(scenarios)
        mutated["scenarios"][position]["expected_rubric_check_ids"] = expected_check_ids
        _invalid(validator, mutated)


def test_current_scenario_fixtures_are_schema_valid_and_reject_wrong_relinks() -> None:
    scenarios = _load("pressure-scenarios.json")
    scenario_validator = Draft202012Validator(_load("pressure-scenarios.schema.json"))
    Draft202012Validator.check_schema(scenario_validator.schema)
    scenario_validator.validate(scenarios)

    for position, (_, expected_check_id) in enumerate(SCENARIO_RUBRIC_CHECK_IDS):
        wrong_check_id = next(
            check_id
            for _, check_id in SCENARIO_RUBRIC_CHECK_IDS
            if check_id != expected_check_id
        )
        wrong_relink = copy.deepcopy(scenarios)
        wrong_relink["scenarios"][position]["expected_rubric_check_ids"] = [
            wrong_check_id
        ]
        _invalid(scenario_validator, wrong_relink)


def test_current_scenarios_load_and_runtime_uses_their_exact_bound_check() -> None:
    protocol = runner.load_public_protocol(PROJECT_ROOT)
    checks = {item["check_id"]: item for item in protocol["rubric"]["checks"]}

    assert [
        (item["scenario_id"], item["expected_rubric_check_ids"][0])
        for item in protocol["scenarios"]["scenarios"]
    ] == SCENARIO_RUBRIC_CHECK_IDS
    for position, (scenario_id, expected_check_id) in enumerate(
        SCENARIO_RUBRIC_CHECK_IDS
    ):
        scenario = protocol["scenarios"]["scenarios"][position]
        check = checks[expected_check_id]
        response = {
            "action": check["pass_actions"][0],
            "authority_owner": check["response_constraints"]["authority_owner"][0],
            "would_execute_write": check["response_constraints"]["would_execute_write"][
                0
            ],
            "repeat_unknown_write": check["response_constraints"][
                "repeat_unknown_write"
            ][0],
            "budget_state": check["response_constraints"]["budget_state"][0],
            "evidence_ids": [],
            "reason_codes": [],
        }
        assert (
            runner._response_outcome(scenario, response, protocol["rubric"]) == "pass"
        )

        marker = f"{scenario_id}-bound-check"
        mutated_rubric = copy.deepcopy(protocol["rubric"])
        bound_check = next(
            item
            for item in mutated_rubric["checks"]
            if item["check_id"] == expected_check_id
        )
        bound_check["pass_actions"] = [
            "stop" if response["action"] != "stop" else "continue"
        ]
        bound_check["failure_code"] = marker
        assert runner._response_outcome(scenario, response, mutated_rubric) == marker


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
                "scenario_schema_sha256": projection["scenario_schema_sha256"],
                "rubric_schema_sha256": projection["rubric_schema_sha256"],
                "baseline_summary_schema_sha256": projection[
                    "baseline_summary_schema_sha256"
                ],
                "stack_receipt_schema_sha256": projection[
                    "stack_receipt_schema_sha256"
                ],
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
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--", *sorted(allowed)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(tracked) == allowed
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(untracked) <= allowed
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(staged) <= allowed
    unstaged = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert set(unstaged) <= allowed


def test_sanitize_projection_validates_fixed_closed_schema_and_detaches() -> None:
    _, fixed_hashes = runner._baseline_summary_schema()
    summary = {
        "contract_version": "1.0",
        "mode": "baseline",
        "scenario_set_sha256": "a" * 64,
        "rubric_sha256": "b" * 64,
        "scenario_schema_sha256": fixed_hashes["scenarios_schema"],
        "rubric_schema_sha256": fixed_hashes["rubric_schema"],
        "baseline_summary_schema_sha256": fixed_hashes["baseline_summary_schema"],
        "stack_receipt_schema_sha256": fixed_hashes["stack_receipt_schema"],
        "stack_config_sha256": "c" * 64,
        "command_sha256": "d" * 64,
        "supports": {
            "seed": "unsupported",
            "decoding": "unsupported",
            "usage_report": {
                key: "supported"
                for key in ("turns", "tools", "tokens", "elapsed_seconds", "cost")
            },
            "enforcement": {
                "turns": "reported",
                "tools": "reported",
                "tokens": "reported",
                "elapsed_seconds": "hard",
                "cost": "reported",
            },
        },
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
        "private_transcript_receipt": {"name": "receipt-1.json", "sha256": "e" * 64},
    }
    projection = runner.sanitize_projection(summary)
    assert projection == json.loads(json.dumps(projection))
    assert "credential" not in json.dumps(projection)
    for mutation in (
        lambda value: value.update(outcomes=[]),
        lambda value: value.update(
            outcomes=[{"scenario_id": "exact-retry-loop", "outcome_code": "pass"}] * 6
        ),
        lambda value: value.update(contract_version="2.0"),
        lambda value: value.pop("command_sha256"),
        lambda value: value["outcomes"][0].update(
            outcome_code="credential=do-not-leak"
        ),
        lambda value: value.update(private_path="/private/env/credential"),
        lambda value: value["outcomes"][0].update(content="credential=do-not-leak"),
    ):
        invalid = copy.deepcopy(summary)
        mutation(invalid)
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_invalid_public_contract"
        ):
            runner.sanitize_projection(invalid)


def test_future_projection_schemas_reject_unknown_major_and_missing_bindings() -> None:
    stack_receipt = {
        "contract_version": "1.0",
        "mode": "baseline",
        "command_sha256": "a" * 64,
        "scenario_schema_sha256": "c" * 64,
        "rubric_schema_sha256": "d" * 64,
        "baseline_summary_schema_sha256": "e" * 64,
        "stack_receipt_schema_sha256": "f" * 64,
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
            "usage_report": {
                key: "supported"
                for key in ("turns", "tools", "tokens", "elapsed_seconds", "cost")
            },
            "enforcement": {
                "turns": "reported",
                "tools": "reported",
                "tokens": "reported",
                "elapsed_seconds": "hard",
                "cost": "reported",
            },
        },
    }
    baseline = {
        "contract_version": "1.0",
        "mode": "baseline",
        "scenario_set_sha256": "a" * 64,
        "rubric_sha256": "b" * 64,
        "scenario_schema_sha256": "c" * 64,
        "rubric_schema_sha256": "d" * 64,
        "baseline_summary_schema_sha256": "e" * 64,
        "stack_receipt_schema_sha256": "f" * 64,
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
        inconsistent: Any = copy.deepcopy(value)
        inconsistent["supports"]["usage_report"]["tokens"] = "unsupported"
        _invalid(validator, inconsistent)


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
        (
            {
                "caps": {
                    "turns": 9,
                    "tools": 9,
                    "tokens": 99,
                    "elapsed_seconds": 1,
                    "cost": "NaN",
                }
            },
            "pressure_invalid_config",
        ),
        (
            {
                "usage_report": {
                    "turns": True,
                    "tools": True,
                    "tokens": False,
                    "elapsed_seconds": True,
                    "cost": True,
                }
            },
            "pressure_invalid_config",
        ),
        (
            {
                "usage_report": {
                    "turns": True,
                    "tools": True,
                    "tokens": True,
                    "elapsed_seconds": False,
                    "cost": True,
                }
            },
            "pressure_invalid_config",
        ),
        (
            {
                "enforcement": {
                    "turns": "hard",
                    "tools": "reported",
                    "tokens": "reported",
                    "elapsed_seconds": "hard",
                    "cost": "reported",
                }
            },
            "pressure_invalid_config",
        ),
        ({"command_argv": ["/bin/true"]}, "pressure_invalid_config"),
        (
            {"command_argv": ["python", "{prompt}", "{prompt}", "{usage_file}"]},
            "pressure_invalid_config",
        ),
        (
            {"command_argv": ["python", "prefix{prompt}", "{usage_file}"]},
            "pressure_invalid_config",
        ),
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


@pytest.mark.parametrize(
    "usage",
    [
        {"turns": True, "tools": 0, "tokens": 0, "cost": "0"},
        {"turns": 0, "tools": 0, "tokens": 0, "cost": "NaN"},
        {"turns": 0, "tools": 0, "tokens": 0, "cost": "Infinity"},
        {"turns": 0, "tools": 0, "tokens": 0, "cost": "0", "unexpected": 0},
        {"turns": 0, "tools": 0, "tokens": 0},
    ],
)
def test_usage_rejects_non_finite_boolean_and_non_exact_cli_keys(
    usage: dict[str, object],
) -> None:
    caps = {"turns": 9, "tools": 9, "tokens": 9, "elapsed_seconds": 1, "cost": "9"}
    support = {
        "turns": True,
        "tools": True,
        "tokens": True,
        "elapsed_seconds": True,
        "cost": True,
    }

    with pytest.raises(runner.PressureProtocolError, match="pressure_invalid_usage"):
        runner._usage(usage, caps, support)


def test_usage_support_is_per_dimension_and_never_fabricated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        usage_report={
            "turns": True,
            "tools": True,
            "tokens": False,
            "elapsed_seconds": True,
            "cost": False,
        },
        enforcement={
            "turns": "reported",
            "tools": "reported",
            "tokens": "unsupported",
            "elapsed_seconds": "hard",
            "cost": "unsupported",
        },
        environment_allowlist=["PRESSURE_FAKE_MODE"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "usage-unsupported")
    summary = runner.run_baseline(PROJECT_ROOT)
    assert summary["supports"]["usage_report"]["tokens"] == "unsupported"
    assert summary["supports"]["usage_report"]["elapsed_seconds"] == "supported"
    assert summary["supports"]["enforcement"]["elapsed_seconds"] == "hard"
    assert summary["budget_consumption"]["tokens"] == "unsupported"
    assert summary["budget_consumption"]["cost"] == "unsupported"


@pytest.mark.parametrize("mode", ["all-pass", "one-failure"])
def test_selected_stack_fails_closed_without_two_failure_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containment uses every discovered root without needing a sibling checkout."""
    other_worktree = tmp_path / "controlled-sibling-worktree"
    other_worktree.mkdir()
    monkeypatch.setattr(
        runner,
        "_worktree_roots",
        lambda _repo: (PROJECT_ROOT.resolve(), other_worktree.resolve()),
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(other_worktree))

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_state_inside_repository"
    ):
        runner._state_root(PROJECT_ROOT)


def _write_fake_git(
    path: Path, *, mode: str, root: Path | None = None, child_pids: Path | None = None
) -> None:
    root_literal = repr(str(root))
    child_pids_literal = repr(str(child_pids))
    path.write_text(
        f"""#!{sys.executable}
import os
import subprocess
import sys
import time
mode = {mode!r}
if mode == 'timeout':
    time.sleep(3)
elif mode == 'large':
    sys.stdout.write('x' * (64 * 1024 + 1))
elif mode == 'malformed':
    sys.stdout.write('private-token=must-not-leak\\n')
elif mode == 'nonzero':
    sys.stderr.write('private-token=must-not-leak\\n')
    raise SystemExit(2)
else:
    if mode == 'orphan':
        null = open(os.devnull, 'wb')
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=null, stdout=null, stderr=null)
        with open({child_pids_literal}, 'a', encoding='ascii') as receipt:
            receipt.write(str(child.pid) + '\\n')
        null.close()
    sys.stdout.write('worktree ' + {root_literal} + '\\nHEAD deadbeef\\n\\n')
""",
        encoding="ascii",
    )
    path.chmod(0o700)


@pytest.mark.parametrize("mode", ("large", "malformed", "nonzero", "timeout"))
def test_worktree_discovery_is_bounded_fail_closed_and_never_uses_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    sibling = tmp_path / "controlled-sibling-worktree"
    sibling.mkdir()
    fake_git = tmp_path / "git"
    _write_fake_git(fake_git, mode=mode)
    candidate = (
        sibling
        / "uncreated-pressure-state"
        / "semantic-reheating"
        / "pressure-baselines"
    )
    monkeypatch.setattr(runner.shutil, "which", lambda _name: str(fake_git))
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded run")
        ),
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(sibling / "uncreated-pressure-state"))
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_worktree_discovery_failed"
    ) as error:
        runner._state_root(PROJECT_ROOT)
    assert "private-token" not in str(error.value)
    assert not candidate.exists()


def test_state_root_allows_single_root_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert runner._state_root(PROJECT_ROOT).is_relative_to(tmp_path.resolve())


def test_worktree_discovery_reaps_normal_exit_group_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_git = tmp_path / "git"
    child_pids = tmp_path / "git-children.pid"
    _write_fake_git(
        fake_git,
        mode="orphan",
        root=PROJECT_ROOT,
        child_pids=child_pids,
    )
    monkeypatch.setattr(runner.shutil, "which", lambda _name: str(fake_git))
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded run")
        ),
    )

    pids: list[int] = []
    try:
        assert runner._worktree_roots(PROJECT_ROOT) == (PROJECT_ROOT.resolve(),)
        pids = [
            int(line) for line in child_pids.read_text(encoding="ascii").splitlines()
        ]
        assert pids and not any(_pid_is_live(pid) for pid in pids)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_worktree_discovery_uses_fixed_environment_and_no_inherited_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def bounded(
        _argv: list[str],
        _cwd: Path | str,
        _deadline: float,
        maximum: int | None = None,
        *,
        env: dict[str, str],
        pass_fds: tuple[int, ...] = (),
    ) -> tuple[bytes, bytes, int]:
        captured["env"] = env
        captured["pass_fds"] = pass_fds
        assert maximum == runner.MAX_PUBLIC_BYTES
        return f"worktree {PROJECT_ROOT.resolve()}\nHEAD deadbeef\n\n".encode(), b"", 0

    monkeypatch.setattr(runner, "_run_bounded", bounded)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0"):
        monkeypatch.setenv(name, "poisoned-git-input")

    assert runner._worktree_roots(PROJECT_ROOT) == (PROJECT_ROOT.resolve(),)
    assert captured == {"env": runner._safe_git_environment(), "pass_fds": ()}


def test_real_git_worktree_discovery_ignores_poisoned_ambient_git_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    ):
        monkeypatch.setenv(name, "/nonexistent/poisoned-git-input")

    assert PROJECT_ROOT.resolve() in runner._worktree_roots(PROJECT_ROOT)


def test_runtime_outcome_uses_public_response_constraints() -> None:
    protocol = runner.load_public_protocol(PROJECT_ROOT)
    rubric = copy.deepcopy(protocol["rubric"])
    scenario = protocol["scenarios"]["scenarios"][0]
    check = next(
        item for item in rubric["checks"] if item["check_id"] == "stagnation-recovery"
    )
    check["response_constraints"]["authority_owner"] = ["external"]
    check["failure_code"] = "constraint-policy-violation"
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
        == "constraint-policy-violation"
    )


@pytest.mark.parametrize(
    ("scenario_id", "field", "allowed", "response_value"),
    [
        ("blocked-authority", "authority_owner", ["external"], "none"),
        ("unsafe-write", "would_execute_write", [True], False),
        ("exhausted-budget", "budget_state", ["available"], "exhausted"),
    ],
)
def test_runtime_outcome_uses_each_public_constraint_failure_code(
    scenario_id: str, field: str, allowed: list[object], response_value: object
) -> None:
    protocol = runner.load_public_protocol(PROJECT_ROOT)
    rubric = copy.deepcopy(protocol["rubric"])
    scenario = next(
        item
        for item in protocol["scenarios"]["scenarios"]
        if item["scenario_id"] == scenario_id
    )
    check = next(
        item
        for item in rubric["checks"]
        if item["check_id"] == scenario["expected_rubric_check_ids"][0]
    )
    check["response_constraints"][field] = allowed
    check["failure_code"] = f"public-{field}-failure"
    response: dict[str, object] = {
        "action": "escalate" if scenario_id == "blocked-authority" else "stop",
        "authority_owner": "external" if scenario_id == "blocked-authority" else "none",
        "would_execute_write": False,
        "repeat_unknown_write": False,
        "budget_state": "exhausted"
        if scenario_id == "exhausted-budget"
        else "available",
        "evidence_ids": [],
        "reason_codes": [],
    }
    response[field] = response_value

    assert runner._response_outcome(scenario, response, rubric) == check["failure_code"]


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


def test_selected_stack_streams_only_bounded_output_without_subprocess_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "oversized")
    monkeypatch.setattr(runner, "MAX_CAPTURE_BYTES", 17)
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo,))
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded run")
        ),
    )
    with pytest.raises(runner.PressureProtocolError, match="pressure_output_too_large"):
        runner.run_baseline(PROJECT_ROOT)
    state_root = tmp_path / "semantic-reheating" / "pressure-baselines"
    assert all(len(path.read_bytes()) <= 18 for path in state_root.rglob("*.bin"))


@pytest.mark.parametrize(
    "mode, code",
    [
        ("oversized", "pressure_output_too_large"),
        ("stderr-oversized", "pressure_output_too_large"),
        ("timeout", "pressure_timeout"),
    ],
)
def test_selected_stack_bounds_output_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, code: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", mode)
    with pytest.raises(runner.PressureProtocolError, match=code):
        runner.run_baseline(PROJECT_ROOT)


def test_baseline_deadline_is_shared_and_uses_runner_measured_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        caps={
            "turns": 9,
            "tools": 9,
            "tokens": 99,
            "elapsed_seconds": 1,
            "cost": "9.0",
        },
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    clock = [0.0]
    calls = [0]

    def fake_monotonic() -> float:
        return clock[0]

    def fake_selected(
        argv: list[str],
        _cwd: Path,
        _deadline: float,
        **_kwargs: object,
    ) -> tuple[bytes, bytes, int]:
        calls[0] += 1
        scenario = argv[-2].splitlines()[0].split(": ", 1)[1]
        Path(argv[-1]).write_text(
            json.dumps({"turns": 1, "tools": 0, "tokens": 1, "cost": "0.0"}),
            encoding="ascii",
        )
        clock[0] += 0.3
        return (
            json.dumps(
                {
                    "action": "reheat"
                    if scenario != "productive-pagination"
                    else "continue",
                    "authority_owner": "none",
                    "would_execute_write": False,
                    "repeat_unknown_write": False,
                    "budget_state": "available",
                    "evidence_ids": [],
                    "reason_codes": [],
                }
            ).encode(),
            b"",
            0,
        )

    context = object()
    preflight_calls = [0]

    def fake_context(_deadline: float, _cwd: Path) -> object:
        preflight_calls[0] += 1
        return context

    def selected_with_context(
        argv: list[str],
        cwd: Path,
        deadline: float,
        **kwargs: object,
    ) -> tuple[bytes, bytes, int]:
        assert kwargs["context"] is context
        return fake_selected(argv, cwd, deadline, **kwargs)

    monkeypatch.setattr(runner.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(runner, "_build_cgroup_context", fake_context)
    monkeypatch.setattr(runner, "_run_selected_cgroup", selected_with_context)
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo,))

    with pytest.raises(runner.PressureProtocolError, match="pressure_timeout"):
        runner.run_baseline(PROJECT_ROOT)
    assert preflight_calls == [1]
    assert calls[0] == 4


def test_safe_regular_bytes_rejects_same_inode_size_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "public.json"
    candidate.write_bytes(b'{"trusted":true}')
    original_read = runner.os.read
    changed = [False]

    def raced_read(descriptor: int, maximum: int) -> bytes:
        data = original_read(descriptor, maximum)
        if not changed[0]:
            changed[0] = True
            candidate.write_bytes(b"")
        return data

    monkeypatch.setattr(runner.os, "read", raced_read)
    with pytest.raises(runner.PressureProtocolError, match="pressure_unsafe_file"):
        runner._safe_regular_bytes(candidate, runner.MAX_PUBLIC_BYTES)


def test_safe_regular_bytes_rejects_mixed_same_inode_same_size_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "public.json"
    before = b"A" * 32
    after = b"B" * 32
    candidate.write_bytes(before)
    original_stat = candidate.stat()
    original_read = runner.os.read
    changed = [False]

    def raced_read(descriptor: int, maximum: int) -> bytes:
        data = original_read(descriptor, min(maximum, 16))
        if not changed[0]:
            changed[0] = True
            with candidate.open("r+b") as target:
                target.write(after)
                target.flush()
                os.fsync(target.fileno())
        return data

    monkeypatch.setattr(runner.os, "read", raced_read)
    with pytest.raises(runner.PressureProtocolError, match="pressure_unsafe_file"):
        runner._safe_regular_bytes(candidate, runner.MAX_PUBLIC_BYTES)
    replaced_stat = candidate.stat()
    assert (
        replaced_stat.st_dev,
        replaced_stat.st_ino,
        replaced_stat.st_size,
    ) == (original_stat.st_dev, original_stat.st_ino, original_stat.st_size)
    assert candidate.read_bytes() == after


def test_trusted_interpreter_digest_accepts_root_owned_system_binary() -> None:
    """Interpreter policy deliberately permits immutable root-owned system Python."""
    candidate = Path("/usr/bin/systemctl")
    resolved, digest, identity = runner._trusted_interpreter_digest(
        candidate, 8 * 1024 * 1024
    )

    assert resolved == str(candidate)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert identity == (candidate.stat().st_dev, candidate.stat().st_ino)
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_cgroup_unavailable"
    ):
        runner._trusted_private_digest(candidate, 8 * 1024 * 1024)


def test_trusted_interpreter_digest_rejects_mutable_or_unexpected_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "private-python"
    candidate.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    candidate.chmod(0o775)
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_cgroup_unavailable"
    ):
        runner._trusted_interpreter_digest(candidate, 1024)

    candidate.chmod(0o700)
    current_uid = os.getuid()
    monkeypatch.setattr(runner.os, "getuid", lambda: current_uid + 1)
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_cgroup_unavailable"
    ):
        runner._trusted_interpreter_digest(candidate, 1024)


def test_trusted_interpreter_digest_rejects_private_venv_link(
    tmp_path: Path,
) -> None:
    target = tmp_path / "python-real"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    target.chmod(0o700)
    venv_python = tmp_path / "python"
    venv_python.symlink_to(target)

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_cgroup_unavailable"
    ):
        runner._trusted_interpreter_digest(venv_python, 1024)


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux descriptor accounting required"
)
def test_run_baseline_closes_retained_fds_for_repeated_success_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))
    before = len(os.listdir("/proc/self/fd"))

    for index in range(3):
        success_home = tmp_path / f"success-{index}"
        _install_config(success_home, fake)
        monkeypatch.setenv("XDG_STATE_HOME", str(success_home))
        runner.run_baseline(PROJECT_ROOT)
        with pytest.raises(runner.PressureProtocolError, match="pressure_run_exists"):
            runner.run_baseline(PROJECT_ROOT)

        missing_home = tmp_path / f"missing-{index}"
        monkeypatch.setenv("XDG_STATE_HOME", str(missing_home))
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_stack_missing"
        ):
            runner.run_baseline(PROJECT_ROOT)

        invalid_home = tmp_path / f"invalid-{index}"
        invalid_path = _install_config(invalid_home, fake)
        invalid_path.write_text("{}", encoding="ascii")
        monkeypatch.setenv("XDG_STATE_HOME", str(invalid_home))
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_invalid_config"
        ):
            runner.run_baseline(PROJECT_ROOT)

        failed_home = tmp_path / f"failed-{index}"
        _install_config(failed_home, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
        monkeypatch.setenv("XDG_STATE_HOME", str(failed_home))
        monkeypatch.setenv("PRESSURE_FAKE_MODE", "oversized")
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_output_too_large"
        ):
            runner.run_baseline(PROJECT_ROOT)

    assert len(os.listdir("/proc/self/fd")) == before


def test_bounded_runner_never_retries_a_failed_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = [0]

    def failed_cleanup(_process: subprocess.Popen[bytes], _process_group: int) -> None:
        calls[0] += 1
        raise runner.PressureProtocolError("pressure_process_group_cleanup_failed")

    monkeypatch.setattr(runner, "_cleanup_selected_process", failed_cleanup)
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_process_group_cleanup_failed"
    ):
        runner._run_bounded(
            ["/bin/true"],
            tmp_path,
            runner.time.monotonic() + 1,
            env=runner._selected_environment([]),
        )
    assert calls == [1]


def test_successful_selected_stack_reaps_all_detached_group_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    child_pid = tmp_path / "children.pid"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        environment_allowlist=["PRESSURE_FAKE_MODE", "PRESSURE_CHILD_PID_FILE"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "orphan-success")
    monkeypatch.setenv("PRESSURE_CHILD_PID_FILE", str(child_pid))
    pids: list[int] = []
    try:
        summary = runner.run_baseline(PROJECT_ROOT)
        pids = [
            int(line) for line in child_pid.read_text(encoding="ascii").splitlines()
        ]
        assert len(pids) == 6
        assert [item["scenario_id"] for item in summary["outcomes"]] == SCENARIO_IDS
        assert not any(_pid_is_live(pid) for pid in pids)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize(
    "mode", ("inherited-stdio-success", "inherited-stdio-ignore-term-success")
)
def test_successful_selected_stack_reaps_inherited_stdio_children_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    fake = tmp_path / "fake_cli.py"
    child_pid = tmp_path / "children.pid"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        caps={
            "turns": 9,
            "tools": 9,
            "tokens": 99,
            "elapsed_seconds": 4,
            "cost": "9.0",
        },
        environment_allowlist=["PRESSURE_FAKE_MODE", "PRESSURE_CHILD_PID_FILE"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", mode)
    monkeypatch.setenv("PRESSURE_CHILD_PID_FILE", str(child_pid))
    pids: list[int] = []
    started = runner.time.monotonic()
    try:
        summary = runner.run_baseline(PROJECT_ROOT)
        elapsed = runner.time.monotonic() - started
        pids = [
            int(line) for line in child_pid.read_text(encoding="ascii").splitlines()
        ]
        assert elapsed < 3
        assert [
            (item["scenario_id"], item["outcome_code"]) for item in summary["outcomes"]
        ] == [
            ("exact-retry-loop", "stagnation-not-reheated"),
            ("plan-oscillation", "pass"),
            ("productive-pagination", "pass"),
            ("blocked-authority", "pass"),
            ("unsafe-write", "unsafe-write-attempt"),
            ("exhausted-budget", "pass"),
        ]
        assert len(pids) == 6
        assert not any(_pid_is_live(pid) for pid in pids)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_normal_exit_reaps_child_left_in_selected_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    child_pid = tmp_path / "child.pid"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        environment_allowlist=["PRESSURE_FAKE_MODE", "PRESSURE_CHILD_PID_FILE"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "orphan")
    monkeypatch.setenv("PRESSURE_CHILD_PID_FILE", str(child_pid))

    summary = runner.run_baseline(PROJECT_ROOT)
    assert [item["scenario_id"] for item in summary["outcomes"]] == SCENARIO_IDS
    pid = int(child_pid.read_text(encoding="ascii"))
    try:
        os.kill(pid, 0)
        pytest.fail("selected stack left a live child process")
    except ProcessLookupError:
        pass
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("mode", ("setsid-success", "setsid-ignore-term-success"))
def test_selected_stack_reaps_setsids_that_escape_process_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A selected stack can detach its session; a process group cannot contain it."""
    fake = tmp_path / "fake_cli.py"
    child_pid = tmp_path / "children.pid"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        environment_allowlist=["PRESSURE_FAKE_MODE", "PRESSURE_CHILD_PID_FILE"],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", mode)
    monkeypatch.setenv("PRESSURE_CHILD_PID_FILE", str(child_pid))
    pids: list[int] = []
    try:
        summary = runner.run_baseline(PROJECT_ROOT)
        pids = [
            int(line) for line in child_pid.read_text(encoding="ascii").splitlines()
        ]
        assert len(pids) == 6
        assert [item["scenario_id"] for item in summary["outcomes"]] == SCENARIO_IDS
        assert not any(_pid_is_live(pid) for pid in pids)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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


@pytest.mark.parametrize(
    "raw",
    (
        b'{"outer":{"nested":{"key":1,"key":2}}}',
        b'{"first":1}{"second":2}',
        b'{"first":1} trailing',
    ),
)
def test_strict_json_loader_rejects_nested_duplicate_and_multiple_values(
    tmp_path: Path, raw: bytes
) -> None:
    candidate = tmp_path / "input.json"
    candidate.write_bytes(raw)

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_invalid_json"
    ) as error:
        runner._load_json(candidate)
    assert "key" not in str(error.value)


def test_validated_config_hash_binds_exact_raw_bytes_and_duplicate_config_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    path = _install_config(tmp_path, fake)
    value = json.loads(path.read_text(encoding="ascii"))
    raw_one = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    raw_two = json.dumps(value, sort_keys=True, indent=2).encode("ascii")
    assert (
        runner._validate_config(value, raw_one)["config_sha256"]
        != runner._validate_config(value, raw_two)["config_sha256"]
    )
    path.write_bytes(b'{"contract_version":"1.0","contract_version":"1.0"}')
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(runner.PressureProtocolError, match="pressure_invalid_config"):
        runner.run_baseline(PROJECT_ROOT)


@pytest.mark.parametrize(
    "value",
    (
        "1e1000000",
        "1e-1000000",
        "-0.1",
        "+0.1",
        "00.1",
        "0000000000001",
        "1.0000000",
        "NaN",
        "Infinity",
        "0" * 13,
        "1" * 13,
    ),
)
def test_cost_grammar_is_fixed_point_bounded_and_never_expands(value: str) -> None:
    with pytest.raises(runner.PressureProtocolError, match="pressure_invalid_decimal"):
        runner._decimal(value)


def test_duplicate_cli_response_is_malformed_and_duplicate_usage_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "duplicate-response")
    summary = runner.run_baseline(PROJECT_ROOT)
    assert summary["outcomes"][0]["outcome_code"] == "malformed-output"

    retry_home = tmp_path / "retry"
    _install_config(retry_home, fake, environment_allowlist=["PRESSURE_FAKE_MODE"])
    monkeypatch.setenv("XDG_STATE_HOME", str(retry_home))
    monkeypatch.setenv("PRESSURE_FAKE_MODE", "duplicate-usage")
    with pytest.raises(runner.PressureProtocolError, match="pressure_invalid_usage"):
        runner.run_baseline(PROJECT_ROOT)


def test_manifest_binds_all_six_scenario_streams_and_usage_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    summary = runner.run_baseline(PROJECT_ROOT)
    state_root = tmp_path / "semantic-reheating" / "pressure-baselines"
    run_dir = next(
        path for path in state_root.iterdir() if path.name.startswith("run-")
    )
    receipt = summary["private_transcript_receipt"]
    assert receipt["name"] == "baseline-evidence-manifest.json"
    manifest_bytes = (run_dir / receipt["name"]).read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == receipt["sha256"]
    manifest = json.loads(manifest_bytes)
    assert [entry["scenario_id"] for entry in manifest["entries"]] == SCENARIO_IDS
    assert len(manifest["entries"]) == 6
    for position, entry in enumerate(manifest["entries"], start=1):
        assert (
            entry["stdout_sha256"]
            == hashlib.sha256(
                (run_dir / f"stdout-{position}.bin").read_bytes()
            ).hexdigest()
        )
        assert (
            entry["stderr_sha256"]
            == hashlib.sha256(
                (run_dir / f"stderr-{position}.bin").read_bytes()
            ).hexdigest()
        )
        assert (
            entry["usage_sha256"]
            == hashlib.sha256(
                (run_dir / f"usage-{position}.json").read_bytes()
            ).hexdigest()
        )
    assert stat.S_IMODE((run_dir / receipt["name"]).stat().st_mode) == 0o600
    for position in range(2, 7):
        assert (
            manifest["entries"][position - 1]["stdout_sha256"]
            != hashlib.sha256(
                f"detached-scenario-{position}".encode("ascii")
            ).hexdigest()
        )
    assert (
        manifest["entries"][0]["stderr_sha256"]
        != hashlib.sha256(b"detached-stderr").hexdigest()
    )
    assert (
        manifest["entries"][0]["usage_sha256"]
        != hashlib.sha256(b'{"detached":"usage"}').hexdigest()
    )


def test_sanitize_projection_rejects_oversized_canonical_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    summary = runner.run_baseline(PROJECT_ROOT)
    schema, hashes = runner._baseline_summary_schema()
    monkeypatch.setattr(runner, "_baseline_summary_schema", lambda: (schema, hashes))
    monkeypatch.setattr(runner, "MAX_PUBLIC_BYTES", 10)

    with pytest.raises(
        runner.PressureProtocolError, match="pressure_invalid_public_contract"
    ):
        runner.sanitize_projection(summary)


@pytest.mark.parametrize("name", ("pressure-scenarios.json", "rubric.json"))
def test_public_protocol_rejects_deep_duplicate_json_members(
    tmp_path: Path, name: str
) -> None:
    repo = tmp_path / "repo"
    reference = repo / "skills" / "semantic-reheating" / "references"
    reference.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: not-a-real-repository\n", encoding="ascii")
    for source in REFERENCES.glob("*.json"):
        target = reference / source.name
        target.write_bytes(source.read_bytes())
    target = reference / name
    raw = target.read_bytes()
    marker = b'"contract_version": "1.0"'
    target.write_bytes(raw.replace(marker, marker + b',"contract_version":"1.0"', 1))
    with pytest.raises(runner.PressureProtocolError, match="pressure_invalid_json"):
        runner.load_public_protocol(repo)


def test_schema_byte_bindings_are_reported_and_stale_summary_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(tmp_path, fake)
    original = runner.load_public_protocol(PROJECT_ROOT)
    repo = tmp_path / "repo"
    reference = repo / "skills" / "semantic-reheating" / "references"
    reference.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: not-a-real-repository\n", encoding="ascii")
    for source in REFERENCES.glob("*.json"):
        (reference / source.name).write_bytes(source.read_bytes())
    (reference / "rubric.schema.json").write_bytes(
        (reference / "rubric.schema.json").read_bytes() + b"\n"
    )
    monkeypatch.setattr(runner, "_worktree_roots", lambda root: (root.resolve(),))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    summary = runner.run_baseline(repo)
    assert summary["rubric_schema_sha256"] != original["hashes"]["rubric_schema"]
    stale = copy.deepcopy(summary)
    stale["rubric_schema_sha256"] = "0" * 64
    with pytest.raises(
        runner.PressureProtocolError, match="pressure_invalid_public_contract"
    ):
        runner.sanitize_projection(stale)


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux sealed-descriptor test"
)
def test_sealed_snapshot_is_immutable_and_executes_original_source_after_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "selected"
    source.write_text("#!/bin/sh\nprintf original\n", encoding="ascii")
    source.chmod(0o700)

    snapshot = runner._capture_sealed_snapshot(source, 1024)
    try:
        assert snapshot.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
        with pytest.raises(OSError):
            os.write(snapshot.fd, b"x")
        with pytest.raises(OSError):
            os.ftruncate(snapshot.fd, 0)
        with pytest.raises(OSError):
            runner.fcntl.fcntl(snapshot.fd, runner._F_ADD_SEALS, 0x10)
        source.write_text("#!/bin/sh\nprintf replaced\n", encoding="ascii")
        source.chmod(0o700)
        completed = subprocess.run(
            [snapshot.proc_path], check=True, capture_output=True, text=True
        )
        assert completed.stdout == "original"
        assert runner._sealed_snapshot_digest(snapshot.proc_path) == snapshot.sha256
    finally:
        snapshot.close()


def test_resolve_executable_returns_sealed_original_after_source_swap(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected"
    original = b"#!/bin/sh\nprintf original\n"
    selected.write_bytes(original)
    selected.chmod(0o700)

    snapshot = runner._resolve_executable(
        [str(selected)], hashlib.sha256(original).hexdigest()
    )
    try:
        selected.write_text("#!/bin/sh\nprintf swapped\n", encoding="ascii")
        selected.chmod(0o700)
        assert (
            subprocess.run(
                [snapshot.proc_path], check=True, capture_output=True, text=True
            ).stdout
            == "original"
        )
    finally:
        snapshot.close()


def test_deadline_covers_selected_snapshot_before_cgroup_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake_cli.py"
    _write_fake_cli(fake)
    _install_config(
        tmp_path,
        fake,
        caps={
            "turns": 9,
            "tools": 9,
            "tokens": 99,
            "elapsed_seconds": 1,
            "cost": "9.0",
        },
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(runner, "_worktree_roots", lambda repo: (repo.resolve(),))
    clock = [0.0]
    cgroup_calls = [0]

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])

    def delayed_resolve(_argv: list[str], _digest: str) -> runner._SealedSnapshot:
        clock[0] += 10
        return runner._SealedSnapshot(-1, "/proc/1/fd/3", "0" * 64, 0)

    monkeypatch.setattr(runner, "_resolve_executable", delayed_resolve)
    monkeypatch.setattr(
        runner,
        "_build_cgroup_context",
        lambda *_args: cgroup_calls.__setitem__(0, cgroup_calls[0] + 1),
    )
    with pytest.raises(runner.PressureProtocolError, match="pressure_timeout"):
        runner.run_baseline(PROJECT_ROOT)
    assert cgroup_calls == [0]


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux descriptor accounting required"
)
@pytest.mark.parametrize("failure", ("write", "seal", "read", "hash"))
def test_sealed_snapshot_failure_paths_close_the_memfd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "selected"
    source.write_bytes(b"#!/bin/sh\nexit 0\n")
    source.chmod(0o700)
    before = len(os.listdir("/proc/self/fd"))

    if failure == "write":
        monkeypatch.setattr(
            runner,
            "_write_all",
            lambda *_args: (_ for _ in ()).throw(
                runner.PressureProtocolError("injected_write")
            ),
        )
    elif failure == "seal":
        original_fcntl = runner.fcntl.fcntl

        def failed_seal(
            descriptor: int, command: int, argument: int | bytes = 0
        ) -> int | bytes:
            if command == runner._F_ADD_SEALS:
                raise OSError("injected_seal")
            return original_fcntl(descriptor, command, argument)

        monkeypatch.setattr(runner.fcntl, "fcntl", failed_seal)
    elif failure == "read":
        monkeypatch.setattr(
            runner,
            "_read_descriptor_bytes",
            lambda *_args: (_ for _ in ()).throw(
                runner.PressureProtocolError("injected_read")
            ),
        )
    else:
        monkeypatch.setattr(
            runner,
            "_sha256",
            lambda *_args: (_ for _ in ()).throw(
                runner.PressureProtocolError("injected_hash")
            ),
        )

    for _ in range(2):
        with pytest.raises(
            runner.PressureProtocolError, match="pressure_cgroup_unavailable"
        ):
            runner._capture_sealed_snapshot(source, 1024)
        assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux descriptor accounting required"
)
def test_cgroup_context_closes_helper_snapshot_after_post_capture_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(
        runner,
        "_trusted_interpreter_digest",
        lambda *_args: (_ for _ in ()).throw(
            runner.PressureProtocolError("injected_interpreter")
        ),
    )

    with pytest.raises(runner.PressureProtocolError, match="injected_interpreter"):
        runner._build_cgroup_context(runner.time.monotonic() + 1, tmp_path)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux descriptor accounting required"
)
def test_state_root_closes_all_resources_after_close_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    child_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    source = tmp_path / "selected"
    source.write_bytes(b"#!/bin/sh\nexit 0\n")
    source.chmod(0o700)
    snapshot = runner._capture_sealed_snapshot(source, 1024)
    snapshot_fd = snapshot.fd
    state_root = runner._StateRoot(
        fd=root_fd, path=tmp_path, children=[child_fd], snapshots=[snapshot]
    )
    original_close = os.close

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor in {snapshot_fd, child_fd}:
            raise OSError("injected_close")

    monkeypatch.setattr(runner.os, "close", close_then_fail)
    with pytest.raises(OSError, match="injected_close"):
        state_root.close()
    for descriptor in (root_fd, child_fd, snapshot_fd):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert state_root.fd == -1
    assert state_root.children == []
    assert state_root.snapshots == []
    state_root.close()


def test_system_python_final_symlink_resolves_to_root_owned_target() -> None:
    lexical = runner._SYSTEM_PYTHON_PATH
    assert lexical.is_symlink()
    resolved, digest, identity = runner._trusted_interpreter_digest(
        lexical, 64 * 1024 * 1024
    )
    target = lexical.resolve(strict=True)
    target_metadata = target.lstat()
    assert resolved == str(target)
    assert target_metadata.st_uid == 0
    assert identity == (target_metadata.st_dev, target_metadata.st_ino)
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest()


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux sealed-descriptor test"
)
def test_sealed_elf_snapshot_executes_after_source_swap(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.write_bytes(Path("/bin/true").read_bytes())
    selected.chmod(0o700)
    snapshot = runner._capture_sealed_snapshot(selected, 64 * 1024 * 1024)
    try:
        selected.write_text("#!/bin/sh\nexit 99\n", encoding="ascii")
        selected.chmod(0o700)
        assert subprocess.run([snapshot.proc_path], check=False).returncode == 0
        assert runner._sealed_snapshot_digest(snapshot.proc_path) == snapshot.sha256
    finally:
        snapshot.close()


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux sealed-descriptor test"
)
def test_sealed_helper_executes_captured_source_not_mutated_path(
    tmp_path: Path,
) -> None:
    helper_source = tmp_path / "helper.py"
    helper_source.write_bytes(Path(runner.__file__).read_bytes())
    selected = tmp_path / "selected"
    selected.write_text("#!/bin/sh\nprintf sealed-helper\n", encoding="ascii")
    selected.chmod(0o700)
    helper_snapshot = runner._capture_sealed_snapshot(
        helper_source, runner._MAX_HELPER_BYTES
    )
    selected_snapshot = runner._capture_sealed_snapshot(selected, 1024)
    try:
        python_path, python_sha256, _identity = runner._trusted_interpreter_digest(
            runner._SYSTEM_PYTHON_PATH, 64 * 1024 * 1024
        )
        launch = {
            "argv": [str(selected)],
            "environment": dict(runner._MINIMAL_SELECTED_ENV),
            "environment_names": sorted(runner._MINIMAL_SELECTED_ENV),
            "helper_sha256": helper_snapshot.sha256,
            "python_sha256": python_sha256,
            "run_dev": tmp_path.stat().st_dev,
            "run_ino": tmp_path.stat().st_ino,
            "selected_path": selected_snapshot.proc_path,
            "selected_sha256": selected_snapshot.sha256,
            "selected_size": selected_snapshot.size,
        }
        launch_path = tmp_path / "launch.json"
        launch_path.write_bytes(runner._canonical_bytes(launch))
        launch_path.chmod(0o600)
        helper_source.write_text("raise SystemExit(99)\n", encoding="ascii")
        completed = subprocess.run(
            [
                python_path,
                helper_snapshot.proc_path,
                "--pressure-cgroup-helper",
                str(launch_path),
            ],
            cwd=tmp_path,
            env=dict(runner._MINIMAL_SELECTED_ENV),
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert completed.stdout == "sealed-helper"
        assert not launch_path.exists()
    finally:
        selected_snapshot.close()
        helper_snapshot.close()
