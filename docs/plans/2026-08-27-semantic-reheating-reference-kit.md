# Semantic Reheating Reference Kit Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Deliver a public, deterministic-first reference kit that turns versioned agent traces and host policy into explainable, bounded recovery decisions without executing agent tools or taking host authority.

**Architecture:** The package is a pure Python controller: JSON Schema contracts are the public boundary, dataclass models and RFC 8785/SHA-256 fingerprints support deterministic replay, and detectors feed a policy-controlled decision envelope. The `reheat` argparse CLI, benchmark, examples, prompt pack, portable Agent Skill, and article consume those contracts rather than adding framework adapters. The host alone authorizes tools, credentials, retries, side effects, and escalation.

**Tech Stack:** Python 3.11+; uv; pytest; Hypothesis; Ruff; mypy; `jsonschema` Draft 2020-12; `rfc8785`; `argparse`; TypeScript (Node 20/22) with AJV, TypeScript, and Vitest; GitHub Actions; Mermaid CLI; SVG; Pillow; Hugo/PaperMod-compatible Markdown.

**Non-negotiable execution rules**

- Preserve the clean canonical `main` worktree and execute Tasks 1–27 in the sibling public feature worktree `../semantic-reheating-implementation` on `feat/semantic-reheating-reference-kit`. Keep raw/local evidence outside both worktrees. Task 28 alone may fast-forward verified feature history into canonical `main`, create `origin`, and publish.
- Use conservative compatible dependency ranges and commit the lockfiles: Python `jsonschema>=4.26,<5`, `rfc8785>=0.1.4,<0.2`, `hypothesis>=6.165,<7`, `pytest>=9.1,<10`, `pytest-cov>=7.1,<8`, `Pillow>=12.3,<13`, `ruff>=0.16,<0.17`, `mypy>=2.3,<3`, and `skills-ref>=0.1.1,<0.2`; TypeScript package ranges AJV `^8.20.0`, canonicalize `^4.0.0`, TypeScript `^7.0.2`, and Vitest `^4.1.11`, with `package-lock.json` pinning resolved bytes. These are lower bounds, not claims that every newer version is required.
- Apply strict vertical TDD in *every production-code task*: add one focused behavioral test first, run the exact selector and observe the stated feature-missing RED, add only the minimum code, rerun the selector, then run the named regression command. If a task lists several assertions, treat each assertion as its own 2–5 minute RED→GREEN micro-cycle and do not write the next test until the current one is green; the task heading is only the narrow commit/review boundary. If the first run passes or fails for an import/typo unrelated to the intended missing behavior, repair the test and repeat RED before adding production code.
- The controller has no network, provider, tool, subprocess, or mutation capability. Any example that acts must do so through a host-owned synthetic tool switch. No framework-specific adapters in v1.
- Public artifacts contain only synthetic/redacted data. Raw model transcripts, caches, private model/provider details, local RDD evidence, and `.hermes/` data remain outside the repository and are never staged.
- Do not run a paid live campaign from CI or before the explicit execution gate in Task 23 passes. A missing cost cap or static pricing schedule blocks paid execution.
- After each implementation task, use a fresh implementer, then a read-only spec-compliance reviewer, then a separate read-only quality/security reviewer. Fix every critical or important finding with a new focused RED→GREEN task before continuing. The final integration and publication reviews remain independent.

**Standard per-task review evidence:** attach the selector RED output, selector GREEN output, regression command output, changed-file list, and commit ID to the task handoff. Reviewers verify that the test failed before its implementation and that no non-task files changed.

---

## Execution topology and ownership

**Sequential foundation (Tasks 1–7):** establish the package, closed contracts, deterministic models, and validation before any detector, fixture, prompt, or example depends on them.

**Dependency-safe staged waves after Task 7:**

| Wave/lane | Tasks | Sole writable ownership | Inputs that must already be stable |
|---|---:|---|---|
| Wave 1 — controller | 8–12 | `src/semantic_reheating/{diagnosis,controller,policies,evidence,cli}.py`, `src/semantic_reheating/detectors/`, lane-owned unit/CLI tests | Tasks 1–7 |
| Wave 2A — corpus/benchmark | 13–15 | `benchmark/corpus/`, `benchmark/scenarios/`, `benchmark/{replay,metrics}.py`, `tests/benchmark/`, `tests/property/`, `tests/golden/`, plus an explicit post-Task-12 handoff for `src/semantic_reheating/cli.py` in Task 14 | Task 12 public decision/CLI format |
| Wave 2B — prompts | 16 | `prompts/`, `tests/prompts/` | Tasks 1–7 and public recovery schemas from Task 3 |
| Wave 3A — skill | 17 | `skills/semantic-reheating/`, `tools/pressure_skill_runner.py`, `tests/skill/` | Tasks 12 and 16 |
| Wave 3B — examples | 18–19 | each example directory and its lane-owned integration test | Tasks 12–15 |

Run tasks in the same wave concurrently only in separate lane worktrees and only while their writable paths remain disjoint. No two agents may share a worktree, Git index, file, lockfile, fixture, or workflow. From the canonical feature worktree, use these exact wave boundaries:

```bash
# Wave 1 after Task 7
git worktree add ../semantic-reheating-controller -b lane/controller HEAD
# Run Tasks 8–12 in the controller worktree.
git merge --no-ff lane/controller -m "merge: integrate controller lane"

# Wave 2 after the controller merge
git worktree add ../semantic-reheating-corpus -b lane/corpus HEAD
git worktree add ../semantic-reheating-prompts -b lane/prompts HEAD
# Run Tasks 13–15 in the corpus worktree and Task 16 in the prompts worktree.
git merge --no-ff lane/corpus -m "merge: integrate corpus lane"
git merge --no-ff lane/prompts -m "merge: integrate prompt lane"

# Wave 3 after both Wave 2 merges
git worktree add ../semantic-reheating-skill -b lane/skill HEAD
git worktree add ../semantic-reheating-python-example -b lane/python-example HEAD
git worktree add ../semantic-reheating-typescript-example -b lane/typescript-example HEAD
# Run Task 17 in the skill worktree, Task 18 in the Python worktree,
# and Task 19 in the TypeScript worktree.
git merge --no-ff lane/skill -m "merge: integrate skill lane"
git merge --no-ff lane/python-example -m "merge: integrate Python example lane"
git merge --no-ff lane/typescript-example -m "merge: integrate TypeScript example lane"
```

Before each merge, the canonical feature worktree and the lane worktree must be clean and the lane's spec/quality reviews must pass. After each merge, rerun that lane's verification command from the canonical worktree; if a conflict occurs despite declared ownership, stop and use an independent merge reconciler rather than editing both lanes opportunistically. Task 20 is the canonical documentation/API fan-in after Waves 1–3 are green. Tasks 21–28 then run sequentially because they consume canonical committed artifacts and publication state.

**Shared-file ownership:** Task 1 owns root `pyproject.toml`, `uv.lock`, and root ignore/configuration files. Later tasks must not casually reformat them. Task 20 is the only post-foundation owner of `README.md`, root package verification commands, and shared fixture index. Task 25 is the only owner of `.github/workflows/ci.yml`. Task 28 is the only task allowed to create the remote repository or set `origin`.

---

### Task 1: Bootstrap an offline-installable Python package and test runner

**Objective:** Create the minimal distributable package and test environment that proves the console entry point can load without a network after dependencies are installed.

**Files:**
- Create: `pyproject.toml`, `src/semantic_reheating/__init__.py`, `src/semantic_reheating/cli.py`, `tests/test_package_smoke.py`, `.gitignore`
- Create: `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CITATION.cff`
- Create: `tests/conftest.py`

**Step 0 — isolate public implementation history:** From the canonical repository root, verify the worktree is clean and create the sibling feature worktree before writing tests:
```bash
git status --short --branch
test -z "$(git status --porcelain)"
git worktree add ../semantic-reheating-implementation -b feat/semantic-reheating-reference-kit main
cd ../semantic-reheating-implementation
git status --short --branch
```
Expected: the canonical `main` worktree remains clean and the new worktree is on `feat/semantic-reheating-reference-kit` at the committed plan revision.

**Step 1 — RED:** Before creating `pyproject.toml` or package code, write `tests/test_package_smoke.py::test_console_help_is_available_offline`. Resolve only the project-local executable (`.venv/Scripts/reheat.exe` on Windows, `.venv/bin/reheat` otherwise), require it to exist, then run `[resolved_reheat, "--help"]` and assert exit zero plus `usage: reheat`. Execute the test in a temporary no-project pytest environment:
```bash
uv run --isolated --no-project --with 'pytest>=9.1,<10' pytest tests/test_package_smoke.py::test_console_help_is_available_offline -q
```

**Expected RED reason:** the assertion fails because no installed `reheat` console executable exists; the test harness itself starts successfully and the failure is not missing-project setup or a Python import typo.

**Step 2 — GREEN:** Add a PEP 621 `pyproject.toml` with `requires-python = ">=3.11"`, runtime dependencies `jsonschema>=4.26,<5` and `rfc8785>=0.1.4,<0.2`, dev dependencies `pytest>=9.1,<10`, `hypothesis>=6.165,<7`, `pytest-cov>=7.1,<8`, `Pillow>=12.3,<13`, `ruff>=0.16,<0.17`, `mypy>=2.3,<3`, and `skills-ref>=0.1.1,<0.2`, package discovery under `src`, and `[project.scripts] reheat = "semantic_reheating.cli:main"`. Register `pressure_live` and configure default pytest runs to exclude it unless explicitly selected. Implement `main(argv: Sequence[str] | None = None) -> int` with only an argparse parser and `--help`; export `__version__ = "0.1.0"`. Add root public documents with bounded, non-temperature-changing language in the README and a `.gitignore` that excludes `.hermes/`, `.env*`, `*.transcript.jsonl`, `benchmark/live/private/`, caches, and local virtual environments.

**Step 3 — verify:**
```bash
uv lock
uv sync --all-groups
uv run pytest tests/test_package_smoke.py::test_console_help_is_available_offline -q
uv run pytest tests -q
UV_OFFLINE=1 uv sync --frozen --offline --all-groups
UV_OFFLINE=1 uv run reheat --help
```
Expected: focused and full tests pass; the final command prints argparse help without a network request.

**Step 4 — commit:**
```bash
git add pyproject.toml uv.lock src tests README.md LICENSE SECURITY.md CONTRIBUTING.md CITATION.cff .gitignore
git commit -m "build: bootstrap offline semantic reheating package"
```

### Task 2: Define closed contract meta-rules and the TraceEvent schema

**Objective:** Make `TraceEvent` a versioned, closed Draft 2020-12 contract rather than an informal fixture shape.

**Files:**
- Create: `contracts/v1/trace-event.schema.json`, `tests/contracts/test_trace_event_schema.py`
- Create: `tests/fixtures/contracts/minimal-trace-event.json`, `tests/fixtures/contracts/unknown-trace-field.json`

**Step 1 — RED:** Test that the minimal event validates, `additionalProperties: false` rejects `unexpected_private_field`, `kind`/`effect_class` are closed enums, exactly one of `payload`, `payload_ref`, and `payload_digest` is required, and the test fixture carries `contract_version: "1.0"`. Run:
```bash
uv run pytest tests/contracts/test_trace_event_schema.py -q
```

**Expected RED reason:** schema files cannot be loaded; no contract definition exists.

**Step 2 — GREEN:** Add the Draft 2020-12 trace schema with `$schema`, stable `$id`, `type: object`, `additionalProperties: false`, explicit required fields, a `oneOf` payload representation, `sequence` minimum 1, and all fields named in design §6.1. Keep descriptions redacted and never model credentials or raw hidden reasoning. Do not publish a partial `run-policy` v1 schema in this task.

**Step 3 — verify:**
```bash
uv run pytest tests/contracts/test_trace_event_schema.py -q
uv run python -m json.tool contracts/v1/trace-event.schema.json >/dev/null
```
Expected: all schema assertions pass and JSON parses.

**Step 4 — commit:**
```bash
git add contracts/v1 tests/contracts/test_trace_event_schema.py tests/fixtures/contracts
git commit -m "feat: add closed trace event contract"
```

### Task 3: Add the remaining closed versioned schemas and public artifact validator

**Objective:** Give every core controller JSON artifact an explicit v1 contract and a single validation seam; later tasks apply the same rule to benchmark, skill, live, and article domain JSON.

**Files:**
- Create: `contracts/v1/run-policy.schema.json`, `contracts/v1/detector-finding.schema.json`, `contracts/v1/decision-envelope.schema.json`, `contracts/v1/recovery-instruction.schema.json`, `contracts/v1/recovery-outcome.schema.json`, `contracts/v1/evidence-record.schema.json`
- Create: `src/semantic_reheating/validation.py`, `tests/contracts/test_public_contract_validation.py`
- Create: `tests/fixtures/contracts/minimal-run-policy.json`, `tests/fixtures/contracts/minimal-detector-finding.json`, `tests/fixtures/contracts/minimal-decision-envelope.json`, `tests/fixtures/contracts/minimal-recovery-instruction.json`, `tests/fixtures/contracts/minimal-recovery-outcome.json`, `tests/fixtures/contracts/minimal-evidence-record.json`

**Step 1 — RED:** Work one schema at a time and complete a separate 2–5 minute RED→GREEN micro-cycle before starting the next. For each core schema, run `Draft202012Validator.check_schema`, prove a minimal valid fixture passes, then add focused negative tests for every required field group, every closed enum, unknown top-level fields, unknown nested fields, wrong scalar/collection types, and `contract_version: "2.0"`; assert stable `ContractValidationError` codes including `unknown_contract_major`. For `DecisionEnvelope`, explicitly prove `escalate` validates and an unknown decision fails. For nested `RunPolicy`, `RecoveryInstruction`, and `EvidenceRecord` records, mutate one nested object at a time so an earlier failure cannot mask the intended closure gate. Task 2 remains the only test of the explicitly opaque `TraceEvent.payload` boundary. Run the exact current micro-cycle selector while developing, then the complete file:
```bash
uv run pytest tests/contracts/test_public_contract_validation.py -q
```

**Expected RED reason:** `semantic_reheating.validation` and the six schema files are absent.

**Step 2 — GREEN:** Implement only a package-relative schema registry and Draft202012Validator call. Publish the complete v1 `RunPolicy` shape in this task—all detector windows/thresholds, signal-class gate, recovery permissions, five resource dimensions (turns, tool calls, tokens, elapsed seconds, cost) for both intervention and whole run, episode/re-entry bounds, side-effect rules, cooling, and optional semantic configuration—rather than an intentionally incomplete v1. Define all required fields from design §§6.3–6.6: normalized finding score and redacted explanation; the exact decision vocabulary `continue | nudge | diagnose | reheat | restart | escalate | stop`; reason/evidence/budget/constraints/cooling/confidence/host-action fields; advisory recovery instruction; outcome; and evidence record. Close every domain object and nested object with `additionalProperties: false`; the only opaque value surface is the separately governed `TraceEvent.payload` from Task 2. Reject unknown major versions before validation and do not silently coerce fields.

**Step 3 — verify:**
```bash
uv run pytest tests/contracts/test_public_contract_validation.py -q
uv run pytest tests/contracts -q
```
Expected: each fixture validates and the unknown-major case fails closed with the typed code.

**Step 4 — commit:**
```bash
git add contracts/v1 src/semantic_reheating/validation.py tests/contracts tests/fixtures/contracts
git commit -m "feat: validate closed v1 recovery contracts"
```

### Task 4: Build immutable models and strict trace/policy parsing

**Objective:** Convert validated JSON to typed, immutable public models and reject sequence/version violations before analysis.

**Files:**
- Create: `src/semantic_reheating/models.py`, `tests/unit/test_models.py`
- Modify: `src/semantic_reheating/__init__.py`

**Step 1 — RED:** Test `TraceEvent.from_dict`, `RunPolicy.from_dict`, `DecisionEnvelope.to_dict`, and a parser rejecting sequence `[1, 3]` with `sequence_gap`. Include all policy groups: windows/thresholds, agreeing-signal count, ladder permissions, full per-intervention and whole-run limits, episode/re-entry limits, side-effect rules, cooling, and optional semantic configuration. Prove an `escalate` envelope round-trips exactly. For each core artifact model introduced here, reuse the Task 3 adversarial fixtures and require schema and runtime parsing to agree on acceptance/rejection with typed domain errors rather than native exceptions. Run:
```bash
uv run pytest tests/unit/test_models.py -q
```

**Expected RED reason:** model classes and typed parse errors do not exist.

**Step 2 — GREEN:** Implement frozen dataclasses and narrow enums for trace kind, effect class, finding class, and the exact decision set `continue | nudge | diagnose | reheat | restart | escalate | stop`. Preserve input values exactly after schema validation; enforce monotonically contiguous sequence per trace and `1.0` major compatibility. Represent all counters (turns, tool calls, tokens, elapsed seconds, cost) explicitly, including nested retries/handoffs/subagents/callbacks/re-entry as counters in the same budget envelope. Export only `analyze`, `build_recovery_instruction`, and `record_outcome` later, not internal helpers.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_models.py -q
uv run pytest tests/contracts tests/unit/test_models.py -q
```
Expected: parse/serialization behavior passes and prior schema tests remain green.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/models.py src/semantic_reheating/__init__.py tests/unit/test_models.py
git commit -m "feat: add typed trace and policy models"
```

### Task 5: Implement RFC 8785 action canonicalization and redacted fingerprints

**Objective:** Create deterministic SHA-256 action fingerprints that ignore explicitly volatile or secret-bearing fields without preserving their values.

**Files:**
- Create: `src/semantic_reheating/canonical.py`, `tests/unit/test_canonical.py`

**Step 1 — RED:** Test that semantically identical I-JSON with different input key order produces the same hexadecimal digest; configured fields `request_id` and `authorization` do not affect an action-equivalence fingerprint; a changed nonvolatile tool argument does; raw secrets never appear in a debug-safe returned fingerprint record. Add adversarial tests that reject duplicate keys at JSON ingestion using `object_pairs_hook`, reject `NaN`, `Infinity`, and `-Infinity`, reject integers outside RFC 8785/I-JSON IEEE-754-safe numeric domain, and preserve Unicode strings exactly without NFC/NFD normalization. Run:
```bash
uv run pytest tests/unit/test_canonical.py -q
```

**Expected RED reason:** canonicalization and fingerprint APIs are unavailable.

**Step 2 — GREEN:** Parse JSON with duplicate-key rejection, validate I-JSON-compatible finite numbers before calling `rfc8785`, and use the `rfc8785` package to canonicalize a copy of JSON. Remove only configured JSON-pointer-equivalent top-level/nested fields before canonicalization and hash bytes using `hashlib.sha256`. Exclude event ID, timestamp, volatile request IDs, and configured secret-bearing fields only from action equivalence, not from the original trace record. Return digest plus excluded field names, never excluded values. Preserve Unicode code points exactly; do not normalize strings. Do not add fuzzy semantic matching.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_canonical.py -q
uv run pytest tests/unit -q
```
Expected: canonicalization is byte stable, rejects non-I-JSON ambiguity, preserves Unicode code points, and changes only when a material action input changes.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/canonical.py tests/unit/test_canonical.py
git commit -m "feat: add RFC 8785 action fingerprints"
```

### Task 6: Implement explicit progress classification and false-positive controls

**Objective:** Decide whether a trace window contains verified progress without an LLM call.

**Files:**
- Create: `src/semantic_reheating/progress.py`, `tests/unit/test_progress.py`

**Step 1 — RED:** Parameterize tests showing progress for changing pagination cursor, changed hypothesis input, new stack frame/error fingerprint, acceptance delta, new evidence reference, productive handoff, and converging polling state. Add a negative test for unchanged state with no evidence. Run:
```bash
uv run pytest tests/unit/test_progress.py -q
```

**Expected RED reason:** `classify_progress` does not exist.

**Step 2 — GREEN:** Implement a deterministic `ProgressAssessment` that returns `made_progress`, stable reason codes, and supporting event IDs. Count only trace-supported facts listed in design §7.3; do not infer progress from changed prose alone. Ensure an expected state change that never appears is no progress, while deliberate acceptance verification is progress only if contract evidence says so.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_progress.py -q
uv run pytest tests/unit -q
```
Expected: all productive controls are classified as progress and the unchanged case is not.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/progress.py tests/unit/test_progress.py
git commit -m "feat: classify trace-supported progress"
```

### Task 7: Complete the detailed RunPolicy contract and policy safety validation

**Objective:** Refuse policies that could bypass full-budget or host-authority safety rules.

**Files:**
- Modify: `src/semantic_reheating/models.py`, `src/semantic_reheating/validation.py`
- Create: `tests/contracts/test_run_policy_safety.py`

**Step 1 — RED:** Test a complete default policy validates, while a policy with no whole-run limits, automatic non-idempotent repeat enabled, or recovery budget counters omitted raises stable `unsafe_policy` validation errors. Generate every allowed combination of finding classes and prove no accepted policy can authorize `reheat` unless at least one repetition-class and one independent no-progress-class finding agree; repetition-only, budget-only, no-progress-only, budget-plus-no-progress, and any two repetition/budget findings never satisfy the gate, while repetition-plus-no-progress does. For every locally expressible structural invariant, require both the Draft 2020-12 schema and runtime validator to reject the same adversarial policy; reserve only cross-field arithmetic/semantic rules for runtime-only rejection. Run:
```bash
uv run pytest tests/contracts/test_run_policy_safety.py -q
```

**Expected RED reason:** detailed policy invariants are not enforced.

**Step 2 — GREEN:** Add semantic validation after the already complete schema validation. Default to deterministic detectors, one repetition-class plus one no-progress-class signal, read-only exploration, one recovery episode before restart, no automatic unconfirmed non-idempotent repetition, all five resource dimensions, and explicit episode/re-entry bounds. Require configured caps whenever a live campaign policy can use paid stacks. The policy may constrain the host but cannot grant authority.

**Step 3 — verify:**
```bash
uv run pytest tests/contracts/test_run_policy_safety.py -q
uv run pytest tests/contracts tests/unit -q
```
Expected: safe policy passes; each unsafe construction is rejected before analysis.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/models.py src/semantic_reheating/validation.py tests/contracts/test_run_policy_safety.py
git commit -m "feat: enforce full-budget policy safety"
```

### Task 8: Add exact-repetition and repeated-error detector slices

**Objective:** Produce explainable repetition findings with exact event evidence.

**Files:**
- Create: `src/semantic_reheating/detectors/__init__.py`, `src/semantic_reheating/detectors/exact_repetition.py`, `src/semantic_reheating/detectors/repeated_error.py`
- Create: `tests/unit/test_exact_repetition.py`, `tests/unit/test_repeated_error.py`

**Step 1 — RED:** Test equivalent tool/action fingerprints and equivalent results produce an `exact_repetition` finding with the repeated event IDs and normalized score; test a normalized error repeated without changed input/hypothesis produces `repeated_error`; test a changed argument does not match. Run:
```bash
uv run pytest tests/unit/test_exact_repetition.py tests/unit/test_repeated_error.py -q
```

**Expected RED reason:** detector modules and finding construction do not exist.

**Step 2 — GREEN:** Implement pure detector functions that consume typed trace and policy windows and return `DetectorFinding`. Normalize only declared error identity fields, use canonical action fingerprints, include detector name/version/class/score/event IDs/reason code/redacted explanation, and make no decisions. Never expose payloads in explanations.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_exact_repetition.py tests/unit/test_repeated_error.py -q
uv run pytest tests/unit -q
```
Expected: recurrence is detected only for equivalent action/result or equivalent unmodified error cases.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/detectors tests/unit/test_exact_repetition.py tests/unit/test_repeated_error.py
git commit -m "feat: detect exact repetitions and repeated errors"
```

### Task 9: Add cycle, unchanged-state, acceptance-stall, and budget-burn detector slices

**Objective:** Cover the remaining deterministic stagnation signals with no LLM dependency.

**Files:**
- Create: `src/semantic_reheating/detectors/cycle.py`, `src/semantic_reheating/detectors/unchanged_state.py`, `src/semantic_reheating/detectors/acceptance_stall.py`, `src/semantic_reheating/detectors/budget_burn.py`
- Create: `tests/unit/test_cycle.py`, `tests/unit/test_unchanged_state.py`, `tests/unit/test_acceptance_stall.py`, `tests/unit/test_budget_burn.py`

**Step 1 — RED:** Add focused tests for a two-step and a five-step no-net-state cycle, an expected mutation with stable state fingerprints, unchanged acceptance checks, and rising turns/tool/tokens/time/cost with no evidence. Test a polling trace whose state changes is not an unchanged-state match. Run:
```bash
uv run pytest tests/unit/test_cycle.py tests/unit/test_unchanged_state.py tests/unit/test_acceptance_stall.py tests/unit/test_budget_burn.py -q
```

**Expected RED reason:** the four detector imports are absent.

**Step 2 — GREEN:** Implement bounded-window pure functions for length 2–5 cycles, expected-state-change checks, acceptance delta checks, and no-evidence budget burn. Emit only normalized scores and exact support IDs; budget burn may be a `budget` finding but does not itself mutate counters or stop a host.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_cycle.py tests/unit/test_unchanged_state.py tests/unit/test_acceptance_stall.py tests/unit/test_budget_burn.py -q
uv run pytest tests/unit -q
```
Expected: pathological windows match; the productive polling control remains unflagged.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/detectors tests/unit/test_cycle.py tests/unit/test_unchanged_state.py tests/unit/test_acceptance_stall.py tests/unit/test_budget_burn.py
git commit -m "feat: add deterministic stagnation detectors"
```

### Task 10: Build diagnosis and constrained recovery-policy selection

**Objective:** Choose the least expensive permitted action and construct a reheating instruction that requires exactly three hypotheses without granting authority.

**Files:**
- Create: `src/semantic_reheating/diagnosis.py`, `src/semantic_reheating/policies.py`, `tests/unit/test_diagnosis.py`, `tests/unit/test_policies.py`

**Step 1 — RED:** Test a missing credential cause maps to `escalate`/`stop` and never retry; every diagnosed uncertainty receives exactly one disposition from `verify | assume | escalate | block`, and a high-risk or authority-related unknown cannot be `assume`; a repetition-plus-no-progress case selects an allowed lowest ladder action; a reheat instruction requires the host/model response to contain exactly three mutually exclusive falsifiable hypotheses and one read-only discriminating test each; a viable evidence-backed branch triggers cooling. Run:
```bash
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_policies.py -q
```

**Expected RED reason:** cause classification and policy selector APIs are missing.

**Step 2 — GREEN:** Map only the eight cause classes in design §8.1 from findings/events. Build a typed uncertainty map in which every entry has one closed disposition (`verify`, `assume`, `escalate`, or `block`), with policy validation forbidding `assume` for authority, credential, side-effect, or other high-risk unknowns. Keep the `DecisionEnvelope.decision` vocabulary exactly `continue | nudge | diagnose | reheat | restart | escalate | stop`: use `escalate` when explicit host action is required and continued automatic execution is not permitted; encode `research`, `branch`, and `model_switch` only as closed `recovery_policy` values under an allowed decision, never as new decisions. Build a deterministic `RecoveryInstruction` that carries diagnosed gaps and a closed response contract requiring three hypotheses; do not invent the hypotheses inside the controller. Require each proposed discriminating test to be read-only/sandboxed; preserve evidence refs, rejected hypotheses, constraints, complete budget, cooling, and stop conditions. Never construct executable credentials, tool calls, or retry commands.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_policies.py -q
uv run pytest tests/unit -q
```
Expected: diagnosis and recovery selection are deterministic, bounded, and host-advisory.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/diagnosis.py src/semantic_reheating/policies.py tests/unit/test_diagnosis.py tests/unit/test_policies.py
git commit -m "feat: select bounded host-advisory recovery policies"
```

### Task 11: Implement controller aggregation, hard stops, cooling, and evidence records

**Objective:** Turn findings into reproducible envelopes while ensuring safety and budget limits dominate recommendations.

**Files:**
- Create: `src/semantic_reheating/controller.py`, `src/semantic_reheating/evidence.py`, `tests/unit/test_controller.py`, `tests/unit/test_evidence.py`
- Modify: `src/semantic_reheating/__init__.py`

**Step 1 — RED:** Test all of these in separate cases: repetition alone yields `continue` with advisory finding; budget-plus-no-progress without repetition cannot yield `reheat`; repetition plus no-progress yields a decision with exact support IDs/reason codes and confidence derived from those two qualifying classes; hard budget yields `stop` at confidence `1.0` despite optional detector/recovery recommendation; repeated unconfirmed `non_idempotent_write` and repeated `effect_class: unknown` each yield `stop`/risk and `build_recovery_instruction` returns `None`; same trace+policy serializes byte-identically twice; `record_outcome` preserves counters/evidence without raw payloads. Run:
```bash
uv run pytest tests/unit/test_controller.py tests/unit/test_evidence.py -q
```

**Expected RED reason:** the public controller API and outcome recording are absent.

**Step 2 — GREEN:** Implement `analyze`, `build_recovery_instruction`, and `record_outcome` precisely as the approved public API. Aggregate each finding-class score as max(configured weight × score), clip to `[0,1]`, and make `reheat` confidence the min of repetition and no-progress class scores; record every contributing score/weight. Record `budget` findings separately for diagnosis, but never substitute them for the repetition-class side of the reheat gate; hard-limit `stop` still dominates at confidence `1.0`. Invoke optional semantic detector only by dependency injection, record unavailable degraded mode, and never let it relax hard limits. Treat unknown effect class as non-repeatable. Detect a new independent episode before renewed exploration; otherwise cool to the sole viable branch or restart once then stop on no evidence gain.

**Step 3 — verify:**
```bash
uv run pytest tests/unit/test_controller.py tests/unit/test_evidence.py -q
uv run pytest tests/unit -q
```
Expected: safety gates dominate and normalized output is reproducible.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/controller.py src/semantic_reheating/evidence.py src/semantic_reheating/__init__.py tests/unit/test_controller.py tests/unit/test_evidence.py
git commit -m "feat: add deterministic recovery controller"
```

### Task 12: Expand the argparse CLI with typed errors and stable stdout/stderr separation

**Objective:** Expose contract validation, analysis, explanation, and benchmark entry points without adding runtime authority.

**Files:**
- Modify: `src/semantic_reheating/cli.py`
- Create: `tests/cli/test_validate.py`, `tests/cli/test_analyze.py`, `tests/cli/test_explain.py`

**Step 1 — RED:** Test `reheat validate TRACE.jsonl --policy POLICY.json`, JSON and text `analyze`, and `explain DECISION.json`. Include a missing-authority fixture whose normalized output is exactly an `escalate` envelope with `requires_host_action: true`; prove the JSON and text paths preserve that decision. Assert machine JSON is stdout-only, diagnostics are stderr-only, and invalid schema, sequence gap, incompatible version, unsafe policy, and unavailable required detector have distinct nonzero exit codes. Run:
```bash
uv run pytest tests/cli/test_validate.py tests/cli/test_analyze.py tests/cli/test_explain.py -q
```

**Expected RED reason:** subcommands and typed exit mapping are not implemented.

**Step 2 — GREEN:** Use only `argparse`; load JSONL/JSON via public validators/models; serialize normalized decision JSON with canonical ordering and no human-only noise. Add `benchmark` argument parsing that delegates to the benchmark package added later, returning a clear unavailable command error until that module is installed. Define named exit constants and never print trace payload data to error output.

**Step 3 — verify:**
```bash
uv run pytest tests/cli/test_validate.py tests/cli/test_analyze.py tests/cli/test_explain.py -q
uv run pytest tests -q
uv run reheat --help
```
Expected: command behaviors and exit codes pass; help lists validate, analyze, explain, and benchmark.

**Step 4 — commit:**
```bash
git add src/semantic_reheating/cli.py tests/cli
git commit -m "feat: add safe reheat command interface"
```

### Task 13: Create a balanced synthetic trace corpus before live evaluation

**Objective:** Publish at least 24 synthetic, redacted traces with explicit expected outcomes and productive controls.

**Files:**
- Create: `benchmark/corpus/`, `benchmark/scenarios/manifest.json`, `benchmark/schemas/v1/corpus-manifest.schema.json`, `tests/benchmark/test_manifest.py`
- Create: `tests/benchmark/test_corpus_privacy.py`

**Step 1 — RED:** Test the manifest validates against a closed Draft 2020-12 versioned schema, rejects unknown fields/unknown major versions, references exactly 24 or more unique JSONL traces, has at least 12 pathological and at least 12 productive-control labels, and declares expected detector names, decision, evidence-event IDs, and safety outcome for every entry. Test fixtures contain no secret-like values, absolute paths, `.hermes/`, client/employer/contact data, or raw reasoning field. Run:
```bash
uv run pytest tests/benchmark/test_manifest.py tests/benchmark/test_corpus_privacy.py -q
```

**Expected RED reason:** corpus/manifest files do not exist.

**Step 2 — GREEN:** Add the closed versioned corpus-manifest schema and 24 named, synthetic traces: pathological exact repetition, two/three/four/five-step cycles, unchanged state, repeated error, each budget burn dimension, blocked authority with expected `escalate`, context restart, and unsafe write repetition; productive controls for pagination, batching, state-changing polls, changed hypotheses, verification reruns, handoffs, eventual consistency, and additional non-stagnant variations. Each event has stable synthetic IDs and redacted payloads/digests; each manifest record declares its schema version and all expectations. Do not run or record any live agent session.

**Step 3 — verify:**
```bash
uv run pytest tests/benchmark/test_manifest.py tests/benchmark/test_corpus_privacy.py -q
uv run python -m json.tool benchmark/scenarios/manifest.json >/dev/null
```
Expected: corpus is balanced, complete, and public-hygiene safe.

**Step 4 — commit:**
```bash
git add benchmark/corpus benchmark/scenarios/manifest.json benchmark/schemas/v1/corpus-manifest.schema.json tests/benchmark/test_manifest.py tests/benchmark/test_corpus_privacy.py
git commit -m "test: add balanced synthetic reheating corpus"
```

### Task 14: Implement deterministic replay and recomputable benchmark metrics

**Objective:** Measure the corpus with exact, reproducible detector/decision metrics and byte-equality replay.

**Files:**
- Create: `benchmark/__init__.py`, `benchmark/replay.py`, `benchmark/metrics.py`, `benchmark/schemas/v1/replay-result.schema.json`, `benchmark/results/deterministic-results.json`, `tests/benchmark/test_replay.py`, `tests/benchmark/test_metrics.py`
- Modify: `src/semantic_reheating/cli.py`

**Step 1 — RED:** Test replaying all manifest entries twice gives byte-identical canonical decision records; test the result validates against a closed versioned Draft 2020-12 schema and unknown fields/major versions fail; test metric output includes detector precision, recall, decision accuracy, false-intervention rate, and deterministic replay status; test a mismatched expected evidence ID is reported rather than hidden; and test regenerated canonical output byte-matches committed `benchmark/results/deterministic-results.json`. Run:
```bash
uv run pytest tests/benchmark/test_replay.py tests/benchmark/test_metrics.py -q
```

**Expected RED reason:** replay and metrics modules are absent.

**Step 2 — GREEN:** Implement the closed versioned replay-result schema, offline manifest loader, replay runner using `analyze`, canonical JSON writer, confusion-count metrics, and a result artifact containing schema version, corpus revision, command/version metadata, aggregate metrics, per-trace expected/actual comparisons, and no private payload. Wire `reheat benchmark benchmark/corpus --manifest benchmark/scenarios/manifest.json` to this code and generate the committed deterministic result from that exact command. Never add probabilistic scoring or external calls.

**Step 3 — verify:**
```bash
uv run pytest tests/benchmark/test_replay.py tests/benchmark/test_metrics.py -q
uv run reheat benchmark benchmark/corpus --manifest benchmark/scenarios/manifest.json --format json > /tmp/semantic-reheating-benchmark.json
cmp /tmp/semantic-reheating-benchmark.json benchmark/results/deterministic-results.json
uv run pytest tests/benchmark -q
```
Expected: all tests pass, the CLI emits deterministic JSON, and the temporary output byte-matches the committed result artifact.

**Step 4 — commit:**
```bash
git add benchmark src/semantic_reheating/cli.py tests/benchmark
git commit -m "feat: replay corpus and compute recovery metrics"
```

### Task 15: Add property and golden tests for deterministic contracts and boundary cases

**Objective:** Prove more than individual examples: model/contract/controller behavior is stable across generated valid inputs and the curated golden corpus.

**Files:**
- Create: `tests/property/test_contract_properties.py`, `tests/property/test_controller_properties.py`, `tests/golden/test_replay_golden.py`

**Step 1 — RED:** Add Hypothesis tests asserting canonical fingerprint key-order invariance, closed-version rejection, bounded confidence, hard-budget-stop dominance, and no `reheat` absent both required signal classes. Generate malformed policies and other core artifacts one invariant at a time and assert every locally expressible violation is rejected by both its Draft 2020-12 schema and the runtime validator with no schema/runtime acceptance disagreement. Add golden assertions for every manifest expected decision/evidence set. Run:
```bash
uv run pytest tests/property/test_contract_properties.py tests/property/test_controller_properties.py tests/golden/test_replay_golden.py -q
```

**Expected RED reason:** production code lacks the invariants or generated strategies expose an unhandled case.

**Step 2 — GREEN:** Make the smallest corrections in existing canonicalization, validation, or controller code required by the first legitimate failing property. Do not weaken a property, add random behavior, or change synthetic expected outcomes merely to mask a controller defect. Add no new feature surface.

**Step 3 — verify:**
```bash
uv run pytest tests/property tests/golden -q
uv run pytest tests -q
```
Expected: generated boundaries and all goldens pass deterministically.

**Step 4 — commit:**
```bash
git add tests/property tests/golden src/semantic_reheating
git commit -m "test: prove deterministic controller invariants"
```

### Task 16: Add prompt assets with closed output contracts and link checks

**Objective:** Publish the five bounded recovery prompts as data assets with explicit triggers, non-triggers, budgets, restrictions, evidence, cooling, and stop conditions.

**Files:**
- Create: `prompts/detection-notice.md`, `prompts/uncertainty-map.md`, `prompts/bounded-reheating.md`, `prompts/select-and-cool.md`, `prompts/verify-or-stop.md`
- Create: `tests/prompts/test_prompt_assets.py`, `tests/prompts/test_prompt_links.py`

**Step 1 — RED:** Test every prompt has runtime and operator forms, explicit trigger/non-trigger text, named Markdown sections for budget/tool restrictions/evidence/stop/cooling, and either a referenced JSON schema or exact named output sections. Test every relative link resolves. Run:
```bash
uv run pytest tests/prompts/test_prompt_assets.py tests/prompts/test_prompt_links.py -q
```

**Expected RED reason:** prompt files and validation rules are absent.

**Step 2 — GREEN:** Write the five assets. `bounded-reheating.md` requires exactly three mutually exclusive falsifiable hypotheses and one discriminating read-only test each; it forbids unsupported invention and writes. All prompts state that host authority is unchanged, carry complete recovery budgets, and stop at hard limits. Use package-relative paths only; no private content or model claims.

**Step 3 — verify:**
```bash
uv run pytest tests/prompts/test_prompt_assets.py tests/prompts/test_prompt_links.py -q
uv run pytest tests/prompts -q
```
Expected: each asset passes structure and link validation.

**Step 4 — commit:**
```bash
git add prompts tests/prompts
git commit -m "docs: add bounded recovery prompt pack"
```

### Task 17: Establish Agent Skill RED baselines locally, then publish sanitized pressure evidence and the minimal Skill

**Objective:** Demonstrate that the Skill changes behavior under pressure without ever publishing raw model transcripts.

**Files:**
- Create: `tests/skill/test_pressure_protocol.py`, `tests/skill/test_skill_package.py`, `tests/skill/test_sanitized_rubric.py`
- Create: `tools/pressure_skill_runner.py`
- Create: `skills/semantic-reheating/references/pressure-scenarios.json`, `skills/semantic-reheating/references/pressure-scenarios.schema.json`, `skills/semantic-reheating/references/rubric.json`, `skills/semantic-reheating/references/rubric.schema.json`, `skills/semantic-reheating/references/stack-receipt.json`, `skills/semantic-reheating/references/stack-receipt.schema.json`, `skills/semantic-reheating/references/baseline-summary.json`, `skills/semantic-reheating/references/baseline-summary.schema.json`, `skills/semantic-reheating/references/results.json`, `skills/semantic-reheating/references/results.schema.json`, `skills/semantic-reheating/SKILL.md`
- Create local-only, never staged: `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/pressure-baselines/pressure-stack.local.json`, `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/pressure-baselines/`

**Step 1 — RED protocol, before writing `SKILL.md`:** Write a test for `tools/pressure_skill_runner.py` that requires `pressure-stack.local.json` to pin the selected locally available CLI command, CLI/framework/model/provider/version metadata, decoding/seed settings or explicit unsupported markers, and caps for turns/tools/tokens/time/cost. Require the six fixed scenarios—exact retry loop, plan oscillation, productive pagination, blocked authority, unsafe write, and exhausted budget—to execute *without the skill*, record outcome codes, scenario/rubric hashes, stack-config hash, command hash, and transcript digests, and place transcripts only under the local state directory. Run before implementing the runner:
```bash
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/pressure-baselines"
uv run pytest -m pressure_live tests/skill/test_pressure_protocol.py::test_baseline_runner_records_six_skill_absent_outcomes -q
```

**Expected RED reason:** the pressure protocol/runner does not exist and there is no recorded baseline; this is intentionally a behavior baseline, not a failed test caused by a prewritten Skill.

**Step 2 — GREEN for the protocol only:** Add the smallest runner at `tools/pressure_skill_runner.py`; it consumes the pinned private stack config and records scenario ID, exact public-safe stack metadata, no-skill outcome code, all binding hashes, budget consumption, and transcript path outside Git. It also produces a deterministic sanitized projection containing only public-safe command structure, CLI/framework/model/provider/version, supported/unsupported seed/decoding controls, caps, scenario outcomes, budget totals, and source hashes—never credentials, environment values, local paths, or transcript content. Mark this provider-calling test `pressure_live` so default/CI test runs exclude it. Execute all six fixed scenarios without the Skill and write `baseline-summary.json` only under the local state directory. Make the test fail closed if stack metadata/caps or a binding hash is missing, a raw transcript path is under either worktree, fewer than six outcomes are present, or fewer than two distinct observed baseline failures exist; if that evidence gate is not met, stop and improve the bounded scenario set rather than fabricating lift. Rerun the exact selector with `-m pressure_live` until it passes, then inspect `git status --short` to confirm no raw transcript is staged.

**Step 3 — second RED, before writing the Skill:** Write package/rubric tests that require the official Agent Skills lowercase hyphenated `name: semantic-reheating` matching its directory, `SKILL.md` YAML frontmatter, package-relative references no deeper than one directory below the Skill root, trigger-only description, the six sanitized scenarios, a canonical public `stack-receipt.json`, a canonical sanitized `baseline-summary.json` projection, baseline and post-skill aggregate results, counterexamples for productive repetition/blocked authority/unsafe writes/exhausted budgets, and a passing published rubric. Require each public domain JSON file to validate against its adjacent closed Draft 2020-12 schema, carry a version, and reject unknown fields/major versions. Run:
```bash
uv run pytest tests/skill/test_skill_package.py tests/skill/test_sanitized_rubric.py -q
uv run skills-ref validate skills/semantic-reheating
```

**Expected RED reason:** the public Skill, sanitized scenario/rubric/results artifacts, and their required evidence do not exist; `skills-ref validate` cannot yet validate a package.

**Step 4 — GREEN:** Write the smallest `SKILL.md` with the required name/frontmatter and only root-local or one-level `references/` links. It must instruct detection only when repetition and no-progress agree, preserve host authority, prohibit unconfirmed write retry, honor all budgets, and route missing authority/exhaustion to escalation/stop. Add adjacent closed versioned schemas, sanitized scenario inputs, a public-safe stack receipt, a baseline projection, an explicit rubric, and aggregate baseline/post-skill counts with no transcript content, local path, provider secret, or private metadata. Run the identical scenario bytes through the same pinned stack config and caps with the Skill; record redacted aggregate outcomes plus scenario/rubric/stack-receipt/command/baseline-projection hashes in `results.json`. Normal CI recomputes every binding from committed public bytes alone. The `pressure_live` test additionally projects the private config/summary and byte-compares those projections to the public receipt/summary before commit. Add a counterexample whenever a new rationalization is observed and rerun until the rubric passes.

**Step 5 — verify:**
```bash
uv run pytest tests/skill -q
uv run pytest -m pressure_live tests/skill/test_pressure_protocol.py -q
uv run skills-ref validate skills/semantic-reheating
git status --short
PRESSURE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/pressure-baselines"
uv run python -c "from pathlib import Path; import sys; root=Path.cwd().resolve(); p=Path(sys.argv[1]).expanduser().resolve(); assert not p.is_relative_to(root)" "$PRESSURE_ROOT"
```
Expected: skill package/rubric tests and official reference validation pass; Git status shows only intended public files; the raw baseline directory is outside the repository and therefore cannot enter a commit.

**Step 6 — commit:**
```bash
git add skills/semantic-reheating tools/pressure_skill_runner.py tests/skill
git commit -m "feat: add pressure-tested semantic reheating skill"
```

### Task 18: Build the generic Python host integration example

**Objective:** Demonstrate the controller beside a host-owned synthetic agent loop for normal progress, stagnation, bounded recovery, cooling, and safe stop.

**Files:**
- Create: `examples/python-generic-agent/main.py`, `examples/python-generic-agent/README.md`, `tests/integration/test_python_example.py`

**Step 1 — RED:** Test the example process returns one named result for each of `productive`, `exact_repetition`, `bounded_recovery`, `cooling`, and `unsafe_write`; assert a fake host switch receives an advisory decision but the controller never invokes a synthetic tool directly. Run:
```bash
uv run pytest tests/integration/test_python_example.py -q
```

**Expected RED reason:** Python example module and host loop do not exist.

**Step 2 — GREEN:** Implement a minimal standard-library example that emits `TraceEvent` records after synthetic tool results, calls `analyze`, applies decision with a host-owned `if` switch, and appends `record_outcome`. Tools are in-memory fixtures only; recovery is bounded by the policy. The unsafe write case records host confirmation absence and stops. Do not import or imitate an agent framework.

**Step 3 — verify:**
```bash
uv run pytest tests/integration/test_python_example.py -q
uv run python examples/python-generic-agent/main.py --scenario productive
uv run python examples/python-generic-agent/main.py --scenario unsafe_write
```
Expected: test passes and both commands emit redacted, normalized demonstration results.

**Step 4 — commit:**
```bash
git add examples/python-generic-agent tests/integration/test_python_example.py
git commit -m "feat: add generic Python host integration example"
```

### Task 19: Build the TypeScript AJV middleware interoperability example

**Objective:** Validate and consume unchanged Python-emitted fixture contracts in a framework-neutral asynchronous loop.

**Files:**
- Create: `examples/typescript-middleware/package.json`, `examples/typescript-middleware/package-lock.json`, `examples/typescript-middleware/tsconfig.json`, `examples/typescript-middleware/src/index.ts`, `examples/typescript-middleware/src/contracts.ts`, `examples/typescript-middleware/test/contracts.test.ts`, `examples/typescript-middleware/fixtures/python-v1-artifacts.json`, `examples/typescript-middleware/fixtures/python-v1-artifacts.schema.json`, `examples/typescript-middleware/export_fixtures.py`, `examples/typescript-middleware/README.md`
- Create: `tests/integration/test_typescript_fixture_contract.py`

**Step 1 — RED:** Add a Vitest test that meta-validates a closed, versioned Draft 2020-12 wrapper schema, rejects unknown wrapper/nested fields and an unknown wrapper major, then imports an unchanged Python-emitted bundle containing `TraceEvent`, `RunPolicy`, `DetectorFinding`, `DecisionEnvelope`, `RecoveryInstruction`, `RecoveryOutcome`, and `EvidenceRecord`; require AJV to validate the wrapper and every embedded artifact against the authoritative v1 schemas with no field translation and explicitly round-trip an `escalate` decision envelope with `requires_host_action: true`. Test a shared valid I-JSON canonicalization fixture with deliberately non-normalized Unicode produces the same RFC 8785 bytes and SHA-256 digest as Python; test the generic loop produces normal progress, stagnation, bounded recovery, cooling, and safe-stop outputs. Add a Python test that regenerates the bundle through public Python models and byte-compares it with the committed fixture before invoking `npm ci` and `npm test`. Run:
```bash
cd examples/typescript-middleware && npm ci && npm test
```

**Expected RED reason:** TypeScript package, AJV schemas, and generic middleware do not exist.

**Step 2 — GREEN:** Add a local TypeScript package with AJV `^8.20.0`, canonicalize `^4.0.0`, TypeScript `^7.0.2`, and Vitest `^4.1.11` pinned in `package-lock.json`; add the explicit closed/versioned aggregate wrapper schema, use AJV Draft 2020-12 validation, and use the zero-dependency `canonicalize` RFC 8785 implementation for the shared parity fixture rather than writing a second canonicalizer. Implement `export_fixtures.py` with only public Python model/serializer APIs and canonical output; resolve the repository's single authoritative `../../contracts/v1` schemas as read-only build-time references. Add an async generic loop that accepts a host executor callback. Consume all seven exact Python artifact shapes without translation; do not reimplement the controller and do not add a framework adapter. Include a script that imports the committed corpus fixture.

**Step 3 — verify:**
```bash
cd examples/typescript-middleware && npm ci && npm run typecheck && npm test
uv run pytest tests/integration/test_typescript_fixture_contract.py -q
```
Expected: TypeScript typecheck/AJV/RFC 8785 parity tests and Python cross-stack test pass without field translation or Unicode normalization.

**Step 4 — commit:**
```bash
git add examples/typescript-middleware tests/integration/test_typescript_fixture_contract.py
git commit -m "feat: add AJV contract interoperability example"
```

### Task 20: Canonical fan-in and public package documentation

**Objective:** Revalidate the merged lane outputs at the canonical fan-in and state the narrow metaphor/safety contract accurately.

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`, `docs/trace-contract.md`, `docs/detectors.md`, `docs/recovery-policies.md`, `docs/evaluation.md`, `docs/prior-art.md`
- Create: `tests/docs/test_readme_claims.py`, `tests/docs/test_docs_links.py`

**Step 1 — RED:** Test README explicitly says semantic reheating is a proposal-policy/search-breadth metaphor, not decoder-temperature control or strict simulated annealing; test it names host authority and non-goals. Test all documentation links resolve and every public contract has an explained link. Run:
```bash
uv run pytest tests/docs/test_readme_claims.py tests/docs/test_docs_links.py -q
```

**Expected RED reason:** required documents/claims/links are incomplete.

**Step 2 — GREEN:** Write concise, evidence-bounded docs that link actual contracts, detector behavior, false-positive protection, controller boundary, recovery ladder, cooling, corpus, and two examples. Describe optional semantic detection as disabled, separately metered, and incapable of weakening a deterministic stop. Do not claim universal improvement or production deployment.

**Step 3 — verify:**
```bash
uv run pytest tests/docs -q
uv run pytest tests -q
cd examples/typescript-middleware && npm run typecheck && npm test
```
Expected: docs tests, Python suite, and examples remain green at the canonical fan-in point.

**Step 4 — commit:**
```bash
git add README.md docs tests/docs
git commit -m "docs: document bounded semantic reheating reference kit"
```

### Task 21: Implement a capped, reproducible live campaign harness and preflight gate

**Objective:** Represent the complete two-stack, three-arm, three-replicate campaign safely and block execution until pricing/caps/metadata are valid.

**Files:**
- Create: `benchmark/live/campaign.schema.json`, `benchmark/live/stacks.schema.json`, `benchmark/live/campaign.example.json`, `benchmark/live/stacks.example.json`, `benchmark/live/runner.py`, `benchmark/live/preflight.py`, `tests/live/test_campaign_preflight.py`, `tests/live/test_campaign_matrix.py`

**Step 1 — RED:** Meta-validate both schemas with Draft 2020-12, require `contract_version: "1.0"`, and add focused negative tests for unknown major, unknown top-level/nested fields, missing fields, wrong types, and closed arm/tool/status vocabularies. Then test the preflight accepts a dry-run config with two named stacks, six matched synthetic tasks, arms `hard_stop_only`, `generic_rethink`, and `semantic_reheating`, and three replicates, yielding exactly 108 planned runs. Require every task to declare a fixture-owned isolated sandbox, synthetic/read-only tool allowlist, and no external side-effect capability. Test it blocks a paid remote stack if price schedule, per-run cap, campaign cap, provider/model/version/CLI/framework metadata, sandbox/tool declaration, or explicit cap values are missing; leave runtime first-cap scheduling behavior to Task 23's executor tests. Run:
```bash
uv run pytest tests/live/test_campaign_preflight.py tests/live/test_campaign_matrix.py -q
```

**Expected RED reason:** no campaign schemas, matrix builder, or preflight gate exists.

**Step 2 — GREEN:** Implement closed, versioned campaign and stack-selection schemas plus an offline preflight-only runner. `stacks.example.json` and later `stacks.selected.json` must validate against the same authoritative stack-selection schema. Require per run: 30 turns, 40 tools, 50,000 tokens, 20 minutes, and USD 1.00 when cost is available; require campaign: 2,000,000 tokens, 4,320 tools, 24 hours, USD 40.00 when cost is available. Require fixed decoding/seeds when supported and record unsupported controls. Require task-local sandbox roots and a closed synthetic/read-only tool allowlist; reject commands or tool declarations that can write outside the sandbox or target external systems. For paid remote stacks lacking provider cost reporting, require reviewed static price schedule and conservative token upper-bound calculation; otherwise return blocked. Local stacks may report direct API cost zero but must include compute/token/time metadata. Never invoke a provider in this task.

**Step 3 — verify:**
```bash
uv run pytest tests/live/test_campaign_preflight.py tests/live/test_campaign_matrix.py -q
uv run python -m benchmark.live.preflight --campaign benchmark/live/campaign.example.json --stacks benchmark/live/stacks.example.json --dry-run
```
Expected: tests pass and dry run reports `planned_runs: 108` with no network activity.

**Step 4 — commit:**
```bash
git add benchmark/live tests/live
git commit -m "feat: gate bounded live evaluation campaign"
```

### Task 22: Add live-run result recording and metric recomputation without a paid run

**Objective:** Make completed, interrupted, provider-failed, and safety-refused cells auditable from redacted artifacts.

**Files:**
- Create: `benchmark/live/results.schema.json`, `benchmark/live/metrics.py`, `benchmark/live/results/example-redacted-results.json`, `tests/live/test_results_metrics.py`, `tests/live/test_results_privacy.py`

**Step 1 — RED:** Meta-validate `results.schema.json` with Draft 2020-12; require a version field and focused rejection of unknown major, unknown top-level/nested fields, missing fields, wrong types, and unknown status/failure vocabularies. Test an example partial matrix preserves missing cells rather than imputing them; provider errors, safety refusals, and infrastructure failures are distinct from controller failures; metrics calculate recovery success, false interventions, token/tool/time/cost per accepted outcome, evidence gain, repeated side effects prevented, restart/stop rates, detector contribution, degraded-mode frequency, sample size, and missing-cell list. Run:
```bash
uv run pytest tests/live/test_results_metrics.py tests/live/test_results_privacy.py -q
```

**Expected RED reason:** result schema and recomputation implementation do not exist.

**Step 2 — GREEN:** Implement the closed, versioned redacted result schema and pure metric computation. Commit only synthetic/example results until Task 23 passes and an approved real execution creates redacted artifacts. Include matched-run manifest IDs, exact public execution scope, stack metadata, caps consumed, and no credentials/private provider metadata/raw outputs.

**Step 3 — verify:**
```bash
uv run pytest tests/live/test_results_metrics.py tests/live/test_results_privacy.py -q
uv run python -m benchmark.live.metrics benchmark/live/results/example-redacted-results.json
```
Expected: metrics are recomputed from artifact data and privacy tests pass.

**Step 4 — commit:**
```bash
git add benchmark/live tests/live
git commit -m "feat: record redacted bounded campaign results"
```

### Task 23: Implement the capped executor, then execute only the explicitly approved live campaign

**Objective:** Prove the executor against fake stacks, then run no more than the approved bounded matrix or record a legitimate blocked/partial result without fabrication.

**Files:**
- Create: `benchmark/live/campaign-run-manifest.schema.json`, `benchmark/live/executor.py`, `tests/live/test_campaign_executor.py`, `tests/live/test_selected_campaign_artifacts.py`
- Create or modify only after a passing preflight and operator approval: `benchmark/live/stacks.selected.json`, `benchmark/live/results/campaign-${RUN_DATE}.json`, `benchmark/live/results/campaign-${RUN_DATE}-manifest.json`

**Step 1 — RED:** Meta-validate the closed/versioned campaign-run manifest schema; reject unknown major, unknown top-level/nested fields, missing path/hash/run-count bindings, wrong types, duplicate cells, and unknown status values. Require selected stacks to validate against Task 21's schema and every emitted campaign result to validate against Task 22's schema. With injected fake stack commands and a fake clock, test the executor counts nested retries/handoffs/re-entry against the same run envelope, stops the current run at the first per-run cap, stops scheduling at the first campaign cap, never exceeds 108 records, classifies provider/safety/infrastructure outcomes separately, and redacts captured output. Test selected-stack metadata contains exact CLI/framework/model/provider/version details and every paid stack has reviewed pricing and caps. Run:
```bash
uv run pytest tests/live/test_campaign_executor.py tests/live/test_selected_campaign_artifacts.py -q
```

**Expected RED reason:** the injectable campaign executor and selected-artifact validator do not exist.

**Step 2 — GREEN executor:** Implement the minimum executor around an injected command runner, clock, and result sink. It must consume only a preflight-approved matrix, create a fresh fixture-owned sandbox per run, expose only an explicit environment allowlist to the stack command, enforce the closed synthetic/read-only tool allowlist, count all nested work and the first cap reached, redact before persistence, and expose no credential fields. Keep real stack selection and provider calls out of unit tests.

**Step 3 — verify the executor without provider calls:**
```bash
uv run pytest tests/live/test_campaign_executor.py tests/live/test_selected_campaign_artifacts.py tests/live/test_results_metrics.py tests/live/test_results_privacy.py -q
```
Expected: fake executions prove cap enforcement, classification, and redaction with no network/provider call.

**Step 4 — execution gate:** Before calling any real stack, run:
```bash
uv run python -m benchmark.live.preflight --campaign benchmark/live/campaign.example.json --stacks benchmark/live/stacks.selected.json --require-executable
uv run pytest tests/live/test_campaign_preflight.py tests/live/test_campaign_executor.py tests/live/test_selected_campaign_artifacts.py -q
```
Proceed only if both are green, the preflight reports exactly 108 or fewer planned runs, every paid stack has a reviewed static schedule or provider cost reporting, and the local operator has explicitly approved the stated cap. If any condition fails, write only a redacted `blocked`/`partial` artifact explaining the missing gate; do not execute a paid run and do not fabricate results.

**Step 5 — execute only after the gate:** Set `RUN_DATE=$(date -u +%F)`, then invoke the selected stacks through their recorded commands with `benchmark.live.executor`; write `campaign-${RUN_DATE}.json` and its manifest, and publish redacted aggregate/result records, not prompts, raw transcripts, hidden reasoning, token content, or private errors. If the gate is blocked, create only the typed blocked artifact through the executor's no-call path.
```bash
RUN_DATE=$(date -u +%F)
uv run python -m benchmark.live.executor \
  --campaign benchmark/live/campaign.example.json \
  --stacks benchmark/live/stacks.selected.json \
  --output "benchmark/live/results/campaign-${RUN_DATE}.json" \
  --manifest-output "benchmark/live/results/campaign-${RUN_DATE}-manifest.json"
```

**Step 6 — verify:**
```bash
RUN_DATE=$(date -u +%F)
uv run pytest tests/live/test_campaign_executor.py tests/live/test_selected_campaign_artifacts.py tests/live/test_results_metrics.py tests/live/test_results_privacy.py -q
uv run python -m benchmark.live.metrics "benchmark/live/results/campaign-${RUN_DATE}.json"
```
Expected: results validate, metrics recompute, and the artifact faithfully lists completed and missing cells. If the gate was blocked, expected output is an explicit blocked artifact and no provider call evidence.

**Step 7 — commit:**
```bash
git add benchmark/live tests/live
git commit -m "test: record bounded live campaign evidence"
```

### Task 24: Create the evidence-led article, scoped ledger, diagrams, and editable cover

**Objective:** Produce a Hugo/PaperMod leaf bundle whose claims, tables, visuals, and citations are generated or validated against committed redacted evidence.

**Files:**
- Create: `article/semantic-reheating/index.md`, `article/semantic-reheating/article-data-manifest.json`, `article/semantic-reheating/article-data-manifest.schema.json`, `article/semantic-reheating/sources-ledger.json`, `article/semantic-reheating/sources-ledger.schema.json`, `article/semantic-reheating/ASSETS.md`, `article/semantic-reheating/architecture.svg`, `article/semantic-reheating/cover.svg`, `article/semantic-reheating/cover.png`, `docs/diagrams/controller-state.mmd`
- Create: `tools/generate_article_data.py`, `tools/render_assets.py`, `tools/validate_article.py`, `tools/assets/package.json`, `tools/assets/package-lock.json`, `tests/article/test_article_bundle.py`, `tests/article/test_article_regeneration.py`, `tests/article/test_citation_ledger.py`, `tests/article/test_visual_assets.py`

**Step 1 — RED:** Test frontmatter has `draft: false` and closed TOC behavior; both the scoped citation ledger and article-data manifest meta-validate against adjacent closed Draft 2020-12 schemas and reject unknown top-level/nested fields, missing bindings, duplicate paths/hashes/order values, unknown source/status vocabularies, and unknown majors. Require the data manifest to list every candidate result artifact explicitly with canonical order, path, SHA-256, `source_kind` (`deterministic_benchmark | synthetic_example | blocked_campaign | partial_campaign | executed_campaign`), status, and include/exclude decision. Reject unlisted files, hash drift, duplicate artifacts, and any synthetic example selected as executed evidence. Require all article citations to have exactly one ledger record and no unused ledger source. `generate_article_data.py --check` regenerates the delimited results section inside `index.md` only from manifest-selected committed redacted artifacts and byte-compares without drift; article tables/claims include source artifact IDs/hashes and sample/missing-cell metadata; code snippets parse or execute through documented commands; Mermaid renders; SVG XML parses; PNG is RGB/RGBA and exactly 1600×900; ASSETS records local provenance; and article text distinguishes documented facts, repository observations, experiment results, and recommendations. Run:
```bash
uv run pytest tests/article/test_article_bundle.py tests/article/test_article_regeneration.py tests/article/test_citation_ledger.py tests/article/test_visual_assets.py -q
```

**Expected RED reason:** article bundle, ledger, render helper, and assets do not exist.

**Step 2 — GREEN:** Implement the closed/versioned article-data manifest and `generate_article_data.py` as a pure deterministic renderer from only its ordered, hash-bound, `include: true` entries. It scans the governed benchmark/live result roots and fails on unlisted candidates, validates each entry with its authoritative schema, distinguishes synthetic examples/blocked/partial/executed artifacts, rejects duplicate path/hash/order bindings, and never treats an excluded synthetic example as experiment evidence. It owns only the bytes between `<!-- BEGIN GENERATED RESULTS -->` and `<!-- END GENERATED RESULTS -->` in `index.md`, records source path/hash and missing cells, and supports `--check` by rendering to memory and comparing that section. Hand edits inside the generated section are forbidden. Write the article in the approved eight sections with title *Semantic Reheating for LLM Agents*, subtitle, and hook. State plainly that prompts do not alter decoder temperature and this is not strict simulated annealing. Cite only sources actually used from the approved design ledger; add a closed versioned ledger schema and store schema version, URL, title, author/publisher where available, accessed date, claim scope, and body citation key. Add a locked local asset tool package with `@mermaid-js/mermaid-cli@11.16.0`; make `render_assets.py` call its repository-local `mmdc` binary. Render `controller-state.mmd` to the architecture SVG. Draw the Systems Paper dark cover in editable SVG, then render to a 1600×900 PNG and visually inspect both. Report sample size, caps, uncertainty, missing cells, and bounded conclusions.

**Step 3 — verify:**
```bash
npm ci --prefix tools/assets
uv run python tools/generate_article_data.py --check
uv run python tools/render_assets.py
uv run pytest tests/article/test_article_bundle.py tests/article/test_article_regeneration.py tests/article/test_citation_ledger.py tests/article/test_visual_assets.py -q
uv run python tools/validate_article.py article/semantic-reheating
```
Expected: rendering/validation succeeds; PNG dimensions and SVG structure meet requirements.

**Step 4 — commit:**
```bash
git add article/semantic-reheating docs/diagrams tools tests/article
git commit -m "docs: publish evidence-led semantic reheating article bundle"
```

### Task 25: Add CI and repository-wide public-hygiene gates

**Objective:** Make all deterministic quality, interoperability, visual, package, and public-history checks mandatory while excluding paid live evaluation.

**Files:**
- Create: `.github/workflows/ci.yml`, `tools/domain_json_registry.py`, `tools/public_hygiene.py`, `tools/clean_checkout_verify.py`, `tools/release_receipt.py`, `tests/tools/test_domain_json_registry.py`, `tests/tools/test_public_hygiene.py`, `tests/tools/test_release_receipt.py`
- Modify: `pyproject.toml`, `.gitignore`

**Step 1 — RED:** Define an explicit Python registry mapping every public domain JSON glob/path to its authoritative schema and expected validation mode, with named exemptions only for ecosystem metadata and intentionally invalid test fixtures. Test that the mapping includes core contracts, corpus manifest/results, TypeScript aggregate wrapper, Skill scenarios/rubric/stack receipt/baseline projection/results, campaign/stacks/run-manifest/results, and article source/data manifests; fail on inferred-by-extension ownership, duplicate mappings, absent schemas, or undeclared public domain JSON. In isolated temporary repositories, test the hygiene scanner detects a fixture credential or private marker that was committed and later deleted, a current user-specific absolute path in Linux, macOS, or drive-letter Windows form, a tracked path beneath `.hermes/`, raw-reasoning/private content marker, unused article asset, an unregistered domain JSON artifact, and a bad relative link. Assemble prohibited sentinel values at test runtime from non-matching fragments so the public test source/history does not itself contain a secret-shaped token. Test the scanner permits synthetic redacted traces, policy text that merely names forbidden path classes, ecosystem metadata such as `package-lock.json`, and explicitly documented generic temporary roots used by clean-check tooling. Test `release_receipt.py` refuses output inside the repository, mismatched local/remote SHAs, missing byte-readback paths, or a dirty worktree, and writes a closed external receipt only for matching verified inputs. Run:
```bash
uv run pytest tests/tools/test_domain_json_registry.py tests/tools/test_public_hygiene.py tests/tools/test_release_receipt.py -q
```

**Expected RED reason:** explicit registry, scanners, and receipt writer do not exist.

**Step 2 — GREEN:** Implement the explicit registry in `tools/domain_json_registry.py`—never infer authority from extension or directory—and deterministic local scanners for secret patterns, user-specific absolute paths, forbidden tracked path classes, private content markers, Draft 2020-12 meta-validation and artifact validation for every registry mapping, package tree/frontmatter, relative links, unused assets, and `git diff --check`. Keep named exemptions only for ecosystem metadata and intentional negative fixtures. `--tracked-only` scans current tracked public files; `--history` enumerates reachable branch/tag blobs and fails on deleted credentials, private markers, user-specific paths, or forbidden artifact paths without printing secret values. Implement `release_receipt.py` with an injected command runner for tests as a fail-closed external-receipt writer that independently rechecks clean Git state, `origin/main` SHA equality, public/default-branch metadata, and remote blob SHA plus decoded byte equality for every required readback path before checking output-path exclusion from the repository and writing. Configure CI matrix Python 3.11/3.12/3.13 and Node 20/22 to run schema, unit/property/golden, corpus/metrics, deterministic prompt/skill package tests plus `uv run skills-ref validate skills/semantic-reheating`, docs/article, local Mermaid/SVG/PNG rendering after `npm ci --prefix tools/assets`, Python example, Ruff, mypy, TypeScript `npm ci`/typecheck/test, both hygiene modes, and clean-checkout verification. CI excludes `pressure_live` and all `benchmark.live` execution; it validates only schemas, sanitized aggregate Skill evidence, and example/redacted live results.

**Step 3 — verify:**
```bash
uv run pytest tests/tools/test_domain_json_registry.py tests/tools/test_public_hygiene.py tests/tools/test_release_receipt.py -q
uv run python tools/public_hygiene.py --tracked-only
uv run python tools/public_hygiene.py --history
uv run python tools/clean_checkout_verify.py --local
uv run ruff check src tests benchmark tools
uv run mypy src
uv run skills-ref validate skills/semantic-reheating
python -m compileall -q src benchmark tools
npm ci --prefix tools/assets
uv run python tools/render_assets.py
cd examples/typescript-middleware && npm ci && npm run typecheck && npm test
```
Expected: scanner, clean-check, Ruff, mypy, and Agent Skills reference checks pass locally; TypeScript checks pass; no paid command is called.

**Step 4 — commit:**
```bash
git add .github/workflows/ci.yml tools tests/tools pyproject.toml .gitignore
git commit -m "ci: enforce deterministic quality and public hygiene"
```

### Task 26: Run independent specification, quality/security, and publication reviews

**Objective:** Obtain independent evidence that implementation meets the approved design without scope creep or public-data leakage.

**Files:**
- Create: `docs/reviews/2026-08-27-spec-compliance.md`, `docs/reviews/2026-08-27-quality-security.md`, `docs/reviews/2026-08-27-publication-readiness.md`
- Create: `tests/docs/test_review_artifacts.py`
- Create local-only, never staged: `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/reviews/`
- Modify only to fix reviewer findings: the exact narrow files identified by a reviewer

**Step 1 — RED:** Add a review-check test that fails when any public review summary lacks reviewer role and fresh-context independence declaration, immutable commit under review, exact commands/output summary, AC checklist, finding severity, and explicit PASS/REQUEST_CHANGES verdict. Reject provider identities, private prompts, raw transcripts, local paths, and internal orchestration metadata. Run:
```bash
uv run pytest tests/docs/test_review_artifacts.py -q
```

**Expected RED reason:** independent review artifacts and review validator do not exist.

**Step 2 — GREEN:** Dispatch three fresh read-only reviewers after all prior tests are green: (1) specification reviewer maps every AC and design boundary to evidence; (2) quality/security reviewer inspects TDD evidence, schemas, side-effect boundaries, privacy, and dependency lockfiles; (3) publication reviewer checks article claims/citations/assets/hygiene and exact public scope. Save raw reviewer outputs only under `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/reviews/`; commit sanitized evidence summaries containing role, independence statement, reviewed commit, commands, bounded findings, and verdict. Any critical/important finding becomes a new narrow TDD fix task with selector RED/GREEN, followed by a fresh reviewer in the same lane. Do not add framework adapters or autonomous execution as review fixes.

**Step 3 — verify:**
```bash
uv run pytest tests/docs/test_review_artifacts.py -q
uv run pytest tests -q
uv run python tools/public_hygiene.py --tracked-only
uv run python tools/public_hygiene.py --history
git diff --check
```
Expected: review artifacts validate, full deterministic suite passes, hygiene passes, and diff check is clean.

**Step 4 — commit:**
```bash
git add docs/reviews tests/docs
git commit -m "docs: record independent release readiness reviews"
```

### Task 27: Reproduce the final candidate from a clean checkout and freeze its receipt

**Objective:** Commit the reproducibility machinery, then prove the resulting final candidate SHA from a clean copy without creating a self-referential evidence commit.

**Files:**
- Create: `docs/reproduction/2026-08-27-clean-checkout.md`
- Create local-only, never staged: `/tmp/semantic-reheating-clean-checkout/`, `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/`

**Step 1 — RED:** Invoke the not-yet-implemented explicit clone/receipt mode:
```bash
uv run python tools/clean_checkout_verify.py \
  --clone-dir /tmp/semantic-reheating-clean-checkout \
  --receipt "${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/precommit-red.json"
```

**Expected RED reason:** the explicit clean-clone/receipt mode does not exist.

**Step 2 — GREEN:** Implement or extend only `tools/clean_checkout_verify.py` to clone a specified local commit into `/tmp/semantic-reheating-clean-checkout`, use `UV_OFFLINE=1 uv sync --frozen --offline --all-groups` after dependency cache is prepared, run installed `reheat --help`, validate a fixture, replay benchmark, execute the Python example, run TypeScript install/typecheck/test from its lockfile, run article plus tracked/history hygiene checks, and generate an archive SHA-256. Write command outcomes, exact commit SHA, archive digest, and pass/fail only to the caller-supplied external receipt path. Write `docs/reproduction/2026-08-27-clean-checkout.md` as public reproducibility instructions without embedding a self-referential current commit. Confirm the clone contains no `.hermes/RDD` files in its tree or reachable history.

**Step 3 — verify the implementation before commit:**
```bash
set -euo pipefail
FEATURE_ROOT=$(pwd)
rm -rf /tmp/semantic-reheating-clean-checkout
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases"
uv sync --all-groups
uv run python tools/clean_checkout_verify.py \
  --clone-dir /tmp/semantic-reheating-clean-checkout \
  --receipt "${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/precommit-check.json"
cd /tmp/semantic-reheating-clean-checkout && git status --short && git diff --check
cd "$FEATURE_ROOT"
```
Expected: all named gates complete, cloned status is empty, and diff check is clean. If an offline dependency is not cached, report the specific missing cache and repeat only after fetching dependencies; do not claim offline reproducibility before the offline command succeeds.

**Step 4 — commit the public machinery:**
```bash
git add docs/reproduction tools/clean_checkout_verify.py
git commit -m "docs: add clean checkout reproduction gate"
```

**Step 5 — reproduce the exact post-commit release candidate:**
```bash
set -euo pipefail
RELEASE_SHA=$(git rev-parse HEAD)
RECEIPT_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases"
FINAL_RECEIPT="${RECEIPT_DIR}/clean-checkout-${RELEASE_SHA}.json"
rm -rf /tmp/semantic-reheating-clean-checkout
uv run python tools/clean_checkout_verify.py \
  --commit "$RELEASE_SHA" \
  --clone-dir /tmp/semantic-reheating-clean-checkout \
  --receipt "$FINAL_RECEIPT"
uv run python -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['commit_sha']==sys.argv[2] and r['status']=='pass'" "$FINAL_RECEIPT" "$RELEASE_SHA"
test -z "$(git status --porcelain)"
```
Expected: the external receipt is PASS for the exact clean post-commit SHA, and the source worktree remains clean. Any subsequent repository commit invalidates this receipt and requires rerunning Step 5 before publication.

### Task 28: Pass the publication gate, create the public GitHub repository, push, and read it back

**Objective:** Perform the only external publication action after all local gates, final-SHA clean reproduction, authentication, and remote state are verified.

**Files:**
- Create local-only, never staged: `${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/publication-${RELEASE_SHA}.json`

**Step 1 — RED/preflight:** From the verified feature worktree, require the Task 27 receipt for its exact SHA, fast-forward that candidate into the clean canonical `main` worktree, then run local and GitHub gates before creating any remote resource:
```bash
set -euo pipefail
test -z "$(git status --porcelain)"
FEATURE_SHA=$(git rev-parse HEAD)
RELEASE_RECEIPT="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/clean-checkout-${FEATURE_SHA}.json"
uv run python -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['commit_sha']==sys.argv[2] and r['status']=='pass'" "$RELEASE_RECEIPT" "$FEATURE_SHA"
test -z "$(git -C ../semantic-reheating status --porcelain)"
git -C ../semantic-reheating merge --ff-only "$FEATURE_SHA"
cd ../semantic-reheating
test "$(git branch --show-current)" = "main"
test "$(git rev-parse HEAD)" = "$FEATURE_SHA"
git status --short
git log -1 --format=%H
gh auth status
if gh repo view forcewake/semantic-reheating --json nameWithOwner,url,visibility,defaultBranchRef > /tmp/semantic-reheating-target.json 2>/dev/null; then
  echo "target repository already exists; inspect and stop" >&2
  exit 2
fi
uv run pytest tests -q
uv run python tools/public_hygiene.py --tracked-only
uv run python tools/public_hygiene.py --history
git diff --check
```

**Expected RED reason:** with the observed unauthenticated GitHub CLI, `gh auth status` fails and/or `gh repo view` cannot prove the target remote; publication is blocked. Do not create a repository, push, or infer remote state from a local commit. The canonical `main` may contain the already clean-reproduced candidate, but no remote side effect occurs.

**Step 2 — GREEN gate:** Only after `gh auth login` succeeds under the repository owner’s explicit authenticated session, repeat the exact preflight. Require clean status, full green suite, both hygiene modes/diff green, final independent publication review PASS, and an exact local `HEAD` SHA with matching Task 27 receipt. If `forcewake/semantic-reheating` already exists, stop and inspect ownership/default branch rather than overwriting it. If it does not exist, create it explicitly public with MIT license omitted because the local repository already has one:
```bash
gh repo create forcewake/semantic-reheating --public --source . --remote origin --push
```

**Step 3 — remote SHA and byte-content readback:**
```bash
set -euo pipefail
LOCAL_SHA=$(git rev-parse HEAD)
git push -u origin HEAD:main
test "$(gh repo view forcewake/semantic-reheating --json visibility --jq .visibility)" = "PUBLIC"
test "$(gh repo view forcewake/semantic-reheating --json defaultBranchRef --jq .defaultBranchRef.name)" = "main"
REMOTE_SHA=$(git ls-remote origin refs/heads/main | cut -f1)
test "$REMOTE_SHA" = "$LOCAL_SHA"
for FILE in README.md contracts/v1/trace-event.schema.json article/semantic-reheating/index.md; do
  LOCAL_BLOB=$(git rev-parse "HEAD:${FILE}")
  REMOTE_BLOB=$(gh api "repos/forcewake/semantic-reheating/contents/${FILE}?ref=main" --jq .sha)
  test "$REMOTE_BLOB" = "$LOCAL_BLOB"
  gh api "repos/forcewake/semantic-reheating/contents/${FILE}?ref=main" --jq .content \
    | tr -d '\n' | base64 --decode | cmp - "$FILE"
done
```
Expected: the repository is public at `https://github.com/forcewake/semantic-reheating`, default branch is `main`, remote main SHA equals the clean-reproduced local SHA, and all three representative remote blobs byte-compare with their local files.

**Step 4 — freeze external publication evidence without changing Git:**
```bash
set -euo pipefail
FINAL_PUBLICATION_RECEIPT="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases/publication-${LOCAL_SHA}.json"
uv run python tools/release_receipt.py \
  --output "$FINAL_PUBLICATION_RECEIPT" \
  --repo . \
  --path README.md \
  --path contracts/v1/trace-event.schema.json \
  --path article/semantic-reheating/index.md
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$LOCAL_SHA"
```
No repository commit is permitted after Task 27's clean receipt. Any source change invalidates the receipt and returns execution to Task 27; publication completes only when external receipt generation succeeds with unchanged local/remote SHA and byte-readback evidence.

---

## Final acceptance-criteria traceability matrix

| Acceptance criterion | Primary task(s) | Verification evidence |
|---|---:|---|
| AC-1 — clean offline install and `reheat --help` | 1, 27 | `UV_OFFLINE=1 uv sync --frozen --offline --all-groups`; `UV_OFFLINE=1 uv run reheat --help`; clean-checkout report |
| AC-2 — versioned schemas and unknown-major fail closed | 2–4, 7, 13–15, 17, 21–22, 24–25 | core contract tests, domain-artifact schema tests, generated unknown-major property cases, and closed registry scan |
| AC-3 — byte-identical replay | 5, 11, 14, 15 | canonicalization, controller repeat, replay and golden tests |
| AC-4 — intervention IDs and reason codes | 8–12, 14 | detector/controller and CLI/replay assertions |
| AC-5 — no reheat from repetition alone | 6, 7, 11, 15 | progress, policy, controller, and property tests |
| AC-6 — hard budgets dominate | 7, 11, 15, 21, 23 | unsafe-policy/controller properties and campaign cap tests |
| AC-7 — repeated non-idempotent write stops | 7, 10, 11, 18 | side-effect policy/controller and Python example tests |
| AC-8 — 24+ balanced corpus | 13 | manifest count/balance/completeness tests |
| AC-9 — corpus metrics | 14, 15 | replay metrics and golden/metric test output |
| AC-10 — unchanged Python fixtures validate in TypeScript | 3–4, 13, 19 | all seven core artifacts exported by Python, AJV validation, canonical parity, and cross-stack tests |
| AC-11 — examples cover full lifecycle | 18, 19, 20 | Python and TypeScript scenario tests |
| AC-12 — Skill baseline, post-skill results, counterexamples, rubric | 17 | pinned local stack/cap receipt, bound baseline/post hashes, and sanitized package/rubric tests |
| AC-13 — capped live runner with exact stack metadata | 21–23 | preflight/matrix/result tests and selected campaign manifest |
| AC-14 — article claims/tables regenerate from redacted artifacts | 14, 22, 24 | committed deterministic/live artifacts plus generator drift and article citation tests |
| AC-15 — article/asset validations | 24, 25 | article, visual, citation, CI, and hygiene checks |
| AC-16 — public-history hygiene | 1, 13, 17, 22, 25–27 | privacy tests, reachable-history scan, tracked scan, clean clone, and review evidence |
| AC-17 — bounded metaphor README language | 1, 20 | README claim test |
| AC-18 — public GitHub remote matches verified local commit | 25, 27–28 | final-SHA clean receipt, authenticated SHA equality, remote blob/byte readback, and external publication receipt |

## Final release checklist

1. Re-read this matrix against design AC-1 through AC-18; no criterion may be marked complete solely from an implementer report.
2. Run `uv run pytest tests -q`, `cd examples/typescript-middleware && npm ci && npm run typecheck && npm test`, `uv run python tools/public_hygiene.py --tracked-only`, `uv run python tools/public_hygiene.py --history`, and `git diff --check` after the final task.
3. Verify `.hermes/`, local RDD records, raw model transcripts, provider caches, temporary tool outputs, user-specific absolute paths, and private markers are absent from `git ls-files`, reachable history via `tools/public_hygiene.py --history`, and the clean checkout.
4. Keep CI free of paid live evaluation. If a campaign was blocked or partial, publish the bounded truth and missing-cell list; never impute or broaden article claims.
5. Publication is complete only when Task 28’s final local SHA equals `origin/main` and GitHub readback returns the expected public files.
