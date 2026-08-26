# Atomic Claim Ledger

## Claim Record

An `AtomicClaim` is a frozen, versioned record with these fields:

- `claim_id` and integer `version`;
- nonblank `proposition`, `scope`, `population_or_system`, and
  `epistemic_modality`;
- `status`, zero or more evidence links, and explicit assumptions;
- `parent_version_id` for versions after version 1; and
- creation timestamp and actor identity.

Version 1 has no parent. Every later version must name
`<claim_id>:<previous-version>` as its parent. Claim versions are append-only; the head
table is a validated effective-state projection. A new claim must begin `PROPOSED`.
External claim and evidence-link JSON forbids unknown fields; additions require an
explicit schema change and cannot be silently discarded before proposal hashing.

The atomic-fact evaluation framing is inspired by SciConBench [S02]. This project uses
its own claim schema and deterministic validators, imports no benchmark content or
source code, and makes no claim to reproduce SciConHarness or SciConBench results.

## Legal Transitions

The implemented directed graph is:

```text
PROPOSED -> EVIDENCE_LINKED | FALSIFIED | WITHDRAWN
EVIDENCE_LINKED -> TESTABLE | CORROBORATED | CONSTRAINT_VALIDATED | FALSIFIED | WITHDRAWN
TESTABLE -> REPRODUCED | CORROBORATED | CONSTRAINT_VALIDATED | FALSIFIED | WITHDRAWN
REPRODUCED -> CORROBORATED | FALSIFIED | SUPERSEDED | WITHDRAWN
CORROBORATED -> FALSIFIED | SUPERSEDED | WITHDRAWN
CONSTRAINT_VALIDATED -> CORROBORATED | FALSIFIED | SUPERSEDED | WITHDRAWN
FALSIFIED -> SUPERSEDED
SUPERSEDED -> (terminal)
WITHDRAWN -> (terminal)
```

`TransitionClaim` carries the complete intended next `AtomicClaim`, not only a target
status. Admission validates that exact record: claim identity content is immutable, the
version and parent must be the exact successor, and `created_by` must match the
transition proposer. The application service projects the admitted record unchanged.
Every edge absent from the graph is rejected. The current CLI exposes only claim
proposal and history commands.

Evidence ingestion actors and initial claim creators must equal their proposal
proposer. A withdrawal changes only status plus required version, lineage, timestamp,
and creator metadata; proposition, scope, system, modality, assumptions, and evidence
links remain exact. It skips admission-time evidence requirements but workspace
verification still validates every historical link on a withdrawn record.

## Evidence Spans

Every status except `PROPOSED` and `WITHDRAWN` requires at least one evidence link. A
link contains an evidence identifier and a nonblank supporting text span. The linked
evidence record may contain an extracted span whose offsets must be nonnegative,
nonempty, and exactly match the extracted text length.

During transition admission, `source_exists` requires the linked evidence identifier to
exist, and `evidence_span_exists` requires the supporting text to occur in that evidence
record's extracted span. The default active policy requires both checks. Missing sources,
missing extracted text, or absent supporting text cause deterministic rejection.
`EVIDENCE_LINKED` must add at least one valid link. `WITHDRAWN` is a terminal intent and
does not run generic evidence checks.

## Deterministic And Review-Required Outcomes

Claim checks have four typed outcomes: `PASS_DETERMINISTIC`, `FAIL_DETERMINISTIC`,
`REQUIRES_INDEPENDENT_REVIEW`, and `NOT_APPLICABLE`. Any deterministic failure rejects
the transition as `MISSING_EVIDENCE`. A review-required result, a missing required check,
or a required check that did not deterministically pass rejects it as
`INDEPENDENT_REVIEW_REQUIRED`.

The current source and span validators produce deterministic pass, fail, or
not-applicable results. Distinct reproduction records, independent corroboration
reviews, encoded constraint proofs, typed counterevidence/falsification review, and
successor references are not implemented. Transitions to `REPRODUCED`, `CORROBORATED`,
`CONSTRAINT_VALIDATED`, `FALSIFIED`, and `SUPERSEDED` therefore fail closed as
`INDEPENDENT_REVIEW_REQUIRED`; ordinary evidence links alone cannot confer those
statuses. These statuses do not certify scientific truth. Source metadata for [S02] is
maintained in `docs/sources/source-register.yaml`.

## Governed-Adaptation Boundary

Research-run progress, evidence-trail structure, hypothesis simulation, reviewer
agreement, behavioral-rule admission, handbook mapping, harness-campaign performance,
and self-improvement measurements are separate typed domains. None automatically
creates a claim, changes a claim head, or satisfies a review-required claim transition.
In particular, a coherent trail is evidence rather than proof, provisional progress is
not completion, deterministic simulator agreement is conditional on the implemented
model, and discovery-set improvement is not general scientific corroboration.

Workspace export carries accepted and rejected claim proposals and rebuildable claim
projection expectations. Import replays those proposals through the original governing
policy and verifies the resulting heads and history; it does not trust the exported
projection expectation as authority. Exact content may replay idempotently. Changed
content under an existing identity is an audited conflict and cannot revise claim
history.

## Governed Cognitive Evidence Boundary

The 0.3.0 test suite supports these implementation claims and no broader scientific
claims:

- When unanimous peer contributions are accepted and later cited without the required
  authoritative claim evidence, the claim-transition handler rejects the transition
  and leaves the claim head unchanged. Verify with
  `python -m pytest tests/adversarial/test_cognitive_authority.py -q`.
- When same-model prompt variants produce different contributions, the diversity
  analyzer may record operational diversity, but the authority checks do not count the
  variants as independent reviewers. Verify with
  `python -m pytest tests/adversarial/test_cognitive_authority.py -q`.
- When a recomputed procedure compilation has domain status `INVALID`, the coordinator
  accepts and retains that compilation history but creates no progress plan or binding.
  A later binding attempt is rejected with transaction code `INVALID_PROCEDURE`. Verify
  with `python -m pytest tests/integration/application/test_procedure_service.py
  tests/adversarial/test_procedure_escalation.py -q`.
- When a trace or reward proposal contains fabricated, stale, or derivation-mismatched
  evidence, the harness handlers reject the proposal and leave claim, policy, harness,
  and progress heads unchanged. When a correctly recomputed reward assessment contains
  invalidating evidence, the handler accepts and retains domain status `INVALID` but
  excludes the assessment from positive and promotion evidence. Verify with
  `python -m pytest tests/adversarial/test_trace_reward_tampering.py -q`.

Capability profiles, cohort agreement, topology, compiled procedures, guidance cells,
model-by-harness analyses, traces, and rewards are not `AtomicClaim` evidence links by
themselves. Findings reported by S30-S35 remain external source evidence marked
`not_reproduced`; this ledger does not claim those findings as project results.
