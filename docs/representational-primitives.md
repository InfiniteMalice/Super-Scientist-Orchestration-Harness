# Quarantined representational primitives

Representational primitives are immutable, versioned vocabulary candidates. A useful-sounding
definition, successful local test, or promotable status does not make one canonical. Every new
version stays quarantined until an accepted admission transaction advances its exact primitive
head after old-frame and new-frame evaluation, protected measurement, independent audit, human
approval, and rollback binding.

The registry distinguishes `INTRA_SPACE_TRANSFORMATION` from
`GENERATIVE_REPRESENTATION_PROPOSAL`. Its statuses are `PROPOSED`, `DUPLICATE_SUSPECTED`,
`UNDER_DEFINITION`, `EXPERIMENTAL`, `LOCALLY_USEFUL`, `REPLICATED`, `STABILIZED`, `REJECTED`,
`SUPERSEDED`, and `RETIRED`.

## Retained contracts

`PrimitiveVersion` retains stable primitive and version identities, semantic version,
transformation kind, definition, motivation, parent vocabulary, contrasts, examples and
counterexamples, construction method, expected uses, predecessors, dependencies, measurements,
falsification tests, ambiguity, the complete proposer identity, status, time, and governing
policy. Versions are append only.

`PrimitiveEvaluation` retains one typed frame evaluation, exact deterministic verification-result
IDs, artifact-verified controlled-experiment evidence, the complete evaluator and check-actor
identities, assessment provenance, findings, outcome, time, and governing policy. An
`OldFrameEvaluation` records preserved constraints, established tests, and regression findings.
A `NewFrameEvaluation` records novel predictions, independent operationalization, non-circular
tests, and retained evidence for later reuse. The complete typed evaluation is round-tripped
through the 0005 record, and redundant columns are reconciled when it is decoded.

Only primitive heads are mutable. A head is a rebuildable projection containing the exact
admitted version ID, semantic version, and status; it is not authoritative history.

## Fixed governed workflow

The transaction router exposes exactly three primitive proposal kinds:

1. `propose_primitive_version` validates policy, authority, dependencies, lineage, semantic
   versioning, and deterministic duplicate classification, then appends the version. It never
   writes a head. Exact resubmission is idempotent; changed content under a stable key is an
   audited `IDEMPOTENCY_CONFLICT`.
2. `record_primitive_evaluation` consumes the exact accepted candidate receipt and appends one
   typed old-frame or new-frame evaluation. Every result, deterministic mechanism, check actor,
   evidence artifact, policy hash, and timestamp must reconcile.
3. `admit_primitive_version` consumes the exact candidate, old-frame, new-frame, evaluator-audit,
   and protected-measurement receipts. It verifies passed checks, evidence and metric bindings,
   complete actor independence, causal order, the current head, and the rollback predecessor,
   then alone may advance the primitive head.

All three use one fixed classification: persistent `SKILL`, `HUMAN_IN_LOOP`,
`INDEPENDENT_DETERMINISTIC_CHECK`, `CONTROLLED_EXPERIMENT`, and `EMPIRICAL_MEASUREMENT`.
Governance V1, a missing V2 persistent-skill requirement, a changed classification, insufficient
grounding, or missing independent human approval fails closed. Protected evaluation and rollback
are promotion requirements; they are not falsely imposed on staging or evaluation.

The receipt chain is causal and content bound:

```text
accepted candidate
-> accepted old/new evaluations
-> accepted evaluator audit
-> accepted protected measurement
-> integration
-> independent human approval
-> primitive-head projection
```

Receipts bind proposal and audit hashes, not just logical IDs. Consequently, an admission cannot
substitute another candidate stage, evaluation, audit, or measurement that happens to name the
same entity.

## Semantic versioning and duplicates

Semantic versions are monotonic and follow the meaning of the change: patch for clarification,
minor for compatible expanded operationalization, and major for incompatible meaning. Build
metadata does not create a later version. Lineage names the exact current head when one exists,
or the exact latest retained predecessor before the first admission.

Duplicate detection is deterministic and grants no learned component promotion authority. Exact
or normalized-semantic overlap with another primitive is retained as `DUPLICATE_SUSPECTED`; it is
not silently discarded and cannot be admitted as canonical.

## Quarantine and independence

`EXPERIMENTAL` and every other non-promotable status remain quarantined. Even a
`LOCALLY_USEFUL`, `REPLICATED`, or `STABILIZED` version remains quarantined unless its version ID,
semantic version, and status exactly equal the canonical primitive head. The same shared gate is
used for canonical claim schemas, governance, active evaluators, adapter data, and public
conclusions.

The primitive author, both evaluators, every deterministic check actor, and the admission
approver must be pairwise independent. Independence is recomputed from actor ID and, when
present, provider, model, adapter, and configuration hash. Reusing an identity or any of those
model/configuration identities produces `CIRCULAR_EVALUATOR_APPROVAL`; a declaration of
independence cannot override the retained identities.

Capabilities are narrow. Staging can append/read versions and read heads; evaluation can append
evaluations and read only its exact receipts and supporting records; admission alone receives the
primitive-head writer. None receives generic repository, governance, quality-registry, protected
test, or arbitrary transaction authority.

## Replay and recovery

Whole-workspace verification rebuilds primitive versions, typed evaluations, and heads in audit
order. It reruns the same three live handlers under each transaction's historical policy and
uses only exact accepted receipts and records available at that point. It then compares the
reconstructed projections with 0005 storage.

Wrong historical authority, broken receipt chronology, changed stable content, missing typed
evaluations, extra records, duplicate admission, or a rewound/tampered primitive head invalidates
the workspace before the next mutation. Recovery restores authoritative transaction and audit
history and rebuilds projections; editing append-only primitive history is not supported.
