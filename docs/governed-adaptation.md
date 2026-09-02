# Governed Adaptation

SSOH treats adaptation as a proposed durable state transition, not ambient model
authority. Every persistent Phase A proposal is strictly parsed, deterministically
decided under the stored active policy, projected through narrow repositories, and only
then recorded by the shared coordinator as one transaction and one audit event.

## Configuration and execution state

Persistent metadata separates the foundation model from the scaffold's prompt, memory,
tools, and control layers. `ConfigurationVersion` contains those layers and rollback
lineage. Transient `ExecutionState` is not a configuration layer and has no authoritative
repository. Adapter training is represented by deterministic metadata only: dataset
lineage, candidate artifact hash, evaluation reference, and rollback configuration. The
kernel gains no model SDK, training SDK, GPU, filesystem, network, subprocess, dynamic
import, `eval`, or `exec` authority.

## Measurement and authority

`SelfImprovementMeasurementRecord` retains a stable change identifier, baseline and
candidate, protected metrics and countermetrics, full `m_0` through `m_T` trajectory,
attempted/admitted/rejected changes, failures, regressions, rollback events, separate
execution/search/evaluation/judging/human budgets, actual resource use, evaluator audit,
decision, and human authority. `T` is explicit and authoritative. Peak and final
observations must identify exact retained trajectory points, and every point binds the
same change and grounding. Aggregate change, regression, rollback, and resource histories
must exactly reconcile with all points. Resource use is retained per category at both the
point and record levels; no category can borrow another category's measurement budget or
dedicated run allocation. Best-only, final-only, omitted-intermediate, and under-reported
summaries are invalid durable evidence. Confidence, likelihood, self-consistency, textual
agreement, and correlated reviewers do not become evidence.

An evaluator audit recomputes identity independence. An evaluator, proposer, or candidate
producer cannot audit the evaluator when identities are equal, model configurations are
shared, organizational dependence is declared, or independence is unknown. Audit authority
is limited to formal verification, execution feedback, external empirical measurement,
independent deterministic checks, and independent learned judges; every other retained
`VerificationLevel` remains serializable but cannot authorize an evaluator audit.

## Constitutional policy transition

V1 remains active until an explicit `ProposeGovernancePolicyTransition` is accepted under
V1. The candidate policy cannot authorize any part of its own activation. A
source-controlled constitutional rule requires an independent human approval, a
non-closed-loop governance classification, a dedicated research run, a passed independent
evaluator audit, a complete accepted protected measurement, exact prior/candidate hashes,
compatibility checks, and an existing rollback policy.

After bootstrap, every V2-to-V2 transition and V2-to-V1 rollback must satisfy both the
source-controlled constitutional floor and the matching requirement in the active V2
policy. Candidate requirements are checked only for constitutional compatibility; the
candidate never grants authority for its own installation.

The coordinator's single database transaction projects in foreign-key-safe order:

1. dedicated research run;
2. evaluator audit;
3. self-improvement measurement;
4. candidate policy snapshot;
5. active-policy pointer.

Only after those projections does the coordinator append the proposal decision and audit
event. The event remains attributed to the prior V1 hash and records prior, candidate, and
rollback hashes. Any unexpected fault rolls back all relational changes. A rollback is
another governed proposal under the then-active policy, never a direct pointer update.

## Evaluator succession

Evaluator versions, thresholds, audit evidence, and succession decisions are append-only.
There is no automatic promotion. A candidate requires protected and external evaluation,
passed independent audit, human review, canary result, and its predecessor as rollback
target. Evaluator-version and succession proposals always use the fixed `EVALUATOR` plus
`EVALUATOR_POLICY` semantic classification; caller-selected unrelated requirements cannot
authorize them. A non-root candidate's accepted measurement must exactly match the proposal
classification and active policy, while its audit, version, and succession decision bind that
same active policy. Each gate result names its candidate evaluator version, stage, governing policy,
evidence, and assessment provenance. Gate identifiers and reviewers are distinct, weak
model-confidence categories are prohibited, reviewers are independent of the evaluator,
change proposer, and candidate producer, and gate evidence must be present in the bound
independent audit. Proposing a version does not update the evaluator head; only an accepted
succession decision changes that rebuildable projection.

## Workspace reconstruction

Before any proposal mutation, whole-workspace verification replays accepted audited
transactions and compares the result with all eight governed-adaptation record tables.
Rejected proposals produce no authoritative projection. Research-run heads are derived
from accepted event order. The evaluator head may be empty or identify the unique root
before the first succession; after that it is derived exactly from accepted succession
decisions. Accepted governance transitions likewise derive the registered policy chain and
active-policy pointer, while their audit payload must exactly bind prior, candidate, and
rollback hashes. The replay boundary is a fixed typed read-only storage snapshot and does
not broaden any proposal handler's repository authority.

## Cognitive-orchestration interlock

The 0.3.0 cognitive plane may retain capability profiles, cohort plans, collaboration
events, compiled procedures, guidance cells, model-by-harness evidence, traces, rewards,
and diagnostics. None of those records is an adaptation decision. When a cognitive
proposal is accepted, only its fixed append-only record family changes; claim, active
policy, evaluator, harness-campaign, and progress heads remain unchanged unless a
separate authorized handler performs that exact transition.

A valid compiled procedure may bind one canonical `ProgressPlan`, but the binding
handler delegates plan admission to the existing progress handler inside the same
transaction. A compiler, peer, reward, or cognitive facade cannot bypass the progress
policy. A high reward with any invalidating reward-hacking finding supplies no promotion
evidence. Verify these outcomes with `python -m pytest
tests/integration/application/test_procedure_service.py
tests/adversarial/test_cognitive_authority.py
tests/adversarial/test_trace_reward_tampering.py -q`.

Operational diversity is a diagnostic and does not satisfy actor independence. When
same-model prompt variants differ, the analyzer records the declared diversity axes and
the authority checker still treats their shared provider/model/adapter identity as
dependent. Peer consensus likewise cannot authorize an adapter, evaluator, policy,
claim, or procedure promotion.

## Source and reproduction boundary

S21 through S35 informed vocabulary and architecture. They are not reproduced. No source
code from those works is imported, and no benchmark gain, compatibility, or transfer claim
is made. The source register distinguishes each source proposal and evidence from SSOH's
adaptation and original synthesis. S33 contributes cohort diagnostics without training
authority, and S35 contributes trace/procedure concepts without reinforcement learning.
