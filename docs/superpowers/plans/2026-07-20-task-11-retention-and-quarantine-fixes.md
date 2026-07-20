# Task 11 Retention and Quarantine Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three canonical Task 11 review findings without changing the 0005 relational schema or expanding into Task 12.

**Architecture:** Extend the existing `PrimitiveVersionRecord.record_json` contract so it is a lossless, reconciled representation of the domain version. Replace live handler repository exposure with separate read façades and role-specific writers, then make every protected primitive-use decision resolve its version and head through an authorized read-only storage resolver.

**Tech Stack:** Python 3.12, strict Pydantic v2 models, SQLAlchemy/SQLite, pytest, Ruff, strict mypy.

## Global Constraints

- Preserve the Task 10 nullable verification-result decoder behavior.
- Preserve live and historical replay through the same Task 11 handlers.
- Do not add Task 12 behavior, dependencies, CI changes, or migration columns.
- Use one exact RED→GREEN cycle for each review finding.
- Create one final follow-up commit with subject `fix: enforce primitive retention and quarantine`.

---

### Task 1: Lossless primitive-version storage

**Files:**
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Modify: `src/super_scientist/application/representations/records.py`
- Modify: `tests/unit/representations/test_registry.py`
- Modify: `tests/property/test_hypothesis_primitive_append_only.py`

**Interfaces:**
- Produces: `primitive_version_from_storage(record: PrimitiveVersionRecord) -> PrimitiveVersion`.
- Extends: `PrimitiveVersionRecord` with `transformation_kind`, complete `proposer`, and retained redundant `proposer_id` validation.

- [x] **Step 1: Write failing lossless/collision tests**

```python
def test_primitive_version_storage_round_trip_is_exact() -> None:
    primitive = _version()
    record = primitive_version_to_storage(primitive)
    assert record.transformation_kind == primitive.transformation_kind
    assert record.proposer == primitive.proposer
    assert primitive_version_from_storage(record) == primitive


@pytest.mark.parametrize(
    "changed",
    (
        {"transformation_kind": TransformationKind.INTRA_SPACE_TRANSFORMATION},
        {"proposer": _actor("different-proposer", model=True)},
    ),
)
def test_primitive_record_hash_changes_for_semantically_distinct_identity(changed: dict[str, object]) -> None:
    original = primitive_version_to_storage(_version())
    candidate = primitive_version_to_storage(_version().model_copy(update=changed))
    assert canonical_json_bytes(original.model_dump(mode="json")) != canonical_json_bytes(
        candidate.model_dump(mode="json")
    )
```

- [x] **Step 2: Run the focused tests and confirm RED because fields/inverse are absent**

Run: `python -m pytest tests/unit/representations/test_registry.py -q`

- [x] **Step 3: Implement the JSON-only record extension and exact inverse**

```python
class PrimitiveVersionRecord(_StrictFrozenStorageRecord):
    transformation_kind: TransformationKind
    proposer: ActorIdentity
    proposer_id: StableIdentifier

    @model_validator(mode="after")
    def reconcile_proposer(self) -> Self:
        if self.proposer_id != self.proposer.actor_id:
            raise ValueError("proposer_id must match the retained proposer identity")
        return self


def primitive_version_from_storage(record: PrimitiveVersionRecord) -> PrimitiveVersion:
    primitive = PrimitiveVersion(...)
    if primitive_version_to_storage(primitive) != record:
        raise ValueError("primitive version storage record does not round-trip exactly")
    return primitive
```

- [x] **Step 4: Add repository tamper tests for unknown JSON fields, proposer mismatch, transformation collision, and identity-dimension collisions**

- [x] **Step 5: Run the unit and Task 10 append-only storage tests and confirm GREEN**

Run: `python -m pytest tests/unit/representations/test_registry.py tests/property/test_hypothesis_primitive_append_only.py -q`

### Task 2: Runtime least-authority capability façades

**Files:**
- Modify: `src/super_scientist/application/transactions/representations.py`
- Modify: `src/super_scientist/application/transactions/coordinator.py`
- Modify: `tests/integration/application/test_representation_service.py`

**Interfaces:**
- Produces: `RepresentationCapabilitySet(reads, writes)`.
- Produces role-specific public writer surfaces: `PrimitiveVersionAppender.append_version`, `PrimitiveEvaluationAppender.append_evaluation`, and `PrimitiveHeadSetter.set_head_from_candidate_receipt`.

- [x] **Step 1: Replace the annotation-only test with failing real-instance surface tests**

```python
@pytest.mark.integration
def test_live_representation_capabilities_expose_only_role_authority(
    representation_runtime: RepresentationRuntime,
) -> None:
    stage = _live_capabilities(representation_runtime, _stage_proposal(...))
    evaluation = _live_capabilities(representation_runtime, _evaluation_proposal(...))
    admission = _live_capabilities(representation_runtime, _admission_proposal(...))
    assert _public_methods(stage.writes) == {"append_version"}
    assert _public_methods(evaluation.writes) == {"append_evaluation"}
    assert _public_methods(admission.writes) == {"set_head_from_candidate_receipt"}
    assert not _public_graph_contains_mutable_repository(stage.reads)
    assert not _public_graph_contains_mutable_repository(evaluation.reads)
    assert not _public_graph_contains_mutable_repository(admission.reads)
```

- [x] **Step 2: Run the exact capability test and confirm RED on concrete repository exposure**

Run: `python -m pytest tests/integration/application/test_representation_service.py -k capability -q`

- [x] **Step 3: Introduce narrow readers, separate writers, and the capability pair**

```python
@dataclass(frozen=True)
class RepresentationCapabilitySet:
    reads: HandlerReadCapability
    writes: HandlerWriteCapability


@dataclass(frozen=True)
class PrimitiveVersionAppender:
    _versions: PrimitiveVersionRepository
    _stages: PrimitiveStageHistoryReader

    def append_version(self, primitive: PrimitiveVersion) -> None:
        ...
```

- [x] **Step 4: Update coordinator wiring and projection methods to consume only their role writer**

- [x] **Step 5: Run capability, coordinator, and replay tests and confirm GREEN**

Run: `python -m pytest tests/integration/application/test_representation_service.py tests/integration/application/test_transaction_coordinator.py tests/integration/application/test_workspace_integrity.py -q`

### Task 3: Storage-resolved primitive quarantine

**Files:**
- Modify: `src/super_scientist/application/representations/service.py`
- Modify: `src/super_scientist/application/transactions/representations.py`
- Modify: `tests/unit/representations/test_registry.py`
- Modify: `tests/integration/application/test_representation_service.py`

**Interfaces:**
- Produces: `PrimitiveRetentionResolver` read protocol with `get_stored_version` and `get_head`.
- Changes: `primitive_use_rejection(candidate_version_id: str, *, resolver: PrimitiveRetentionResolver, use: PrimitiveUse)`.

- [x] **Step 1: Write failing all-use-category orphan, fabricated-head, and retained-mismatch tests**

```python
@pytest.mark.parametrize("use", tuple(PrimitiveUse))
def test_protected_primitive_use_requires_storage_resolved_exact_head(use: PrimitiveUse) -> None:
    assert primitive_use_rejection("missing", resolver=_Resolver(), use=use) is QUARANTINED
    assert primitive_use_rejection("candidate", resolver=_Resolver(head_only=True), use=use) is QUARANTINED
    assert primitive_use_rejection("candidate", resolver=_Resolver(mismatched=True), use=use) is QUARANTINED
```

- [x] **Step 2: Run the exact unit tests and confirm RED because the gate accepts caller state**

Run: `python -m pytest tests/unit/representations/test_registry.py -k quarantine -q`

- [x] **Step 3: Implement storage resolution and exact domain/storage reconciliation**

```python
def primitive_use_rejection(
    candidate_version_id: str,
    *,
    resolver: PrimitiveRetentionResolver,
    use: PrimitiveUse,
) -> RejectionCode | None:
    stored = resolver.get_stored_version(candidate_version_id)
    if stored is None:
        return RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
    retained = primitive_version_from_storage(stored)
    head = resolver.get_head(retained.primitive_id)
    if head != (retained.primitive_version_id, retained.semantic_version, retained.status):
        return RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
    return None if status_is_promotable(retained.status) else RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
```

- [x] **Step 4: Update the real storage consumer and integration assertion**

- [x] **Step 5: Run all representation tests and confirm GREEN**

Run: `python -m pytest tests/unit/representations tests/adversarial/test_primitive_circularity.py tests/integration/application/test_representation_service.py -q`

### Task 4: Documentation, verification, and focused follow-up commit

**Files:**
- Modify: `docs/representational-primitives.md`
- Modify: `.superpowers/sdd/task-11-report.md` (ignored handoff report)

- [x] **Step 1: Document lossless JSON retention, role-specific capabilities, and resolver-owned quarantine**

- [x] **Step 2: Run focused Task 11 and adjacent Task 10/coordinator/workspace tests**

- [x] **Step 3: Run bounded full repository inventory, branch coverage, Ruff, owned formatting, strict mypy, dependency/security/package checks, and `git diff --check`**

- [x] **Step 4: Review the final diff against all three findings and verify the ignored report exists**

- [x] **Step 5: Commit once with the exact required subject**

Run: `git commit -m "fix: enforce primitive retention and quarantine"`
