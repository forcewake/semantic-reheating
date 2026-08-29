from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _documents() -> tuple[dict[str, object], dict[str, object]]:
    campaign = json.loads(
        (PROJECT_ROOT / "benchmark" / "live" / "campaign.example.json").read_text()
    )
    stacks = json.loads(
        (PROJECT_ROOT / "benchmark" / "live" / "stacks.example.json").read_text()
    )
    return campaign, stacks


def test_preflight_accepts_the_bounded_dry_run_without_calling_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmark.live.preflight import preflight_documents

    campaign, stacks = _documents()

    def no_provider(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must not invoke a provider")

    monkeypatch.setattr("socket.create_connection", no_provider)
    result = preflight_documents(campaign, stacks, dry_run=True)

    assert result == {
        "status": "ready",
        "dry_run": True,
        "planned_runs": 108,
        "blockers": [],
    }


@pytest.mark.parametrize(
    ("document", "mutate"),
    [
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("pricing")),
        (
            "stacks",
            lambda campaign, stacks: stacks["stacks"][1]["pricing"].pop(
                "static_schedule"
            ),
        ),
        (
            "stacks",
            lambda campaign, stacks: stacks["stacks"][1]["pricing"][
                "static_schedule"
            ].pop("conservative_token_upper_bound"),
        ),
        ("campaign", lambda campaign, stacks: campaign["per_run_caps"].pop("cost_usd")),
        (
            "campaign",
            lambda campaign, stacks: campaign["campaign_caps"].pop("cost_usd"),
        ),
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("provider")),
        (
            "stacks",
            lambda campaign, stacks: stacks["stacks"][1]["model"].pop("version"),
        ),
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("cli")),
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("framework")),
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("sandbox")),
        ("stacks", lambda campaign, stacks: stacks["stacks"][1].pop("tools")),
    ],
)
def test_paid_remote_preflight_blocks_missing_required_metadata_or_caps(
    document: str, mutate: object
) -> None:
    from benchmark.live.preflight import preflight_documents

    campaign, stacks = _documents()
    assert callable(mutate)
    mutate(campaign, stacks)
    result = preflight_documents(campaign, stacks, dry_run=True)

    assert result["status"] == "blocked"
    assert result["planned_runs"] == 0
    assert result["blockers"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda campaign, stacks: campaign["tasks"][0]["sandbox"].update(
            external_side_effect_capability=True
        ),
        lambda campaign, stacks: campaign["tasks"][0]["sandbox"].update(
            root="/tmp/not-task-local"
        ),
        lambda campaign, stacks: campaign["tasks"][0]["tools"].update(
            allowlist=["synthetic_trace_read", "network_fetch"]
        ),
        lambda campaign, stacks: stacks["stacks"][0]["sandbox"].update(
            network="enabled"
        ),
        lambda campaign, stacks: stacks["stacks"][0]["command"].append("--network"),
        lambda campaign, stacks: stacks["stacks"][0]["tools"].update(
            external_side_effect_capability=True
        ),
    ],
)
def test_preflight_blocks_non_isolated_or_side_effect_capable_declarations(
    mutate: object,
) -> None:
    from benchmark.live.preflight import preflight_documents

    campaign, stacks = _documents()
    assert callable(mutate)
    mutate(campaign, stacks)
    result = preflight_documents(campaign, stacks, dry_run=True)

    assert result["status"] == "blocked"
    assert result["planned_runs"] == 0
    assert result["blockers"]


def test_cli_dry_run_reports_the_exact_matrix_without_network_activity() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.live.preflight",
            "--campaign",
            "benchmark/live/campaign.example.json",
            "--stacks",
            "benchmark/live/stacks.example.json",
            "--dry-run",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ready",
        "dry_run": True,
        "planned_runs": 108,
        "blockers": [],
    }
