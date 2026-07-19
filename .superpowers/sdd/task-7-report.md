# Task 7 implementation report: source-bound evidence trails

## Outcome

Implemented governed, versioned natural evidence trails on base
`f685014a56b0426b70b416d8e54a84a011f7f74e`. The implementation adds strict graph contracts,
source-byte validation, independent assessment and causality gates, immutable successor builders,
two fixed transaction proposal kinds and handlers, six fixed storage wrappers, derived report
bindings, and whole-workspace semantic replay. No migration or dependency files were changed.

## Delivered behavior

- Closed vocabularies and strict frozen models cover every relation, outcome, structural-location,
  check, assessment, modality, node-role, geometry, and construction-method value.
- `validate_trail()` recomputes artifact fidelity, exact UTF-8 spans, structural bounds, graph
  partitions, relation scope/endpoints, ordering/cycles, temporal semantics, modality, necessity,
  independent assessment provenance, causal support, deterministic check results, and final status.
- `validate_report_binding()` retains exact source spans, contradictions, opposing nodes,
  uncertainty, modality, claim version, trail version, outcome, and policy. Bindings cannot admit or
  transition claims.
- `EvidenceTrailVersionBuilder` creates version one and builds complete immutable `add_node()` /
  `add_relation()` successors with new version-scoped children; prior snapshots remain unchanged.
- Fixed handlers for `record_evidence_trail_version` and `bind_report_sentence` are registered in
  the explicit router. Governance V1 fails closed. V2 requires the exact governed run-local
  research-process requirement, primary-source grounding, independent deterministic verification,
  and independent human approval.
- Projection is atomic: version, nodes, relations, checks, assessments, and head commit together;
  injected failure rolls all of them, the transaction, and audit decision back. Rejections retain
  audit/transaction history and project nothing.
- Exactly six public append-only record repositories bind the 0003 trail tables to their strict
  records and relationships. Trail heads require version one and exact monotonic successors.
- Workspace integrity reconstructs all trail/binding projections and heads solely from accepted
  audited transactions, rereads retained claims/evidence/artifacts, and reruns semantic validation.
  Missing/extra/corrupt/reparented/rewound/cross-version state and forged bindings fail closed.
- `verified_artifact_bytes()` safely extends evidence verification so consumers receive bytes only
  after artifact and extracted-span verification.
- `docs/evidence-trails.md` documents source-first construction, validation, authority, immutable
  revision, report-binding, atomicity, and recovery behavior.

## TDD and verification evidence

- Initial absence RED: the mandated focused test command failed because the evidence-trail test
  package/modules did not exist.
- Contract/validator cycle: focused unit suite reached 21 passing tests.
- Closed-vocabulary property coverage: 40 passing cases, including every relation type, outcome,
  structural-location kind, and assessment category.
- Repository/shared-storage cycle: public-wrapper, canonical round-trip, corruption, monotonic-head,
  append-only, and 0003 compatibility coverage passed.
- Application cycle: fixed routing, V1/V2 policy behavior, exact replay, causal rejection, atomic
  rollback, immutable v1-to-v3 successors, binding non-authority, missing child, head rewind, and
  forged binding coverage passed.
- Combined Task 7/shared-storage/workspace command: **182 passed** in 65.61 seconds.
- First full-suite run exposed one stale pre-Task-7 exact `ProposalKind` expectation:
  **940 passed, 3 skipped, 1 failed**. The expectation was updated from thirteen to fifteen fixed
  additive persistent kinds. Its targeted proposal/trail/workspace rerun passed **53 tests**.
- Fresh unchanged full suite: **941 passed, 3 skipped** in 452.79 seconds.
- Ruff: `ruff check src tests` — **all checks passed**.
- Strict mypy: `mypy src/super_scientist` — **success, 60 source files**.
- `git diff --check` — **passed** (only Git's configured LF-to-CRLF notices).

## Commit

The implementation is committed with the required message:

`feat: add source-bound evidence trails`
