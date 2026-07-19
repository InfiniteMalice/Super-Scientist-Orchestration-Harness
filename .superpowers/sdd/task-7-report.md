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

## Review hardening follow-up

The post-review hardening closes the epistemic-authority gaps without changing migrations,
dependencies, the legacy `EvidenceRecord` wire format, or the prior commit. The implementation now
requires exact category scopes and deterministic checker IDs, typed causal endpoint support,
fresh successor validation artifacts, retained primary-source grounding, canonical source-first
stage provenance, complete actor/source independence, derived graph semantics, unique V2 policy
keys, and exact report relevance.

### RED to GREEN checkpoints

- Assessment/check authority: **27 failed, 20 passed** to **47 passed** focused; evidence-trail
  unit suite **68 passed** at that checkpoint.
- Causal authority: **7 failed, 5 passed, 43 deselected** to **12 passed, 43 deselected**.
  Fresh-successor integration: **1 failed** to **1 passed**; selected successor/replay set
  **4 passed**.
- Primary/source-first authority: **11 failed, 55 deselected** to **12 passed, 55 deselected**;
  evidence-trail unit **88 passed** and trail integration **16 passed**.
- Complete independence: assessor **3 failed, 67 deselected** and approver
  **3 failed, 16 deselected** to **3 passed** in each focused set; evidence-trail unit
  **91 passed**, trail integration **19 passed**.
- Graph/structure/relation/conflict semantics: **22 failed, 70 deselected** to
  **22 passed, 70 deselected**; evidence-trail unit/property **153 passed**, trail integration
  **19 passed**.
- V2 requirement uniqueness: unit **2 failed** and storage **2 failed, 5 deselected** to policy
  unit **16 passed** and combined policy-storage/trail integration **26 passed**.
- Exact report relevance: **7 failed, 92 deselected** to **7 passed, 92 deselected**; combined
  evidence-trail unit/property/application **179 passed**, trail integration **20 passed**.

### Hardening quality gates

- Ruff: `ruff check src tests` — **all checks passed**.
- Strict mypy: `mypy` — **success, 74 source files**.
- `git diff --check` — **passed** (Git emitted only configured LF-to-CRLF notices).
- Final complete suite: `pytest -q` — **1,050 passed, 3 skipped, 6 expected negative-test serialization warnings in 365.18s**.

## Auditable evidence-trail stage follow-up

The final review follow-up replaces caller-authored stage events and timestamps with exact
accepted proposal/audit receipt references. It adds two fixed, no-projection stage proposals,
reuses accepted `AddEvidence`, `ProposeClaim`, and `TransitionClaim` transactions, and verifies the
same policy, approval, classification, actor-independence, chronology, and graph-content authority
both live and during workspace replay. Successors now require a fresh contiguous claim transition;
trail outcomes are derived, not drafted; and identity relations use typed legacy provenance keys.
The legacy evidence/claim proposal JSON and hashes, migrations, dependencies, and generic authority
surface remain unchanged.

### RED to GREEN checkpoints

- Baseline before this follow-up: **1,050 passed, 3 skipped, 6 warnings in 406.03s**.
- Stage contracts and routing: initial **4 failed** in 5.29s, followed by negative stage-authority
  cases; the complete cluster reached **11 passed** in 8.61s.
- Receipt-only provenance: **2 failed, 99 deselected** before implementation, then
  **2 passed, 99 deselected**. Caller-authored stage event IDs/timestamps are no longer accepted.
- Durable successor lineage: **1 failed, 27 deselected** while replay retained the old claim, then
  **1 passed, 27 deselected** in 4.42s with an accepted fresh `TransitionClaim` receipt.
- Derived outcome/conflict semantics: **2 failed, 28 deselected**, then
  **3 passed, 28 deselected** in 6.03s, including a durable successor with opposing evidence,
  `CONTRADICTS`, and passed counterevidence deriving `CONFLICTED`.
- Typed exact identity provenance: **2 failed, 6 passed, 95 deselected**, then
  **8 passed, 95 deselected**.
- Shared live/replay authority: **1 failed, 31 deselected** before fixed classification was shared,
  then the classification/backdating/inactive-policy set passed **3 tests, 31 deselected** in
  4.49s. Dependent approval, wrong classification, non-primary grounding, protected-evaluation,
  rollback, and wrong historical-policy replay cases passed **6 tests, 34 deselected** in 5.77s.
- Combined focused gate: **519 passed in 212.38s**.
- The first full suite found one historical-policy regression: **1 failed, 1,073 passed,
  3 skipped in 416.65s**. Workspace replay had incorrectly treated an extra registered, inactive
  policy as corruption even when no transition activated it. The check was narrowed to reject only
  audit events that actually use the wrong historically active policy; the exact regression plus
  wrong-historical-policy replay test then passed **2 tests in 4.69s**.

### Follow-up quality gates

- Ruff: `ruff check src tests` — **all checks passed**.
- Strict mypy: `mypy` — **success, 75 source files**.
- `git diff --check` — **passed** (Git emitted only configured LF-to-CRLF notices).
- Final complete suite: `pytest -q` — **1,074 passed, 3 skipped in 411.52s**.
