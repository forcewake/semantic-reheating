from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

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
RECORD_BLOCK = re.compile(
    r"^### Structured record: (DecisionEnvelope|RecoveryInstruction|RecoveryOutcome|EvidenceRecord)\n"
    r"(?:(?!^### ).)*?^```json\n(.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
H3_PATTERN = re.compile(r"^### ([^\n]+)$", re.MULTILINE)

RECORD_KIND = {
    "DecisionEnvelope": "decision_envelope",
    "RecoveryInstruction": "recovery_instruction",
    "RecoveryOutcome": "recovery_outcome",
    "EvidenceRecord": "evidence_record",
}
EXPECTED_RECORDS = {
    "detection-notice.md": ("DecisionEnvelope",),
    "uncertainty-map.md": ("RecoveryInstruction",),
    "bounded-reheating.md": ("RecoveryInstruction",),
    "select-and-cool.md": ("DecisionEnvelope", "RecoveryInstruction"),
    "verify-or-stop.md": ("RecoveryOutcome", "EvidenceRecord"),
}
OPERATOR_SECTIONS = {
    "detection-notice.md": (
        "Detected signals",
        "Independent no-progress evidence",
        "Prohibited retry",
        "Bounded next action",
        "Host authority",
    ),
    "uncertainty-map.md": (
        "Unknown",
        "Disposition",
        "Evidence refs",
        "Test",
        "Owner",
        "Assumption boundary",
    ),
    "bounded-reheating.md": (
        "Hypotheses",
        "Evidence comparison",
        "Read-only tests",
        "Stop decision",
    ),
    "select-and-cool.md": (
        "Selected branch",
        "Evidence delta",
        "Rejected hypotheses",
        "Next action",
        "Remaining budget",
        "Cooling status",
    ),
    "verify-or-stop.md": (
        "Deterministic acceptance result",
        "Expected state fingerprints",
        "Observed state fingerprints",
        "Outcome/stop code",
        "Decision IDs",
        "Evidence IDs",
        "Blind retry prohibition",
    ),
}


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


def _records(text: str) -> list[tuple[str, dict[str, Any]]]:
    matches = list(RECORD_BLOCK.finditer(_section(text, "Output contract")))
    blocks = re.findall(r"^```json\n(.*?)\n```$", text, re.MULTILINE | re.DOTALL)
    assert len(matches) == len(blocks), (
        "every JSON fence needs one preceding exact record label"
    )
    parsed: list[tuple[str, dict[str, Any]]] = []
    for match in matches:
        value = json.loads(match.group(2))
        assert type(value) is dict, "structured examples must be JSON objects"
        assert "<" not in match.group(2) and ">" not in match.group(2), (
            "no placeholder tokens"
        )
        parsed.append((match.group(1), value))
    return parsed


def _schema_for(kind: str) -> dict[str, Any]:
    from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

    return json.loads((PROJECT_ROOT / PUBLIC_CONTRACT_SCHEMAS[kind]).read_text())


def _assert_schema_shape(schema: dict[str, Any], value: Any) -> None:
    """Derive each populated allowed/required shape from the linked schema."""
    if type(value) is dict:
        properties = schema.get("properties", {})
        assert type(properties) is dict
        assert set(value) <= set(properties)
        assert set(schema.get("required", ())) <= set(value)
        for key, child in value.items():
            if type(child) in (dict, list):
                assert type(properties[key]) is dict
                _assert_schema_shape(properties[key], child)
    elif type(value) is list:
        item_schema = schema.get("items")
        if item_schema is not None:
            assert type(item_schema) is dict
            for item in value:
                _assert_schema_shape(item_schema, item)


def _runtime_validate(kind: str, value: dict[str, Any]) -> None:
    from semantic_reheating.validation import validate_public_artifact

    assert validate_public_artifact(kind, value) == value
    if kind == "decision_envelope":
        from semantic_reheating.models import DecisionEnvelope

        assert DecisionEnvelope.from_dict(value).to_dict() == value


def _validate_record(kind: str, value: dict[str, Any]) -> None:
    schema = _schema_for(kind)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    _runtime_validate(kind, value)


def _validate_common_prompt(name: str, text: str) -> None:
    assert _sections(text) == list(SECTION_ORDER), f"{name} H2 headings drifted"
    for section_name in ("Runtime form", "Operator form"):
        section = _section(text, section_name)
        assert len(section.split()) >= 12, f"{name} {section_name} must be actionable"
        _assert_contains(section, "must")
    _assert_contains(_section(text, "Trigger"), "use only when", "evidence")
    _assert_contains(_section(text, "Non-trigger"), "do not use", "normal progress")
    _assert_contains(
        text,
        "host remains the sole authority",
        "does not grant tools",
        "credentials",
        "approvals",
        "permissions",
        "side-effect authority",
    )
    _assert_contains(
        _section(text, "Budget"),
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
    _assert_contains(
        _section(text, "Tool restrictions"),
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
    _assert_contains(
        _section(text, "Evidence"),
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
    _assert_contains(
        _section(text, "Stop conditions"),
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
    assert "### Operator Markdown rendering (not JSON)" in contract
    exact = "; ".join(OPERATOR_SECTIONS[name])
    assert f"Allowed headings, in order: {exact}." in contract
    _assert_contains(
        contract,
        "replace example values",
        "trace-derived real values",
        "never fabricate ids",
        "unknown major",
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


@pytest.mark.parametrize("path", _prompt_paths(), ids=lambda path: path.name)
def test_prompt_record_inventory_is_exact_and_examples_validate_schema_and_runtime(
    path: Path,
) -> None:
    records = _records(_read_prompt(path))
    assert tuple(label for label, _ in records) == EXPECTED_RECORDS[path.name]
    for label, value in records:
        kind = RECORD_KIND[label]
        schema = _schema_for(kind)
        from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

        schema_name = Path(PUBLIC_CONTRACT_SCHEMAS[kind]).name
        assert re.search(
            rf"### Structured record: {label}\n.*?\]\(\.\./contracts/v1/{re.escape(schema_name)}\)",
            _section(_read_prompt(path), "Output contract"),
            re.DOTALL,
        )
        _assert_schema_shape(schema, value)
        _validate_record(kind, value)


def test_bounded_reheating_example_has_the_schema_conditional_hypothesis_contract() -> (
    None
):
    records = _records(_read_prompt(PROMPTS_DIR / "bounded-reheating.md"))
    assert len(records) == 1
    _, record = records[0]
    assert record["selected_prompt_asset_id"] == "prompt-reheat-v1"
    hypothesis = record["expected_output"]["hypothesis_contract"]
    assert hypothesis["allowed_test_effect_classes"] == ["read_only"]
    assert hypothesis["exact_hypotheses"] == 3
    for field in _schema_for("recovery_instruction")["required"]:
        assert field in record


def test_bounded_reheating_has_exactly_three_operator_hypotheses_not_json_labels() -> (
    None
):
    text = _read_prompt(PROMPTS_DIR / "bounded-reheating.md")
    contract = _section(text, "Output contract")
    headings = re.findall(r"^### (Hypothesis \d+)$", contract, re.MULTILINE)
    assert headings == ["Hypothesis 1", "Hypothesis 2", "Hypothesis 3"]
    assert "### Hypothesis 4" not in contract
    assert not re.search(r"^#### ", contract, re.MULTILINE)
    assert H3_PATTERN.findall(contract)[-1] == "Structured record: RecoveryInstruction"
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
            assert item.count(label) == 1
        assert item.lower().count("exactly one test") == 1


def _validate_detection_semantics(text: str) -> None:
    trigger = _section(text, "Trigger").lower()
    _assert_contains(
        trigger,
        "at least one repetition-class finding",
        "independent measurable no-progress-class finding",
        "and",
        "risk",
        "hard budget",
        "stop",
        "escalate",
        "never reheat",
        "budget evidence alone",
    )
    non_trigger = _section(text, "Non-trigger").lower()
    _assert_contains(
        non_trigger,
        "single class",
        "two same-class",
        "changed pagination cursor",
        "batch item",
        "changed hypothesis",
        "tool input",
        "new evidence",
        "error fingerprint",
        "acceptance-required verification rerun",
        "productive handoff",
        "converging state poll",
    )


def test_detection_gate_requires_independent_and_and_productive_controls() -> None:
    text = _read_prompt(PROMPTS_DIR / "detection-notice.md")
    _validate_detection_semantics(text)
    for mutation in (
        text.replace(" finding AND one independent", " finding OR one independent"),
        text.replace("Budget evidence alone never counts as either class.", ""),
        text.replace("changed pagination cursor", "same pagination cursor"),
    ):
        with pytest.raises(AssertionError):
            _validate_detection_semantics(mutation)


def test_asset_specific_cooling_and_verify_trigger_are_not_copied() -> None:
    detection = _read_prompt(PROMPTS_DIR / "detection-notice.md")
    uncertainty = _read_prompt(PROMPTS_DIR / "uncertainty-map.md")
    select = _read_prompt(PROMPTS_DIR / "select-and-cool.md")
    bounded = _read_prompt(PROMPTS_DIR / "bounded-reheating.md")
    verify = _read_prompt(PROMPTS_DIR / "verify-or-stop.md")
    _assert_contains(
        _section(detection, "Cooling"),
        "evidence is recorded",
        "diagnose/continue/stop/escalate",
        "does not select or execute",
    )
    _assert_contains(
        _section(uncertainty, "Cooling"),
        "all unknowns classified",
        "verify items",
        "bounded host-approved checks",
        "no branch selection claim",
    )
    _assert_contains(
        _section(select, "Cooling"),
        "greater verified support",
        "selected authorized action",
    )
    _assert_contains(
        _section(bounded, "Cooling"),
        "greater verified support",
        "selected authorized action",
    )
    _assert_contains(
        _section(verify, "Cooling"),
        "deterministic expected-vs-observed comparison",
        "outcome/evidence record",
        "no blind retry/research",
        "new host-authorized independent episode",
        "new budget",
    )
    _assert_contains(
        _section(verify, "Trigger"), "after an authorized host action", "outcome"
    )
    _assert_contains(
        _section(verify, "Non-trigger"), "before action", "no expected state"
    )
    with pytest.raises(AssertionError):
        _assert_contains(
            _section(
                verify.replace("outcome/evidence record", "branch selection", 1),
                "Cooling",
            ),
            "outcome/evidence record",
        )


def _validate_annealing_hygiene(text: str) -> None:
    exact = "Semantic reheating is not decoding-temperature control and is not simulated annealing in the strict mathematical sense."
    required_sections = ("Purpose", "Runtime form")
    for section_name in required_sections:
        assert _section(text, section_name).count(exact) == 1
    assert text.count(exact) == len(required_sections)
    remaining = text.replace(exact, "")
    assert "simulated annealing" not in remaining.lower()
    assert not re.search(
        r"\bdecoding[- ]temperature control\b", remaining, re.IGNORECASE
    )


def test_bounded_reheating_has_only_the_exact_strict_annealing_nonclaim() -> None:
    text = _read_prompt(PROMPTS_DIR / "bounded-reheating.md")
    exact = "Semantic reheating is not decoding-temperature control and is not simulated annealing in the strict mathematical sense."
    _validate_annealing_hygiene(text)
    for mutation in (
        text.replace("not simulated annealing", "simulated annealing", 1),
        text.replace("strict mathematical ", "", 1),
        f"{text}\nSemantic reheating is simulated annealing.\n",
        f"{text}\nSemantic reheating equals simulated annealing.\n",
        f"{text}\nSemantic reheating is equivalent to simulated annealing.\n",
        f"{text}\n{exact}\n",
    ):
        with pytest.raises(AssertionError):
            _validate_annealing_hygiene(mutation)


@pytest.mark.parametrize("path", _prompt_paths(), ids=lambda path: path.name)
def test_prompt_hygiene_is_public_and_non_secret(path: Path) -> None:
    text = _read_prompt(path).lower()
    forbidden = (
        ".hermes",
        "/home/",
        "private chat",
        "chain-of-thought",
        "hidden reasoning transcript",
        "gpt-",
        "claude",
        "openai",
        "anthropic",
        "certified",
        "compliant",
        "price per",
    )
    if path.name != "bounded-reheating.md":
        forbidden += ("simulated annealing",)
    for term in forbidden:
        assert term not in text, (
            f"{path.name} exposes prohibited public-hygiene term {term!r}"
        )


def _closed_nested_objects(
    schema: dict[str, Any], value: dict[str, Any]
) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    for field, child_schema in schema["properties"].items():
        child = value.get(field)
        if type(child) is dict and child_schema.get("additionalProperties") is False:
            closed.append(child)
    return closed


def _assert_rejected_by_schema_and_runtime(kind: str, invalid: dict[str, Any]) -> None:
    from semantic_reheating.validation import ContractValidationError

    with pytest.raises(ValidationError):
        Draft202012Validator(_schema_for(kind)).validate(invalid)
    with pytest.raises(ContractValidationError):
        _runtime_validate(kind, invalid)


@pytest.mark.parametrize("path", _prompt_paths(), ids=lambda path: path.name)
def test_examples_reject_schema_derived_unknown_missing_nested_and_unknown_major_mutations(
    path: Path,
) -> None:
    for label, example in _records(_read_prompt(path)):
        kind = RECORD_KIND[label]
        schema = _schema_for(kind)
        invalids: list[dict[str, Any]] = []
        unknown = deepcopy(example)
        unknown["unexpected_prompt_field"] = True
        invalids.append(unknown)
        missing = deepcopy(example)
        del missing[next(iter(schema["required"]))]
        invalids.append(missing)
        unknown_major = deepcopy(example)
        unknown_major["contract_version"] = "2.0"
        invalids.append(unknown_major)
        nested = deepcopy(example)
        for nested_target in _closed_nested_objects(schema, nested):
            nested_target["unexpected_prompt_field"] = True
            invalids.append(deepcopy(nested))
            del nested_target["unexpected_prompt_field"]
        assert len(invalids) >= 4
        for invalid in invalids:
            _assert_rejected_by_schema_and_runtime(kind, invalid)


def test_examples_reject_merged_records_and_bounded_conditional_mutations() -> None:
    select_records = _records(_read_prompt(PROMPTS_DIR / "select-and-cool.md"))
    decision = deepcopy(select_records[0][1])
    instruction = deepcopy(select_records[1][1])
    decision.update(instruction)
    _assert_rejected_by_schema_and_runtime("decision_envelope", decision)
    bounded = deepcopy(
        _records(_read_prompt(PROMPTS_DIR / "bounded-reheating.md"))[0][1]
    )
    del bounded["expected_output"]["hypothesis_contract"]
    _assert_rejected_by_schema_and_runtime("recovery_instruction", bounded)
    bounded = deepcopy(
        _records(_read_prompt(PROMPTS_DIR / "bounded-reheating.md"))[0][1]
    )
    bounded["expected_output"]["hypothesis_contract"]["allowed_test_effect_classes"] = [
        "idempotent_write"
    ]
    _assert_rejected_by_schema_and_runtime("recovery_instruction", bounded)
