# Governed Cognitive Cohorts, Procedure Compilation, and Harness-Native Evaluation Design

**Status:** Proposed for human approval
**Date:** 2026-08-23
**Target release:** 0.3.0 candidate
**Baseline:** `main` at `1ea575e4887633d94fc0f3c183dc86d868fd9e15`
**Decision record:** `docs/adr/0002-governed-cognitive-plane-and-procedure-compilation.md`

## 1. Purpose

This design adds a coherent, typed, deterministic vertical slice for capability-aware
cognitive cohorts, bounded peer collaboration, method-to-procedure compilation,
guidance-gradient experiments, model-by-harness evaluation, harness-native execution
traces, and reward-validity diagnostics.

The governing boundary is unchanged:

> Models, tools, peers, compilers, evaluators, and rewards may propose or supply
> evidence. `TransactionCoordinator` alone may commit canonical state.

The cognitive plane may decentralize proposal generation inside a fixed collaboration
envelope. It may not decentralize governance, transaction authority, quality gates,
protected evaluation, or admission.

This document is a design, not an implementation plan. No production implementation
starts until a human explicitly approves it.

## 2. Observed baseline

The inspected baseline is package version 0.2.0 on Python 3.12 or later. It is a
transactional modular monolith with strict Pydantic contracts, SQLAlchemy/SQLite,
Alembic migrations, a content-addressed artifact store, Typer CLI groups, and offline
deterministic examples.

Its canonical write flow is:

```text
typed attempt or normalized untrusted proposal
-> TransactionCoordinator
-> BEGIN IMMEDIATE
-> workspace-integrity verification
-> exact idempotency lookup
-> fixed ProposalRouter dispatch
-> domain authority validation
-> accepted projection/history append, if any
-> transaction decision append
-> audit event append
-> atomic commit or rollback
```

The existing progress domain already owns hierarchical plans, weighted subtasks,
dependencies, validator identity and version, evidence requirements, category budgets,
checkpoints, completion, and false-finish detection. The existing harness evaluation
domain already owns immutable two-variant campaigns under one fixed model identity,
matched budgets, five dataset partitions, protected checker results,
multidimensional metrics, confounds, and governed decisions.

The design therefore extends these domains through narrow contracts. It does not
replace either one.

### 2.1 Baseline integrity correction in scope

Migration 0006 added durable harness-evaluation tables, but current durable-state
detection and full workspace reconstruction do not cover every rule and harness table.
The 0.3.0 integrity work must cover all existing 0004 and 0006 records before adding
new record expectations. This is a compatibility correction, not permission to change
their scientific semantics.

### 2.2 Baseline documentation corrections in scope

The user manual currently describes reviewer independence less strictly than
`are_independent()` implements, and its quality-check inventory is behind the nine
checks registered by `quality.runner.CHECKS`. The 0.3.0 manual update must describe the
implemented fail-closed behavior and derive or accurately enumerate the current check
set.

## 3. Goals

The 0.3.0 candidate will provide:

1. Evidence-backed capability profiles with explicit unknown and self-reported states.
2. Deterministic cohort construction with task-fit diagnostics and stable tie behavior.
3. Operational-diversity measurement that cannot satisfy governance independence.
4. Bounded, auditable peer collaboration with deterministic topology updates and
   termination.
5. Pure method-to-procedure compilation with static feasibility, dependency, resource,
   verifier, artifact, termination, and progress-binding checks.
6. A governed binding from valid procedures to the existing `ProgressPlan` domain.
7. Guidance-gradient experiments that hold task, model, harness, verifier, and budget
   identity constant.
8. Model-by-harness matrices that separate held-model, held-harness, and descriptive
   interaction comparisons.
9. Harness-native traces with explicit metadata availability and freshness.
10. Reward-validity assessments and reward-hacking diagnostics that fail closed.
11. Append-only persistence, integrity verification, replay, export, and import for the
    complete vertical slice.
12. A deterministic offline example, focused CLI inspection, tests, documentation, and
    exact S30-S35 attribution.

## 4. Non-goals

This release will not add:

- live reinforcement learning, policy-gradient updates, or adapter training;
- GPU, Kubernetes, distributed-worker, or model-provider requirements;
- a live multi-agent service, dynamic peer recruitment, or arbitrary remote peers;
- arbitrary shell, subprocess, imported-code, plugin, or tool execution;
- hidden chain-of-thought, scratchpad, rationale, or token-stream persistence;
- fabricated token IDs, probabilities, uncertainty, tool results, or capability facts;
- automatic method, procedure, harness, model, evaluator, reward, policy, or governance
  promotion;
- a second workflow engine, planner, transaction path, or quality framework;
- full reproduction of S30-S35 results or import of their source code;
- claims that prompt diversity establishes reviewer independence;
- causal model/harness attribution from unmatched observations; or
- breaking mutation of a released 0.2.0 table or schema contract.

## 5. Source provenance

The following exact source versions were consulted on 2026-08-23. Paper and
companion-repository licenses apply to their own material; SSOH imports no source code
from them.

| ID | Exact source boundary | Adapted design signal | Limitation |
| --- | --- | --- | --- |
| S30 | *Spark-to-Paper: End-to-End Research Paper Generation as a Composable Skill*, arXiv:2608.11924v1, arXiv non-exclusive distribution license; `Spark-To-Paper-Skills/spark-to-paper-skills@c17149def034bc777462de612926c8e3b6d01b8c`, MIT | stage-shaped method development and skill-like procedure artifacts | reported system results are not reproduced; companion code is not imported |
| S31 | *Modular Cognitive Architecture Emerges in Large Language Models*, arXiv:2608.13567v1, CC BY 4.0; `Pengrui-Han/LLM_Modularity_Final@e3ac7fbb3a6caea05c88343a8de6ec04a4035db8`, MIT | modular cognitive roles, bounded topology, and operational diversity axes | modular roles do not establish independent governance or correctness |
| S32 | *ASI-Bench: At the Dawn of Artificial Superintelligence*, arXiv:2608.17271v1, CC BY 4.0; `apexin-ai/ASI-Bench@9a86de643331d2b3a3d95744040881a95aa3fdc6`, Apache-2.0 | capability grounding and explicit task-fit evidence | benchmark scores are contextual evidence, not universal capability truth |
| S33 | *Co-RL: Unsupervised Reasoning Emerges from Diverse Cohort in Multi-agent RL*, arXiv:2608.17253v1, arXiv non-exclusive distribution license; `DrStranded/Co-RL@ff476f06e42eeca4d5c198b93eadd7547876e5e5`, Apache-2.0 | peer collaboration, contribution accounting, and reward-hacking concerns | no live RL, policy update, or training loop is adopted |
| S34 | *DeAR: Decentralized Agentic Reasoning via Capability Grounding and Collaborative Thought Navigation*, arXiv:2608.17282v1, arXiv non-exclusive distribution license; code marked unavailable until acceptance | decentralized reasoning as a bounded proposal topology | unavailable code cannot be inspected or reused; hidden reasoning is not persisted |
| S35 | *LEGO-RL: Harness-Native Reinforcement Learning for Coding Agents*, arXiv:2608.17393v1, CC0 1.0; `LegoX/Lego-RL@58f89aa039373afc962ad836d67eca8436b48af6`, Apache-2.0 | reusable procedure structure and compositional execution metadata | training and benchmark claims are not reproduced; no RL implementation is adopted |

The source register will record the following authors exactly:

- S30: Zhuoyang Qian, Biao Wu, Yiran Wang, Chris D Yan, Desan Dai, Liangwei
  Zheng, Jin Jiang, Junsheng Zhang, and Wenhao Wang.
- S31: Pengrui Han, Jacob Andreas, Evelina Fedorenko, and Andrea Gregor de Varda.
- S32: Junwei Zhou, Zhen Sun, Binyu Li, Jiangyu Zhou, Yuexi Pan, Hengyu Wang,
  Honghe Ren, Xiaohan Jia, Xueyang Zhou, Xiaoyu Cao, Yongchao Chen, Yuanning
  Feng, Junhao Wu, Cheng Zhang, Sijia Chen, Haoyu Xue, Chengsong You, Huan
  Wang, Koutian Wu, Peigan Gao, Jiakun Wu, Wenzhe Li, Ergan Shang, Qingyuan
  Zheng, Jingjing Zhou, Ruixuan Jia, Yan Xu, Hongrui Zhang, Xiao-Han Ma,
  Zhengxiang Cheng, Yuexing Hao, Liting Mai, Xianglin Ji, Wenjun Zhang, Zhuofan
  Chen, Yixiao Huang, Chi Wang, Wenyue Hua, Yilun Hao, Yuantao Zhai, Ziyan
  Zhao, and Jingyan Xie.
- S33: Yunhao Yang, Yuexin Bian, Yunjie Tian, Di Fu, Tianjin Huang, Yuanyuan
  Shi, Ziang Xiao, Nuno Vasconcelos, and Yijiang Li.
- S34: Xing Wei, Changmeng Zheng, XiaoYong Wei, Xiufen Ye, and Qing Li.
- S35: Yiming Du, Yuxin Jiang, Tao Yuan, Jianbo Dai, Shaowei Wang, Jierun Chen,
  Chaofan Tao, Xianzhi Yu, Lifeng Shang, Kam-Fai Wong, Xiaohui Li, and Haoli Bai.

The register's adaptation and limitation fields will preserve these boundaries:

- S30 informs model-judgment versus deterministic-execution separation, persistent
  artifact interfaces, planning before result observation, evidence-conditioned claim
  revision, and bounded self-refutation. Its small generated-topic evaluation does not
  establish general scientific validity.
- S31 motivates functional specialization and query-conditioned routing. Inferring
  benefits for external agent modules from internal neuronal modularity is SSOH's
  project inference, not a reproduced finding.
- S32 motivates the guidance gradient, the method-selection/procedure-operationalization
  distinction, model-by-harness evaluation, and resource consequences of incomplete
  guidance. SSOH does not adopt its ASI framing; benchmark performance is neither
  scientific truth nor safe-autonomy evidence.
- S33 motivates heterogeneous cohorts, correlated-error diagnostics, behavioral
  diversity, and diversity-aware peer evaluation. Peer reward and cohort agreement are
  not truth or admission authority, and no Co-RL training is implemented.
- S34 motivates task-conditioned grounding, peer-to-peer routing, and adaptive local
  topology. Its reasoning/QA evaluation does not cover scientific governance;
  governance remains centralized in the control plane.
- S35 motivates harness-native trace fidelity, train/inference mismatch awareness,
  environment isolation, reward-hacking-aware execution, and a future training
  interface. Coding-agent RL results are not generalized to scientific training, and
  this release implements no live RL.

Each S30-S35 register entry must separately state `source_proposal`,
`source_evidence`, `project_adaptation`, `project_original_synthesis`, adoption status,
reproduction status, limitations, and notes. Adoption is `inspired` or `adapted`;
reproduction is always `not_reproduced`.

## 6. Alternatives considered

### 6.1 One immutable cognitive-run blob

One proposal could contain the profile, cohort, collaboration, method, procedure,
evaluation, trace, and reward result. This minimizes migrations but makes partial
rejection, provenance, references, querying, replay diagnostics, and schema evolution
coarse. It also encourages a god object. Rejected.

### 6.2 A new live orchestration and learning runtime

A separate runtime could recruit peers, call model providers, execute tools, learn from
rewards, and write its own checkpoints. This conflicts with the offline and
deterministic scope, introduces a second authority path, and would require new threat
models for networks, secrets, code execution, and training. Rejected.

### 6.3 Typed modular evidence on the existing control plane — selected

Strict records, pure deterministic services, narrow repositories, and coordinated
proposal handlers preserve local modularity while sharing one authority boundary.
This design is more explicit, but every state transition remains reviewable and
replayable.

## 7. Selected architecture

```text
declared task + evidence catalog + fixed peer roster
                 |
                 v
       pure capability grounding
                 |
                 v
       pure cohort construction ----> DiversityAssessment (diagnostic only)
                 |
                 v
       bounded collaboration engine
                 |
                 v
       CandidateMethod proposals
                 |
                 v
       pure ProcedureCompiler
          |                 |
       invalid            valid
          |                 |
          v                 v
 compilation history    compiled-plan binding proposal
                              |
                              v
                    existing ProgressPlan domain

fixture execution -> harness-native trace -> reward validity
        |                    |                    |
        +------ guidance/model-by-harness evidence-+
                                      |
                                      v
                      existing governed harness campaign

Every durable arrow passes through TransactionCoordinator.
```

The cognitive plane owns evidence generation and deterministic derivation. The control
plane owns admission, persistence, active policy, audit, rollback, protected
evaluation, and promotion. No cognitive-plane object receives a unit of work,
repository, artifact-store write capability, governance repository, or protected
answer capability.

## 8. Package and ownership boundaries

The intended boundaries are conceptual; an implementation plan may refine filenames
without changing ownership.

| Boundary | Owns | Must not own |
| --- | --- | --- |
| `domain/cognition` | capability profiles, cohort requests/plans, diversity and error-correlation diagnostics | governance independence, persistence, provider clients |
| `domain/collaboration` | peer roster, contributions, topology events, budgets, deterministic termination | dynamic discovery, network calls, transactions |
| `domain/procedures` | candidate methods, executable procedures, static compiler and validation reports | a second progress planner, artifact execution |
| existing `domain/progress` | plan/subtask validation, budgets, checkpoints, completion, false-finish | method selection or peer coordination |
| existing `domain/harness_eval` extensions | guidance conditions, evaluation cells, matrices, traces, reward validity | automatic promotion, model invocation, training |
| `application` services | fixed sequencing of pure derivation and proposal submission | bypassing `TransactionCoordinator` |
| `kernel/transactions` | strict proposal union, fixed routing, decisions, atomic unit of work | scientific judgment generation |
| `infrastructure` | append-only repositories, migration, artifact and bundle adapters | domain policy decisions |

No new untyped cross-domain dictionary is permitted. Cross-domain references use
stable IDs, schema versions, and content hashes.

## 9. Core contracts

All new contracts are frozen, strict Pydantic models with `extra="forbid"`, explicit
schema versions, bounded text and collection lengths, normalized UTC timestamps where
timestamps are admissible, and canonical JSON hashing. Enums are closed.

### 9.1 Capability grounding

`CapabilityProfile` records:

- `profile_id`, `actor_id`, `actor_kind`, and `schema_version`;
- exact `model_identity`, `provider_identity`, `adapter_identity`, and configuration
  hashes when known;
- declared allowed tools, modalities, supported schemas, execution constraints, and
  known failure categories;
- a sorted tuple of `CapabilityAssertion` values;
- for each assertion: capability name, task-family scope, status
  (`verified`, `self_reported`, `unsupported`, or `unknown`), evidence references,
  verifier identity/version when verified, and freshness inputs; and
- a canonical content hash.

Absence of evidence produces `unknown`, never `verified`. A self-description produces
`self_reported`, never `verified`. Stale evidence remains addressable but is not used as
current verified evidence.

`CohortRequest` declares required and preferred capabilities, minimum/maximum cohort
size, fixed candidate actor IDs, prohibited combinations, task identity, and a stable
tie-break policy. `CohortPlan` records selected and excluded actors, per-requirement
coverage, unresolved gaps, ranked tie sets, and the exact evidence snapshot.

Selection is a pure stable sort over declared facts. The default tie breaker is the
canonical actor ID after recording the tied score set. No runtime randomness is
allowed.

### 9.2 Operational diversity and error correlation

`DiversityAssessment` records declared differences across model identity, provider,
adapter, configuration, evidence basis, assigned role, procedure family, tool surface,
and observed error family. Every axis has `different`, `same`, or `unknown`; unknown is
never treated as different.

`ErrorCorrelationAssessment` records a matched evaluation-set identity, sample count,
method, value when computable, and status (`known`, `insufficient_data`, or
`not_comparable`). It is descriptive and cannot be converted to reviewer independence.

The public type exposes no `is_independent` field. Production code must not call
`are_independent()` from diversity calculation or call diversity calculation from
`are_independent()`. Only the existing governance function decides reviewer
independence, retaining its fail-closed equality checks.

Peer agreement and majority counts may be stored only as diagnostics. They never set
a claim status, procedure validity, reviewer independence, or admission disposition.

### 9.3 Bounded collaboration

`CollaborationSession` declares:

- task and cohort identities;
- a fixed peer roster and fixed role assignments;
- allowed input artifact references and their content hashes;
- a `CollaborationBudget` with `max_peers`, `max_hops`, `max_contributions`,
  `max_contributions_per_peer`, `max_topology_changes`, allowed tool identities, and
  exact upper bounds for abstract token, time, cost, and tool-use units;
- a closed allowed-contribution-kind set;
- the deterministic scheduling and topology-policy versions; and
- an explicit completion predicate.

`PeerContribution` contains a contribution ID, peer ID, parent contribution IDs,
contribution kind, bounded public rationale summary, structured candidate content,
evidence/artifact references, and content hash. It may contain concise justifications
needed for audit but must not contain hidden chain-of-thought, scratch work, or
provider-native reasoning payloads.

`PeerRequest` names one declared recipient, requested capability, structured question,
allowed artifact references, parent contribution, and remaining session budget.
`TopologySnapshot` contains the active declared peers and directed edges in canonical
order. Neither contract can add a peer, capability, tool permission, or resource unit
that is absent from the accepted session.

`TopologyEvent` records a before topology hash, an allowed operation, its declared
inputs, an after topology hash, and a deterministic reason code. Allowed operations
are limited to enabling or disabling a declared directed peer edge and changing a
declared peer's active/inactive scheduling state. No actor may be added after session
creation.

The pure collaboration engine schedules the lexicographically first eligible peer at
each step. It terminates with one of:

- `completed`;
- `max_hops_reached`;
- `max_contributions_reached`;
- `per_peer_limit_reached`;
- `topology_change_limit_reached`;
- `no_eligible_peer`;
- `repeated_state_loop`;
- `topology_churn`; or
- `contribution_monopoly`.

A repeated canonical state hash is a loop. Alternating topology hashes beyond the
declared churn threshold is churn. A peer exceeding its declared share bound is a
monopoly finding and terminates before accepting another contribution. Tests use
in-memory fixed peers; production code adds no live peer runtime.

The active topology is transient in memory by default. A snapshot or update becomes
durable only when a caller explicitly submits the matching audit proposal through the
coordinator. A durable topology record is history, never permission or governance
state.

### 9.4 Candidate methods and executable procedures

`CandidateMethod` records objective, assumptions, ordered method stages, evidence
references, claimed capability requirements, expected artifacts, verifier
requirements, resource estimates, termination conditions, and provenance from peer
contributions. It is a proposal artifact, not a committed scientific conclusion.

`ExecutableProcedure` contains:

- stable procedure and compiler identity/version;
- an ordered set of typed `ProcedureStep` values;
- step dependencies forming a directed acyclic graph;
- required capability and artifact inputs;
- artifact outputs with media type and integrity expectation;
- resource costs mapped to existing progress budget categories;
- exact validator identity/version and evidence requirements;
- success, failure, retry, and termination conditions;
- a deterministic mapping to `ProgressSubtask` fields; and
- source candidate and compilation hashes.

Each `ProcedureStep` has its own objective, required inputs, produced artifacts,
dependencies, allowed registered tools, preconditions, completion criteria, evidence
requirements, exact validator binding, failure signals, bounded recovery action, and
resource allocation. A bounded recovery action names another declared step or a
terminal outcome; it is not executable code.

Procedure steps select from a closed operation vocabulary such as
`inspect_declared_artifact`, `derive_structured_candidate`,
`run_registered_deterministic_fixture`, `evaluate_with_registered_validator`, and
`record_declared_output`. The contract never stores a command line, import path,
provider request, arbitrary URI action, or executable code.

### 9.5 Static procedure compilation

`ProcedureCompiler` is a pure function over a candidate method, capability snapshot,
artifact catalog, validator catalog, and budget envelope. It returns an
`ExecutableProcedure` plus `ProcedureValidationReport`.

The report has deterministic ordered findings and one status:

- `valid` only when no error finding exists;
- `invalid` when an error exists; or
- `inconclusive` when a required catalog fact is unknown.

Required checks are:

1. schema and compiler-version support;
2. unique IDs and normalized ordering;
3. no unknown dependency and no dependency cycle;
4. every required input exists or is produced by an ancestor step;
5. every produced artifact has one unambiguous producer;
6. every required tool exists in the fixed catalog and is authorized for the session;
7. no step requires governance, transaction, protected-evaluator, or other impossible
   authority;
8. every declared output is defined and every referenced output exists;
9. every capability requirement has current verified evidence;
10. every validator is registered with exact identity/version;
11. each step has completion criteria with evidence and validator requirements;
12. every resource cost maps to an existing budget category and fits the envelope;
13. retry limits and all paths have explicit termination;
14. the closed operation vocabulary contains every step operation;
15. no forbidden executable, provider, network, secret, protected-answer, or hidden
    reasoning field is present; and
16. the generated progress mapping passes the existing progress-domain validation.

Compilation does not execute a step or infer missing catalog facts. Invalid and
inconclusive attempts are submitted as immutable history with their findings. They do
not create a plan or an admitted method head. No separate mutable candidate-method
head is introduced.

An untrusted serialized compilation result crosses the transaction-proposal parser only
inside `OpaqueProcedureCompilationEnvelope`. The envelope contains compilation metadata,
base64-encoded canonical UTF-8 JSON bytes, and the exact byte hash. The decoded bytes are
limited to 4 MiB and must pass the fixed iterative JSON-depth scan. The envelope validates
JSON syntax, canonical bytes, encoding, size, depth, and hash without constructing a
`ProcedureCompilationResult`.

The `RecordProcedureCompilation` proposal contains that envelope, not a
`ProcedureCompilationRecord`. Its handler calls
`parse_untrusted_procedure_compilation_result()` before recomputation, authority checks,
or typed-record construction. The handler rejects a safe-parse failure with the fixed
invalid-procedure disposition and does not retain the exception or rejected plaintext.
After successful recomputation, persistence and integrity reconstruction use
`ProcedureCompilationRecord.build_from_untrusted_envelope()`; no other component may
treat the proposal envelope as a durable typed record.

Serialized proposals enter through `parse_untrusted_proposal_json()`, not a public raw
`PROPOSAL_ADAPTER` call. That boundary checks the 8 MiB proposal limit and iterative
depth limit before Pydantic parsing. It converts validation and resource failures to one
fixed error after leaving the caught-exception scope. The fixed error has no cause,
context, structured diagnostics, or rejected payload.
Base64 is only the opaque JSON transport representation; it is not treated as encryption
or redaction.

`parse_untrusted_procedure_compilation_envelope()` fresh-validates every envelope field
before the procedure handler reads compilation ID, created time, governing policy hash,
or result bytes. If the supplied value is already an envelope model, the parser requires
exact equality after validation. `ProcedureCompilationRecord.build_from_untrusted_envelope()`
uses that parser first. The record content hash therefore binds only normalized metadata
and a validated result.

### 9.6 Binding to `ProgressPlan`

A separate `BindCompiledProgressPlan` proposal references a committed, current,
`valid` compilation and carries the exact generated `ProgressPlan` and subtasks. Its
handler:

1. loads the compilation by ID and verifies its content hash;
2. verifies compiler and catalog freshness;
3. deterministically regenerates the plan mapping;
4. requires byte-for-byte canonical equality with the proposed plan;
5. invokes the existing progress-domain validation and persistence functions inside
   the same coordinator unit of work; and
6. appends a binding record linking compilation, procedure, and plan IDs.

The handler must reuse the progress implementation; copied dependency, budget,
checkpoint, or false-finish logic is prohibited. A later progress mutation continues
to use existing progress proposals and semantics.

## 10. Bounded method revision and self-refutation

Procedure failure feeds the existing progress budget and hypothesis-model-checker
records; it does not create an independent retry loop. A compilation or execution may
request a bounded method revision, experiment repetition, recovery action, or
alternative-method search only when the corresponding existing budget category has
remaining allocation.

`MethodDirectionOutcome` records one of `supported`, `unsupported`, `inconclusive`, or
`abandoned`, with evidence, failed-method, rejected-procedure, and budget references.
`supported` is evidence status, not claim admission. Exhausting revision/recovery
budget yields `inconclusive` or `abandoned` according to the declared terminal rule;
the system must not manufacture a successful narrative. Failed methods, negative
results, and rejected procedures remain immutable history.

## 11. Guidance-gradient evaluation

`GuidanceCondition` is a closed ordinal enum:

1. `full_procedure_guidance`;
2. `method_only`;
3. `objective_and_data_only`; and
4. `objective_data_with_distractors`.

The order identifies an experimental gradient; it is not a universal claim that more
guidance is better. The distractor condition must declare the added artifacts and keep
the required objective, input data, outputs, evaluator, checker, and scoring criteria
identical to its matched cells.

`GuidanceEvaluationProtocol` fixes task identity, task input hash, model identity,
harness identity/version, verifier identity/version, allowed artifacts, random seed if
a deterministic fixture consumes one, resource budget, and output schema. Only the
guidance condition may differ between matched cells.

Each `GuidanceEvaluationCell` stores separately:

- task score;
- procedure-compilation status;
- procedure execution success;
- method-selection outcome;
- typed failure events;
- typed recovery events;
- resource use by category;
- output, trace, verifier, and reward-assessment references; and
- missingness reasons.

No composite score is canonical. A derived report may show a vector and deltas, but it
must retain every component and cannot silently impute missing values. Any mismatch in
the held-constant identity produces an explicit confound and prevents a guidance-only
comparison.

## 12. Model-by-harness evaluation

`ModelHarnessProtocol` declares at least two fixed model identities, at least two fixed
harness identities, one task-set identity, one verifier identity/version, one matched
budget, and the complete expected cell grid. Every observed cell references the same
metric vector used for guidance evaluation.

`ModelHarnessAnalysis` emits only these comparison kinds:

- `model_held_constant`: compare harnesses for one exact model;
- `harness_held_constant`: compare models for one exact harness;
- `interaction_descriptive`: describe non-additive observed differences with no causal
  claim; and
- `train_test_transfer`: compare a declared development partition with held-out
  validation, transfer, regression, or safety partitions.

Missing cells, task drift, verifier drift, budget mismatch, trace staleness, and reward
invalidity are typed confounds. The first three comparison kinds are unavailable until
their required matched cells are complete.

Existing `HarnessCampaign` remains unchanged and remains the only harness-promotion
record. A new analysis can be cited as campaign evidence, but it cannot set a campaign
decision or mutate a harness head.

## 13. Harness-native execution trace

`HarnessExecutionTrace` records what the harness can verifiably observe:

- trace ID and schema version;
- task, model, harness, procedure, and environment identities and versions;
- declared context artifact references and a canonical context hash;
- an ordered tuple of public input/context transformations, including explicit
  `context_compaction` and `reserialization` events when they occur;
- typed tool or fixture observations with request/response hashes and safe status;
- ordered typed environment events;
- output artifact references and output hash;
- validator and checker-result references;
- reward-observation and reward-validity references when present;
- generation metadata fields with per-field availability;
- resource accounting; and
- provenance and a trace content hash.

Generation fields such as token IDs, token counts, log probabilities, sampling
parameters, stop reasons, or provider request IDs use an explicit
`available`, `unavailable`, or `not_applicable` wrapper. `available` requires a value
and evidence basis; the other states prohibit a fabricated value.

Tool observations store registered fixture/tool identity, typed status, and hashes or
safe bounded public output. They do not store secrets, arbitrary commands, protected
answers, reversible protected-store locations, or raw exception text.

A trace is current only when its task input, model, harness, procedure, environment,
validator, and referenced artifacts match the protocol's exact hashes. Freshness is a
pure calculation; timestamps alone cannot make a trace current.

## 14. Reward validity and reward-hacking diagnostics

`RewardObservation` separates an optional numeric or categorical reward from the
verifier result and trace. A reward may be present even when it is invalid.

`RewardValidityAssessment` has status `valid`, `invalid`, or `inconclusive`, ordered
reason codes, evidence references, assessor identity/version, and freshness inputs.
It is `valid` only when:

- the trace is current and complete for required fields;
- the verifier/checker completed successfully under the declared version;
- environment and task identities match the evaluation protocol;
- no protected-data or undeclared-artifact boundary was crossed;
- no invalidating reward-hacking finding exists; and
- all required evidence is available.

`RewardHackingFinding` uses closed diagnostic families:

- proxy gaming;
- verifier gaming;
- environment tampering;
- data or answer leakage;
- reward-channel manipulation;
- metric cherry-picking;
- premature termination;
- resource-accounting evasion;
- trace inconsistency; and
- distribution or partition contamination.

Findings cite observable evidence and never infer hidden motives. Existing evaluator
collapse metrics remain separate supporting diagnostics.

Closed invalidation reasons include `environment_crash`, `incomplete_execution`,
`verifier_mismatch`, `corrupted_artifact`, `protected_answer_leakage`,
`reward_hacking_finding`, `evaluator_failure`, `stale_harness_trace`, and
`task_runtime_mismatch`. Unknown evidence needed to decide one of these conditions
produces `inconclusive`, not `valid`.

Only rewards with a current `valid` assessment may enter a promotion-facing aggregate
or be cited as positive campaign evidence. Invalid or inconclusive rewards remain in
history, even when numerically high. Missing assessments fail closed.

## 15. Transaction proposals and authority

The fixed `Proposal` union gains narrowly scoped kinds:

- `RecordCapabilityProfile`;
- `RecordCohortPlan`;
- `RecordDiversityAssessment`;
- `RecordCollaborationSession`;
- `AppendPeerRequest`;
- `AppendPeerContribution`;
- `AppendTopologyEvent`;
- `RecordCollaborationTermination`;
- `RecordProcedureCompilation`;
- `RecordMethodDirectionOutcome`;
- `BindCompiledProgressPlan`;
- `RecordGuidanceEvaluationProtocol` and `AppendGuidanceEvaluationCell`;
- `RecordModelHarnessProtocol`, `AppendModelHarnessCell`, and
  `RecordModelHarnessAnalysis`;
- `RecordHarnessExecutionTrace`; and
- `RecordRewardAssessment`, which atomically carries one observation, its findings,
  and its validity assessment.

Each handler accepts only its domain repository capability and read-only capabilities
for referenced records. It returns a typed disposition to the coordinator. Handlers do
not commit, publish audit events independently, call one another through nested
transactions, or accept a generic repository set.

Derived objects supplied by a proposal are recomputed inside the handler from
committed inputs and compared canonically. A mismatch is rejected. This applies to
cohort selection, diversity, topology transitions, compiler output, progress mapping,
matrix analysis, trace freshness, and reward validity.

Proposal parsing treats serialized procedure compilation results as opaque bounded
envelopes. It must not construct a nested `ProcedureCompilationResult`. The procedure
handler owns safe normalization and performs it before any authority decision.

Rejected proposals remain in transaction and audit history under existing semantics.
Accepted evidence records do not imply scientific or governance admission.

## 16. Persistence and migration 0007

Migration 0007 adds append-only tables for:

- capability profiles, cohort plans, diversity assessments, and error-correlation
  records;
- collaboration sessions, peer requests, contributions, topology events, and
  termination records;
- procedure compilations, method-direction outcomes, and compiled-plan bindings;
- guidance protocols and cells;
- model-harness protocols, cells, and analyses;
- harness execution traces; and
- reward observations, findings, and validity assessments.

Large public artifacts continue to use the content-addressed artifact store. Database
records store references and hashes. Hidden reasoning, secrets, protected answers, and
raw provider payloads are forbidden in both places.

Each table has a primary record ID, schema version, canonical payload or normalized
columns, content hash, transaction ID, policy version, and created-at value supplied by
the coordinator's clock where the existing convention requires it. Append-only
triggers reject update and delete. Foreign references are checked in the handler and
enforced by database constraints where the existing migration style supports them.

Released 0.2.0 rows are not altered, backfilled, or rehashed. An upgraded empty 0007
table set is valid. Existing proposal schemas keep their current versions; new
cross-domain relationships use binding records rather than silently extending old
hash contracts.

## 17. Integrity, replay, export, and import

The workspace integrity snapshot gains expectations for every new append-only record
and for all previously omitted rule and harness records. `has_durable_state()` must
return true for a workspace containing any released durable table, including 0004,
0006, or 0007 records.

Full verification reconstructs records from accepted transaction proposals in a fresh
logical projection and compares IDs, canonical payloads, content hashes, policy
attribution, and reference closure. It must not trust current projection rows merely
because their hashes are internally consistent.

For `RecordProcedureCompilation`, reconstruction first normalizes the accepted opaque
envelope with `ProcedureCompilationRecord.build_from_untrusted_envelope()` and repeats
deterministic compiler equality. An accepted proposal envelope is not itself a durable
compilation record.

Bundle export includes all accepted and rejected proposal/decision history under the
existing bundle contract, every referenced public artifact, migration/schema
metadata, and projection expectations for new records. Import remains coordinator
replay into a fresh workspace followed by full verification. No direct row-copy path
is added.

Compatibility requirements are:

- an untouched 0.2.0 workspace upgrades and verifies;
- a 0.3.0 bundle round-trips to an identical integrity snapshot;
- a 0.2.0 bundle imports under 0.3.0 without synthetic 0007 records;
- unknown future schema versions fail closed with a fixed safe error; and
- tampering with a record, reference, ordering field, availability state, or artifact
  is detected before another write.

## 18. Application and CLI surface

Application services expose sequencing helpers but submit every durable stage as its
own proposal. A helper stops after rejection and returns the typed decision; it cannot
silently retry with weakened constraints.

A `ResearchCoordinator` application service may arrange those submissions, but it is
an orchestration convenience only. It receives no admission or repository authority.

A single read-only `cognitive inspect` CLI group will inspect a capability profile,
cohort, collaboration session, procedure compilation, evaluation protocol, trace, or
reward assessment by ID. It prints canonical JSON with stable field ordering and never
executes a peer, procedure, tool, or model. Existing transaction and audit commands
remain the authoritative way to inspect decisions and policy attribution.

No command accepts arbitrary provider URLs, command strings, Python paths, or dynamic
plugins.

## 19. Offline deterministic example

`examples/governed_cognitive_procedure_vertical_slice.py` will use only in-memory
fixed peers, declared fixture artifacts, a registered deterministic toy validator, and
a temporary local workspace. It will demonstrate:

1. recording verified, self-reported, and unknown capability assertions;
2. constructing a cohort and showing operational diversity separately from a failed
   governance-independence check for same-model peers;
3. appending bounded peer contributions and a deterministic topology change;
4. compiling one invalid method and retaining its findings;
5. compiling one valid method and binding it to an existing `ProgressPlan`;
6. recording a guidance gradient and a two-model-by-two-harness matrix;
7. recording traces with both available and unavailable generation metadata;
8. rejecting a high but invalid reward from aggregate evidence;
9. verifying, exporting, importing, and replay-verifying the workspace; and
10. printing stable JSON summaries with no network or optional external dependency.

The example is executable documentation, not a benchmark result.

## 20. Security and privacy boundaries

The existing local trusted interpreter/process and private protected-store assumptions
remain. New untrusted inputs include peer contributions, candidate methods, imported
traces, generation metadata, tool observations, reward values, and source bundles.

Required defenses include strict size/depth/count limits, closed enums, canonical
normalization, hash verification, reference closure, fixed error messages, no retained
validation cause containing input data, artifact namespace checks, and complete
transaction rollback.

The collaboration and procedure layers do not introduce code execution. The trace
layer does not introduce passive secret capture. Public rationale summaries must be
bounded and user-supplied or fixture-supplied; the system never asks for or stores
private chain-of-thought. Documentation must distinguish concise audit justification
from hidden reasoning.

The threat-model update must analyze peer collusion, correlated consensus, capability
spoofing, routing loops, topology manipulation, malicious delegation, procedure-input
prompt injection, tool escalation through a compiled procedure, reward hacking,
invalid environment rewards, trace tampering, method anchoring, and evaluator leakage.
For each threat it must identify the deterministic prevention or detection boundary
and demonstrate that the failure grants no governance authority.

## 21. Determinism and failure semantics

Deterministic functions consume no wall clock, environment variable, filesystem
enumeration order, network state, or global random generator. Ordered outputs use
canonical IDs and explicit sort keys. Decimal/resource values use the repository's
established exact numeric convention rather than binary-float equality.

Expected validation failures produce closed reason codes and rejected decisions, not
tracebacks. Infrastructure failures roll back the domain record, decision, and audit
event together. Integrity failure prevents the next write. Replay does not repair or
discard history.

Stale records stay queryable and are marked stale by pure comparison. Staleness never
silently rewrites the original record.

## 22. Test strategy and acceptance properties

Implementation will follow test-driven development. Required coverage includes:

### 22.1 Unit and property tests

- strict schema rejection, bounds, canonical ordering, and hash stability;
- unknown/self-reported capability fail-closed behavior;
- stable cohort ties and complete exclusion/gap reasons;
- diversity/independence separation, including same-model prompt variants;
- topology bounds, repeated-state loops, churn, monopoly, and every termination code;
- peer-count, abstract token/time/cost, tool-permission, and recursive-delegation
  limits;
- compiler DAG, artifact flow, capability, validator, resource, termination, forbidden
  operation, and progress-mapping checks;
- guidance and matrix matched-identity/confound logic;
- metadata availability truth tables and trace freshness;
- reward-validity truth tables and aggregate filtering; and
- deterministic output under permuted equivalent input ordering.

### 22.2 Transaction and repository tests

- exact idempotent accept and reject replay;
- concurrent same-intent submission under SQLite's writer boundary;
- atomic rollback for each handler after injected repository failure;
- append-only trigger enforcement;
- policy and audit attribution;
- stale-reference rejection; and
- capability-graph tests proving no cognitive object reaches transaction, repository,
  governance-write, artifact-write, or protected-answer authority.

### 22.3 Compatibility and integrity tests

- migration from a fixture at each released schema head;
- 0.2.0 rule and harness reconstruction coverage;
- full 0.3.0 projection reconstruction;
- public artifact tampering and protected-boundary leakage checks;
- 0.2.0 and 0.3.0 bundle import/export round trips;
- replay equality after process restart; and
- unknown schema/version fail-closed behavior.

### 22.4 Documentation, packaging, and example tests

- source-register schema and exact S30-S35 metadata checks;
- README, architecture, governance, security, threat model, reproducibility, claim
  ledger, and manual cross-reference checks;
- user-manual commit/version binding and manual-map coverage;
- clean wheel install with migration 0007 and the example present;
- all nine registered quality checks represented accurately; and
- offline example output stable across two fresh runs.

The release gate is the repository's complete quality suite, including format, lint,
types, tests, security, dependencies, build, package inspection, and clean-wheel
installation. Passing only focused tests is insufficient.

## 23. Documentation changes required after implementation

- `README.md`: explain the evidence-only cognitive plane and link the workflow.
- `ARCHITECTURE.md`: add package ownership, data flow, trust boundaries, and the
  control-plane/cognitive-plane distinction.
- `GOVERNANCE.md`: state that diversity never satisfies independence and rewards never
  grant admission authority.
- `SECURITY.md` and `THREAT_MODEL.md`: add peer, method, trace, metadata, and reward
  inputs plus prohibited hidden-reasoning and execution surfaces.
- `REPRODUCIBILITY.md`: define matched identities, freshness, unavailable metadata,
  matrix confounds, and replay expectations.
- `CLAIM_LEDGER.md`: add only claims supported by implementation tests; source-paper
  findings remain external evidence.
- `docs/USER_MANUAL.md`: update `MAN-01` document control and capability statuses;
  update `MAN-03` with the two-plane architecture; expand `MAN-05` with Research
  Coordinator, Capability Grounder, Peer Reasoner, Procedure Compiler, Procedure
  Validator, Cohort/Diversity Auditor, and Harness Trace Recorder roles; update
  `MAN-06` assignment guidance; add one cohesive `MAN-16` workflow from capability
  requirements through record inspection; extend `MAN-11` troubleshooting, `MAN-13`
  security, `MAN-14` glossary, and `MAN-15` source map; correct independence and
  quality-check descriptions; and bind the final manual to the implementation commit
  using the repository's two-commit convention. Every new role entry includes status,
  purpose, recommended actor/model type, required capabilities, authority,
  independence, inputs, outputs, failures, resolution, unsuitable model types, and
  source/code references.
- `docs/sources/source-register.yaml`: add exact S30-S35 entries and pinned repository
  boundaries.

## 24. Capability status at the target release

| Status | Capabilities |
| --- | --- |
| Implemented | strict contracts, pure grounding/selection/compiler/validation/analysis functions, governed persistence, replay, bundles, inspection |
| Example only | fixed in-memory peers, synthetic capability evidence, deterministic procedure execution, synthetic guidance and model-by-harness cells |
| Interface only | live LLM grounding, live peer adapters, harness-native training-payload handoff, provider-native generation-metadata ingestion |
| Experimental | operational-diversity diagnostics, guidance-gradient analysis, descriptive model-by-harness interaction, reward-hacking diagnostics |
| Deferred | Co-RL or other policy-gradient training, live model proxies, online weights, arbitrary external harnesses, automatic model/harness co-evolution, learned admission, self-modifying governance |

LEGO-RL contributes only trace-fidelity and future training-interface concepts; Co-RL
contributes cohort and diagnostic concepts without training or peer-derived authority;
DeAR contributes capability-routing and topology concepts without live agents or
governance decentralization.

## 25. Release and compatibility policy

The intended package version is 0.3.0 because the release adds significant public
typed contracts, proposal kinds, persistence, and CLI inspection while preserving
0.x compatibility where promised. Existing 0.2.0 workspaces and bundles remain
readable and verifiable. Existing CLI behavior and proposal schema hashes are not
silently redefined.

Deprecation is not required by this design. If implementation discovers that an
existing public contract must change, work stops and this design returns for approval.

## 26. Approval boundary

Human approval of this document authorizes a separate implementation plan. It does not
authorize live providers, reinforcement learning, external deployment, automatic
promotion, or any scope listed as a non-goal.

After approval, the next artifact will be a file-by-file, test-first implementation
plan with checkpoints. Until then, no production code, migration, version, source
register, or release documentation is changed.
