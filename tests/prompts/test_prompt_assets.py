from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
EXPECTED_PROMPTS = {
    "detection-notice.md",
    "uncertainty-map.md",
    "bounded-reheating.md",
    "select-and-cool.md",
    "verify-or-stop.md",
}
SECTION_ORDER = (
    "Purpose",
    "Runtime form",
    "Operator form",
    "Trigger",
    "Non-trigger",
    "Budget",
    "Tool restrictions",
    "Evidence",
    "Cooling",
    "Stop conditions",
    "Output contract",
)
H2_PATTERN = re.compile(r"^## ([^\n]+)$", re.MULTILINE)


def _prompt_paths() -> list[Path]:
    return sorted(PROMPTS_DIR.glob("*.md"))


def _read_prompt(path: Path) -> str:
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name} has a UTF-8 BOM"
    assert b"\r" not in data, f"{path.name} must use LF only"
    assert data.endswith(b"\n"), f"{path.name} must end with one final LF"
    assert data.strip(), f"{path.name} is empty"
    assert len(data) <= 64 * 1024, f"{path.name} exceeds 64 KiB"
    return data.decode("utf-8")


def _sections(text: str) -> list[str]:
    return H2_PATTERN.findall(text)


def _section(text: str, name: str) -> str:
    headings = list(H2_PATTERN.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group(1) == name:
            end = (
                headings[index + 1].start() if index + 1 < len(headings) else len(text)
            )
            return text[heading.end() : end].strip()
    raise AssertionError(f"missing section {name!r}")


def _assert_contains(section: str, *terms: str) -> None:
    lowered = section.lower()
    for term in terms:
        assert term.lower() in lowered, f"missing required concept: {term!r}"


def _validate_common_prompt(name: str, text: str) -> None:
    assert _sections(text) == list(SECTION_ORDER), f"{name} H2 headings drifted"
    for section_name in ("Runtime form", "Operator form"):
        section = _section(text, section_name)
        assert len(section.split()) >= 12, f"{name} {section_name} must be actionable"
        _assert_contains(section, "must")

    _assert_contains(_section(text, "Trigger"), "use only when", "evidence")
    _assert_contains(_section(text, "Non-trigger"), "do not use", "normal progress")

    whole = text.lower()
    _assert_contains(
        whole,
        "host remains the sole authority",
        "does not grant tools",
        "credentials",
        "approvals",
        "permissions",
        "side-effect authority",
    )
    budget = _section(text, "Budget")
    _assert_contains(
        budget,
        "turns",
        "tool calls",
        "tokens",
        "elapsed time",
        "seconds",
        "cost",
        "per-intervention",
        "whole-run",
        "remaining",
        "limits",
        "retries",
        "handoffs",
        "callbacks",
        "re-entry",
        "recovery episodes",
        "re-entry depth",
        "no fixed universal iteration count",
        "first hard-limit breach",
    )
    restrictions = _section(text, "Tool restrictions")
    _assert_contains(
        restrictions,
        "read-only",
        "sandbox",
        "no writes",
        "external side effects",
        "unknown",
        "unconfirmed",
        "non-idempotent writes",
        "credential",
        "host policy",
        "allowlist",
        "advisory",
    )
    evidence = _section(text, "Evidence")
    _assert_contains(
        evidence,
        "event ids",
        "evidence ids",
        "fingerprints",
        "digests",
        "observed facts",
        "unknowns",
        "assumptions",
        "no hidden reasoning",
        "no raw transcript",
        "unsupported invention",
    )
    cooling = _section(text, "Cooling")
    _assert_contains(
        cooling,
        "greater verified support",
        "concrete next action",
        "remaining budget",
        "host executes at most",
        "deterministic verification",
        "new independent episode",
        "new budget",
    )
    stopping = _section(text, "Stop conditions")
    _assert_contains(
        stopping,
        "unsafe",
        "missing authority",
        "non-idempotent uncertainty",
        "no information gain",
        "ambiguous acceptance",
        "hard budget",
        "stop",
        "escalate",
        "block",
    )
    contract = _section(text, "Output contract")
    _assert_contains(
        contract,
        "only these",
        "unlisted additions",
        "unknown major",
        "fabricated ids",
    )
    assert re.search(r"\[[^]]+\]\((?:\.\./)?contracts/v1/[^)#]+\.json\)", text), (
        f"{name} must link to a committed schema"
    )


def test_prompt_inventory_is_exact_and_regular() -> None:
    assert PROMPTS_DIR.is_dir(), "prompts directory is missing"
    entries = list(PROMPTS_DIR.iterdir())
    assert {entry.name for entry in entries} == EXPECTED_PROMPTS
    for entry in entries:
        assert entry.is_file() and not entry.is_symlink(), (
            f"{entry.name} must be a regular file"
        )


@pytest.mark.parametrize("path", _prompt_paths(), ids=lambda path: path.name)
def test_prompt_assets_are_utf8_bounded_and_structured(path: Path) -> None:
    _validate_common_prompt(path.name, _read_prompt(path))


@pytest.mark.parametrize(
    ("name", "required_terms"),
    [
        (
            "detection-notice.md",
            (
                "detected signals",
                "independent no-progress evidence",
                "prohibited retry",
                "bounded next action",
                "host authority",
                "decision_id",
                "decision",
                "reason_codes",
                "evidence_event_ids",
            ),
        ),
        (
            "uncertainty-map.md",
            (
                "verify",
                "assume",
                "escalate",
                "block",
                "exactly one",
                "evidence refs",
                "test",
                "owner",
                "policy-authorized",
                "bounded",
                "instruction_id",
                "selected_prompt_asset_id",
                "variables",
                "diagnosed_gaps",
            ),
        ),
        (
            "bounded-reheating.md",
            (
                "mutually exclusive",
                "falsifiable",
                "exactly one",
                "read-only",
                "discriminating",
                "semantic reheating is not decoding temperature",
                "side-effect exploration",
                "hypothesis_contract",
                "allowed_test_effect_classes",
            ),
        ),
        (
            "select-and-cool.md",
            (
                "selected branch",
                "evidence delta",
                "rejected hypotheses",
                "next action",
                "remaining budget",
                "cooling status",
                "no selection",
                "decision_id",
                "rejected_hypothesis_refs",
                "cooling_conditions",
            ),
        ),
        (
            "verify-or-stop.md",
            (
                "deterministic acceptance result",
                "expected state fingerprints",
                "observed state fingerprints",
                "outcome/stop code",
                "decision ids",
                "evidence ids",
                "blind retry",
                "outcome_id",
                "host_result",
                "consumed_counters",
                "final_status",
            ),
        ),
    ],
)
def test_each_prompt_has_a_closed_asset_specific_output_contract(
    name: str, required_terms: tuple[str, ...]
) -> None:
    text = _read_prompt(PROMPTS_DIR / name)
    _assert_contains(_section(text, "Output contract"), *required_terms)


def test_bounded_reheating_has_exactly_three_structured_hypotheses() -> None:
    text = _read_prompt(PROMPTS_DIR / "bounded-reheating.md")
    contract = _section(text, "Output contract")
    headings = re.findall(r"^### (Hypothesis \d+)$", contract, re.MULTILINE)
    assert headings == ["Hypothesis 1", "Hypothesis 2", "Hypothesis 3"]
    assert "### Hypothesis 4" not in contract
    assert not re.search(r"^#### ", contract, re.MULTILINE)
    for heading in headings:
        start = contract.index(f"### {heading}")
        next_heading = contract.find("### ", start + 4)
        item = contract[start : next_heading if next_heading != -1 else len(contract)]
        for label in (
            "**Claim**",
            "**Falsifier**",
            "**Supporting evidence**",
            "**Refuting evidence**",
            "**Discriminating read-only test**",
        ):
            assert item.count(label) == 1, f"{heading} must contain one {label}"
        assert item.lower().count("exactly one test") == 1


@pytest.mark.parametrize("path", _prompt_paths(), ids=lambda path: path.name)
def test_prompt_hygiene_is_public_and_non_secret(path: Path) -> None:
    text = _read_prompt(path).lower()
    forbidden = (
        ".hermes",
        "/home/",
        "private chat",
        "chain-of-thought",
        "hidden reasoning transcript",
        "simulated annealing",
        "gpt-",
        "claude",
        "openai",
        "anthropic",
        "certified",
        "compliant",
        "price per",
    )
    for term in forbidden:
        assert term not in text, (
            f"{path.name} exposes prohibited public-hygiene term {term!r}"
        )


def test_asset_validator_mutations_are_rejected() -> None:
    text = _read_prompt(PROMPTS_DIR / "bounded-reheating.md")
    with pytest.raises(AssertionError):
        _validate_common_prompt(
            "mutated.md", text.replace("## Trigger", "## Missing", 1)
        )
    with pytest.raises(AssertionError):
        _validate_common_prompt(
            "mutated.md",
            text.replace("host remains the sole authority", "host authority", 1),
        )
    with pytest.raises(AssertionError):
        _validate_common_prompt("mutated.md", text.replace("tokens", "units", 1))
    with pytest.raises(AssertionError):
        _validate_common_prompt(
            "mutated.md", re.sub(r"\[[^]]+\]\([^)]*\)", "schema", text, count=1)
        )
    with pytest.raises(AssertionError):
        altered = text.replace(
            "### Hypothesis 3", "### Hypothesis 3\n\n### Hypothesis 4", 1
        )
        contract = _section(altered, "Output contract")
        headings = re.findall(r"^### (Hypothesis \d+)$", contract, re.MULTILINE)
        assert headings == ["Hypothesis 1", "Hypothesis 2", "Hypothesis 3"]
