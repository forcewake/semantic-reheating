"""Deterministic bounded advisory budget-burn detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import BudgetCounters, TraceKind
from semantic_reheating.progress import classify_progress

from . import _fail, _finding, _validated_inputs

_COUNTER_NAMES = (
    "turns",
    "tool_calls",
    "tokens",
    "elapsed_seconds",
    "cost",
)


def _is_reset(current: BudgetCounters, prior: BudgetCounters) -> bool:
    """Return whether an exact counter dimension declined from its prior value."""
    return any(getattr(current, name) < getattr(prior, name) for name in _COUNTER_NAMES)


def _is_burn_candidate(current: BudgetCounters, baseline: BudgetCounters) -> bool:
    """Return whether counters are monotonic from baseline with an exact rise."""
    return all(
        getattr(current, name) >= getattr(baseline, name) for name in _COUNTER_NAMES
    ) and any(
        getattr(current, name) > getattr(baseline, name) for name in _COUNTER_NAMES
    )


def detect_budget_burn(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect earliest bounded cumulative budget burn without documented progress."""
    window, parsed_policy = _validated_inputs(
        trace, policy, window_policy="no_progress"
    )
    baseline: tuple[int, str, BudgetCounters] | None = None
    prior: BudgetCounters | None = None
    for position, event in enumerate(window):
        counters = event.budget_counters
        if event.kind is not TraceKind.BUDGET or type(counters) is not BudgetCounters:
            continue
        if baseline is None:
            baseline = (position, event.event_id, counters)
            prior = counters
            continue
        if prior is not None and _is_reset(counters, prior):
            baseline = (position, event.event_id, counters)
            prior = counters
            continue
        prior = counters
        baseline_position, baseline_id, baseline_counters = baseline
        if not _is_burn_candidate(counters, baseline_counters):
            continue
        classification_failed = False
        try:
            made_progress = classify_progress(
                window[baseline_position : position + 1]
            ).made_progress
        except (MemoryError, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - public detector boundary is sanitized.
            classification_failed = True
            made_progress = False
        if classification_failed:
            _fail("invalid_progress_classification")
        if not made_progress:
            return _finding(
                "budget_burn",
                window,
                parsed_policy,
                [baseline_id, event.event_id],
                True,
                finding_class="budget",
            )
        baseline = (position, event.event_id, counters)
    return _finding(
        "budget_burn",
        window,
        parsed_policy,
        [window[-1].event_id],
        False,
        finding_class="budget",
    )
