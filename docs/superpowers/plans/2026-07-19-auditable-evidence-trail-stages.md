# Auditable Evidence-Trail Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace self-attested evidence-trail chronology with accepted transaction/audit receipts,
then close successor-status, identity-relation, and historical replay-authority gaps.

**Architecture:** Add exactly two no-projection proposal kinds to the fixed router. Resolve strict
receipt references through a narrow read-only transaction/audit reader and validate the same receipt,
policy, approval, graph, and identity semantics in live handlers and workspace replay. Continue using
the existing append-only transaction and audit tables without migrations.

**Tech Stack:** Python 3.12, Pydantic v2 strict frozen models, SQLAlchemy/SQLite, pytest, Hypothesis,
Ruff, strict mypy.

## Global Constraints

- Do not amend `bc59b77`, push, migrate schema, or change dependencies.
- Do not change legacy `AddEvidence`, `ProposeClaim`, `TransitionClaim`, or `EvidenceRecord` JSON/hashes.
- Do not expose a generic repository, transaction writer, audit writer, commit, or rollback capability.
- Preserve the existing assessment matrix, exact scopes/checkers, causal support, graph derivation,
  duplicate policy keys, report bindings, fixed repositories, atomicity, and replay gates.
- Every production change follows an observed focused RED caused by the missing behavior.
- Commit once as `fix: make evidence trail stages auditable` after fresh complete verification.

---

### Task 1: Durable stage proposals and accepted receipt ordering

**Files:**
- Modify: `src/super_scientist/kernel/transactions/models.py`
- Modify: `src/super_scientist/providers/storage/repositories.py`
- Create: `src/super_scientist/application/trails/receipts.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `src/super_scientist/application/transactions/trails.py`
- Modify: `src/super_scientist/application/transactions/coordinator.py`
- Modify: `tests/unit/improvement/test_models.py`
- Modify: `tests/integration/storage/test_repositories.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `AcceptedProposalReceiptRef` stores only proposal/audit IDs and canonical hashes.
- Typed refs fix proposal kinds for `add_evidence`, `propose_evidence_trail_nodes`,
  `propose_evidence_trail_relations`, `propose_claim`, and `transition_claim`.
- `ProposeEvidenceTrailNodes` binds future trail/version, fixed classification, ordered source refs,
  and exact nodes.
- `ProposeEvidenceTrailRelations` binds future trail/version, accepted node-stage ref, exact ordered
  node IDs plus `canonical_node_set_hash(nodes)`, and exact relations.
- `StoredTransaction.created_at: UtcTimestamp` is decoded from the existing database column.
- `AcceptedProposalReceiptReader.get(ref)` resolves only an accepted transaction with one exact
  persisted audit event and returns its immutable proposal, database timestamp, audit sequence/time,
  and governing historical policy hash.

- [ ] **Step 1: Write proposal-union, stored-time, router, and stage persistence tests**

```python
def test_proposal_union_adds_exactly_two_trail_stage_kinds_in_order() -> None:
    assert TypeAdapter(ProposalKind).json_schema()["enum"][-4:] == [
        "propose_evidence_trail_nodes",
        "propose_evidence_trail_relations",
        "record_evidence_trail_version",
        "bind_report_sentence",
    ]


def test_accepted_node_stage_is_transactional_audited_and_has_no_projection(runtime) -> None:
    decision = runtime.coordinator.submit(runtime.node_stage_proposal())
    assert decision.accepted
    assert runtime.accepted_receipt("proposal-node-stage-1") is not None
    assert runtime.trail_projection_is_empty()
```

- [ ] **Step 2: Run the cluster and record RED caused by absent proposal classes/receipt timestamps**

Run: `pytest tests/unit/improvement/test_models.py tests/integration/storage/test_repositories.py tests/integration/application/test_trail_service.py -k "stage or receipt or proposal_union" -q`

Expected: collection/import or assertion failures naming the two absent proposal kinds and absent
`StoredTransaction.created_at`.

- [ ] **Step 3: Implement the strict proposal/ref models and receipt reader**

```python
class ProposeEvidenceTrailNodes(ProposalBase):
    proposal_type: Literal["propose_evidence_trail_nodes"] = "propose_evidence_trail_nodes"
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    source_receipts: tuple[AddEvidenceReceiptRef, ...] = Field(min_length=1)
    nodes: tuple[EvidenceTrailNode, ...] = Field(min_length=1)


class ProposeEvidenceTrailRelations(ProposalBase):
    proposal_type: Literal["propose_evidence_trail_relations"] = "propose_evidence_trail_relations"
    trail_id: StableIdentifier
    trail_version_id: StableIdentifier
    classification: ChangeClassification
    node_stage_receipt: NodeStageReceiptRef
    node_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    nodes_hash: Sha256Hex
    relations: tuple[EvidenceTrailRelation, ...]
```

Decode `created_at` with the existing strict timestamp adapter and include it in every
`StoredTransaction`. Resolve receipts by exact proposal/decision JSON, transaction persistence,
proposal hash, audit IDs/hashes, and verified audit chain.

- [ ] **Step 4: Register fixed no-projection handlers and narrow capabilities**

The coordinator routes only the two concrete stage proposal classes to trail-stage capabilities.
Stage handlers receive policy/evidence/artifact/receipt reads and a no-op sentinel that cannot write;
they never receive `RepositorySet`, `TransactionRepository`, `AuditRepository`, or trail-table writes.

- [ ] **Step 5: Add fail-closed cases and verify GREEN**

Cover V1 rejection with durable transaction/audit, V2 acceptance, wrong-kind receipt,
self-computed hash, backdated/reordered source receipt, rejected stage, and missing/mismatched audit.

Run: `pytest tests/unit/improvement/test_models.py tests/integration/storage/test_repositories.py tests/integration/application/test_trail_service.py -k "stage or receipt or proposal_union" -q`

---

### Task 2: Fresh successor stages and contiguous claim receipts

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/models.py`
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `src/super_scientist/application/transactions/trails.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_authority_hardening.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `SourceFirstProvenance` contains ordered accepted source refs, one node-stage ref, one relation-stage
  ref, and a discriminated `ProposeClaimReceiptRef | TransitionClaimReceiptRef`; it contains no stage
  timestamps or caller-computed event IDs.
- `EvidenceTrailDraft` retains the parent claim version only as lineage context.
- `EvidenceTrailVersionBuilder.finalize(..., claim: AtomicClaim, source_first_provenance: ... )`
  requires a newly accepted claim receipt for every graph-changing successor and binds checks,
  assessments, and the final version to that claim.

- [ ] **Step 1: Write v1→v2→v3 and stale-stage RED tests**

```python
def test_graph_successors_require_new_stage_and_transition_claim_receipts(runtime) -> None:
    v1 = runtime.accept_initial_trail()
    v2 = runtime.accept_graph_successor(v1)
    v3 = runtime.accept_graph_successor(v2)
    assert (v1.claim_version_id, v2.claim_version_id, v3.claim_version_id) == (
        "claim-1:1",
        "claim-1:2",
        "claim-1:3",
    )
    assert runtime.snapshot(v1.trail_version_id) == v1
```

Add negatives for reused node/relation refs, `ProposeClaim` on a successor, `TransitionClaim` on v1,
noncontiguous claim lineage, claim before relation stage, and child timestamps copied from the parent.

- [ ] **Step 2: Run the successor cluster and record RED**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/integration/application/test_trail_service.py -k "successor or fresh_stage or claim_receipt or chronological" -q`

- [ ] **Step 3: Replace self-attested provenance and bind receipt-resolved stages**

Validate proposal kind, proposal hash, audit ref, exact source evidence/artifact, exact node proposal,
exact relation proposal, exact claim/version/content hash, builder/claim actor, historical policy, and
transaction/audit order. Previously accepted parent stage refs are forbidden for a changed child.

- [ ] **Step 4: Make finalization consume the fresh claim and keep report-only binding independent**

`add_node()` and `add_relation()` return drafts. `finalize()` changes the draft's claim version only
after receiving the fresh durable claim receipt and exact next `AtomicClaim`; unchanged report binding
continues to target an existing accepted snapshot without constructing a successor.

- [ ] **Step 5: Run the successor cluster GREEN**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/integration/application/test_trail_service.py -k "successor or fresh_stage or claim_receipt or chronological" -q`

---

### Task 3: Derive successor status from fresh assessments

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `tests/unit/evidence_trails/test_authority_hardening.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `derive_trail_outcome(assessments, *, conflicted) -> TrailOutcome` is the single pure status
  function used by builder finalization, validation, and replay.
- `EvidenceTrailDraft` has no caller-controlled `status`.

- [ ] **Step 1: Add RED transitions for CONFLICTED, INSUFFICIENT, UNANSWERABLE, and forged status**

```python
@pytest.mark.parametrize(
    "assessment_result, expected",
    [
        (AssessmentOutcome.FAILED, TrailOutcome.INSUFFICIENT),
        (AssessmentOutcome.ABSTAINED, TrailOutcome.UNANSWERABLE),
    ],
)
def test_finalize_derives_successor_status(assessment_result, expected, successor_draft):
    proposal = finalize_with_result(successor_draft, assessment_result)
    assert proposal.trail_version.status is expected
```

- [ ] **Step 2: Run focused status tests and record RED showing copied parent status**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/integration/application/test_trail_service.py -k "status or conflicted or insufficient or unanswerable" -q`

- [ ] **Step 3: Extract and use the pure outcome function**

Make validation pass the exact eight-category assessment mapping to the shared function. Builder
derives status after validating complete fresh assessments; no public argument or draft field can
override it.

- [ ] **Step 4: Run status tests GREEN**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/integration/application/test_trail_service.py -k "status or conflicted or insufficient or unanswerable" -q`

---

### Task 4: Typed SAME_ENTITY and SAME_EVENT evidence provenance

**Files:**
- Modify: `src/super_scientist/domain/evidence_trails/authority.py`
- Modify: `src/super_scientist/domain/evidence_trails/validation.py`
- Modify: `tests/unit/evidence_trails/conftest.py`
- Modify: `tests/unit/evidence_trails/test_authority_hardening.py`
- Modify: `tests/property/test_evidence_trail_graphs.py`
- Modify: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- `EvidenceIdentityProvenance(entity_id: NonBlankText | None, event_id: NonBlankText | None)` is a
  strict frozen parsed view of exact existing `EvidenceRecord.provenance` keys.
- `parse_identity_provenance(evidence)` rejects missing required keys, empty/whitespace values, and
  non-string/malformed values.
- Relation validation resolves each endpoint node's exact retained evidence record.

- [ ] **Step 1: Add positive and negative RED tests for both identity relations**

Cover equal entity IDs, equal event IDs, missing key, blank key, mismatched IDs, wrong endpoint
evidence, temporal-only SAME_EVENT, and SAME_ENTITY with equal content hashes but unequal entity IDs.

- [ ] **Step 2: Run identity tests and record RED**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/property/test_evidence_trail_graphs.py tests/integration/application/test_trail_service.py -k "same_entity or same_event or identity" -q`

- [ ] **Step 3: Implement exact parser and endpoint binding**

Use retained `sources_by_id` plus each endpoint node's `evidence_id`; never infer identity from content
hash, source ID, time, or graph position. Preserve the existing SAME_EVENT temporal equality rule as
an additional independent condition.

- [ ] **Step 4: Run identity tests GREEN**

Run: `pytest tests/unit/evidence_trails/test_authority_hardening.py tests/property/test_evidence_trail_graphs.py tests/integration/application/test_trail_service.py -k "same_entity or same_event or identity" -q`

---

### Task 5: Shared historical policy/approval authority and semantic replay

**Files:**
- Modify: `src/super_scientist/application/trails/service.py`
- Modify: `src/super_scientist/application/trails/receipts.py`
- Modify: `src/super_scientist/application/workspace_integrity.py`
- Modify: `src/super_scientist/application/transactions/trails.py`
- Modify: `tests/integration/application/test_trail_service.py`
- Modify: `tests/integration/application/test_workspace_integrity.py`
- Modify: `tests/integration/storage/test_policy_versions.py`
- Modify: `docs/evidence-trails.md`
- Modify: `.superpowers/sdd/task-7-report.md`

**Interfaces:**
- `trail_authority_rejection(...)` remains pure and is used unchanged by live handlers and replay.
- `FIXED_TRAIL_CLASSIFICATION` is exactly `RESEARCH_PROCESS`, `HUMAN_IN_LOOP`, `RUN_LOCAL`,
  `INDEPENDENT_DETERMINISTIC_CHECK`, `PRIMARY_SOURCE`, `EXTRINSIC_GROUNDED_EXPERIENCE`.
- Workspace replay derives the active historical policy at each audit sequence, validates every
  accepted stage/trail/binding against that snapshot, and builds receipt maps only from already
  replayed accepted audited transactions.

- [ ] **Step 1: Add durable tamper RED tests**

Add fixtures for dependent approver, shared model/configuration approver, wrong classification,
registered-but-inactive/weak/duplicate policy, non-primary retained source, protected/rollback flags,
rejected stage, missing audit, wrong proposal hash, and reordered stage receipt. Each must invalidate
`verify_workspace()` even if the graph validator itself remains green.

- [ ] **Step 2: Run live/replay authority tests and record RED**

Run: `pytest tests/integration/application/test_trail_service.py tests/integration/application/test_workspace_integrity.py tests/integration/storage/test_policy_versions.py -k "authority or tamper or replay or policy or approval or stage" -q`

- [ ] **Step 3: Extract shared authority and historical-policy replay**

Replay initializes the historical active policy from registered transition history, requires each
audit's governing hash to name the then-active snapshot, advances only after an accepted governance
transition, and calls the same authority function as live admission. Accepted receipt maps advance in
audit sequence and compare stored transaction keys `(created_at, proposal_id)`.

- [ ] **Step 4: Run all Task 7/proposal/policy/storage/replay suites GREEN**

Run: `pytest tests/unit/evidence_trails tests/property/test_evidence_trail_graphs.py tests/property/test_progress_trail_append_only.py tests/property/test_transaction_replay.py tests/unit/improvement/test_models.py tests/unit/config/test_policy_versions.py tests/integration/application/test_trail_service.py tests/integration/application/test_workspace_integrity.py tests/integration/application/test_transaction_coordinator.py tests/integration/storage/test_evidence_trail_repositories.py tests/integration/storage/test_repositories.py tests/integration/storage/test_policy_versions.py -q`

- [ ] **Step 5: Update documentation/report and run final gates**

Append exact RED/GREEN counts and commands to `.superpowers/sdd/task-7-report.md`. Document that
accepted transaction/audit receipts, not embedded stage timestamps, establish source-first order.

Run:

```text
ruff check src tests
mypy
git diff --check
pytest -q
```

- [ ] **Step 6: Audit scope and commit**

Confirm no migration, dependency, legacy evidence-model, or legacy proposal wire/hash files changed.
Stage only intended files, run `git diff --cached --check`, then commit:

```text
git commit -m "fix: make evidence trail stages auditable"
```

Verify the exact commit subject/hash and a clean worktree without generated bytecode caches.
