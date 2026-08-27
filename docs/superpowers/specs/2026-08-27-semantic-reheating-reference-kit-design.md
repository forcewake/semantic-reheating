# Semantic Reheating Reference Kit — Design Specification

- **Status:** Approved design, pending implementation plan
- **Date:** 2026-08-27
- **Repository:** `forcewake/semantic-reheating`
- **Primary audience:** engineers building production agent runtimes and frameworks
- **License:** MIT
- **Source cutoff for the first article:** 2026-08-27

## 1. Executive summary

`semantic-reheating` will be a public, framework-neutral reference kit for detecting when an LLM agent has stopped making measurable progress and selecting a bounded recovery intervention.

The central claim is deliberately narrow:

> A prompt such as “do some research” can improve a stale agent trajectory because it changes the agent's proposal policy, accessible tools, and search breadth. It does not secretly change decoder temperature, and it is not simulated annealing in the strict mathematical sense.

The kit will make that claim executable through five proof-bearing surfaces:

1. versioned language-neutral trace and decision contracts;
2. a deterministic-first Python reference engine and `reheat` CLI;
3. prompts, a portable Agent Skill, and Python/TypeScript integration examples;
4. a deterministic trace benchmark plus bounded live A/B runs on two available agent/model stacks;
5. an evidence-led English article bundle with diagrams, citations, and an editable social cover.

The repository is not a new orchestration framework. It observes traces and emits typed decisions. The host agent runtime retains authority over tools, credentials, side effects, retries, and human escalation.

## 2. Problem and terminology

Long-running agents can repeat equivalent tool calls, oscillate between plans, consume budget without changing external state, or paraphrase the same unsuccessful strategy. A maximum-turn limit stops the loss but does not provide a recovery policy. A generic “try again” prompt can change wording without changing the trajectory.

The design uses **semantic reheating** as an engineering metaphor for a state-dependent, bounded expansion of the proposal space. Classical simulated annealing accepts worse moves according to a temperature-dependent probability over an explicit objective landscape.[1] This project does not claim that a natural-language instruction reproduces that mechanism.

Related foundations include exploration/exploitation[2] and bounded restarts under uncertain run times.[14]

Branching search provides a closer model for deliberate hypothesis expansion.[3][6]

Iterative refinement and reflection address trajectory improvement.[4][5]

OPRO and GEPA optimize textual instructions from execution feedback.[7][8]

Existing runtimes already implement important parts of the proposed pattern: Magentic-One keeps a progress ledger and re-enters an outer planning loop after stalls.[9] Gemini CLI detects several loop forms and can issue a bounded “step back and rethink” recovery instruction.[10][11]

### 2.1 Normative terms

- **Progress:** a verified change in an acceptance criterion, environment state, evidence set, or error diagnosis that narrows the remaining problem.
- **Repetition:** equivalent actions or reasoning states recur inside a bounded window.
- **Stagnation:** repetition and lack of progress agree; repetition alone is insufficient.
- **Reheating:** a bounded intervention that deliberately expands hypotheses, sources, tools, or strategies.
- **Cooling:** reconvergence to one evidence-backed strategy and deterministic acceptance checks.
- **Recovery budget:** the complete allowance for an intervention, including retries, handoffs, sub-agent calls, and re-entry.
- **Evidence gain:** new verified information that changes the set or ranking of plausible strategies.

## 3. Goals and non-goals

### 3.1 Goals

1. Provide a stable, language-neutral contract for agent traces and recovery decisions.
2. Detect common stagnation patterns without requiring an LLM call.
3. Combine repetition signals with explicit progress signals to reduce false interventions.
4. Emit explainable, evidence-linked decisions: `continue`, `nudge`, `diagnose`, `reheat`, `restart`, or `stop`.
5. Bound all recovery work by turns, tool calls, tokens, time, cost when available, and side-effect policy.
6. Provide reusable prompts and an Agent Skill whose behavior is pressure-tested through RED–GREEN–REFACTOR.
7. Demonstrate integration without binding the core to one agent framework.
8. Measure both recovery benefit and intervention harm.
9. Publish a technically rigorous article whose claims do not exceed the repository evidence.

### 3.2 Non-goals

1. Replacing an agent orchestrator, planner, sandbox, policy engine, or human approval system.
2. Granting tools or permissions to an agent.
3. Treating real external side effects as “worse moves” that may be accepted for exploration.
4. Claiming a universal improvement over greedy execution, reflection, search, or restart. Search can cost materially more than greedy execution, so the first release treats it as a triggered recovery mode rather than a default.[13]
5. Providing production adapters for every framework in the first release.
6. Using a hidden LLM judge as the sole detector or evaluator.
7. Publishing private prompts, provider credentials, raw proprietary traces, internal workflow metadata, or client context.

## 4. Approved architecture

### 4.1 System boundary

```text
Agent/runtime trace + environment evidence + run policy
                         │
                         ▼
          Semantic Reheating Controller
     ┌─────────────┬──────────────┬──────────────┐
     │ detectors   │ diagnosis    │ policy       │
     │ progress    │ cause class  │ selection    │
     └─────────────┴──────────────┴──────────────┘
                         │
                         ▼
 DecisionEnvelope + RecoveryInstruction + EvidenceRecord
                         │
                         ▼
        Host runtime authorizes and executes
```

The controller is a pure decision component for the first release. It may construct a recovery instruction but does not call an agent, tool, network service, or provider by itself. Offline replay is therefore deterministic for the same trace, policy, contract version, and installed detector set.

### 4.2 State machine

```text
NORMAL → DIAGNOSE → REHEAT → SELECT → COOL → VERIFY
   │         │          │         │        │       │
   └─────────┴──────────┴─────────┴────────┴───────┘
                STOP / ESCALATE on hard limits
```

- `NORMAL`: no intervention; productive or inconclusive execution continues.
- `DIAGNOSE`: classify the blockage and build an uncertainty map.
- `REHEAT`: create bounded, mutually exclusive hypotheses and discriminating tests.
- `SELECT`: rank candidates by evidence gain, feasibility, safety, and remaining budget.
- `COOL`: collapse to one branch and prohibit further search unless a new independent stagnation episode is detected.
- `VERIFY`: run host-defined deterministic acceptance checks.
- `STOP`: terminate on exhausted budget, missing authority, unsafe repetition, or failure to gain information.

## 5. Repository structure

```text
semantic-reheating/
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── CITATION.cff
├── pyproject.toml
├── contracts/v1/
│   ├── trace-event.schema.json
│   ├── run-policy.schema.json
│   ├── detector-finding.schema.json
│   ├── decision-envelope.schema.json
│   ├── recovery-instruction.schema.json
│   ├── recovery-outcome.schema.json
│   └── evidence-record.schema.json
├── src/semantic_reheating/
│   ├── models.py
│   ├── canonical.py
│   ├── progress.py
│   ├── diagnosis.py
│   ├── controller.py
│   ├── policies.py
│   ├── evidence.py
│   ├── cli.py
│   └── detectors/
│       ├── exact_repetition.py
│       ├── cycle.py
│       ├── unchanged_state.py
│       ├── repeated_error.py
│       ├── acceptance_stall.py
│       └── budget_burn.py
├── prompts/
│   ├── detection-notice.md
│   ├── uncertainty-map.md
│   ├── bounded-reheating.md
│   ├── select-and-cool.md
│   └── verify-or-stop.md
├── skills/semantic-reheating/
│   ├── SKILL.md
│   └── references/
├── examples/
│   ├── python-generic-agent/
│   └── typescript-middleware/
├── benchmark/
│   ├── corpus/
│   ├── scenarios/
│   ├── live/
│   ├── replay.py
│   └── metrics.py
├── article/semantic-reheating/
│   ├── index.md
│   ├── cover.png
│   ├── cover.svg
│   ├── architecture.svg
│   ├── ASSETS.md
│   └── sources-ledger.json
├── docs/
│   ├── architecture.md
│   ├── trace-contract.md
│   ├── detectors.md
│   ├── recovery-policies.md
│   ├── evaluation.md
│   └── prior-art.md
├── tests/
└── .github/workflows/ci.yml
```

Local RDD evidence, model transcripts, temporary provider outputs, benchmark caches, and the visual brainstorming session are excluded from public history.

## 6. Versioned contracts

All public JSON documents carry `contract_version: "1.0"`. Unknown major versions fail closed. Minor additive fields are ignored only when the schema explicitly allows them.

### 6.1 `TraceEvent`

Required fields:

- `contract_version`;
- `run_id`, `event_id`, and monotonically increasing `sequence`;
- `kind`: `message`, `plan`, `tool_call`, `tool_result`, `state_observation`, `acceptance_check`, `handoff`, `error`, or `budget`;
- `actor` and optional `parent_event_id`;
- `effect_class`: `read_only`, `idempotent_write`, `non_idempotent_write`, or `unknown`;
- one of `payload`, `payload_ref`, or `payload_digest`;
- optional `state_fingerprint`, `error_fingerprint`, `acceptance_delta`, `evidence_refs`, and budget counters.
- optional `expected_state_change` when the host can state whether an action should mutate observable state.

Payloads are canonicalized with RFC 8785 JSON Canonicalization Scheme and fingerprinted with SHA-256. Event IDs, timestamps, volatile request IDs, and configured secret-bearing fields are excluded from action-equivalence fingerprints. Unknown effect class is treated as non-repeatable until the host classifies it.

### 6.2 `RunPolicy`

Required policy groups:

- detector windows and thresholds;
- minimum agreeing signal count;
- recovery ladder permissions;
- per-intervention and whole-run turn/tool/token/time/cost limits;
- maximum recovery episodes and re-entry depth;
- side-effect repetition rules;
- cooling conditions;
- optional semantic detector configuration.

Default policy posture:

- deterministic detectors only;
- at least one repetition signal and one no-progress signal before `reheat`;
- read-only exploration;
- no automatic repeat of an unconfirmed non-idempotent write;
- one recovery episode before `restart`, then `stop` if no evidence gain occurs.

### 6.3 `DetectorFinding`

A finding contains:

- detector name and version;
- boolean `matched` and normalized `score` in `[0,1]`;
- finding class: `repetition`, `no_progress`, `risk`, or `budget`;
- exact supporting `event_ids`;
- a stable reason code;
- redacted human explanation;
- any degraded-mode or unavailable-detector notice.

### 6.4 `DecisionEnvelope`

```text
decision: continue | nudge | diagnose | reheat | restart | escalate | stop
reason_codes: string[]
evidence_event_ids: string[]
recovery_policy: null | policy identifier
recovery_budget: explicit counters
constraints: explicit safety and tool restrictions
cooling_conditions: measurable conditions
confidence: bounded score plus contributing findings
requires_host_action: boolean
human_summary: redacted explanation
```

`confidence` is a reproducible aggregation of detector findings, not a probability that recovery will succeed. For recovery decisions, the default aggregation computes each finding-class score as the maximum configured detector weight multiplied by finding score, clipped to `[0,1]`; decision confidence is the minimum of the `repetition` and `no_progress` class scores. An explicit hard-budget violation has confidence `1.0` for `stop`. The envelope records every contributing score and weight.

### 6.5 `RecoveryInstruction` and `RecoveryOutcome`

`RecoveryInstruction` contains the selected prompt asset identifier, structured variables, complete recovery budget, allowed tools, forbidden actions, evidence references to preserve, expected output contract, cooling conditions, and stop conditions. It is advisory data for the host and contains no executable credential or authority grant.

`RecoveryOutcome` records the host's execution result: status, consumed counters, evidence gained, acceptance delta, state delta, error class, and any host denial or human escalation. Hosts may use references or digests instead of raw payloads.

### 6.6 `EvidenceRecord`

The host can append an outcome record containing the trigger, chosen policy, actual counters consumed, new evidence references, acceptance-criterion delta, repeated side effects avoided, and final status. This creates the dataset for replay and live evaluation without exposing raw hidden reasoning.

## 7. Detection and progress model

### 7.1 Deterministic detectors

1. **Exact repetition:** equivalent tool and arguments recur with equivalent results.
2. **Cycle/oscillation:** a sequence of length two through five repeats with no net state change.
3. **Unchanged state:** state fingerprints remain stable across an action window that was expected to mutate state.
4. **Repeated error:** normalized error fingerprints recur without a changed hypothesis or input.
5. **Acceptance stall:** host-provided acceptance checks show no delta.
6. **Budget burn:** turns, tool calls, tokens, elapsed time, or cost rise without evidence gain.

A detector cannot independently trigger `reheat`. The controller requires agreement between at least one repetition/budget signal and one no-progress signal. Hard safety or budget limits may trigger `stop` directly.

### 7.2 Optional semantic detector

An extension protocol may detect paraphrased plan repetition or semantically equivalent tool intent. It is disabled by default, separately metered, and never permitted to relax a deterministic stop or side-effect limit. Its contribution and failure state remain visible in the decision envelope.

### 7.3 False-positive protection

The following count as progress when supported by the trace:

- different pagination cursors or batch items;
- changed tool inputs intended to test a hypothesis;
- changed error fingerprints or newly exposed stack frames;
- new evidence or eliminated hypotheses;
- deliberate verification reruns required by acceptance criteria;
- handoffs that produce a new plan or capability;
- environment changes that are expected to converge over repeated polls.

## 8. Diagnosis and recovery policies

### 8.1 Cause classes

- missing knowledge or inaccessible source;
- incorrect or underspecified plan;
- unsuitable or unavailable tool;
- environment/runtime defect;
- missing authority, credential, or human decision;
- unsafe or non-repeatable side effect;
- ambiguous completion criterion;
- exhausted budget.

### 8.2 Recovery ladder

0. **Continue:** signals disagree or progress exists.
1. **Nudge:** state the detected repetition and forbid the exact retry.
2. **Diagnose:** produce a structured uncertainty map.
3. **Reheat:** generate exactly three mutually exclusive, falsifiable hypotheses and one discriminating read-only test per hypothesis.
4. **Research:** query only the named knowledge gaps and preserve retrieved evidence.
5. **Restart or switch:** start with clean context while retaining verified facts, evidence references, constraints, and rejected hypotheses; do not copy the failed reasoning transcript wholesale.
6. **Stop or escalate:** no information gain within budget, unsafe action, missing authority, or exhausted hard limit.

The policy selector chooses the least expensive permitted intervention likely to distinguish among current hypotheses. A fixed “five reasoning iterations” is not a universal rule; the policy uses explicit counters appropriate to the host.

### 8.3 Cooling rule

Recovery ends as soon as one strategy has both:

1. greater verified evidence support than alternatives; and
2. a concrete next action within the remaining budget and authority.

The host then executes one branch and runs deterministic acceptance checks. New exploration requires a new independent stagnation episode and remaining episode budget.

### 8.4 Side-effect boundary

- Exploration and hypothesis testing are read-only or sandboxed by default.
- The controller cannot authorize a tool or mutate state.
- Unconfirmed non-idempotent writes are never repeated automatically.
- “Try a worse move” applies only to planning candidates, not production side effects.
- The budget covers retries, handoffs, subagents, callbacks, and re-entry, addressing the full feedback path rather than only the visible top-level loop. Infinite-loop research motivates treating those nested paths as part of one bounded run.[12]

## 9. Python reference engine and CLI

### 9.1 Public Python API

```python
from semantic_reheating import analyze, build_recovery_instruction

result = analyze(trace_events, run_policy)
instruction = build_recovery_instruction(result)
```

Required interfaces:

```python
def analyze(
    trace: Sequence[TraceEvent],
    policy: RunPolicy,
    *,
    semantic_detector: SemanticDetector | None = None,
) -> DecisionEnvelope: ...


def build_recovery_instruction(
    decision: DecisionEnvelope,
) -> RecoveryInstruction | None: ...


def record_outcome(
    decision: DecisionEnvelope,
    outcome: RecoveryOutcome,
) -> EvidenceRecord: ...
```

`analyze` has no network or provider side effects. The optional detector interface is dependency-injected.

### 9.2 CLI

```text
reheat validate TRACE.jsonl --policy POLICY.json
reheat analyze TRACE.jsonl --policy POLICY.json --format json|text
reheat explain DECISION.json
reheat benchmark benchmark/corpus --manifest benchmark/scenarios/manifest.json
```

The CLI writes machine-readable output to stdout and diagnostics to stderr. Invalid schema, sequence gaps, incompatible versions, unsafe policies, and unavailable required detectors produce distinct non-zero exit codes.

## 10. Prompt pack and Agent Skill

### 10.1 Prompt assets

Each prompt has:

- explicit trigger and non-trigger conditions;
- JSON output schema or named Markdown sections;
- complete budget and tool restrictions;
- evidence references to preserve;
- stop and cooling conditions;
- a short runtime form and an explanatory operator form.

The pack includes detection notice, uncertainty map, bounded reheating, select-and-cool, and verify-or-stop. “Wild hypotheses” is replaced by mutually exclusive, falsifiable hypotheses to preserve diversity without rewarding unsupported invention.

### 10.2 Portable Agent Skill

The Agent Skill uses Agent Skills-compatible frontmatter and package-relative references. Its description states only the triggering symptoms so agents must load the full skill.

Skill development follows RED–GREEN–REFACTOR:

1. run at least five pressure scenarios without the skill and retain the baseline failures outside public history;
2. implement the smallest skill that addresses observed failures;
3. rerun the same scenarios with the skill;
4. add counterexamples for productive repetition, blocked authority, unsafe writes, and exhausted budgets;
5. close new rationalizations and rerun until the published rubric passes.

The repository publishes sanitized scenarios, rubric, and aggregate results, not private model transcripts.

## 11. Integration examples

### 11.1 Python generic agent

A minimal agent loop emits contract events, calls `analyze` after each tool result, applies the returned decision through a host-owned policy switch, and appends the outcome record. It uses synthetic tools and demonstrates productive execution, exact repetition, bounded recovery, cooling, and safe stop.

### 11.2 TypeScript middleware

A framework-neutral TypeScript example validates the same JSON contracts with AJV and wraps a generic asynchronous tool loop. It demonstrates cross-language interoperability rather than a second controller implementation. Contract fixtures emitted by Python must validate and replay unchanged in TypeScript.

Framework-specific adapters are deferred until the contracts and benchmark are stable.

## 12. Evaluation design

### 12.1 Deterministic corpus

The first release contains at least 24 synthetic traces:

- at least 12 pathological traces covering exact repetition, two- to five-step cycles, unchanged state, repeated errors, budget burn, blocked authority, context restart, and unsafe write repetition;
- at least 12 productive controls covering pagination, batch processing, polling with state changes, changed hypotheses, verification reruns, handoffs, and eventual consistency.

Every trace has an expected detector set, decision, evidence-event set, and safety outcome. Corpus tests measure detector precision and recall, decision accuracy, deterministic replay, schema compatibility, and false-intervention rate.

### 12.2 Baselines

Each live task runs under:

1. maximum-turn hard stop only;
2. one generic “step back and rethink” recovery prompt;
3. the full Semantic Reheating Controller.

A no-guard condition is permitted only in deterministic simulation; live runs always retain hard budgets.

### 12.3 Bounded live A/B campaign

The initial campaign is implemented as two pairwise comparisons—hard-stop-only versus full controller, and generic-rethink versus full controller—using one shared three-arm run matrix. It uses:

- six matched synthetic tasks;
- two locally available agent/model stacks selected before execution and recorded with exact CLI, framework, model, provider, and version metadata;
- three conditions;
- three replicates per task/stack/condition;
- a maximum of 108 runs;
- per-run defaults of 30 agent turns, 40 tool calls, 50,000 total tokens, 20 minutes, and USD 1.00 when cost metering is available;
- campaign defaults of 2,000,000 total tokens, 4,320 tool calls, 24 wall-clock hours, and USD 40.00 when cost metering is available;
- fixed decoding parameters and seeds where a stack supports them, with unsupported controls recorded explicitly;
- execution stopping when the first per-run or campaign cap is reached.

For a paid remote stack without provider cost reporting, the runner requires a reviewed static price schedule and computes a conservative upper bound from metered tokens; otherwise that stack is blocked from the campaign. Local models may declare direct API cost as zero while still reporting compute environment and token/time counters.

If all 108 runs cannot be completed within the approved campaign cap, the article reports the completed matrix and missing cells; it does not impute results. Provider errors, safety refusals, and infrastructure failures are separate outcomes rather than controller failures.

### 12.4 Metrics

- recovery success rate;
- false-intervention rate;
- tokens, tool calls, elapsed time, and metered cost per accepted outcome;
- evidence gain before and after intervention;
- repeated side effects prevented;
- restart rate and stop/escalation rate;
- detector contribution and degraded-mode frequency.

The article reports uncertainty and sample size. It does not claim statistical generality from the bounded campaign.

## 13. Failure handling

| Failure | Required behavior |
|---|---|
| Invalid trace or policy | Reject with a typed error; emit no fabricated decision. |
| Unknown contract major | Fail closed with an upgrade message. |
| Optional detector unavailable | Record degraded mode and continue with deterministic detectors. |
| Required detector unavailable | Stop analysis with a typed error. |
| Missing progress signal | Emit `continue` with an advisory finding; repetition alone cannot trigger recovery. |
| Hard budget reached | Emit `stop`; no recovery prompt may override it. |
| Missing authority or credential | Emit `stop` or `escalate`, never repeated retries. |
| Non-idempotent write repeated | Emit a risk finding and stop pending host confirmation. |
| No evidence gain after recovery | Cool only if a viable branch exists; otherwise restart once or stop. |
| Live-eval provider failure | Classify separately and preserve the matched-run manifest. |

## 14. Article and visual design

### 14.1 Selected direction

- **Title:** *Semantic Reheating for LLM Agents*
- **Subtitle:** *Detect stagnation. Explore deliberately. Re-converge on evidence.*
- **Hook:** Why “do some research” can work—and why it is not simulated annealing.

The selected visual direction is **Systems Paper**: a high-contrast dark 1600×900 cover showing a closed loop breaking into a bounded branch and reconverging on a verified path. The repository retains editable SVG source and a visually inspected PNG.

### 14.2 Article structure

1. the useful observation;
2. audit of the simulated-annealing metaphor;
3. academic and runtime prior art;
4. controller architecture and safety boundary;
5. executable trace-to-decision example;
6. deterministic and live evaluation results;
7. failures and cases where reheating should not be used;
8. production-runtime checklist.

The article is a Hugo/PaperMod-compatible leaf bundle with `draft: false`, closed table of contents by default, local assets, a scoped citation ledger, and only sources actually cited in the body. Documented facts, repository observations, experimental results, and architecture recommendations remain visibly distinct.

## 15. Security, privacy, and public hygiene

1. Public fixtures are synthetic and contain no client, employer, contact, pricing, credential, or private-chat data.
2. Trace payloads support references and digests so secret-bearing content need not be stored.
3. Redaction runs before persistence and publication, not only in rendering.
4. No absolute local paths, `.hermes/` metadata, provider tokens, raw hidden reasoning, or temporary model outputs enter git history.
5. CI runs secret scanning, private-marker scanning, relative-link checks, package-tree validation, and `git diff --check`.
6. Live results include exact execution scope but redact credentials and private provider metadata.
7. Successful tests prove only the exercised contracts, traces, and environments; README and article language must remain bounded to that scope.

## 16. Verification and CI

Required gates:

- Python 3.11, 3.12, and 3.13 package tests;
- Node.js 20 and 22 TypeScript example tests;
- JSON Schema validation for all contracts and fixtures;
- Python unit, property, and golden replay tests on supported Python versions;
- deterministic output equality across repeated replays;
- TypeScript type-check and AJV validation of Python-generated fixtures;
- prompt schema and relative-link validation;
- Agent Skill package-tree, frontmatter, discovery, and pressure-test rubric checks;
- benchmark manifest completeness and metric recomputation;
- article frontmatter parsing and strict citation validation;
- Mermaid/SVG rendering and PNG dimension/mode checks;
- local-path, credential, private-marker, and unused-asset scans;
- clean-checkout install, CLI smoke, example execution, and archive integrity.

CI does not run paid live evaluation. Live runs are explicit, locally initiated, budget-gated, and publish only redacted result artifacts.

## 17. Acceptance criteria

- [ ] **AC-1:** A clean checkout installs the Python package and `reheat --help` succeeds without network access after dependency installation.
- [ ] **AC-2:** Every public JSON artifact validates against a versioned schema, and unknown major versions fail closed.
- [ ] **AC-3:** Replaying the same trace and policy produces byte-identical normalized decisions.
- [ ] **AC-4:** `reheat analyze` reports exact supporting event IDs and reason codes for every intervention.
- [ ] **AC-5:** `reheat` cannot emit `reheat` from repetition alone when no no-progress signal exists.
- [ ] **AC-6:** A hard run or recovery budget always dominates any prompt or optional detector recommendation.
- [ ] **AC-7:** An unconfirmed repeated non-idempotent write produces a stop/risk decision and no executable retry instruction.
- [ ] **AC-8:** The deterministic corpus contains at least 24 balanced pathological/control traces with expected findings and decisions.
- [ ] **AC-9:** The full corpus reports detector precision/recall, decision accuracy, false interventions, and deterministic replay status.
- [ ] **AC-10:** TypeScript validates and consumes Python-emitted fixtures without field translation.
- [ ] **AC-11:** Both integration examples exercise normal progress, stagnation, bounded recovery, cooling, and safe stop.
- [ ] **AC-12:** The Agent Skill has recorded baseline failures, post-skill pressure results, counterexamples, and a passing sanitized rubric.
- [ ] **AC-13:** The live campaign runner enforces per-run and campaign caps and records exact stack/model/version metadata.
- [ ] **AC-14:** Article tables and claims can be regenerated from committed redacted result artifacts.
- [ ] **AC-15:** The article bundle passes frontmatter, citation, code-snippet, diagram, cover, asset-provenance, and private-marker checks.
- [ ] **AC-16:** A clean public-history scan finds no credentials, absolute local paths, `.hermes/` artifacts, private context, or raw hidden reasoning.
- [ ] **AC-17:** README describes semantic reheating as a metaphor and does not claim that prompts alter decoder temperature.
- [ ] **AC-18:** The GitHub repository is public at `https://github.com/forcewake/semantic-reheating`, its default branch matches the verified local commit, and remote files are read back before success is claimed.

## 18. Implementation sequencing

The implementation plan will use vertical, reviewable slices:

1. repository bootstrap and closed contract tests;
2. core models, canonicalization, and validation;
3. deterministic detectors and progress model;
4. controller, diagnosis, recovery policies, evidence records, and CLI;
5. balanced trace corpus and replay benchmark;
6. Python and TypeScript integration examples;
7. pressure-tested Agent Skill and prompt assets;
8. bounded live evaluation harness and campaign;
9. article, diagrams, cover, and citation ledger;
10. independent spec, quality/security, and publication reviews;
11. clean-environment reproduction, public repository creation, push, and remote readback.

No slice may broaden the controller into an autonomous executor without a new design review.

## 19. Risks and mitigations

| Risk | Mitigation |
|---|---|
| “Semantic reheating” is mistaken for real decoding-temperature control | Put the distinction in README, schemas, article opening, and tests for status language. |
| False positives on legitimate repeated work | Require repetition plus no-progress agreement and include balanced productive controls. |
| Semantic detector adds cost or judge bias | Optional, disabled by default, separately metered, unable to relax hard limits. |
| Recovery consumes more than a restart | Measure cost per accepted outcome and include restart as a baseline/policy. |
| Trace contracts leak secrets | Support digests/refs, redact before persistence, synthetic public fixtures, secret scans. |
| Nested retries escape the budget | Count retries, handoffs, subagents, callbacks, and re-entry in one run envelope. |
| Framework churn makes adapters stale | Ship only generic Python and TypeScript examples in v1. |
| Small live sample invites broad claims | Publish exact matrix, caps, missing cells, and bounded conclusions. |
| Skill looks persuasive but changes no behavior | Require failing baselines and pressure-tested post-skill evidence. |
| Article outruns implementation | Generate examples and result tables from committed artifacts and validate citations strictly. |

## 20. Approved decisions

- Product shape: **Semantic Reheating Reference Kit**.
- Core: **language-neutral contracts + Python engine/CLI + TypeScript integration example**.
- Audience: **agent/framework engineers building production runtimes**.
- Publication: **public `forcewake` repository and publish-ready article bundle**.
- Evaluation: **deterministic benchmark plus bounded live A/B on two available stacks**.
- Architecture: **observer/controller beside the agent; host retains authority**.
- Trigger: **repetition and no-progress signals must agree**.
- Editorial direction: **Systems Paper**.
- Public title: **Semantic Reheating for LLM Agents**.

## Sources

[1] https://doi.org/10.1126/science.220.4598.671 — Optimization by Simulated Annealing
[2] https://doi.org/10.1023/A:1013689704352 — Finite-time Analysis of the Multiarmed Bandit Problem
[3] https://arxiv.org/abs/2305.10601 — Tree of Thoughts
[4] https://arxiv.org/abs/2303.17651 — Self-Refine
[5] https://arxiv.org/abs/2303.11366 — Reflexion
[6] https://arxiv.org/abs/2310.04406 — Language Agent Tree Search
[7] https://arxiv.org/abs/2309.03409 — Large Language Models as Optimizers
[8] https://arxiv.org/abs/2507.19457 — GEPA
[9] https://github.com/microsoft/autogen/blob/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one/_magentic_one_orchestrator.py — Magentic-One Orchestrator source
[10] https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts — Gemini CLI LoopDetectionService source
[11] https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/core/client.ts — Gemini CLI recovery source
[12] https://arxiv.org/html/2607.01641v1 — Infinite Agentic Loops
[13] https://arxiv.org/html/2603.27415 — Greedy Is a Strong Default
[14] https://doi.org/10.1016%2F0020-0190%2893%2990029-9 — Optimal Speedup of Las Vegas Algorithms
