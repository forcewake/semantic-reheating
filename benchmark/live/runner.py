"""Offline-only expansion and validation of bounded campaign declarations.

This module deliberately contains no provider, process, or network integration.  It
only converts already-declared JSON documents into a finite planned-run matrix.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_ALLOWED_ARMS = frozenset({"hard_stop_only", "generic_rethink", "semantic_reheating"})
_REQUIRED_COMMAND_FLAGS = frozenset(
    {"--offline", "--read-only", "--sandbox=$TASK_SANDBOX"}
)
_FORBIDDEN_COMMAND_MARKERS = (
    "http",
    "curl",
    "wget",
    "ssh",
    "network",
    "upload",
    "write",
    "delete",
    " rm",
)


class CampaignConfigurationError(ValueError):
    """Raised when an in-memory campaign cannot form a safe finite matrix."""


def _schema(name: str) -> Draft202012Validator:
    path = Path(__file__).with_name(name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_blockers(document: object, schema_name: str, label: str) -> list[str]:
    if not isinstance(document, Mapping):
        return [f"{label}_schema_invalid"]
    validator = _schema(schema_name)
    if next(validator.iter_errors(document), None) is not None:
        return [f"{label}_schema_invalid"]
    return []


def _has_safe_command(command: object) -> bool:
    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        return False
    joined = " ".join(command).lower()
    return _REQUIRED_COMMAND_FLAGS.issubset(command) and not any(
        marker in joined for marker in _FORBIDDEN_COMMAND_MARKERS
    )


def _campaign_blockers(campaign: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    tasks = campaign["tasks"]
    task_ids = [task["task_id"] for task in tasks]
    fixture_paths = [task["fixture_path"] for task in tasks]
    if len(set(task_ids)) != len(task_ids) or len(set(fixture_paths)) != len(
        fixture_paths
    ):
        blockers.append("campaign_tasks_not_unique")
    if campaign["arms"] != ["hard_stop_only", "generic_rethink", "semantic_reheating"]:
        blockers.append("campaign_arms_not_complete")
    if set(campaign["arms"]) != _ALLOWED_ARMS:
        blockers.append("campaign_arms_invalid")
    for task in tasks:
        if task["sandbox"]["root"] != f"$TASK_SANDBOX/{task['task_id']}":
            blockers.append("task_sandbox_not_task_local")
        if task["sandbox"]["external_side_effect_capability"]:
            blockers.append("task_sandbox_side_effect_capable")
        if task["tools"]["external_side_effect_capability"]:
            blockers.append("task_tools_side_effect_capable")
    return blockers


def _stack_blockers(
    stacks: Mapping[str, Any], campaign: Mapping[str, Any]
) -> list[str]:
    blockers: list[str] = []
    selected = stacks["stacks"]
    stack_ids = [stack["stack_id"] for stack in selected]
    if len(set(stack_ids)) != len(stack_ids):
        blockers.append("stack_ids_not_unique")
    per_run_tokens = campaign["per_run_caps"]["tokens"]
    per_run_cost = campaign["per_run_caps"]["cost_usd"]
    for stack in selected:
        if stack["status"] != "selected":
            blockers.append("stack_not_selected")
        if not _has_safe_command(stack["command"]):
            blockers.append("stack_command_not_offline_read_only")
        sandbox = stack["sandbox"]
        if (
            sandbox["mode"] != "task_local_isolated"
            or sandbox["writable_scope"] != "task_sandbox_only"
            or sandbox["network"] != "disabled"
            or sandbox["external_side_effect_capability"]
        ):
            blockers.append("stack_sandbox_not_isolated")
        if stack["tools"]["external_side_effect_capability"]:
            blockers.append("stack_tools_side_effect_capable")
        pricing = stack["pricing"]
        if stack["kind"] == "local":
            if pricing["cost_reporting"] != "local_zero":
                blockers.append("local_cost_mode_invalid")
            telemetry = stack["telemetry"]
            if (
                telemetry["token_reporting"] != "available"
                or telemetry["time_reporting"] != "available"
            ):
                blockers.append("local_telemetry_incomplete")
        elif pricing["cost_reporting"] == "not_available":
            schedule = pricing["static_schedule"]
            upper_bound = schedule["conservative_token_upper_bound"]
            estimated_cost = (
                max(
                    schedule["input_usd_per_million"],
                    schedule["output_usd_per_million"],
                )
                * upper_bound
                / 1_000_000
            )
            if upper_bound < per_run_tokens or estimated_cost > per_run_cost:
                blockers.append("paid_static_price_not_conservative")
        elif pricing["cost_reporting"] != "provider_reported":
            blockers.append("paid_cost_reporting_invalid")
    return blockers


def configuration_blockers(campaign: object, stacks: object) -> list[str]:
    """Return typed, bounded preflight blockers without executing any stack."""
    blockers = _schema_blockers(campaign, "campaign.schema.json", "campaign")
    blockers.extend(_schema_blockers(stacks, "stacks.schema.json", "stacks"))
    if blockers:
        return sorted(set(blockers))
    assert isinstance(campaign, Mapping)
    assert isinstance(stacks, Mapping)
    blockers.extend(_campaign_blockers(campaign))
    blockers.extend(_stack_blockers(stacks, campaign))
    return sorted(set(blockers))


def build_run_matrix(
    campaign: Mapping[str, Any], stacks: Mapping[str, Any]
) -> tuple[dict[str, object], ...]:
    """Expand exactly the declared offline campaign, or fail before scheduling."""
    blockers = configuration_blockers(campaign, stacks)
    if blockers:
        raise CampaignConfigurationError(",".join(blockers))
    return tuple(
        {
            "stack_id": stack["stack_id"],
            "task_id": task["task_id"],
            "arm": arm,
            "replicate": replicate,
        }
        for stack in stacks["stacks"]
        for task in campaign["tasks"]
        for arm in campaign["arms"]
        for replicate in range(1, campaign["replicates"] + 1)
    )
