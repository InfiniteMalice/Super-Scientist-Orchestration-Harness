# Evidence Trail Epistemic Authority Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every review gap that can turn unsupported, non-primary, conclusion-first,
dependent, stale, or semantically forged evidence graphs into authoritative trails or report
bindings.

**Architecture:** Keep the existing 0003 append-only tables and legacy `EvidenceRecord` wire
format. Add strict Task 7 records inside canonical trail JSON, parse grounding and source structure
from existing immutable evidence mappings, and make one pure validator the authority used by both
admission and workspace replay. Source-controlled tables derive exact scopes, IDs, checker identity,
relation rules, graph geometry, causal positions, assessment outcomes, and report relevance.

**Tech Stack:** Python 3.12, Pydantic v2 strict frozen models, SQLAlchemy/SQLite, pytest,
Hypothesis, Ruff, strict mypy.

## Global Constraints

- Do not amend commit `28303f7`, push, or modify migrations/dependencies.
- Do not change legacy `EvidenceRecord` serialization or hashes.
- Do not expose generic repository, transaction, or dynamic authority.
- Do not weaken Task 2/4/5/6 validation, storage, policy, or workspace integrity.
- Every production behavior change follows an observed RED then GREEN test command.
- Commit only after final gates as `fix: enforce evidence trail epistemic authority`.

---

### Task 1: Exact assessment and deterministic-check authority

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/models.py`
- Create: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/property/test_evidence_trail_graphs.py`

**Interfaces:**
- Produce `TrailScope`, `required_assessment_scope(category, snapshot)`,
  `trusted_check_id(trail_version_id, category)`, `trusted_assessment_id(...)`,
  `TRUSTED_TRAIL_CHECKER_ID`, `TRUSTED_TRAIL_CHECKER_VERSION`, and an explicit 8-by-4 assessment
  outcome matrix.
- Require version/check/assessment IDs, category order, scopes, findings, checker identity/version,
  and timestamps as exact canonical tuples/values.

- [x] Add parameterized tests for all 32 category/outcome pairs and empty, partial, duplicate, and
  reordered scope/ID/check/finding cases.
- [x] Run only those tests and verify expected failures from current set-normalized logic.
- [x] Add strict scope records, source-controlled ID/checker functions, the category scope table,
  exact tuple validation, and aggregate outcome matrix.
- [x] Run Task 1 unit/property tests and verify GREEN.

### Task 2: Exact causal support and fresh successor authority

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/models.py`
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- Replace `tuple[StableIdentifier, ...]` causal support with strict `tuple[CausalSupport, ...]`.
- `required_causal_support(relation, nodes)` returns source/target support in endpoint order, bound
  to exact version, relation, node, evidence, span, and content hash.
- `add_node()` and `add_relation()` require caller-supplied fresh complete checks, assessments, and
  source-first provenance; they never clone or widen prior results.

- [x] Add RED tests for temporal-only, co-occurrence, arbitrary evidence/span, missing/stale causal
  assessment, and all three causal relation types.
- [x] Add RED integration coverage proving copied prior checks/assessments cannot authorize a
  changed successor graph.
- [x] Implement exact endpoint support, identical causal gates for `CAUSES_CANDIDATE`, `ENABLES`,
  and `PREVENTS`, and fresh successor input requirements.
- [x] Run Task 2 unit/application tests and verify GREEN.

### Task 3: Retained primary-source and source-first process provenance

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/models.py`
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `src/super_scientist/application/transactions/trails.py`
- Modify: `src/super_scientist/application/workspace_integrity.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `parse_external_grounding(evidence) -> ExternalGrounding` reads exact provenance key
  `external_grounding` and fails closed for absent, non-string, or unknown values.
- Add strict `SourceFirstStageEvent` and `SourceFirstProvenance`; deterministic stage event IDs bind
  exact source/evidence/artifact hashes, node IDs/content hashes, relation IDs/canonical hashes,
  claim version/hash, full actors, and UTC timestamps.
- Enforce source retrieval < node proposal < relation proposal < claim creation < assessment/check
  completion < final version creation, with exact stage sets.

- [x] Add RED tests for model/synthetic/mixed/missing/unknown grounding and conclusion-first,
  missing, reordered, and fabricated process stages.
- [x] Implement parsers, records, deterministic event binding, stage ordering, and policy-derived
  every-source primary grounding in handler and replay.
- [x] Add later durable-provenance forgery replay test and run Task 3 tests GREEN.

### Task 4: Complete actor/source independence

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `trail_actors_are_independent(left, right)` rejects actor IDs, shared model/provider/adapter, and
  shared non-null configuration hashes.
- Assessors and human approvers are compared against builder/proposer, claim author actor ID, every
  covered source ingestion actor ID, and every full source-stage actor regardless of source order.

- [x] Add RED multi-source tests for second-ingestor alias, claim-author alias, approver alias,
  shared model alias, and shared configuration alias.
- [x] Implement complete deterministic independence and exact source coverage.
- [x] Run Task 4 tests GREEN.

### Task 5: Derived graph, structure, relation, and conflict semantics

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/property/test_evidence_trail_graphs.py`

**Interfaces:**
- `parse_source_structure(evidence)` reads strict
  `structured_observation.source_structure={schema_version: 1, locations: [...]}`.
- `derive_geometry(snapshot)` deterministically returns `LINEAR`, `CONVERGENT`, `DIVERGENT`,
  `BRANCHED`, or `NETWORK` from canonical graph topology.
- `RELATION_SCHEMAS` has one explicit entry for all 13 relation types covering roles, modality,
  temporal, causal, identity, and contradiction constraints.
- Causal positions equal deterministic DAG layers for causal members and are `None` otherwise.
- `CONFLICTED` requires exact opposing nodes participating in `CONTRADICTS` plus a PASSED exact-scope
  counterevidence assessment.

- [x] Add RED tests for forged geometry, causal positions, structure kind/locator/bounds, all 13
  relation schemas, and opposing-without-contradiction.
- [x] Implement exact parsers and semantic derivations.
- [x] Run Task 5 unit/property tests GREEN.

### Task 6: Unique GovernancePolicyV2 requirement keys

**Files:**
- Modify: `src/super_scientist/config/models.py`
- Modify: `tests/unit/config/test_policy_versions.py`
- Modify: `tests/integration/storage/test_policy_versions.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- A strict after-model validator rejects duplicate `(change_target, persistence)` pairs without
  changing enum values, GovernancePolicyV1 parsing, or existing unique V2 hashes.

- [x] Add reversed-order duplicate requirement RED tests.
- [x] Implement uniqueness validation.
- [x] Run affected Task 2/4 policy hash, codec, storage-history, governance, and trail tests GREEN.

### Task 7: Exact relevant report bindings and semantic replay

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `src/super_scientist/application/workspace_integrity.py`
- Modify: `tests/unit/evidence_trails/test_validation.py`
- Modify: `tests/integration/application/test_trail_service.py`
- Modify: `docs/evidence-trails.md`
- Modify: `.superpowers/sdd/task-7-report.md`

**Interfaces:**
- `required_report_nodes(snapshot, outcome)` returns exact canonical relevant nodes; source spans
  must equal those nodes in the same order.
- Contradiction/opposing IDs equal actual `CONTRADICTS` participants; conflicted bindings require
  PASSED counterevidence, retained uncertainty, and non-overclaiming modality.
- Workspace replay calls the same grounding, provenance, independence, graph, assessment, causal,
  and report validators before any new mutation.

- [x] Add RED partial/irrelevant/duplicate/reordered binding tests and durable semantic-forgery
  replay tests.
- [x] Implement exact report relevance and replay checks.
- [x] Run focused Task 7/unit/property/application/integrity/storage and affected policy suites.
- [x] Run Ruff, strict mypy, and diff checks; append exact RED/GREEN evidence to the Task 7 report.
- [x] Run one complete suite with sufficient timeout.
- [x] Commit all reviewed changes as `fix: enforce evidence trail epistemic authority` and verify a
  clean worktree without caches.
