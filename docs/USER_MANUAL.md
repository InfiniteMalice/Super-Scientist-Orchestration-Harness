# Super Scientist Orchestration Harness User Manual

This manual is STE-aligned. It serves human operators and Large Language Model (LLM) agents.

## Manual Map

| Section ID | Section | Purpose | Capability status |
| --- | --- | --- | --- |
| `MAN-01` | Document Control | Identify the controlled manual baseline. | Implemented |
| `MAN-02` | Safety and Authority Summary | Define authority and safety limits. | Implemented |
| `MAN-03` | System Overview | Explain the transaction flow. | Implemented |
| `MAN-04` | Installation and Initial Setup | Install and initialize the local harness. | Implemented |
| `MAN-05` | LLM and Human Roles | Define actor purposes and limits. | Mixed |
| `MAN-06` | Model Assignment Guidance | Select the least capable reliable actor. | Guidance |
| `MAN-07` | Core Kernel Admission Stages | Describe the coordinator flow. | Implemented |
| `MAN-08` | Hypothesis, Model, Checker, and Revision Loop | Describe eight durable hypothesis stages. | Implemented |
| `MAN-09` | Governed-Adaptation Workflow | Describe the 21-step deterministic example. | Example only |
| `MAN-10` | Behavioral-Rule Review Roles | Separate five reviewer questions. | Implemented |
| `MAN-11` | Troubleshooting Guide | Recover without bypassing governance. | Implemented guidance |
| `MAN-12` | Operational Examples | Run supported commands and examples. | Implemented |
| `MAN-13` | Security and Safe Operation | State actual security controls and residual risks. | Implemented |
| `MAN-14` | Glossary | Define approved project terms. | Reference |
| `MAN-15` | Source Map | Map manual sections to sources and tests. | Reference |
| `MAN-16` | Cognitive Cohorts and Procedure Compilation | Run the governed cognitive and procedure workflow. | Implemented with example-only actors |

## `MAN-01` — Document Control

| Item | Controlled value |
| --- | --- |
| Manual title | Super Scientist Orchestration Harness User Manual |
| Repository | `InfiniteMalice/Super-Scientist-Orchestration-Harness` |
| Repository commit | `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450` |
| Package version | `0.3.0` |
| Manual status | STE-aligned manual for the named commit |
| Python | Python 3.12 or newer |
| Operating systems | Windows PowerShell and POSIX shells are documented. SQLite and local filesystem behavior apply. |
| Intended audience | Operators, researchers, developers, reviewers, and LLM agents |
| Scope | Local transactional admission, governed cognitive evidence, procedure compilation, durable records, read-only inspection, examples, audit, workspace exchange, and development quality checks |
| Out of scope | Live providers, live peer or model execution, live experiments, arbitrary execution, reinforcement learning, automatic promotion, and scientific-truth certification |

“STE-aligned” means the manual applies ASD-STE100 Issue 9 principles.

The project did not validate the complete controlled dictionary or every applicable ASD-STE100 rule.

### Capability status terms

| Status | Meaning |
| --- | --- |
| Implemented | Current code and tests implement the capability. |
| Example only | Deterministic synthetic data demonstrates the capability. |
| Interface only | Typed contracts exist, but no operational provider exists. |
| Experimental | The capability can change and has no production authority. |
| Deferred | Project documents describe future work. |
| Prohibited | The security boundary intentionally prevents the capability. |

### Controlled release facts

`are_independent()` rejects actors that share any declared operational identity dimension.

Operational diversity does not satisfy reviewer independence.

`quality.runner.CHECKS` contains exactly nine checks: `format`, `lint`, `types`,
`tests`, `security`, `dependencies`, `build`, `package`, and `wheel-install`.

## `MAN-02` — Safety and Authority Summary

> **WARNING:** Admission does not prove scientific truth. Admission proves compliance with recorded constraints and inputs.
>
> **WARNING:** A proposal is untrusted input. A proposal is not committed state.
>
> **CAUTION:** If audit integrity fails, stop the affected operation. Do not bypass the integrity check.

The harness owns admission and committed state.

Models cannot approve their own proposals.

Configuration aliases do not establish reviewer independence.

Learned judges do not have formal-verification authority.

Metadata-only model artifacts cannot execute.

Only registered deterministic simulators can execute in the current hypothesis slice.

Human authority is mandatory when the active policy requires human approval.

The repository contains no live model provider or autonomous model-execution loop.

### Authority types

- **Proposal authority:** An actor can submit a typed candidate record.
- **Admission authority:** Deterministic handlers accept or reject proposals under the active policy.
- **Human authority:** A typed human approval satisfies gates that explicitly require a human.
- **Model authority:** A model can propose or assess. A model cannot commit or self-approve.
- **Tool authority:** A deterministic tool can calculate or validate within a fixed capability.
- **Service authority:** The coordinator can persist one atomic decision and its projections.
- **Audit authority:** The verifier detects inconsistency. The verifier cannot create scientific truth.
- **Rollback authority:** A recorded rollback target limits recovery. A target does not erase history.
- **Evidence authority:** Verified bytes and exact spans provide grounding. Evidence does not prove a proposition.
- **Protected evaluation authority:** Role-specific capabilities separate answers, results, validation, and integrity audit.

| Actor or component | Can propose | Can validate | Can approve | Can commit state | Can execute code |
| --- | --- | --- | --- | --- | --- |
| Human actor | Yes | Yes, through a recorded role | Yes, when independent | No | Outside the research runtime only |
| Model actor | Yes | Yes, as a non-authoritative or typed reviewer | Limited; never self or human-required approval | No | No |
| Deterministic tool | Yes, through typed input | Yes, within its fixed check | Only when policy permits that actor type | No | Fixed calculation only |
| Service actor | Yes, through typed input | Yes, within a fixed capability | Only when policy permits that actor type | No | Fixed service logic only |
| `TransactionCoordinator` | No | Yes | No | Yes | Only fixed handler and simulator paths |
| Audit verifier | No | Yes | No | No | Deterministic verification only |
| `FileArtifactStore` | No | Hash and path checks only | No | Artifact bytes only | No |

Actor identity metadata is not authentication.

Operators must administer actor identities truthfully.

## `MAN-03` — System Overview

The control plane owns policy, admission, transaction, audit, storage, artifact,
integrity, workspace-exchange, progress-head, and protected-evaluator authority. The
cognitive plane produces typed evidence and pure analyses. A cognitive record cannot
change a claim head, policy, harness head, progress head, model weight, tool permission,
or protected answer. Only a separate fixed control-plane handler can perform an
authorized transition.

1. A human, model, tool, or service creates a proposal.
2. The coordinator treats the proposal as untrusted.
3. The coordinator validates identity and policy.
4. The coordinator performs deterministic admission checks.
5. The coordinator accepts or rejects the proposal.
6. The coordinator records the decision.
7. The coordinator updates projections only after acceptance.
8. The coordinator appends a tamper-evident audit event.
9. The audit verifier reconciles the workspace.

```text
[Human / model / tool / service]
              |
              v
   UNTRUSTED TYPED PROPOSAL
   cognitive plane may supply evidence and pure analysis
              |
              v
  +-----------------------------+
  | CONTROL PLANE               |
  | integrity -> replay ->      |
  | schema -> policy -> identity|
  | -> domain gates             |
  +-----------------------------+
       | accept        | reject
       v               v
  DURABLE RECORDS   DURABLE REJECTION
       |               |
       +-------+-------+
               v
       TAMPER-EVIDENT AUDIT

Human authority enters only at explicit approval gates.
PROHIBITED: record -> shell, network, dynamic import, eval, exec, or live model provider.
```

`CognitiveOrchestrationService` and `ResearchCoordinator` are sealed and stateless.
Each operation receives the exact `TransactionCoordinator` only as a call-local
argument. Neither facade retains storage, artifact, execution, or protected authority.

The SQLite transaction uses `BEGIN IMMEDIATE`.

Accepted projections, the transaction decision, and the audit event commit or roll back together.

Exact replay returns the stored decision without another mutation or audit event.

## `MAN-04` — Installation and Initial Setup

### Create and activate an environment

| Command | Purpose | Expected result | Common failure | Corrective action |
| --- | --- | --- | --- | --- |
| `python --version` | Check Python. | Version 3.12 or newer appears. | Python is unavailable. | Install Python 3.12 or newer. |
| `python -m venv .venv` | Create an isolated environment. | `.venv` exists. | The `venv` module is unavailable. | Install a complete Python distribution. |
| `.\.venv\Scripts\Activate.ps1` | Activate in PowerShell. | The prompt uses `.venv`. | Script execution is blocked. | Apply an approved PowerShell execution policy for the process. |
| `source .venv/bin/activate` | Activate in a POSIX shell. | The prompt uses `.venv`. | The path does not exist. | Create the environment with the same shell user. |

### Install and verify

| Command | Purpose | Expected result | Common failure | Corrective action |
| --- | --- | --- | --- | --- |
| `python -m pip install .` | Install core runtime dependencies. | Pip installs package version `0.3.0`. | Package index access fails. | Restore approved index access and retry. |
| `python -m pip install -e ".[dev]"` | Install development checks. | Pip installs the editable package and tools. | A build dependency is unavailable. | Preserve the pip log and restore index access. |
| `scientist-harness --help` | Verify the CLI entry point. | The command list appears. | The command is not found. | Activate the environment and reinstall the package. |
| `scientist-harness init --root .kernel --json` | Initialize an empty workspace. | JSON reports success. | The target contains orphaned or incompatible state. | Preserve the target and choose a new empty root. |
| `scientist-harness audit verify --root .kernel --json` | Verify the empty workspace. | `valid` is `true`; `checked_events` is `0`. | Integrity verification fails. | Preserve the database, artifacts, and output. Investigate before mutation. |

Core installation requires no model Software Development Kit (SDK), Graphics Processing Unit (GPU), or paid Application Programming Interface (API).

Installation and dependency audit can access the configured package index.

## `MAN-05` — LLM and Human Roles

Use one field order for every role. A role name describes responsibility, not a new authority source.

### `ROLE-RESEARCH-PROPOSER` — Research proposer

**Capability status:** Implemented contract; model operation is interface only.

**Purpose:** Create typed research, claim, hypothesis, or adaptation proposals.

**Recommended actor type:** Human, LLM, or combined workflow.

**Suggested model type:** Frontier scientific-reasoning model for open scientific proposals.

**Required capabilities:** Structured output, scientific reasoning, uncertainty reporting, schema compliance, and low hallucination rate.

**Authority:** The role can propose. The role cannot admit, commit, or self-approve.

**Independence requirement:** An approver must share no declared identity dimension with the proposer.

**Inputs:** Governing policy, evidence identifiers, schemas, and prior receipts.

**Outputs:** A typed `Proposal` or `ProposalAttempt`.

**Common failures:** Invalid schema, wrong policy hash, unsupported claims, or self-approval.

**Resolution:** Correct the proposal. Use an independent approver. Submit a new stable intent when content changes.

**Unsuitable model types:** Models with weak schema control, hidden tool use, or high unsupported-claim rates.

**Source references:** `ProposalBase`, `ProposalAttempt`, `ProposalKind`, `TransactionCoordinator.submit_intent()`; `tests/unit/admission/test_engine.py`.

#### `ROLE-EVIDENCE-COLLECTOR` — Evidence collector

**Capability status:** Implemented for explicit local files.

**Purpose:** Place source bytes in the content-addressed artifact store.

**Recommended actor type:** Human or deterministic tool.

**Suggested model type:** No LLM required.

**Required capabilities:** Tool-use discipline, provenance retention, and exact byte handling.

**Authority:** The role can submit unverified evidence. Only `KernelService` can project `HASH_VERIFIED` evidence.

**Independence requirement:** The evidence ingestion actor must equal the proposal actor.

**Inputs:** Local file, source locator, and media type.

**Outputs:** `ArtifactRef`, `EvidenceRecord`, decision, receipt, and audit event.

**Common failures:** Missing file, hash mismatch, unsafe path, or wrong ingestion actor.

**Resolution:** Preserve the file. Recompute the artifact through `evidence add`. Do not edit stored digest paths.

**Unsuitable model types:** Models that summarize bytes instead of retaining the source.

**Source references:** `EvidenceRecord`, `FileArtifactStore`, `verify_artifact_binding()`, `evidence_add()`; artifact and kernel-service tests.

#### `ROLE-EVIDENCE-EXTRACTOR` — Evidence extractor

**Capability status:** Interface only.

**Purpose:** Produce an exact `EvidenceSpan` from retained source bytes.

**Recommended actor type:** Small local extraction model with deterministic verification.

**Suggested model type:** Small local extraction model.

**Required capabilities:** Citation grounding, exact offsets, structured output, and low hallucination rate.

**Authority:** The role can propose text and offsets. The role cannot verify the source hash.

**Independence requirement:** No separate actor is required for extraction. Later authority checks remain separate.

**Inputs:** Artifact bytes and media type.

**Outputs:** Proposed `EvidenceSpan` inside an `EvidenceRecord`.

**Common failures:** Offset mismatch, decoded-text mismatch, or invented text.

**Resolution:** Extract from retained bytes again. Verify `end - start == len(text)`.

**Unsuitable model types:** Generative models that paraphrase or normalize source text.

**Source references:** `EvidenceSpan`, `EvidenceRecord`; `tests/unit/evidence/test_models.py`.

#### `ROLE-EVIDENCE-TRAIL-CONSTRUCTOR` — Evidence-trail constructor

**Capability status:** Implemented contracts and service; synthetic example only for model work.

**Purpose:** Build source-first nodes, relations, checks, assessments, and sentence bindings.

**Recommended actor type:** Long-context evidence model with deterministic tools.

**Suggested model type:** Long-context evidence model.

**Required capabilities:** Evidence-span grounding, long context, uncertainty reporting, and schema compliance.

**Authority:** The role can propose trail records. Trail coherence cannot prove a claim.

**Independence requirement:** Assessors and required human approvers must be independent from builders and relevant authors.

**Inputs:** Accepted evidence receipts, claim receipts, exact spans, and active policy.

**Outputs:** `EvidenceTrailNode`, `EvidenceTrailRelation`, `EvidenceTrailVersion`, and `ReportSentenceBinding` proposals.

**Common failures:** Receipt mismatch, invalid span, missing category, or correlated assessor.

**Resolution:** Rebuild from accepted receipts. Run deterministic trail validation. Preserve dissent.

**Unsuitable model types:** Models without long-context grounding or exact citation support.

**Source references:** `domain/evidence_trails`, `TrailService`, trail handlers; trail authority and integration tests.

#### `ROLE-HYPOTHESIS-PROPOSER` — Hypothesis proposer

**Capability status:** Implemented contract; synthetic example only.

**Purpose:** Propose a versioned, falsifiable `HypothesisSpec`.

**Recommended actor type:** Human and high-diversity hypothesis model.

**Suggested model type:** High-diversity hypothesis model.

**Required capabilities:** Scientific reasoning, uncertainty reporting, counterexample awareness, and schema compliance.

**Authority:** The role can propose a hypothesis version. The role cannot advance `HypothesisHead`.

**Independence requirement:** Every hypothesis mutation requires independent human approval under the current V2 policy.

**Inputs:** Hash-verified evidence, assumptions, variables, predictions, falsification conditions, and policy hash.

**Outputs:** `ProposeHypothesisVersion` and an accepted receipt.

**Common failures:** Missing evidence, wrong actor, non-first version, or invalid chronology.

**Resolution:** Bind exact evidence and actor identity. Use `ReviseHypothesis` for successors.

**Unsuitable model types:** Models that cannot state falsification conditions or uncertainty.

**Source references:** `HypothesisSpec`, `ProposeHypothesisVersionHandler`; hypothesis service tests.

#### `ROLE-MODEL-DESIGNER` — Scientific model designer

**Capability status:** Interface only, except fixed simulator metadata.

**Purpose:** Describe a model without adding execution authority.

**Recommended actor type:** Human expert with a code and formal-reasoning model.

**Suggested model type:** Code and formal-reasoning model.

**Required capabilities:** Mathematical reasoning, schema compliance, assumptions, bounds, and safe artifact handling.

**Authority:** The role can propose `ExecutableModelSpec`. Metadata cannot execute.

**Independence requirement:** The registrar must differ from the hypothesis proposer where stage rules require distinct roles.

**Inputs:** Hypothesis receipt, schemas, resource bounds, artifact metadata, or fixed simulator identifier.

**Outputs:** `RegisterExecutableModel`.

**Common failures:** Executable fields, unknown simulator, mixed artifact and built-in mode, or lineage mismatch.

**Resolution:** Remove executable configuration. Select an allowed execution mode and exact receipt.

**Unsuitable model types:** Agents that require shell, import, network, or generated-code execution.

**Source references:** `ExecutableModelSpec`, `ExecutionMode`; model-execution adversarial tests.

#### `ROLE-SIMULATOR-SELECTOR` — Simulator selector

**Capability status:** Implemented for two registered simulators.

**Purpose:** Select `thermal-chamber-v1` or `exponential-decay-v1` with strict inputs.

**Recommended actor type:** Deterministic tool or human.

**Suggested model type:** No LLM required.

**Required capabilities:** Schema compliance, exact calculation, and resource-bound selection.

**Authority:** The role can select a registered simulator. The role cannot register new executable code.

**Independence requirement:** Follow the stage actor separation recorded by the proposal.

**Inputs:** Model receipt, input schema, numeric values, seed, step limit, and state limit.

**Outputs:** Reproducible `SimulationResult`.

**Common failures:** Unknown identifier, nonnumeric input, bound mismatch, or non-reproducible output.

**Resolution:** Use the fixed registry. Recreate the result with exact retained inputs.

**Unsuitable model types:** Any model that substitutes estimated output for simulator output.

**Source references:** `simulators.py`, `RecordSimulationResultHandler`; simulator tests.

#### `ROLE-VERIFICATION-DESIGNER` — Verification-mechanism designer

**Capability status:** Implemented contract.

**Purpose:** Register a formal, deterministic, or learned verification mechanism.

**Recommended actor type:** Human expert or code and formal-reasoning model.

**Suggested model type:** Code and formal-reasoning model.

**Required capabilities:** Formal reasoning, schema compliance, provenance design, and uncertainty reporting.

**Authority:** The role can define metadata. The role cannot assign a stronger provenance category to a result.

**Independence requirement:** The creator must satisfy exact stage separation and identity rules.

**Inputs:** Hypothesis receipt, schemas, mechanism description, and specification hash.

**Outputs:** `VerificationMechanismSpec` and receipt.

**Common failures:** Mechanism/result discriminator mismatch or policy mismatch.

**Resolution:** Use one exact discriminator. Retain the accepted mechanism receipt.

**Unsuitable model types:** Models that confuse learned confidence with proof.

**Source references:** `FormalVerifierSpec`, `DeterministicCheckerSpec`, `LearnedJudgeSpec`; hypothesis tests.

#### `ROLE-DETERMINISTIC-CHECKER` — Deterministic checker

**Capability status:** Implemented.

**Purpose:** Recompute exact checks and counterexample-search results.

**Recommended actor type:** Deterministic tool.

**Suggested model type:** Deterministic checker or formal solver.

**Required capabilities:** Exact calculation, reproducibility, schema compliance, and retained search evidence.

**Authority:** The role can produce deterministic results. The role cannot admit a hypothesis.

**Independence requirement:** Provenance must be `INDEPENDENT_DETERMINISTIC_CHECK` and operationally independent.

**Inputs:** Candidate, mechanism, model, simulation, evidence, and accepted receipts.

**Outputs:** `DeterministicCheckResult`.

**Common failures:** Missing search evidence, wrong provenance, or mismatched lineage.

**Resolution:** Recompute from retained inputs. Record search evidence and exact receipts.

**Unsuitable model types:** Probabilistic judges used as exact calculators.

**Source references:** `DeterministicCheckResult`, `RecordVerificationResultHandler`; hypothesis service tests.

#### `ROLE-LEARNED-JUDGE` — Learned judge

**Capability status:** Interface only.

**Purpose:** Produce rubric-based, explicitly learned assessment records.

**Recommended actor type:** Independent evaluator model.

**Suggested model type:** Low-temperature classification model.

**Required capabilities:** Rubric fidelity, uncertainty reporting, schema compliance, and low hallucination rate.

**Authority:** The role cannot claim formal or deterministic provenance. The role cannot satisfy deterministic search gates.

**Independence requirement:** A second prompt to the same operational identity is not independent review.

**Inputs:** Rubric, candidate, evidence, and retained receipts.

**Outputs:** `LearnedJudgeResult`.

**Common failures:** Formal-authority claim, missing abstention, or correlated identity.

**Resolution:** Label learned provenance correctly. Use a deterministic checker for deterministic gates.

**Unsuitable model types:** Self-evaluating candidate models and models without calibrated abstention.

**Source references:** `LearnedJudgeResult`; hypothesis model and authority tests.

#### `ROLE-COUNTEREXAMPLE-SEARCHER` — Counterexample searcher

**Capability status:** Implemented record path; open-ended search is interface only.

**Purpose:** Search for falsifying inputs and retain negative results.

**Recommended actor type:** Adversarial falsification model with deterministic checker.

**Suggested model type:** Adversarial falsification model.

**Required capabilities:** Counterexample generation, tool discipline, scientific reasoning, and uncertainty reporting.

**Authority:** The role can propose `CounterexampleRecord`. A found counterexample blocks candidate admission.

**Independence requirement:** Search provenance must meet the registered mechanism requirements.

**Inputs:** Hypothesis, model, simulations, verification results, and evidence.

**Outputs:** Search result or `RecordCounterexample`.

**Common failures:** Unretained search, candidate mismatch, or a passing result with a found counterexample.

**Resolution:** Retain the failed verification chain. Revise the hypothesis before a new admission attempt.

**Unsuitable model types:** Models optimized only to confirm the candidate.

**Source references:** `CounterexampleRecord`, `RecordCounterexampleHandler`; counterexample admission tests.

#### `ROLE-PROGRESS-MONITOR` — Progress monitor

**Capability status:** Implemented.

**Purpose:** Separate provisional progress from independently validated progress.

**Recommended actor type:** Deterministic tool with independent validators.

**Suggested model type:** Low-temperature classification model for proposed status only.

**Required capabilities:** Dependency analysis, budget accounting, schema compliance, and uncertainty reporting.

**Authority:** The role can calculate progress. The role cannot declare final completion alone.

**Independence requirement:** A `VALIDATED` event requires an independent validator and retained evidence.

**Inputs:** `ProgressPlan`, events, budgets, checkpoints, and evidence.

**Outputs:** `ProgressSummary`, validation events, and false-finish findings.

**Common failures:** Dependency cycle, stale event, correlated validator, or unsupported completion.

**Resolution:** Repair the plan or event. Preserve earlier events. Run the final checklist again.

**Unsuitable model types:** Models that infer completion from activity or confidence.

**Source references:** `domain/progress`, `calculate_progress()`, `detect_false_finish()`; progress tests.

#### `ROLE-FINAL-VALIDATOR` — Final validator

**Capability status:** Implemented contract.

**Purpose:** Decide whether every completion gate passed.

**Recommended actor type:** Human expert or deterministic tool under human authority.

**Suggested model type:** Human expert.

**Required capabilities:** Evidence review, independence, schema compliance, and failure retention.

**Authority:** The role can validate completion. The role cannot bypass budget, evidence, or checklist gates.

**Independence requirement:** The validator must be independent from the run creator and completion proposer.

**Inputs:** Ordered checklist, final validation, progress, budget, and failure history.

**Outputs:** `CompletionDecision`.

**Common failures:** `FALSE_FINISH`, failed final validation, or incomplete checklist.

**Resolution:** Continue the run or record a non-success termination. Do not relabel provisional progress.

**Unsuitable model types:** The completion proposer or its configuration alias.

**Source references:** `CompletionDecision`, `CompletionChecklistStep`; false-finish tests.

#### `ROLE-RULE-REVIEWER` — Behavioral-rule reviewer family

**Capability status:** Implemented for five `ReviewerRole` values.

**Purpose:** Submit immutable semantic, conflict, abstraction, adversarial, or verification assessments.

**Recommended actor type:** Independent human, LLM, or deterministic checker by role.

**Suggested model type:** Independent evaluator model or adversarial falsification model.

**Required capabilities:** Schema compliance, dissent, uncertainty reporting, and role-specific analysis.

**Authority:** Reviewers can assess. Reviewers cannot mutate a rule head or form the canonical candidate diff.

**Independence requirement:** Each reviewer and the integrator must share no declared identity dimension.

**Inputs:** Incidents, rule versions, evidence, and review proposal.

**Outputs:** `ReviewerAssessment`.

**Common failures:** Missing reviewer role, correlated identity, or canonical-state mutation attempt.

**Resolution:** Import five independent assessments. Route consolidation through the integrator.

**Unsuitable model types:** The proposal author, integrator alias, or one model reused as independent reviewers.

**Source references:** `ReviewerRole`, `ReviewerAssessment`, rule capabilities; `test_reviewer_authority.py`.

#### `ROLE-RULE-INTEGRATOR` — Rule integrator

**Capability status:** Implemented.

**Purpose:** Consume five assessments and propose one governed canonical rule change.

**Recommended actor type:** Human expert or combined workflow.

**Suggested model type:** Frontier scientific-reasoning model with human authority.

**Required capabilities:** Conflict analysis, synthesis, schema compliance, and dissent preservation.

**Authority:** The role can form the candidate diff. Admission handlers alone update the rule head.

**Independence requirement:** The integrator must be independent from every reviewer.

**Inputs:** Five assessments, incidents, measurement, evaluator audit, rollback rule, and regression cases.

**Outputs:** `ConsolidationProposal`, dispositions, findings, dissent, and regression cases.

**Common failures:** Missing assessment, unresolved conflict, duplicate rule, or missing rollback.

**Resolution:** Preserve every assessment disposition. Add a separating-boundary test for conflicts.

**Unsuitable model types:** A reviewer reused as integrator or a model that discards dissent.

**Source references:** `ConsolidationProposal`, rule service and handlers; rule integration tests.

#### `ROLE-HARNESS-EVALUATOR` — Harness evaluator

**Capability status:** Implemented contracts and deterministic fixtures.

**Purpose:** Compare harness variants with matched budgets and separated partitions.

**Recommended actor type:** Deterministic service with an independent evaluator.

**Suggested model type:** Independent evaluator model only for non-deterministic assessments.

**Required capabilities:** Exact metrics, budget accounting, confound reporting, and protected-data discipline.

**Authority:** The role can produce reports. Discovery gain alone cannot authorize admission.

**Independence requirement:** Evaluation, audit, candidate production, and decision authority must meet recorded separation rules.

**Inputs:** Campaign, iterations, protected results, confounds, budgets, audit, measurement, and rollback.

**Outputs:** `HarnessCampaignReport` and `HarnessDecision`.

**Common failures:** `UNMATCHED_BUDGETS`, regression, confound, leakage, or benchmark-specific gain.

**Resolution:** Match budgets. Preserve negative results. Run held-out transfer and independent audit.

**Unsuitable model types:** Candidate models that score their own output or access protected answers.

**Source references:** `domain/harness_eval`, harness capabilities and service; harness and leakage tests.

#### `ROLE-EVALUATOR-AUDITOR` — Evaluator auditor

**Capability status:** Implemented contract.

**Purpose:** Audit evaluator independence, inputs, results, and authority boundaries.

**Recommended actor type:** Human expert or independent deterministic service.

**Suggested model type:** Independent evaluator model with human review.

**Required capabilities:** Audit reasoning, provenance review, schema compliance, and uncertainty reporting.

**Authority:** The role can pass or fail an evaluator audit. The role cannot promote a candidate alone.

**Independence requirement:** The auditor must be independent from evaluator, proposer, and candidate producer.

**Inputs:** Evaluator version, candidate, protected or external results, relationships, and evidence.

**Outputs:** `EvaluatorAuditRecord`.

**Common failures:** Circular approval, missing external evaluation, or correlated identity.

**Resolution:** Assign an independent auditor. Preserve the failed audit. Run required external checks.

**Unsuitable model types:** The evaluated model, proposer, or shared operational alias.

**Source references:** `EvaluatorAuditRecord`; evaluator and governance tests.

#### `ROLE-GOVERNANCE-PROPOSER` — Governance proposer

**Capability status:** Implemented.

**Purpose:** Propose a policy transition under the prior active policy.

**Recommended actor type:** Human-led combined workflow.

**Suggested model type:** Frontier scientific-reasoning model with human expert review.

**Required capabilities:** Policy analysis, complete measurement review, rollback design, and schema compliance.

**Authority:** The role can propose. A candidate policy cannot authorize its own activation.

**Independence requirement:** The transition requires independent audit and human approval.

**Inputs:** Prior policy, candidate policy, dedicated run, audit, measurement, classification, and rollback policy.

**Outputs:** `ProposeGovernancePolicyTransition`.

**Common failures:** Policy mismatch, incomplete measurement, circular authority, or missing rollback.

**Resolution:** Evaluate under the retained prior policy. Supply exact accepted support and rollback binding.

**Unsuitable model types:** Autonomous policy writers with self-approval or hidden changes.

**Source references:** governance handler, `ProposeGovernancePolicyTransition`; governance transition tests.

#### `ROLE-HUMAN-APPROVER` — Human approver

**Capability status:** Implemented identity and policy contract; real review remains external.

**Purpose:** Supply mandatory human authority after reviewing retained inputs.

**Recommended actor type:** Human expert.

**Suggested model type:** Human expert.

**Required capabilities:** Domain judgment, evidence review, authority awareness, and rollback review.

**Authority:** The role can approve only within policy. Approval does not prove truth or commit state.

**Independence requirement:** The human must be independent under `are_independent()`.

**Inputs:** Complete proposal, evidence, checks, audit, policy, and rollback target.

**Outputs:** `Approval` or domain decision authority record.

**Common failures:** Self-approval, asserted but unverified identity, or incomplete review material.

**Resolution:** Preserve the rejection. Assign a verified independent human. Review all retained records.

**Unsuitable model types:** No model can replace a required human actor.

**Source references:** `Approval`, `ActorIdentity`, stage authority validators; governance and hypothesis tests.

#### `ROLE-TRANSACTION-COORDINATOR` — Transaction coordinator

**Capability status:** Implemented.

**Purpose:** Own deterministic admission, atomic persistence, projections, and audit append.

**Recommended actor type:** Service.

**Suggested model type:** No LLM required.

**Required capabilities:** Determinism, idempotency, policy enforcement, and transactional storage.

**Authority:** The coordinator can commit accepted state. The coordinator cannot invent scientific authority.

**Independence requirement:** Not applicable. The coordinator enforces actor independence.

**Inputs:** `Proposal` or `ProposalAttempt`, policy, repositories, clock, and artifact store.

**Outputs:** `TransactionDecision`, records, projections, and `AuditEvent`.

**Common failures:** Integrity error, invalid proposal, policy mismatch, or idempotency conflict.

**Resolution:** Preserve the workspace. Correct untrusted input. Never bypass the integrity precheck.

**Unsuitable model types:** Any LLM used as an admission or persistence engine.

**Source references:** `TransactionCoordinator`, `ProposalRouter`; coordinator and replay tests.

#### `ROLE-AUDIT-VERIFIER` — Audit verifier

**Capability status:** Implemented.

**Purpose:** Reconcile policies, transactions, projections, history, artifacts, and audit hashes.

**Recommended actor type:** Deterministic service.

**Suggested model type:** No LLM required.

**Required capabilities:** Hash validation, schema validation, replay, and exact comparison.

**Authority:** The verifier can fail closed. The verifier cannot repair or approve state.

**Independence requirement:** The verifier uses source-controlled logic and stored authority anchors.

**Inputs:** SQLite workspace and artifact root.

**Outputs:** `WorkspaceIntegrityResult` or an integrity error.

**Common failures:** Broken chain, missing row, altered projection, missing bytes, or orphaned governance.

**Resolution:** Preserve all files. Restore from a verified source or investigate the first failing invariant.

**Unsuitable model types:** LLMs used for hash, replay, or exact integrity decisions.

**Source references:** `verify_workspace()`, audit chain, `audit_verify()`; workspace-integrity and audit tests.

#### `ROLE-ARTIFACT-STORE` — Artifact store

**Capability status:** Implemented local service.

**Purpose:** Store immutable bytes by SHA-256 digest under a contained root.

**Recommended actor type:** Service.

**Suggested model type:** No LLM required.

**Required capabilities:** Hashing, path containment, atomic file handling, and byte verification.

**Authority:** The store can retain and verify bytes. The store cannot interpret evidence or execute artifacts.

**Independence requirement:** Not applicable.

**Inputs:** Bytes and media type.

**Outputs:** `ArtifactRef` and content-addressed file.

**Common failures:** Traversal, symlink, Windows reparse point, digest mismatch, or non-regular file.

**Resolution:** Use a private local root. Restore exact bytes through the store API.

**Unsuitable model types:** Any model used instead of deterministic byte and path checks.

**Source references:** `FileArtifactStore`; artifact and Windows reparse-point tests.

#### `ROLE-RESEARCH-COORDINATOR` — Research Coordinator

**Capability status:** Implemented stateless sequencing service; live research planning
is interface only.

**Purpose:** Submit one declared tuple of governed proposals and stop after the first
rejection.

**Recommended actor type:** Human researcher using the fixed service.

**Suggested model type:** No LLM is required for sequencing. A scientific-reasoning
model may prepare proposals outside the service authority boundary.

**Required capabilities:** Typed proposal construction, stable identifiers, policy
awareness, and interpretation of fixed transaction decisions.

**Authority:** The role can arrange submissions. The role cannot retain the coordinator,
admit a proposal, access storage, execute a method, or read protected answers.

**Independence requirement:** The role grants no reviewer independence. Each submitted
proposal must satisfy its own actor-independence rule.

**Inputs:** Exact `CognitiveOrchestrationService`, exact `TransactionCoordinator`, and an
exact tuple of typed proposals.

**Outputs:** An ordered prefix of `TransactionDecision` records ending at the first
rejection or at the declared tuple boundary.

**Common failures:** Wrong facade type, wrong coordinator type, non-tuple input, malformed
proposal, or a rejected stage.

**Resolution:** Preserve the decisions. Correct the rejected proposal and use a new
stable identity when its content changes. Do not retry with weaker constraints.

**Unsuitable model types:** Autonomous agents that require retained storage, provider,
tool-execution, or policy authority.

**Source references:** `src/super_scientist/application/cognitive/service.py`:
`ResearchCoordinator.run_declared_slice()` and `CognitiveOrchestrationService.submit()`;
`tests/integration/application/test_cognitive_service.py`.

#### `ROLE-CAPABILITY-GROUNDER` — Capability Grounder

**Capability status:** Implemented pure grounding and governed persistence; live model
grounding is interface only.

**Purpose:** Classify each declared capability as verified, self-reported, unknown, or
unsupported from exact accepted evidence.

**Recommended actor type:** Deterministic service with human-curated evidence.

**Suggested model type:** No LLM is required for grounding. A bounded extraction model
may propose assertions before deterministic validation.

**Required capabilities:** Exact receipt resolution, evidence-status classification,
task-conditioned requirement matching, and canonical structured output.

**Authority:** The role can propose a `CapabilityProfile`. A profile is evidence only and
cannot authorize review, claim transition, tool access, or procedure binding.

**Independence requirement:** Capability evidence does not establish actor independence.
Later reviewer gates compare operational identities separately.

**Inputs:** `CapabilityRequirement` values, capability assertions, exact accepted
evidence receipts, task identity, actor identity, and active policy hash.

**Outputs:** `CapabilityProfile`, `CapabilityAssessment`, coverage, exclusions, and an
accepted transaction receipt when admission succeeds.

**Common failures:** Self-report presented as verification, unknown capability,
unsupported requirement, stale receipt, or hash mismatch.

**Resolution:** Retain the fail-closed status. Add accepted source evidence or select a
different actor. Never relabel self-reported evidence as verified.

**Unsuitable model types:** Models that infer capability from reputation, fluent output,
or unretained assertions.

**Source references:** `src/super_scientist/domain/cognition/grounding.py`:
`assess_capability()`; `src/super_scientist/application/cognition/service.py`:
`RecordCapabilityProfileHandler`; `tests/unit/cognition` and
`tests/integration/application/test_cognitive_service.py`.

#### `ROLE-PEER-REASONER` — Peer Reasoner

**Capability status:** Implemented collaboration contracts and persistence; fixed peers
are example only and live peer adapters are interface only.

**Purpose:** Produce bounded requests, observable contributions, topology proposals, and
termination evidence within one collaboration session.

**Recommended actor type:** Human or model actor selected through a grounded cohort.

**Suggested model type:** A task-appropriate reasoning model with strict structured
output and bounded context.

**Required capabilities:** Session and receipt binding, bounded contribution production,
uncertainty reporting, topology discipline, and termination-code compliance.

**Authority:** The role can append collaboration evidence. Peer majority or unanimity
cannot transition a claim, change policy, bind a procedure, or promote a candidate.

**Independence requirement:** Same-provider, model, adapter, or operational aliases are
not independent reviewers even when prompts or contributions differ.

**Inputs:** Accepted cohort receipt, collaboration session, peer request, declared
budget, prior topology, and accepted evidence references.

**Outputs:** `PeerRequest`, `PeerContribution`, `TopologyEvent`, or
`CollaborationTermination` records.

**Common failures:** Undeclared peer, stale session, recursive delegation, routing loop,
budget exhaustion, topology monopoly, or fabricated evidence reference.

**Resolution:** Preserve the rejected or terminated history. Use a declared peer and a
new bounded request. Do not convert consensus into authority.

**Unsuitable model types:** Unbounded autonomous agents, agents that delegate
recursively, or correlated models presented as independent reviewers.

**Source references:** `src/super_scientist/domain/collaboration/models.py`;
`src/super_scientist/application/collaboration/service.py`:
`AppendPeerContributionHandler` and `AppendTopologyEventHandler`;
`tests/integration/application/test_collaboration_service.py` and
`tests/adversarial/test_cognitive_authority.py`.

#### `ROLE-PROCEDURE-COMPILER` — Procedure Compiler

**Capability status:** Implemented deterministic compiler and governed persistence;
candidate-method authorship is interface only.

**Purpose:** Compile one candidate method from declared current evidence into an
immutable procedure result with explicit findings.

**Recommended actor type:** Deterministic service consuming a human- or model-authored
candidate method.

**Suggested model type:** A scientific planning model may draft `CandidateMethod`; no
LLM performs compilation or admission.

**Required capabilities:** Exact receipt resolution, directed-acyclic-graph validation,
artifact-flow analysis, catalog matching, resource bounds, termination rules, and
canonical plan mapping.

**Authority:** The role can create an accepted compilation record with domain status
`VALID` or `INVALID`. The role cannot execute steps or create a progress plan directly.

**Independence requirement:** Compilation does not satisfy reviewer or human approval.
Validator and binding requirements remain separate.

**Inputs:** Candidate method plus exact current capability-profile, artifact-catalog,
tool-catalog, validator-catalog, and procedure-source-snapshot receipts.

**Outputs:** `ProcedureCompilationRecord`, `ExecutableProcedure`, validation report,
findings, terminal outcomes, and canonical progress-plan mapping when valid.

**Common failures:** Invalid graph, missing artifact flow, unsupported capability,
undeclared tool or validator, resource excess, recursive delegation, forbidden
operation, or invalid termination rule.

**Resolution:** Retain the accepted `INVALID` compilation as history. Correct the
candidate or declared evidence and submit a new compilation. Bind only a `VALID` record.

**Unsuitable model types:** Agents that require Python imports, shell commands, dynamic
providers, arbitrary tools, hidden execution, or self-selected validators.

**Source references:** `src/super_scientist/domain/procedures/compiler.py`:
`compile_method()`; `src/super_scientist/application/procedures/service.py`:
`RecordProcedureCompilationHandler` and `BindCompiledProgressPlanHandler`;
`tests/unit/procedures` and `tests/integration/application/test_procedure_service.py`.

#### `ROLE-PROCEDURE-VALIDATOR` — Procedure Validator

**Capability status:** Implemented validation contracts; the registered deterministic
toy validator is example only. Arbitrary validator execution is prohibited.

**Purpose:** Recompute declared deterministic checks and produce bounded validation
findings without executing arbitrary artifact content.

**Recommended actor type:** Registered deterministic tool.

**Suggested model type:** No LLM required. Use a fixed checker or formal solver for the
declared validation operation.

**Required capabilities:** Exact artifact-byte reading, SHA-256 comparison, catalog
identity checks, bounded output, and typed pass/fail reporting.

**Authority:** The role can report a deterministic validation result. It cannot claim
human provenance, admit a compilation, execute the procedure, or bind progress.

**Independence requirement:** Automated checker provenance is `TOOL`, never `HUMAN`.
Policy-defined human or reviewer independence remains unsatisfied.

**Inputs:** Declared procedure artifacts, expected hashes, registered validator identity,
procedure step, and retained source receipts.

**Outputs:** A typed pass/fail validator outcome. The deterministic compiler records the
corresponding `ProcedureFinding` values in the compilation result.

**Common failures:** Tampered bytes, digest mismatch, unavailable artifact, undeclared
validator, hostile envelope, or fabricated success flag.

**Resolution:** Preserve the finding. Restore exact retained bytes or declare the correct
expected hash. Re-run the registered checker; do not override its result.

**Unsuitable model types:** Learned judges used as exact validators or agents that
execute artifact bytes, imports, commands, or provider calls.

**Source references:** `src/super_scientist/domain/procedures/compiler.py`:
`validate_procedure()`; `examples/governed_cognitive_procedure_vertical_slice.py`:
`DeterministicToyValidator`; `tests/e2e/test_governed_cognitive_procedure_vertical_slice.py`
and `tests/adversarial/test_procedure_escalation.py`.

#### `ROLE-COHORT-DIVERSITY-AUDITOR` — Cohort/Diversity Auditor

**Capability status:** Implemented pure analysis and governed persistence; operational
diversity diagnostics are experimental.

**Purpose:** Assess declared model, prompt, tool, evidence, method, topology, and error-
correlation differences without converting diversity into authority.

**Recommended actor type:** Deterministic service reviewed by a human researcher.

**Suggested model type:** No LLM required for the assessment. A model may summarize the
retained diagnostics without changing them.

**Required capabilities:** Exact cohort/profile receipt resolution, canonical axis
comparison, correlation classification, bounded cohort checks, and identity separation.

**Authority:** The role can record `DiversityAssessment`. The assessment cannot satisfy
reviewer independence or authorize a claim, policy, procedure, harness, or promotion.

**Independence requirement:** Operational diversity and reviewer independence are
separate checks. Shared operational identity fails independence even when diversity is
high.

**Inputs:** Accepted cohort and capability-profile receipts, diversity fingerprints,
declared axes, error-correlation evidence, and active policy hash.

**Outputs:** `DiversityAssessment`, axis statuses, correlation diagnostics, and explicit
gaps or exclusions.

**Common failures:** Same-model prompt variants counted as independent, missing profile,
stale cohort receipt, undeclared axis, or ambiguous correlation evidence.

**Resolution:** Keep the diagnostic result. Assign operationally independent reviewers
when authority requires independence. Do not change the diversity result to pass policy.

**Unsuitable model types:** The cohort members being audited or any model asked to infer
independence from style, agreement, or prompt variation.

**Source references:** `src/super_scientist/domain/cognition/diversity.py`:
`assess_diversity()`; `src/super_scientist/application/cognition/service.py`:
`RecordDiversityAssessmentHandler`; `tests/unit/cognition` and
`tests/adversarial/test_cognitive_authority.py`.

#### `ROLE-HARNESS-TRACE-RECORDER` — Harness Trace Recorder

**Capability status:** Implemented typed trace admission and persistence;
provider-native generation-metadata ingestion is interface only.

**Purpose:** Retain one exact observable execution trace and explicit metadata
availability state for a declared protocol cell.

**Recommended actor type:** Deterministic service at the harness boundary.

**Suggested model type:** No LLM required. The evaluated model supplies observable
output only and is not the recorder.

**Required capabilities:** Exact protocol/cell binding, artifact and verifier receipt
binding, canonical metadata capture, resource accounting, freshness checks, and strict
size bounds.

**Authority:** The role can propose trace evidence. A trace cannot grant claim, policy,
harness-head, progress-head, reward, tool, or protected-evaluator authority.

**Independence requirement:** Trace capture does not establish evaluator independence.
The matched protocol retains evaluator and audit requirements separately.

**Inputs:** Accepted protocol and cell identity, output artifact, verifier result,
environment and transformation events, tool observations, generation metadata, resource
usage, termination, reward observation, and exact provenance receipts.

**Outputs:** `HarnessExecutionTrace` and an accepted transaction receipt, or a fixed
rejection such as `UNMATCHED_EVALUATION`, `STALE_REFERENCE`, or `UNMATCHED_BUDGETS`.

**Common failures:** Cross-protocol evidence, stale receipt, fabricated value marked
`UNAVAILABLE`, context-hash mismatch, token or log-probability tampering, surplus
evidence, or caller mutation between admission stages.

**Resolution:** Reconstruct one trace from the exact accepted evidence chain. Use
`UNAVAILABLE` without a value when metadata is not exposed. Submit a new trace identity
when content changes.

**Unsuitable model types:** Passive secret-capture agents, hidden-reasoning collectors,
or evaluated models that self-certify trace provenance.

**Source references:** `src/super_scientist/domain/harness_eval/traces.py`:
`HarnessExecutionTrace` and `trace_freshness()`;
`src/super_scientist/application/harness_eval/extensions.py`:
`RecordHarnessExecutionTraceHandler`; `tests/integration/application/test_harness_eval_extensions.py`
and `tests/adversarial/test_trace_reward_tampering.py`.

## `MAN-06` — Model Assignment Guidance

| Role | Preferred model class | Context need | Creativity | Determinism | Tool use | Independent review required | Human approval required |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Evidence extraction | Small local extraction model | Source-local | Low | High | Span tools | For authority claims | No |
| Evidence-trail construction | Long-context evidence model | High | Low | Medium | Citation tools | Yes | Stage policy |
| Hypothesis proposal | High-diversity hypothesis model | High | High | Low | Evidence tools | Yes | Yes in current V2 stages |
| Model or verifier design | Code and formal-reasoning model | Medium | Medium | Medium | Schema tools | Yes | Yes in current V2 stages |
| Deterministic checks | Deterministic checker or formal solver | Bounded | None | Exact | Fixed tools | No | No |
| Learned judgment | Independent evaluator model | Medium | Low | Medium | Rubric tools | Yes | Cannot replace a human gate |
| Counterexample search | Adversarial falsification model | High | High | Medium | Fixed simulator and checker | Yes | Admission requires human authority |
| Progress classification | Low-temperature classification model | Medium | Low | High | Record tools | Yes for `VALIDATED` | Final policy dependent |
| Rule review | Independent evaluator model | Medium | Medium | Medium | Evidence tools | Yes | Consolidation policy dependent |
| Rule integration | Strong synthesis model and human | High | Medium | Medium | Diff and test tools | Five reviewers | Yes when policy requires |
| Harness evaluation | Deterministic service | High | None | Exact | Protected checker | Independent audit | Admission requires human authority |
| Research coordination | Human researcher with stateless service | Declared proposal tuple | Medium | Exact sequencing | Proposal submission only | Per proposal | Per active policy |
| Capability grounding | Deterministic service with curated evidence | Task and receipts | Low | High | Evidence resolution | Does not establish independence | No |
| Peer reasoning | Task-appropriate reasoning model | Bounded session | Medium | Medium | Declared collaboration tools only | Yes for authority | Per active policy |
| Procedure compilation | Deterministic compiler with authored candidate | Declared sources and catalogs | None in compiler | Exact | Registered catalog lookup | Separate validator | Binding policy only |
| Procedure validation | Registered deterministic tool | Declared artifacts | None | Exact | Bounded artifact hash checker | Automated result is not human review | No |
| Cohort/diversity audit | Deterministic service | Cohort and fingerprints | None | Exact | Receipt and identity comparison | Diversity never substitutes | Per authority gate |
| Harness trace recording | Deterministic service | One matched cell | None | Exact | Observable evidence capture | Does not establish evaluator independence | No |
| Audit and policy enforcement | Deterministic service | Full workspace | None | Exact | Fixed local tools | No | No LLM decision |

Use the least capable model that reliably satisfies the role.

Use smaller or local models for extraction, formatting, and bounded classification.

Use stronger reasoning models for hypotheses, scientific planning, and synthesis.

Prefer deterministic tools for hashes, schemas, audits, calculations, simulation, policy, idempotency, and formal checks.

Operational diversity does not satisfy reviewer independence.

A second prompt to the same model is not independent review when the operational
identity dimensions remain shared.

## `MAN-07` — Core Kernel Admission Stages

The stage order follows `TransactionCoordinator`, `AdmissionEngine`, and replay tests.

### `KERNEL-STAGE-01` — Verify workspace integrity

**Capability status:** Implemented.

**Goal:** Stop mutation when durable state or artifacts are inconsistent.

**Primary actor:** `ROLE-TRANSACTION-COORDINATOR`.

**Suggested model type:** No LLM required.

**Prerequisites:** Workspace paths and registered policy state.

**Inputs:** Repositories and artifact store.

**Procedure:**

1. Open one unit of work.
2. Run `require_workspace_integrity()`.
3. Stop if verification fails.

**Outputs:** Verified starting state or `StorageIntegrityError`.

**Acceptance conditions:** Policies, audit, transactions, projections, histories, receipts, and artifacts reconcile.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Integrity error before admission | Corrupt or orphaned state | Preserve the workspace and investigate | Run `audit verify` |

**Rollback or retry behavior:** No mutation occurs. Retry only after a verified recovery.

**Authority boundary:** Integrity success does not prove scientific truth.

**Source references:** `require_workspace_integrity()`; workspace-integrity tests.

#### `KERNEL-STAGE-02` — Resolve replay identity

**Capability status:** Implemented.

**Goal:** Return exact prior decisions and reject changed stable intents.

**Primary actor:** `ROLE-TRANSACTION-COORDINATOR`.

**Suggested model type:** No LLM required.

**Prerequisites:** A valid `idempotency_key` and, for intent mode, `ProposalAttempt`.

**Inputs:** Key, proposal hash, and service-owned intent fingerprint.

**Procedure:**

1. Look up the idempotency key.
2. Compare all trusted identity fields.
3. Return the prior decision or record `IDEMPOTENCY_CONFLICT`.

**Outputs:** Replayed decision or audited conflict.

**Acceptance conditions:** Every stored identity and hash matches exactly.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `IDEMPOTENCY_CONFLICT` | Content or replay mode changed | Use a new stable intent for changed content | Inspect `transaction list` |

**Rollback or retry behavior:** Exact replay adds no mutation or audit. Conflict history remains durable.

**Authority boundary:** Replay never readmits under a new policy.

**Source references:** `submit_intent()`, `_attempt_fingerprint()`; replay and idempotency tests.

#### `KERNEL-STAGE-03` — Normalize untrusted input

**Capability status:** Implemented.

**Goal:** Produce one strict discriminated proposal or a safe rejection.

**Primary actor:** `ROLE-TRANSACTION-COORDINATOR`.

**Suggested model type:** No LLM required.

**Prerequisites:** Recoverable proposal and idempotency identifiers for durable rejection.

**Inputs:** Direct object or intent-factory result.

**Procedure:**

1. Parse the strict proposal union.
2. Match the attempt envelope in intent mode.
3. Redact validation diagnostics.

**Outputs:** `Proposal`, `InvalidProposal`, or non-durable `INVALID_PROPOSAL` decision.

**Acceptance conditions:** Schema, discriminator, identifiers, proposer, and kind match exactly.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_PROPOSAL` | Malformed or extra fields | Correct input against the exact model | Retry with a new key if content changed |

**Rollback or retry behavior:** Expected parse failures become replayable when safe durable identities exist.

**Authority boundary:** The input cannot define its trusted fingerprint.

**Source references:** `_normalize_proposal()`, `ProposalAttempt`; strict parsing tests.

#### `KERNEL-STAGE-04` — Validate governing policy

**Capability status:** Implemented.

**Goal:** Bind admission to the registered active policy.

**Primary actor:** `ROLE-TRANSACTION-COORDINATOR`.

**Suggested model type:** No LLM required.

**Prerequisites:** A registered active `PolicySnapshot`.

**Inputs:** Stored and configured policy hashes.

**Procedure:**

1. Read the stored active policy.
2. Compare the configured policy hash.
3. If the stored policy is absent or its hash differs, return `POLICY_HASH_MISMATCH`.
4. If the stored policy exists, persist and audit the rejection under that stored
   policy. If no stored policy exists, return the rejection without mutation.

**Outputs:** Policy context or `POLICY_HASH_MISMATCH`.

**Acceptance conditions:** The hashes match and the snapshot validates.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `POLICY_HASH_MISMATCH` | Stale service or altered policy | Reopen with the registered active policy | Run `governance show` and `audit verify` |

**Rollback or retry behavior:** A mismatch cannot replace policy state.

**Authority boundary:** Candidate policy cannot authorize its activation.

**Source references:** coordinator policy branch; governance tests.

#### `KERNEL-STAGE-05` — Validate actor identity and approval

**Capability status:** Implemented.

**Goal:** Enforce proposer ownership and independent approval.

**Primary actor:** Deterministic admission handler.

**Suggested model type:** No LLM required.

**Prerequisites:** Typed `ActorIdentity` and optional `Approval`.

**Inputs:** Proposer, record creators, approver, and role relationships.

**Procedure:**

1. Match record actors to the proposal actor.
2. Run `are_independent()`.
3. Enforce stage-specific human authority.

**Outputs:** Actor gate result.

**Acceptance conditions:** All actor bindings and required relationships pass.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `SELF_APPROVAL` | Shared actor identity dimension | Assign a genuinely independent actor | Review full `ActorIdentity` values |

**Rollback or retry behavior:** Preserve the rejection. Submit corrected authority as a new intent.

**Authority boundary:** Configuration changes alone cannot establish independence.

**Source references:** `are_independent()`, `AdmissionEngine.decide()`; identity and reviewer-authority tests.

#### `KERNEL-STAGE-06` — Run domain admission

**Capability status:** Implemented.

**Goal:** Apply the fixed handler for the exact proposal kind.

**Primary actor:** Deterministic proposal handler.

**Suggested model type:** No LLM required.

**Prerequisites:** Valid proposal, policy, actors, and handler registration.

**Inputs:** Proposal and narrow read capability.

**Procedure:**

1. Resolve the fixed handler.
2. Build the typed context.
3. Evaluate exact domain gates.

**Outputs:** Accepted or rejected `TransactionDecision`.

**Acceptance conditions:** Every required domain gate passes.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Domain rejection code | Missing evidence, lineage, review, or budget | Correct the named condition | Inspect decision reasons |

**Rollback or retry behavior:** Rejections retain reasons. Changed content needs a new stable intent.

**Authority boundary:** A learned result cannot replace a deterministic gate.

**Source references:** `ProposalRouter`, fixed handler registries, domain integration tests.

#### `KERNEL-STAGE-07` — Project accepted state

**Capability status:** Implemented.

**Goal:** Append canonical records and update only authorized projections.

**Primary actor:** Fixed domain handler.

**Suggested model type:** No LLM required.

**Prerequisites:** Accepted decision.

**Inputs:** Accepted proposal and narrow write capability.

**Procedure:**

1. Append authoritative records.
2. Update the permitted head or projection.
3. Reject unauthorized writer access.

**Outputs:** Canonical records and effective-state projections.

**Acceptance conditions:** The handler uses only its fixed narrow writer.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Projection mismatch | Partial or unauthorized mutation | Roll back the transaction | Run `audit verify` |

**Rollback or retry behavior:** The database transaction rolls back on exceptions.

**Authority boundary:** Reviewers cannot mutate canonical state.

**Source references:** handler `project()` methods; capability and append-only tests.

#### `KERNEL-STAGE-08` — Persist decision and audit event

**Capability status:** Implemented.

**Goal:** Commit one attributable decision and one tamper-evident event atomically.

**Primary actor:** `ROLE-TRANSACTION-COORDINATOR`.

**Suggested model type:** No LLM required.

**Prerequisites:** Domain decision and active transaction.

**Inputs:** Proposal, decision, policy, fingerprint, and prior audit event.

**Procedure:**

1. Append the transaction record.
2. Build the canonical audit payload.
3. Append the linked audit event.
4. Commit the unit of work.

**Outputs:** Durable transaction, audit event, and accepted projections.

**Acceptance conditions:** `transaction_persisted` matches actual storage and hashes link in sequence.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Missing transaction or audit row | Storage failure or tampering | Preserve state and restore from verified source | Run `audit verify` |

**Rollback or retry behavior:** An exception rolls back all writes.

**Authority boundary:** Audit evidence is tamper-evident, not signed or externally timestamped.

**Source references:** `_audit()`, `append_event()`; transaction and audit-chain tests.

#### `KERNEL-STAGE-09` — Reconcile durable workspace

**Capability status:** Implemented.

**Goal:** Detect later corruption, missing records, and non-reproducible state.

**Primary actor:** `ROLE-AUDIT-VERIFIER`.

**Suggested model type:** No LLM required.

**Prerequisites:** Workspace access.

**Inputs:** Database, policies, artifact bytes, and source-controlled replay handlers.

**Procedure:**

1. Verify the audit chain.
2. Reconcile transactions and projections.
3. Rehash authoritative artifacts.
4. Replay hypothesis mutations.

**Outputs:** Valid result or fail-closed integrity error.

**Acceptance conditions:** Every recomputed value matches durable state.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Nonzero audit command | Corruption or missing bytes | Preserve all evidence and investigate | Repeat only after recovery |

**Rollback or retry behavior:** Verification performs no repair.

**Authority boundary:** Verification cannot authorize a missing record.

**Source references:** `verify_workspace()`; audit, replay, and workspace-integrity tests.

## `MAN-08` — Hypothesis, Model, Checker, and Revision Loop

All eight stages use `RESEARCH_PROCESS / HUMAN_IN_LOOP / RUN_LOCAL / INDEPENDENT_DETERMINISTIC_CHECK / CONTROLLED_EXPERIMENT / EMPIRICAL_MEASUREMENT`.

Every current stage requires independent human approval and exact active-policy attribution.

The template fields below apply to each stage. Each issue table uses defined rejection codes only.

### `HYPOTHESIS-STAGE-01` — Propose a hypothesis version

**Capability status:** Implemented. **Goal:** Retain version one. **Primary actor:** `ROLE-HYPOTHESIS-PROPOSER`. **Suggested model type:** High-diversity hypothesis model.

**Prerequisites:** Hash-verified controlled-experiment evidence and human approval. **Inputs:** `HypothesisSpec`. **Procedure:** 1. Bind exact evidence. 2. Submit `ProposeHypothesisVersion`. 3. Retain the accepted receipt.

**Outputs:** Append-only hypothesis version and receipt. **Acceptance conditions:** Actor, policy, evidence, chronology, and first-version lineage pass.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_LINEAGE` | A successor used the proposal stage | Use `ReviseHypothesis` | Inspect hypothesis history |

**Rollback or retry behavior:** Rejection remains durable; exact retry replays. **Authority boundary:** The stage does not advance a head. **Source references:** `ProposeHypothesisVersionHandler`; hypothesis service tests.

#### `HYPOTHESIS-STAGE-02` — Register an executable-model specification

**Capability status:** Implemented. **Goal:** Retain inert metadata or one fixed simulator selection. **Primary actor:** `ROLE-MODEL-DESIGNER`. **Suggested model type:** Code and formal-reasoning model.

**Prerequisites:** Candidate receipt and human approval. **Inputs:** `ExecutableModelSpec`. **Procedure:** 1. Select one execution mode. 2. Bind schemas and bounds. 3. Submit `RegisterExecutableModel`.

**Outputs:** Model record and receipt. **Acceptance conditions:** Mode shape, candidate lineage, actor, policy, and chronology pass.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_PROPOSAL` | Unsupported execution shape | Use `METADATA_ONLY` or a registered simulator | Inspect `ExecutionMode` |

**Rollback or retry behavior:** Metadata stays inert. **Authority boundary:** `METADATA_ONLY` cannot execute. **Source references:** `RegisterExecutableModelHandler`; model-execution tests.

#### `HYPOTHESIS-STAGE-03` — Register a verification mechanism

**Capability status:** Implemented. **Goal:** Retain one typed mechanism. **Primary actor:** `ROLE-VERIFICATION-DESIGNER`. **Suggested model type:** Code and formal-reasoning model.

**Prerequisites:** Candidate receipt and human approval. **Inputs:** `VerificationMechanismSpec`. **Procedure:** 1. Choose one discriminator. 2. Bind schemas and specification hash. 3. Submit `RegisterVerificationMechanism`.

**Outputs:** Mechanism record and receipt. **Acceptance conditions:** Candidate, actor, policy, and chronology match.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_LINEAGE` | Wrong candidate receipt | Use the exact accepted receipt | Resolve receipt from audit history |

**Rollback or retry behavior:** Append a new proposal for changed metadata. **Authority boundary:** Mechanism metadata is not a result. **Source references:** `RegisterVerificationMechanismHandler`; hypothesis tests.

#### `HYPOTHESIS-STAGE-04` — Record a simulation result

**Capability status:** Implemented for fixed simulators. **Goal:** Retain reproducible bounded output. **Primary actor:** `ROLE-SIMULATOR-SELECTOR`. **Suggested model type:** No LLM required.

**Prerequisites:** Candidate and model receipts plus human approval. **Inputs:** `SimulationResult`. **Procedure:** 1. Resolve retained inputs. 2. Re-execute the fixed simulator. 3. Compare exact output. 4. Submit the result.

**Outputs:** Simulation record and receipt. **Acceptance conditions:** Schemas, seed, bounds, output, policy, and chronology match.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INSUFFICIENT_GROUNDING` | Output does not reproduce | Recompute from exact retained input | Run simulator tests |

**Rollback or retry behavior:** Failed attempts remain durable. **Authority boundary:** No arbitrary model artifact executes. **Source references:** `RecordSimulationResultHandler`; simulator tests.

#### `HYPOTHESIS-STAGE-05` — Record a verification result

**Capability status:** Implemented. **Goal:** Retain formal, deterministic, or learned findings with exact provenance. **Primary actor:** `ROLE-DETERMINISTIC-CHECKER` or `ROLE-LEARNED-JUDGE`. **Suggested model type:** Deterministic checker or independent evaluator model.

**Prerequisites:** Candidate, mechanism, optional model, simulations, evidence, and human approval. **Inputs:** `VerificationResult`. **Procedure:** 1. Resolve every receipt. 2. Validate provenance. 3. Retain counterexample-search facts. 4. Submit the result.

**Outputs:** Verification record and receipt. **Acceptance conditions:** All lineage and provenance gates pass.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INDEPENDENT_REVIEW_REQUIRED` | Provenance or search evidence is insufficient | Use the correct independent mechanism | Inspect result discriminator and provenance |

**Rollback or retry behavior:** Append a corrected result; do not overwrite. **Authority boundary:** Learned results cannot claim deterministic authority. **Source references:** `RecordVerificationResultHandler`; hypothesis tests.

#### `HYPOTHESIS-STAGE-06` — Record a counterexample

**Capability status:** Implemented. **Goal:** Retain one found counterexample and block the candidate. **Primary actor:** `ROLE-COUNTEREXAMPLE-SEARCHER`. **Suggested model type:** Adversarial falsification model with deterministic checker.

**Prerequisites:** Failed retained search result, evidence, receipts, and human approval. **Inputs:** `CounterexampleRecord`. **Procedure:** 1. Bind failed verification evidence. 2. Bind candidate and outputs. 3. Submit `RecordCounterexample`.

**Outputs:** Counterexample record and receipt. **Acceptance conditions:** Failed search, candidate, evidence, policy, and chronology match.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_LINEAGE` | Counterexample does not bind failed retained search | Use the exact failed receipt | Inspect verification history |

**Rollback or retry behavior:** The counterexample remains append-only. **Authority boundary:** Confidence cannot erase a counterexample. **Source references:** `RecordCounterexampleHandler`; counterexample tests.

#### `HYPOTHESIS-STAGE-07` — Revise a hypothesis

**Capability status:** Implemented. **Goal:** Preserve failure and append a contiguous successor. **Primary actor:** `ROLE-HYPOTHESIS-PROPOSER`. **Suggested model type:** Frontier scientific-reasoning model.

**Prerequisites:** Prior candidate receipt, failed results, optional counterexamples, and human approval. **Inputs:** `RevisionRecord` and successor `HypothesisSpec`. **Procedure:** 1. Retain triggering failures. 2. State changed predictions. 3. State changed falsification conditions. 4. Submit `ReviseHypothesis`.

**Outputs:** Revision and successor hypothesis records. **Acceptance conditions:** Version, receipts, changes, policy, and chronology form one chain.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INVALID_LINEAGE` | Noncontiguous or cosmetic revision | Create the immediate successor with explicit scientific changes | Inspect revision chain |

**Rollback or retry behavior:** The failed predecessor remains. **Authority boundary:** Revision does not admit the successor. **Source references:** `ReviseHypothesisHandler`; revision tests.

#### `HYPOTHESIS-STAGE-08` — Admit a hypothesis

**Capability status:** Implemented admission contract. **Goal:** Advance `HypothesisHead` after every gate passes. **Primary actor:** Independent human decision authority. **Suggested model type:** Human expert.

**Prerequisites:** `TRANSFER_VALIDATED` candidate, built-in model, passing deterministic checks, no candidate counterexample, complete revision lineage, controlled evidence, primitive heads, passed audit, accepted measurement, and rollback binding. **Inputs:** `AdmitHypothesis` and exact receipts. **Procedure:** 1. Resolve all receipts. 2. Validate transfer and chronology. 3. Validate evidence and counterexamples. 4. Validate audit, measurement, primitives, human authority, and rollback. 5. Project the head.

**Outputs:** `HypothesisAdmissionDecision` and updated head. **Acceptance conditions:** Every named prerequisite passes exactly.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| `INDEPENDENT_REVIEW_REQUIRED` | Human or support authority is incomplete | Supply independent retained support | Run focused hypothesis tests and `audit verify` |

**Rollback or retry behavior:** A successor must name the current head as rollback target. **Authority boundary:** Admission is not scientific truth. **Source references:** `AdmitHypothesisHandler`; admission and transfer tests.

## `MAN-09` — Governed-Adaptation End-to-End Workflow

The executable example has exactly 21 ordered steps.

All actors and data are synthetic. Typed human identities do not constitute real human review.

For every stage below, the durable controller is `TransactionCoordinator` unless the stage says otherwise.

| Stage ID | Stable step code | Goal and primary role | Inputs and durable output | Admission, issue, resolution, and verification |
| --- | --- | --- | --- | --- |
| `ADAPTATION-STAGE-01` | `initialize_v1_kernel` | Register bootstrap policy. Service role. No LLM required. | Empty workspace; policy snapshot and active pointer. | Require genuinely empty state. Preserve orphaned state. Verify policy versions. |
| `ADAPTATION-STAGE-02` | `approve_v1_to_v2_transition` | Activate V2 under V1 authority. Governance proposer and human approver. | Run, measurement, audit, approval, rollback; policy transition. | Reject policy mismatch or circular authority. Rebuild complete support. Verify `(1, 2)` history. |
| `ADAPTATION-STAGE-03` | `add_synthetic_source_evidence` | Retain incident bytes. Evidence collector. | Synthetic file; hash-verified evidence. | Reject hash mismatch. Restore exact bytes. Rehash artifact. |
| `ADAPTATION-STAGE-04` | `create_research_run_and_progress_plan` | Create run, plan, budget, and initial event. Progress roles. | Typed plan and actors; durable run records. | Reject dependency or budget errors. Correct records. Verify run status. |
| `ADAPTATION-STAGE-05` | `propose_competing_thermal_hypotheses` | Retain alternatives. Hypothesis proposer. | Evidence and assumptions; two hypotheses. | Reject missing grounding. Bind evidence. Inspect hypothesis history. |
| `ADAPTATION-STAGE-06` | `register_builtin_thermal_simulator` | Select `thermal-chamber-v1`. Simulator selector. | Candidate receipt and bounds; model record. | Reject unknown execution. Use fixed registry. Inspect model record. |
| `ADAPTATION-STAGE-07` | `record_predictions_and_falsification_criteria` | Run model and evaluate boundaries. Deterministic checker. | Model input and hypotheses; simulation and checks. | Reject non-reproducible output. Recompute. Compare exact result. |
| `ADAPTATION-STAGE-08` | `construct_and_validate_natural_evidence_trail` | Bind exact spans and relations. Trail constructor. | Evidence and claim receipts; trail records. | Reject span or receipt mismatch. Rebuild source-first. Run trail validation. |
| `ADAPTATION-STAGE-09` | `validate_partial_progress` | Separate provisional and official progress. Progress monitor. | Plan and events; progress summary. | Reject correlated validation. Assign independent validator. Compare weights. |
| `ADAPTATION-STAGE-10` | `reject_false_finish` | Reject premature completion. Final validator. | Checklist and progress; `FALSE_FINISH` rejection. | Keep unfinished work open. Verify `false_finish_rejected`. |
| `ADAPTATION-STAGE-11` | `preserve_failed_hypothesis_and_revision` | Retain failure and successor. Hypothesis proposer. | Failed checks; revision records. | Reject broken lineage. Append contiguous revision. Verify predecessor remains. |
| `ADAPTATION-STAGE-12` | `record_incident_and_propose_rule` | Retain incidents and a rule proposal. Research proposer. | Two incidents and evidence; proposed rule. | Reject duplicate or missing evidence. Correct proposal. Inspect rule history. |
| `ADAPTATION-STAGE-13` | `import_five_reviewer_roles` | Retain five independent assessments. Rule reviewers. | Incidents and proposal; five assessments. | Reject correlated roles. Assign independent actors. Count all five roles. |
| `ADAPTATION-STAGE-14` | `consolidate_canonical_boundary_rule` | Form canonical candidate diff. Rule integrator. | Assessments, audit, measurement, rollback; consolidation. | Reject unresolved conflict. Add boundary test and dispositions. Verify head. |
| `ADAPTATION-STAGE-15` | `preserve_incident_regression_cases` | Keep both incidents as regression cases. Rule integrator. | Candidate rule and incidents; regression records. | Reject missing case bindings. Retain each incident. Inspect regression IDs. |
| `ADAPTATION-STAGE-16` | `link_rule_and_verify_source_mapping` | Map behavior to a source symbol. Deterministic AST verifier. | Rule and repository source; handbook mapping. | Reject `STALE_HANDBOOK_MAPPING`. Repair mapping. Run handbook verify. |
| `ADAPTATION-STAGE-17` | `compare_matched_budget_harness_candidate` | Compare variants under equal budgets. Harness evaluator. | Campaign partitions and budgets; iterations. | Reject `UNMATCHED_BUDGETS`. Match every budget. Recompute report. |
| `ADAPTATION-STAGE-18` | `reject_benchmark_specific_discovery_gain` | Prevent transfer overclaim. Harness evaluator. | Discovery results; `BENCHMARK_SPECIFIC` decision. | Do not promote. Run held-out transfer. Verify first status. |
| `ADAPTATION-STAGE-19` | `admit_held_out_transfer_candidate` | Admit only after transfer and authority gates. Human authority. | Transfer, regressions, audit, measurement, rollback; `ADMITTED` decision. | Reject failed transfer or regression. Correct evidence. Verify second status. |
| `ADAPTATION-STAGE-20` | `export_self_improvement_measurement_report` | Store canonical measurement bytes. Evaluator auditor. | Full trajectory and budgets; content-addressed report. | Reject incomplete measurement. Preserve failures and gaps. Rehash bytes. |
| `ADAPTATION-STAGE-21` | `verify_workspace_and_mixed_policy_audit` | Reconcile all durable state. Audit verifier. | Database and artifacts; valid report. | Fail closed on any mismatch. Preserve workspace. Run ordinary audit verification. |

### Required stage-template interpretation for the table

**Capability status:** Every row is example only as scientific evidence. Each named contract and deterministic control is implemented.

**Prerequisites:** Complete every earlier row in order. Use a new empty workspace.

**Procedure:** 1. Resolve prior durable records. 2. Perform the named action. 3. Submit through deterministic control. 4. Verify the named output.

**Rollback or retry behavior:** Exact retries replay. Changed content requires a new intent. Rejections and failed hypotheses remain durable.

**Authority boundary:** No row establishes scientific truth, provider autonomy, real human review, or live experiment authority.

**Source references:** `STEP_CODES`, `VerticalSlice.run()`, each `_step_N_*()` function; `test_governed_adaptation_vertical_slice.py`.

### Detailed governed-adaptation stages

#### `ADAPTATION-STAGE-01` — Register bootstrap policy

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Register bootstrap policy.

**Primary actor:** Service role.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Empty workspace.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `initialize_v1_kernel`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** policy snapshot and active pointer.

**Acceptance conditions:** Require genuinely empty state.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `initialize_v1_kernel`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-02` — Activate V2 under V1 authority

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Activate V2 under V1 authority.

**Primary actor:** Governance proposer and human approver.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Run, measurement, audit, approval, rollback.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `approve_v1_to_v2_transition`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** policy transition.

**Acceptance conditions:** Reject policy mismatch or circular authority.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `approve_v1_to_v2_transition`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-03` — Retain incident bytes

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Retain incident bytes.

**Primary actor:** Evidence collector.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Synthetic file.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `add_synthetic_source_evidence`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** hash-verified evidence.

**Acceptance conditions:** Reject hash mismatch.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `add_synthetic_source_evidence`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-04` — Create run, plan, budget, and initial event

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Create run, plan, budget, and initial event.

**Primary actor:** Progress roles.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Typed plan and actors.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `create_research_run_and_progress_plan`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** durable run records.

**Acceptance conditions:** Reject dependency or budget errors.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `create_research_run_and_progress_plan`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-05` — Retain alternatives

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Retain alternatives.

**Primary actor:** Hypothesis proposer.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Evidence and assumptions.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `propose_competing_thermal_hypotheses`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** two hypotheses.

**Acceptance conditions:** Reject missing grounding.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `propose_competing_thermal_hypotheses`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-06` — Select `thermal-chamber-v1`

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Select `thermal-chamber-v1`.

**Primary actor:** Simulator selector.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Candidate receipt and bounds.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `register_builtin_thermal_simulator`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** model record.

**Acceptance conditions:** Reject unknown execution.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `register_builtin_thermal_simulator`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-07` — Run model and evaluate boundaries

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Run model and evaluate boundaries.

**Primary actor:** Deterministic checker.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Model input and hypotheses.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `record_predictions_and_falsification_criteria`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** simulation and checks.

**Acceptance conditions:** Reject non-reproducible output.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `record_predictions_and_falsification_criteria`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-08` — Bind exact spans and relations

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Bind exact spans and relations.

**Primary actor:** Trail constructor.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Evidence and claim receipts.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `construct_and_validate_natural_evidence_trail`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** trail records.

**Acceptance conditions:** Reject span or receipt mismatch.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `construct_and_validate_natural_evidence_trail`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-09` — Separate provisional and official progress

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Separate provisional and official progress.

**Primary actor:** Progress monitor.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Plan and events.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `validate_partial_progress`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** progress summary.

**Acceptance conditions:** Reject correlated validation.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `validate_partial_progress`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-10` — Reject premature completion

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Reject premature completion.

**Primary actor:** Final validator.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Checklist and progress.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `reject_false_finish`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** `FALSE_FINISH` rejection.

**Acceptance conditions:** Keep unfinished work open.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `reject_false_finish`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-11` — Retain failure and successor

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Retain failure and successor.

**Primary actor:** Hypothesis proposer.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Failed checks.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `preserve_failed_hypothesis_and_revision`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** revision records.

**Acceptance conditions:** Reject broken lineage.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `preserve_failed_hypothesis_and_revision`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-12` — Retain incidents and a rule proposal

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Retain incidents and a rule proposal.

**Primary actor:** Research proposer.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Two incidents and evidence.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `record_incident_and_propose_rule`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** proposed rule.

**Acceptance conditions:** Reject duplicate or missing evidence.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `record_incident_and_propose_rule`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-13` — Retain five independent assessments

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Retain five independent assessments.

**Primary actor:** Rule reviewers.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Incidents and proposal.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `import_five_reviewer_roles`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** five assessments.

**Acceptance conditions:** Reject correlated roles.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `import_five_reviewer_roles`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-14` — Form canonical candidate diff

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Form canonical candidate diff.

**Primary actor:** Rule integrator.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Assessments, audit, measurement, rollback.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `consolidate_canonical_boundary_rule`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** consolidation.

**Acceptance conditions:** Reject unresolved conflict.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `consolidate_canonical_boundary_rule`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-15` — Keep both incidents as regression cases

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Keep both incidents as regression cases.

**Primary actor:** Rule integrator.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Candidate rule and incidents.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `preserve_incident_regression_cases`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** regression records.

**Acceptance conditions:** Reject missing case bindings.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `preserve_incident_regression_cases`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-16` — Map behavior to a source symbol

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Map behavior to a source symbol.

**Primary actor:** Deterministic AST verifier.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Rule and repository source.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `link_rule_and_verify_source_mapping`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** handbook mapping.

**Acceptance conditions:** Reject `STALE_HANDBOOK_MAPPING`.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `link_rule_and_verify_source_mapping`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-17` — Compare variants under equal budgets

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Compare variants under equal budgets.

**Primary actor:** Harness evaluator.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Campaign partitions and budgets.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `compare_matched_budget_harness_candidate`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** iterations.

**Acceptance conditions:** Reject `UNMATCHED_BUDGETS`.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `compare_matched_budget_harness_candidate`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-18` — Prevent transfer overclaim

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Prevent transfer overclaim.

**Primary actor:** Harness evaluator.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Discovery results.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `reject_benchmark_specific_discovery_gain`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** `BENCHMARK_SPECIFIC` decision.

**Acceptance conditions:** Do not promote.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `reject_benchmark_specific_discovery_gain`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-19` — Admit only after transfer and authority gates

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Admit only after transfer and authority gates.

**Primary actor:** Human authority.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Transfer, regressions, audit, measurement, rollback.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `admit_held_out_transfer_candidate`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** `ADMITTED` decision.

**Acceptance conditions:** Reject failed transfer or regression.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `admit_held_out_transfer_candidate`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-20` — Store canonical measurement bytes

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Store canonical measurement bytes.

**Primary actor:** Evaluator auditor.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Full trajectory and budgets.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `export_self_improvement_measurement_report`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** content-addressed report.

**Acceptance conditions:** Reject incomplete measurement.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `export_self_improvement_measurement_report`; matching `_step_N_*` function; governed example end-to-end tests.

#### `ADAPTATION-STAGE-21` — Reconcile all durable state

**Capability status:** Example only for scientific evidence. The named deterministic control is implemented.

**Goal:** Reconcile all durable state.

**Primary actor:** Audit verifier.

**Suggested model type:** Use the model class for the primary role in `MAN-06`. Use no LLM for deterministic control.

**Prerequisites:** Complete all earlier example stages. Retain their accepted records and receipts.

**Inputs:** Database and artifacts.

**Procedure:**

1. Resolve the retained prerequisites.
2. Perform `verify_workspace_and_mixed_policy_audit`.
3. Submit each mutation through deterministic admission.
4. Verify the durable output.

**Outputs:** valid report.

**Acceptance conditions:** Fail closed on any mismatch.

**Possible issues:**

| Symptom | Probable cause | Resolution | Verification |
| --- | --- | --- | --- |
| Stage rejection or verification failure | A prerequisite, authority, lineage, or domain gate failed. | Correct the named condition. Do not bypass governance. | Verify the named durable output and run `audit verify`. |

**Rollback or retry behavior:** Exact retries replay. Changed content needs a new stable intent. Retained failures remain append-only.

**Authority boundary:** Synthetic actors and data do not provide scientific truth or real human validation.

**Source references:** `STEP_CODES` entry `verify_workspace_and_mixed_policy_audit`; matching `_step_N_*` function; governed example end-to-end tests.


## `MAN-10` — Behavioral-Rule Review Roles

| Role ID | Exact `ReviewerRole` | Distinct question | Suitable model class |
| --- | --- | --- | --- |
| `ROLE-SEMANTIC-REVIEWER` | `SEMANTIC` | Does the candidate duplicate or overlap existing meaning? | Long-context evidence model |
| `ROLE-CONFLICT-REVIEWER` | `CONFLICT` | Does the candidate contradict, compete, or need precedence? | Code and formal-reasoning model |
| `ROLE-ABSTRACTION-REVIEWER` | `ABSTRACTION` | Is the rule at the correct reusable scope? | Frontier scientific-reasoning model |
| `ROLE-ADVERSARIAL-REVIEWER` | `ADVERSARIAL` | Which counterexamples or unsafe edge cases break the rule? | Adversarial falsification model |
| `ROLE-VERIFICATION-REVIEWER` | `VERIFICATION` | Do evidence and regression tests support the proposed boundary? | Deterministic checker with evidence model |

### `ROLE-SEMANTIC-REVIEWER` — semantic reviewer

**Capability status:** Implemented assessment role.

**Purpose:** Does the candidate duplicate or overlap existing meaning?

**Recommended actor type:** Independent human, LLM, or deterministic tool.

**Suggested model type:** Long-context evidence model.

**Required capabilities:** Structured output, evidence grounding, uncertainty reporting, schema compliance, and dissent.

**Authority:** The role can submit `ReviewerAssessment`. The role cannot mutate canonical rule state.

**Independence requirement:** The reviewer must be independent from the proposer, other required roles, and integrator.

**Inputs:** Rule proposal, incidents, evidence, prior rules, and role-specific review scope.

**Outputs:** Immutable `SEMANTIC` assessment with findings, uncertainty, and recommendation.

**Common failures:** Correlated identity, missing evidence, role collapse, or attempted canonical mutation.

**Resolution:** Assign an independent actor. Recreate the assessment from retained inputs. Preserve dissent.

**Unsuitable model types:** The proposer, integrator, configuration alias, or a model without evidence grounding.

**Source references:** `ReviewerRole.SEMANTIC`, `ReviewerAssessment`; reviewer-authority and rule-service tests.

#### `ROLE-CONFLICT-REVIEWER` — conflict reviewer

**Capability status:** Implemented assessment role.

**Purpose:** Does the candidate contradict, compete, or need precedence?

**Recommended actor type:** Independent human, LLM, or deterministic tool.

**Suggested model type:** Code and formal-reasoning model.

**Required capabilities:** Structured output, evidence grounding, uncertainty reporting, schema compliance, and dissent.

**Authority:** The role can submit `ReviewerAssessment`. The role cannot mutate canonical rule state.

**Independence requirement:** The reviewer must be independent from the proposer, other required roles, and integrator.

**Inputs:** Rule proposal, incidents, evidence, prior rules, and role-specific review scope.

**Outputs:** Immutable `CONFLICT` assessment with findings, uncertainty, and recommendation.

**Common failures:** Correlated identity, missing evidence, role collapse, or attempted canonical mutation.

**Resolution:** Assign an independent actor. Recreate the assessment from retained inputs. Preserve dissent.

**Unsuitable model types:** The proposer, integrator, configuration alias, or a model without evidence grounding.

**Source references:** `ReviewerRole.CONFLICT`, `ReviewerAssessment`; reviewer-authority and rule-service tests.

#### `ROLE-ABSTRACTION-REVIEWER` — abstraction reviewer

**Capability status:** Implemented assessment role.

**Purpose:** Is the rule at the correct reusable scope?

**Recommended actor type:** Independent human, LLM, or deterministic tool.

**Suggested model type:** Frontier scientific-reasoning model.

**Required capabilities:** Structured output, evidence grounding, uncertainty reporting, schema compliance, and dissent.

**Authority:** The role can submit `ReviewerAssessment`. The role cannot mutate canonical rule state.

**Independence requirement:** The reviewer must be independent from the proposer, other required roles, and integrator.

**Inputs:** Rule proposal, incidents, evidence, prior rules, and role-specific review scope.

**Outputs:** Immutable `ABSTRACTION` assessment with findings, uncertainty, and recommendation.

**Common failures:** Correlated identity, missing evidence, role collapse, or attempted canonical mutation.

**Resolution:** Assign an independent actor. Recreate the assessment from retained inputs. Preserve dissent.

**Unsuitable model types:** The proposer, integrator, configuration alias, or a model without evidence grounding.

**Source references:** `ReviewerRole.ABSTRACTION`, `ReviewerAssessment`; reviewer-authority and rule-service tests.

#### `ROLE-ADVERSARIAL-REVIEWER` — adversarial reviewer

**Capability status:** Implemented assessment role.

**Purpose:** Which counterexamples or unsafe edge cases break the rule?

**Recommended actor type:** Independent human, LLM, or deterministic tool.

**Suggested model type:** Adversarial falsification model.

**Required capabilities:** Structured output, evidence grounding, uncertainty reporting, schema compliance, and dissent.

**Authority:** The role can submit `ReviewerAssessment`. The role cannot mutate canonical rule state.

**Independence requirement:** The reviewer must be independent from the proposer, other required roles, and integrator.

**Inputs:** Rule proposal, incidents, evidence, prior rules, and role-specific review scope.

**Outputs:** Immutable `ADVERSARIAL` assessment with findings, uncertainty, and recommendation.

**Common failures:** Correlated identity, missing evidence, role collapse, or attempted canonical mutation.

**Resolution:** Assign an independent actor. Recreate the assessment from retained inputs. Preserve dissent.

**Unsuitable model types:** The proposer, integrator, configuration alias, or a model without evidence grounding.

**Source references:** `ReviewerRole.ADVERSARIAL`, `ReviewerAssessment`; reviewer-authority and rule-service tests.

#### `ROLE-VERIFICATION-REVIEWER` — verification reviewer

**Capability status:** Implemented assessment role.

**Purpose:** Do evidence and regression tests support the proposed boundary?

**Recommended actor type:** Independent human, LLM, or deterministic tool.

**Suggested model type:** Deterministic checker with evidence model.

**Required capabilities:** Structured output, evidence grounding, uncertainty reporting, schema compliance, and dissent.

**Authority:** The role can submit `ReviewerAssessment`. The role cannot mutate canonical rule state.

**Independence requirement:** The reviewer must be independent from the proposer, other required roles, and integrator.

**Inputs:** Rule proposal, incidents, evidence, prior rules, and role-specific review scope.

**Outputs:** Immutable `VERIFICATION` assessment with findings, uncertainty, and recommendation.

**Common failures:** Correlated identity, missing evidence, role collapse, or attempted canonical mutation.

**Resolution:** Assign an independent actor. Recreate the assessment from retained inputs. Preserve dissent.

**Unsuitable model types:** The proposer, integrator, configuration alias, or a model without evidence grounding.

**Source references:** `ReviewerRole.VERIFICATION`, `ReviewerAssessment`; reviewer-authority and rule-service tests.


Each reviewer records findings, uncertainty, and a recommended action.

Each reviewer can dissent through `findings`, `uncertainty`, and `recommended_action`.

Reviewers submit immutable assessments. Reviewers cannot mutate canonical state.

The integrator consumes all five assessments in canonical order.

`ConsolidationProposal` preserves accepted recommendations, rejected recommendations, findings, dissent, and regression cases.

## `MAN-11` — Troubleshooting Guide

Preserve the database, artifact bytes, input files, command, JSON output, package version, and commit for every failure.

| Condition | Observable symptom | Meaning or likely cause | Safe resolution | Verification | Preserve |
| --- | --- | --- | --- | --- | --- |
| Invalid proposal | `INVALID_PROPOSAL` | Strict parsing or envelope matching failed | Correct exact schema; use a new key for changed content | Inspect decision | Input and output |
| Invalid argument | `INVALID_ARGUMENT`, exit 2 | CLI syntax, value, or path failed | Correct the command from `--help` | Retry with `--json` | Command and JSON |
| Invalid policy | `INVALID_POLICY` | Policy JSON, UTF-8, version, or fields failed | Restore a valid schema-version-1 policy | Run `init` on a new empty root | Policy bytes |
| Policy mismatch | `POLICY_HASH_MISMATCH` | Configured and stored policies differ | Reopen with the registered policy | `governance show`; `audit verify` | Both hashes |
| Self-approval | `SELF_APPROVAL` | Actor identities correlate | Assign a truly independent approver | Compare every identity field | Both identities |
| Correlated reviewer | Authority rejection | Reviewer shares an identity dimension | Assign another operational identity | Run reviewer authority tests | Assessments |
| Missing evidence | `MISSING_EVIDENCE` | Required record or link is absent | Add and verify evidence; submit a new proposal | `evidence show` | Source bytes |
| Evidence hash mismatch | `EVIDENCE_HASH_MISMATCH` | Bytes, size, path, or digest differ | Restore exact source through artifact API | `audit verify` | Original bytes |
| Invalid evidence span | Validation or grounding failure | Offsets or text do not match source | Extract exact text and offsets again | Trail or evidence validation | Source and span |
| Illegal claim transition | `INVALID_STATUS_TRANSITION` | Edge, version, parent, or immutable content is wrong | Use the legal graph and exact successor | `claim history` | Claim versions |
| Missing upstream receipt | `INVALID_LINEAGE` | Accepted dependency receipt is absent | Resolve the exact audit-backed receipt | Inspect transaction and audit | Receipt fields |
| Receipt mismatch | `INVALID_LINEAGE` | Receipt hashes or candidate identity differ | Use the original accepted receipt | `audit verify` | Proposal and receipt |
| Idempotency conflict | `IDEMPOTENCY_CONFLICT` | Stable identity was reused for changed intent | Use a new key and proposal ID | `transaction list` | Both intents |
| Orphaned governance | Integrity error; `init` refuses | Durable state lacks active policy pointer | Do not initialize over state; investigate or restore | Storage-only `audit verify` | Whole workspace |
| False finish | `FALSE_FINISH` | Completion gates or final validation failed | Continue work or record non-success termination | `progress status` | Checklist and budget |
| Counterexample blocks admission | `INVALID_LINEAGE` or review rejection | Candidate has a retained counterexample | Create a successor revision and new check chain | Inspect hypothesis history | Failed candidate |
| Non-reproducible simulation | `INSUFFICIENT_GROUNDING` | Submitted output differs from fixed execution | Recompute exact input and seed | Run simulator tests | Input and output |
| Learned judge used as authority | `INDEPENDENT_REVIEW_REQUIRED` | Learned provenance tried to satisfy deterministic gate | Use a deterministic checker or formal solver | Inspect discriminator | Learned result |
| Metadata execution attempt | `INVALID_PROPOSAL` or service refusal | `METADATA_ONLY` artifact was selected for execution | Keep artifact inert; use registered simulator | Model-execution tests | Model record |
| Benchmark-specific improvement | `BENCHMARK_SPECIFIC` or `BENCHMARK_SPECIFIC_ADMISSION` | Discovery gain lacks transfer | Run held-out transfer under matched conditions | Inspect harness decision | Discovery results |
| Failed transfer validation | Non-admitted harness decision | Transfer, safety, or regression gate failed | Preserve failure and revise candidate | Recompute campaign report | All partitions |
| Budget mismatch | `UNMATCHED_BUDGETS` | Variant budgets differ | Match execution, search, evaluation, judging, and human budgets | Recompute comparisons | Budget records |
| Regression failure | Non-admitted decision | Required regression failed | Repair candidate; rerun the declared regression | Inspect report metrics | Negative results |
| Missing rollback target | Lineage or admission rejection | Successor lacks a valid predecessor binding | Register and bind the current approved target | Inspect decision | Old and new records |
| Capability is self-reported or unknown | Profile records fail-closed capability status | Accepted evidence does not verify the requirement | Add exact accepted evidence or choose a qualified actor | Inspect `capability-profile` | Assertions and receipts |
| Diverse cohort fails independence | Authority rejection despite varied prompts or outputs | Peers share an operational identity dimension | Assign operationally independent reviewers | Inspect diversity and actor identities separately | Cohort and diversity records |
| Compilation has domain status `INVALID` | Compilation transaction is accepted; no plan exists | Deterministic findings invalidate the candidate method | Preserve the compilation; correct the candidate and submit a new identity | Inspect `procedure-compilation` | Compilation and findings |
| Invalid compilation binding | `INVALID_PROCEDURE` | The binding references an accepted compilation with domain status `INVALID` | Bind only an accepted current `VALID` compilation | Inspect compilation and binding decision | Compilation receipt |
| Evaluation evidence does not match | `UNMATCHED_EVALUATION` | Protocol, cell, trace, output, verifier, reward, budget, or receipt chain differs | Rebuild from one exact accepted evidence chain | Inspect protocol, cell, trace, and reward records | All referenced receipts |
| Invalidating high reward | Reward assessment is accepted with domain status `INVALID`; `promotion_evidence` is false | A verifier, environment, trace, contamination, termination, resource, or reward-hacking finding invalidates the reward | Preserve the assessment; repair and rerun the evaluated method | Inspect `reward-assessment` | Trace and findings |
| Cognitive record is absent | `MISSING_ENTITY` | The exact kind and identifier do not resolve after integrity verification | Correct the canonical kind or identifier; do not mutate the workspace | Retry `cognitive inspect --json` | Command and JSON |
| Audit-chain corruption | Nonzero audit verification | Hash, link, sequence, payload, or row changed | Stop mutation; restore from a verified source | `audit verify` | Database and logs |
| Missing artifact bytes | Integrity failure | Referenced digest path is absent | Restore exact verified bytes through the store | `audit verify` | Artifact metadata |
| Import into nonempty target | Import conflict or error | Import is not a general merge | Use an empty target or exact replay | Re-export and compare | Source bundle and target |
| CLI JSON parsing failure | One JSON error envelope, exit 2 | Invalid JSON or option parsing | Correct UTF-8 JSON and command syntax | Retry with `--json` | Rejected input |
| Windows path or reparse rejection | Artifact or handbook path failure | Path escapes containment or uses reparse point | Use a regular contained local path | Run path-focused tests | Path metadata |

Never delete the database as the default repair.

Never bypass policy, admission, or audit checks.

## `MAN-12` — Operational Examples

### Initialize and verify a workspace

```powershell
scientist-harness init --root .kernel --json
scientist-harness audit verify --root .kernel --json
```

Expected empty result: `valid` equals `true`; `checked_events` equals `0`.

### Add evidence

```powershell
scientist-harness evidence add --root .kernel --source local-note --file .\note.txt --media-type text/plain --json
```

```bash
scientist-harness evidence add --root .kernel --source local-note --file ./note.txt --media-type text/plain --json
```

Preserve the returned `data.evidence_id`.

### Propose and inspect a claim

```powershell
scientist-harness claim propose --root .kernel --proposition "The chamber warmed." --scope "Local demonstration." --system "Synthetic chamber." --modality "observed" --json
scientist-harness claim history --root .kernel CLAIM_ID --json
```

Replace `CLAIM_ID` with the returned `data.claim_id`.

The proposal creates a `PROPOSED` claim. The proposal does not establish truth.

### Run the kernel vertical slice

```powershell
python examples\kernel_vertical_slice.py
```

```bash
python examples/kernel_vertical_slice.py
```

The script uses a temporary workspace and expects three audit events.

### Run and verify the governed-adaptation example

```powershell
python examples\governed_adaptation_vertical_slice.py --root .example-governed-adaptation
scientist-harness audit verify --root .example-governed-adaptation --json
```

```bash
python examples/governed_adaptation_vertical_slice.py --root .example-governed-adaptation
scientist-harness audit verify --root .example-governed-adaptation --json
```

Use a new empty root. The example does not support reuse of a populated root.

### Export and import a workspace

Workspace exchange is a Python application interface. No CLI export or import command exists.

```python
from super_scientist.application.workspace_exchange import export_workspace, import_workspace

bundle = export_workspace(uow_factory=source_uow_factory, artifact_store=source_store)
result = import_workspace(
    bundle,
    uow_factory=empty_target_uow_factory,
    artifact_store=target_store,
    source_artifact_store=source_store,
    clock=clock,
)
```

The caller must supply configured unit-of-work factories, stores, and a clock.

After a conflict-free import, call `export_workspace()` on the target. Compare the result with `bundle`.

See `tests/integration/application/test_workspace_exchange.py` for executable setup examples.

### Run the quality gate

```powershell
scientist-harness quality-gate
```

The source registry currently runs nine fixed checks.

## `MAN-13` — Security and Safe Operation

Treat evidence, proposals, reviewer output, manifests, results, and imported bundles as untrusted.

Treat capability assertions, peer contributions, candidate methods, procedure envelopes,
generation metadata, traces, tool observations, reward values, and cognitive record
identifiers as untrusted.

Content-addressed artifacts use SHA-256-derived paths below a private local root.

The runtime rejects absolute paths, traversal, symlinks, non-regular files, and Windows reparse-point escapes.

The scientific runtime has no arbitrary network, subprocess, dynamic import, `eval`, `exec`, or shell authority.

Procedure steps cannot name Python imports, shell commands, model providers, dynamic
entry points, unauthorized tools, hidden execution, or protected evaluators. The
example's deterministic toy validator has `TOOL` provenance. The validator reads bounded
artifact bytes, compares the declared SHA-256 digest, and never executes artifact bytes.

Hidden chain-of-thought is not persisted.

Public schemas retain only bounded observable outputs, artifacts, decisions,
diagnostics, and provenance. Do not submit private reasoning, credentials, protected
answers, or reversible protected locators as rationale or trace metadata.

The quality gate is separate development authority. The dependency audit can use network access.

Protected evaluation uses separate role-specific capabilities. Export prohibits protected answers and live protected-store references.

The trace handler owns one freshly reconstructed exact proposal snapshot for admission,
decision, projection, transaction storage, and audit. Metadata marked `UNAVAILABLE`
cannot contain a fabricated value. A reward assessment is evidence only; a high number
cannot override an invalidating finding or authorize promotion.

Audit verification checks the chain, policy attribution, transactions, projections, histories, receipts, and artifact bytes.

> **WARNING:** If audit verification fails, continued mutation can compound integrity loss. Preserve the workspace and stop the operation.

Human approval remains mandatory at policy-defined gates.

Rollback selects a retained predecessor. Rollback does not delete append-only history.

The audit chain is not a signature, external timestamp, remote attestation, or truth guarantee.

The runtime has no secret store. Do not store credentials, tokens, regulated data, or protected answers in the workspace.

## `MAN-14` — Glossary

### Approved technical nouns

| Term | Definition |
| --- | --- |
| Actor | A typed human, model, tool, or service identity. |
| Admission | A deterministic decision that a proposal satisfies implemented gates. |
| Approval | A typed authority record from an actor other than the proposer. |
| Artifact | Immutable bytes addressed by SHA-256 digest. |
| Audit event | An immutable, hash-linked event that records an attributable decision. |
| Capability profile | A task-conditioned record of verified, self-reported, unknown, and unsupported capability assertions. |
| Canonical record | An append-only durable domain record. |
| Claim | A versioned `AtomicClaim` with scope, modality, status, assumptions, and evidence links. |
| Cognitive plane | Typed evidence construction and pure analysis with no retained control-plane authority. |
| Cohort | A bounded selected set of actors whose capability and exclusion evidence remains explicit. |
| Control plane | Policy, admission, transaction, audit, storage, integrity, progress, artifact, and protected-evaluator authority. |
| Counterexample | A retained input and observation that falsifies a candidate under the recorded mechanism. |
| Evidence | Source metadata and content-addressed bytes used for grounding. |
| Evidence span | Exact text with start and end offsets in retained evidence. |
| Evidence trail | A source-first graph of exact spans, relations, checks, and assessments. |
| Governing policy | The registered active policy that authorizes a transaction decision. |
| Harness candidate | A proposed harness variant evaluated against a baseline. |
| Hypothesis head | The effective-state pointer to the admitted hypothesis version. |
| Idempotency | Exact replay behavior for one stable intent identity. |
| Metadata availability | An explicit `AVAILABLE`, `UNAVAILABLE`, or `UNKNOWN` state kept separately from a metadata value. |
| Operational diversity | Declared differences in model, prompt, tools, evidence, method, topology, or errors; not reviewer independence. |
| Projection | A rebuildable effective-state view derived from canonical records. |
| Proposal | Untrusted typed input submitted for admission. |
| Procedure compilation | An immutable deterministic result with domain status `VALID` or `INVALID` and retained findings. |
| Receipt | An exact reference to an accepted proposal and audit event. |
| Reviewer independence | A policy authority property based on distinct declared operational identities, not output diversity or agreement. |
| Reward validity | A recomputed assessment of exact trace, verifier, environment, budget, termination, contamination, and reward-hacking evidence. |
| Revision | An append-only successor that preserves its failed predecessor. |
| Rollback target | A retained approved predecessor bound to a candidate transition. |
| Transaction | One atomic proposal decision, projection update, and audit append boundary. |
| Transfer validation | Evaluation on declared held-out tasks under protected, matched conditions. |
| Verification mechanism | Typed metadata for a formal verifier, deterministic checker, or learned judge. |
| Workspace | One SQLite database and its content-addressed artifact root. |

### Approved technical verbs

| Verb | Controlled meaning |
| --- | --- |
| Propose | Submit untrusted typed input. |
| Admit | Accept input under current deterministic policy gates. |
| Approve | Supply actor authority for a gate. |
| Commit | Persist one atomic transaction. |
| Project | Derive or update effective state after acceptance. |
| Verify | Recompute and compare an exact invariant. |
| Replay | Return or reconstruct from stable accepted history. |
| Roll back | Select a retained prior target through governed procedure. |

## `MAN-15` — Source Map

| Manual section | Primary code source | Primary document source | Validation tests |
| --- | --- | --- | --- |
| `MAN-01` | `pyproject.toml`, `identity.py`, `quality/runner.py` at `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450` | `README.md`, `GOVERNANCE.md` | `tests/unit/docs/test_user_manual.py`, package, identity, and quality-runner tests |
| `MAN-02` | identity, admission, policy, protected-evaluation models | `GOVERNANCE.md`, `SECURITY.md` | authority, leakage, governance tests |
| `MAN-03` | `transactions/coordinator.py`, `application/cognitive/service.py`, audit chain | `ARCHITECTURE.md` | coordinator, cognitive-service, replay, and audit tests |
| `MAN-04` | CLI bootstrap and kernel commands | `README.md`, `REPRODUCIBILITY.md` | CLI integration and JSON-envelope tests |
| `MAN-05` | cognition, collaboration, procedure, harness-evaluation domain models and application services | subsystem documents | cognition, collaboration, procedure, harness, and authority tests |
| `MAN-06` | actor, capability, independence, procedure-validator, and trace contracts | governance, procedure, and harness documents | identity, cognitive-authority, procedure-escalation, and trace-tampering tests |
| `MAN-07` | coordinator, admission engine, workspace integrity | `ARCHITECTURE.md` | admission, coordinator, replay, audit tests |
| `MAN-08` | hypothesis models, handlers, simulators | `docs/hypothesis-model-checker-loop.md` | hypothesis service, simulator, transfer tests |
| `MAN-09` | governed example `STEP_CODES` and step functions | governed example guide | governed example end-to-end tests |
| `MAN-10` | behavioral-rule models, service, capabilities | `docs/behavioral-rules.md` | reviewer-authority and rule-service tests |
| `MAN-11` | rejection codes, cognitive reader, procedure and evaluation handlers | security and reproducibility documents | negative, adversarial, procedure, harness, and integrity tests |
| `MAN-12` | CLI commands and workspace exchange | example guides | CLI, examples, and workspace-exchange tests |
| `MAN-13` | artifact store, protected evaluation, single-snapshot trace admission, workspace integrity | `SECURITY.md`, `THREAT_MODEL.md` | artifact, leakage, reparse, cognitive-authority, trace-tampering, and audit tests |
| `MAN-14` | domain contracts | architecture and subsystem documents | strict parsing and domain-model tests |
| `MAN-15` | all listed sources | source register and research inspirations | `tests/unit/docs/test_user_manual.py`, `tests/unit/docs/test_source_register.py`, and handbook verification tests |
| `MAN-16` | cognitive facade, 18 fixed handlers, procedure binding, harness evidence, reader, integrity, and workspace exchange | governed cognitive/procedure example guide | cognitive-service, procedure-service, harness-extension, reader/CLI, integrity, exchange, replay, and end-to-end tests |

## MAN-16 — Cognitive Cohorts and Procedure Compilation

**Capability status:** Strict contracts, pure computations, governed persistence,
read-only inspection, integrity verification, workspace exchange, and replay are
implemented. Fixed peers, synthetic evidence, deterministic procedure validation,
guidance cells, and model-by-harness cells are example only. Live LLM grounding, live
peer adapters, provider-native metadata ingestion, and training-payload handoff are
interface only. Diversity, guidance, model-by-harness interaction, and reward-hacking
diagnostics are experimental. Reinforcement learning, live model proxies, online
weights, arbitrary harnesses, learned admission, and self-modifying governance are
deferred or prohibited.

**Authority boundary:** The Research Coordinator sequences typed proposals through the
control plane. The Research Coordinator cannot retain the transaction coordinator or
any storage, artifact, execution, tool, provider, policy, or protected-answer authority.
Every durable action below remains a separate governed transaction.

**Prerequisites:** Initialize an empty workspace, retain source artifacts, record the
active policy, and construct exact accepted receipts for every declared input. Use
stable new proposal identifiers when content changes.

### Ordered workflow

1. **Declare capability requirements.** The human researcher records task-conditioned
   `CapabilityRequirement` values before selecting peers. Each requirement names the
   capability, evidence threshold, and task scope. The observable result is a bounded
   canonical requirement tuple used by capability grounding.
2. **Record grounded profiles.** The Capability Grounder calls `assess_capability()`
   over exact accepted evidence and submits `RecordCapabilityProfile`. The accepted
   `CapabilityProfile` preserves verified, self-reported, unknown, unsupported, and gap
   states. Self-reported or unknown capability never becomes verified implicitly.
3. **Select a bounded cohort.** The deterministic selector calls `build_cohort()` with
   accepted current profile receipts and a bounded `CohortRequest`. The accepted
   `CohortPlan` records selected members, exclusions, coverage, stable ties, and gaps.
4. **Inspect diversity separately from independence.** The Cohort/Diversity Auditor
   calls `assess_diversity()` and submits `RecordDiversityAssessment`. Inspect declared
   diversity axes and error-correlation evidence. Then apply the policy identity check
   separately. Operational diversity does not satisfy reviewer independence.
5. **Open a collaboration session.** Submit `RecordCollaborationSession`, then append
   bounded peer requests, observable contributions, and topology events. Finish with an
   explicit termination record. Peer agreement remains evidence and cannot transition
   a claim, policy, harness, procedure, or promotion state.
6. **Compile a candidate method.** The Procedure Compiler resolves only the declared,
   current, accepted profile, catalog, and procedure-source-snapshot receipts. Submit
   `RecordProcedureCompilation`. The compiler recomputes the procedure graph, artifact
   flow, tools, validators, resources, termination rules, and progress mapping.
7. **Inspect invalid or inconclusive findings.** A well-formed compilation with domain
   status `INVALID` is accepted and retained as history. It creates no progress plan.
   Preserve every `ProcedureFinding` and terminal outcome. Malformed, stale,
   source-mismatched, or derivation-mismatched compilation proposals are rejected.
8. **Bind only a valid procedure.** Submit `BindCompiledProgressPlan` only for an
   accepted current compilation with domain status `VALID`. The binding handler
   delegates the canonical plan to `RecordProgressPlanHandler` in the same transaction.
   An attempt to bind an accepted `INVALID` compilation is rejected with transaction
   code `INVALID_PROCEDURE`; no plan or binding is projected.
9. **Record matched evaluations, traces, and reward validity.** Record all four guidance
   conditions and the declared model-by-harness grid. Each cell must bind one exact
   protocol, model, harness, partition, budget, output, verifier, trace, and reward
   evidence chain. Record metadata availability separately from values. A correctly
   recomputed reward assessment with an invalidating finding is accepted and retained
   with domain status `INVALID`, but `promotion_evidence` is `false`. Fabricated, stale,
   ambiguous, surplus, or derivation-mismatched evidence is rejected.
10. **Inspect records.** Run the integrity-first read-only command for one exact record:

    ```powershell
    scientist-harness cognitive inspect --root .cognitive-workspace --kind capability-profile --id PROFILE_ID --json
    ```

    Replace `PROFILE_ID` with an exact canonical identifier. `CognitiveRecordKind`
    exposes exactly 18 kinds. The command validates the complete workspace before one
    point lookup. The command does not list, aggregate, mutate, execute, import, or call
    a model, tool, command, provider, or protected evaluator.
11. **Verify, export, import, and replay.** Run `scientist-harness audit verify --root
    .cognitive-workspace --json`. Export only after integrity succeeds. Import the
    canonical version-0.3 bundle into an empty workspace through
    `TransactionCoordinator.submit_intent()`. Verify the imported workspace, export it
    again, compare the canonical bundle, and replay an identical stable intent. Exact
    replay returns the stored decision and appends no transaction or audit event.

### Model-free executable example

Run the complete fixed workflow against a new empty root:

```powershell
python examples/governed_cognitive_procedure_vertical_slice.py --root .cognitive-example --json
```

The command emits one canonical JSON object. Verify that the summary reports verified,
self-reported, and unknown capabilities; same-model diversity without independence; a
topology update; a bounded challenge; retained `INVALID` and `VALID` compilations; one
accepted progress binding; all four guidance conditions; a two-model by two-harness
grid; available and unavailable metadata; an accepted invalidating high reward with
`promotion_evidence` equal to `false`; `verified` equal to `true`; `import_verified`
equal to `true`; and successful exact replay. The registered toy validator is a `TOOL`
actor. It compares bounded artifact bytes with the declared SHA-256 digest and never
executes artifact content.

### Verification and source boundary

MAN-16 maps to these exact implementation sources:

- `src/super_scientist/application/cognitive/service.py`
- `src/super_scientist/domain/cognition/grounding.py`
- `src/super_scientist/domain/cognition/diversity.py`
- `src/super_scientist/application/collaboration/service.py`
- `src/super_scientist/domain/procedures/compiler.py`
- `src/super_scientist/application/procedures/service.py`
- `src/super_scientist/application/harness_eval/extensions.py`
- `src/super_scientist/application/cognitive/reader.py`
- `src/super_scientist/application/cognitive/integrity.py`
- `src/super_scientist/application/workspace_exchange.py`

Run these focused checks:

```powershell
python -m pytest `
  tests/integration/application/test_cognitive_service.py `
  tests/integration/application/test_collaboration_service.py `
  tests/integration/application/test_procedure_service.py `
  tests/integration/application/test_harness_eval_extensions.py `
  tests/integration/application/test_cognitive_workspace_integrity.py `
  tests/integration/application/test_cognitive_workspace_exchange.py `
  tests/integration/application/test_transaction_coordinator.py `
  tests/integration/cli/test_cognitive_cli.py `
  tests/e2e/test_governed_cognitive_procedure_vertical_slice.py `
  -q
```

For the complete release gate, run `scientist-harness quality-gate`. The command runs
exactly nine checks in this order: `format`, `lint`, `types`, `tests`, `security`,
`dependencies`, `build`, `package`, and `wheel-install`.

S30-S35 informed the vocabulary and design constraints. Their paper, benchmark,
training, modularity, cohort, and agentic-reasoning results were not reproduced. No
source code from S30-S35 was imported or reused. The source register separates each
external proposal and reported result from this project's adaptation, original
synthesis, adoption status, reproduction status, and limitations.
