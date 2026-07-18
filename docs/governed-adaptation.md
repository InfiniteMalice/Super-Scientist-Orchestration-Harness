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
decision, and human authority. Best-only and final-only summaries are invalid durable
evidence. Confidence, likelihood, self-consistency, textual agreement, and correlated
reviewers do not become evidence.

An evaluator audit recomputes identity independence. An evaluator, proposer, or candidate
producer cannot audit the evaluator when identities are equal, model configurations are
shared, organizational dependence is declared, or independence is unknown.

## Constitutional policy transition

V1 remains active until an explicit `ProposeGovernancePolicyTransition` is accepted under
V1. The candidate policy cannot authorize any part of its own activation. A
source-controlled constitutional rule requires an independent human approval, a
non-closed-loop governance classification, a dedicated research run, a passed independent
evaluator audit, a complete accepted protected measurement, exact prior/candidate hashes,
compatibility checks, and an existing rollback policy.

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
target. Proposing a version does not update the evaluator head; only an accepted succession
decision changes that rebuildable projection.

## Source and reproduction boundary

S21 through S29 informed vocabulary and architecture. They are not reproduced. No source
code from those works is imported, and no benchmark gain, compatibility, or transfer claim
is made. The source register distinguishes each source proposal and evidence from SSOH's
adaptation and original synthesis.
