# Governed Cognitive Cohorts, Procedure Compilation, and Harness-Native Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a backward-compatible 0.3.0 candidate with governed capability grounding, diverse bounded cohorts, deterministic peer collaboration, method-to-procedure compilation, progress-plan binding, guidance and model-by-harness evaluation, harness-native traces, and reward-validity diagnostics without granting models or peers admission authority.

**Architecture:** Add an evidence-only cognitive plane to the existing transactional modular monolith. Pure domain functions derive cohort, collaboration, procedure, trace, and evaluation records; fixed proposal handlers recompute those derivations and `TransactionCoordinator` remains the only commit boundary. New append-only storage and replay expectations extend the existing progress and harness-evaluation spine rather than introducing a second planner, evaluator, or runtime.

**Tech Stack:** Python 3.12.13+, Pydantic 2.12+, SQLAlchemy 2.x, SQLite, Alembic, Typer 0.19.2, Click 8.3.3, pytest 9, Hypothesis 6, Ruff 0.16.0, strict mypy, branch coverage, Bandit, pip-audit, hatchling/build, and Twine.

**Spec:** `docs/superpowers/specs/2026-08-23-governed-cognitive-cohorts-procedure-compilation-design.md`

## Global Constraints

- Read the approved specification and `docs/adr/0002-governed-cognitive-plane-and-procedure-compilation.md` before Task 1.
- Start execution from design commit `fa3124a` in an isolated worktree created with `superpowers:using-git-worktrees`; use branch `feat/governed-cognitive-cohorts-procedure-compilation` and do not implement on `main`.
- Before the first implementation commit, `git config user.name` and `git config user.email` must return user-approved values. Pause if either value is absent; do not invent an identity.
- Preserve every released 0.2.0 proposal schema, canonical hash, decision, audit envelope, CLI command, exit status, migration path, and protected-evaluation boundary.
- Leave `alembic/versions/0001_epistemic_kernel.py` through `0006_handbook_and_harness_evaluation.py` byte-for-byte unchanged. Add only migration `0007`.
- Keep `ProgressPlan`, `ProgressSubtask`, progress budgets, checkpoints, completion, and false-finish behavior canonical. Procedure binding must call the existing progress handler rather than copy its rules.
- Keep `HarnessCampaign` and its protected promotion decision canonical. Guidance and model-by-harness records are evidence only.
- `DiversityAssessment` must not import, call, satisfy, replace, or weaken `are_independent()`.
- Peer agreement, majority, task score, trace, reward value, and reward-validity status must not mutate a claim or authorize promotion.
- All new Pydantic records use `frozen=True`, `strict=True`, `extra="forbid"`, explicit `schema_version`, bounded collections/text, and canonical hashes.
- New handlers receive focused capabilities. No peer, compiler, evaluator, trace object, reward object, or application coordinator receives `RepositorySet`, a unit of work, governance-write authority, protected answers, or artifact-write authority.
- Do not add network, arbitrary provider, shell, subprocess, dynamic import, `eval`, `exec`, GPU, Kubernetes, model SDK, RL, or training dependencies.
- Persist no hidden chain-of-thought, scratchpad, provider-native reasoning payload, secret, protected answer, arbitrary command, or reversible protected-store location.
- When metadata is absent, persist `UNAVAILABLE` or `NOT_APPLICABLE`; never synthesize token IDs, log probabilities, provider identifiers, capability evidence, or tool observations.
- All source use is attribution only. Do not copy code from S30-S35.
- Use red-green-refactor TDD for every behavior. Run the named failing test, implement the minimum behavior, rerun the focused suite, run Ruff and mypy for touched modules, and commit.
- After Tasks 7, 14, and 20, run separate fresh-context specification-compliance and code-quality reviews before continuing.
- The complete repository quality gate, migration suite, workspace round trip, offline example, and authority review must pass before Task 21 claims completion.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `src/super_scientist/domain/cognition/models.py` | capability, cohort, diversity, and error-correlation contracts |
| `src/super_scientist/domain/cognition/grounding.py` | task-conditioned capability assessment and deterministic cohort selection |
| `src/super_scientist/domain/cognition/diversity.py` | operational-diversity diagnostics only |
| `src/super_scientist/domain/collaboration/models.py` | sessions, budgets, requests, contributions, topology, and termination records |
| `src/super_scientist/domain/collaboration/engine.py` | deterministic routing, transition, loop, churn, monopoly, and bound checks |
| `src/super_scientist/domain/procedures/models.py` | candidate methods, procedure IR, catalogs, findings, outcomes, and receipts |
| `src/super_scientist/domain/procedures/compiler.py` | pure method compiler and static validator |
| `src/super_scientist/domain/procedures/progress_binding.py` | deterministic `ExecutableProcedure` to existing `ProgressPlan` mapping |
| `src/super_scientist/domain/harness_eval/guidance.py` | four-condition guidance protocols, cells, and matched comparisons |
| `src/super_scientist/domain/harness_eval/matrix.py` | model-by-harness protocols, cells, confounds, and analyses |
| `src/super_scientist/domain/harness_eval/traces.py` | harness-visible context, transformation, tool, environment, metadata, and observed reward records |
| `src/super_scientist/domain/harness_eval/rewards.py` | reward-hacking findings, validity assessment, and aggregate filtering |
| `src/super_scientist/kernel/transactions/models.py` | closed proposal union and additive rejection codes |
| `src/super_scientist/providers/storage/append_only.py` | reusable strict append-only repository primitive extracted without semantic change |
| `src/super_scientist/providers/storage/cognitive_records.py` | cognition, collaboration, and procedure repositories |
| `src/super_scientist/providers/storage/evaluation_records.py` | guidance, matrix, trace, and reward repositories |
| `src/super_scientist/providers/storage/schema.py` | SQLAlchemy table metadata for migration 0007 |
| `src/super_scientist/providers/storage/integrity_records.py` | legacy harness plus new cognitive/evaluation integrity snapshots |
| `src/super_scientist/providers/storage/repositories.py` | complete durable-state detection and snapshot factories |
| `src/super_scientist/application/cognitive/service.py` | narrow submission facade and non-authoritative `ResearchCoordinator` sequencing |
| `src/super_scientist/application/cognitive/reader.py` | read-only record lookup used by CLI inspection |
| `src/super_scientist/application/cognition/service.py` | capability, cohort, and diversity proposal handlers |
| `src/super_scientist/application/collaboration/service.py` | session, request, contribution, topology, and termination handlers |
| `src/super_scientist/application/procedures/service.py` | compilation, method outcome, and progress-binding handlers |
| `src/super_scientist/application/harness_eval/extensions.py` | guidance, matrix, trace, and reward handlers |
| `src/super_scientist/application/transactions/cognition.py` | focused cognition repository capabilities |
| `src/super_scientist/application/transactions/collaboration.py` | focused collaboration repository capabilities |
| `src/super_scientist/application/transactions/procedures.py` | focused procedure capabilities plus existing progress capability composition |
| `src/super_scientist/application/transactions/harness_extensions.py` | focused evaluation-extension repository capabilities |
| `src/super_scientist/application/transactions/coordinator.py` | fixed handler registration and capability selection only |
| `src/super_scientist/application/workspace_integrity.py` | full accepted-proposal reconstruction and tamper detection |
| `src/super_scientist/application/workspace_exchange.py` | export/import expectations and coordinator replay |
| `src/super_scientist/cli/cognitive.py` | read-only `cognitive inspect` command |
| `alembic/versions/0007_governed_cognitive_procedures.py` | additive append-only schema and triggers |
| `examples/governed_cognitive_procedure_vertical_slice.py` | complete model-free deterministic demonstration |
| `docs/examples/governed-cognitive-procedure-vertical-slice.md` | exact example invocation, output interpretation, and simulation limits |
| `tests/unit/cognition/` | capability, selection, diversity, correlation, and strict-schema tests |
| `tests/unit/collaboration/` | transition and bound tests |
| `tests/unit/procedures/` | compiler, validator, method outcome, and progress-mapping tests |
| `tests/unit/harness_eval/` | guidance, matrix, trace, and reward tests |
| `tests/integration/application/` | proposal handler, rollback, idempotency, and facade tests |
| `tests/integration/storage/test_migration_0007.py` | migration, strict decode, and append-only tests |
| `tests/integration/application/test_cognitive_workspace_exchange.py` | verification and bundle round trips |
| `tests/integration/cli/test_cognitive_cli.py` | stable read-only JSON inspection |
| `tests/property/test_cognitive_append_only.py` | ordering, hash, replay, and immutability properties |
| `tests/adversarial/test_cognitive_authority.py` | authority graph, consensus, spoofing, escalation, and trace leakage tests |
| `tests/e2e/test_governed_cognitive_procedure_vertical_slice.py` | complete offline 0.3.0 proof |

---

### Task 1: Repair Released Rule and Harness Workspace Integrity Coverage

**Files:**
- Modify: `src/super_scientist/providers/storage/integrity_records.py`
- Modify: `src/super_scientist/providers/storage/repositories.py:939`
- Modify: `src/super_scientist/application/workspace_integrity.py:621`
- Modify: `src/super_scientist/application/workspace_exchange.py:640`
- Test: `tests/integration/application/test_workspace_integrity.py`
- Test: `tests/integration/application/test_workspace_exchange.py`
- Test: `tests/integration/application/test_harness_eval_service.py`

**Interfaces:**
- Consumes: existing rule repositories, harness repositories, accepted proposal history, and `HarnessCampaignHeadRepository`.
- Produces: `HarnessIntegritySnapshot`; complete 0004/0006 durable-state detection; full rule/harness reconstruction; stable export expectations for harness heads and append-only records.

- [ ] **Step 1: Add failing legacy-coverage tests**

```python
def test_harness_observation_tampering_invalidates_workspace(runtime: HarnessRuntime) -> None:
    runtime.record_complete_campaign()
    runtime.tamper_append_only_row(
        table="harness_observations",
        record_id="observation-1",
        values={"candidate_output_hash": "0" * 64},
    )
    assert verify_workspace(runtime.repositories, runtime.artifacts).valid is False


def test_rule_only_state_counts_as_durable(runtime: WorkspaceRuntime) -> None:
    runtime.insert_rule_incident_without_policy()
    result = verify_workspace(runtime.repositories, runtime.artifacts)
    assert result.valid is False
    assert "active registered policy" in (result.reason or "")
```

- [ ] **Step 2: Run the tests and confirm the current omissions**

Run: `python -m pytest tests/integration/application/test_workspace_integrity.py tests/integration/application/test_workspace_exchange.py tests/integration/application/test_harness_eval_service.py -k "harness or rule_only" -v`

Expected: FAIL because `RepositorySet.has_durable_state()` omits 0004/0006 tables and `verify_workspace()` does not compare the full harness record set.

- [ ] **Step 3: Add the complete snapshot and reconstruction path**

```python
@dataclass(frozen=True)
class HarnessIntegritySnapshot:
    campaigns: tuple[HarnessCampaignRecord, ...]
    partitions: tuple[HarnessPartitionManifestRecord, ...]
    budgets: tuple[HarnessBudgetRecord, ...]
    observations: tuple[HarnessObservationRecord, ...]
    metrics: tuple[HarnessMetricRecord, ...]
    confounds: tuple[HarnessConfoundRecord, ...]
    decisions: tuple[HarnessDecisionRecord, ...]
    heads: tuple[tuple[str, str, HarnessDecisionStatus], ...]
```

Add `RepositorySet.harness_integrity_snapshot()`. Add every 0004 table and every 0006 table to `has_durable_state()`. Reconstruct harness records from accepted `CreateHarnessCampaign`, `RecordHarnessIteration`, `RecordHarnessProtectedResult`, `RecordHarnessConfound`, and `DecideHarnessCampaign` proposals. Verify non-transaction handbook records through their canonical content hashes and source/manifest bindings; never infer a transaction that did not occur. The test-only `tamper_append_only_row()` helper must drop the named table's update trigger, alter one explicit row, and recreate the trigger inside its isolated test database.

- [ ] **Step 4: Prove verification and bundle behavior**

Run: `python -m pytest tests/integration/application/test_workspace_integrity.py tests/integration/application/test_workspace_exchange.py tests/integration/application/test_harness_eval_service.py tests/property/test_rule_append_only.py tests/property/test_harness_eval_append_only.py -v`

Expected: PASS; row tampering fails verification, an untouched 0.2.0 workspace verifies, and existing bundle hashes remain stable when no new rows exist.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/providers/storage/integrity_records.py src/super_scientist/providers/storage/repositories.py src/super_scientist/application/workspace_integrity.py src/super_scientist/application/workspace_exchange.py tests/integration/application/test_workspace_integrity.py tests/integration/application/test_workspace_exchange.py && python -m mypy src`

Expected: both commands exit 0.

```bash
git add src/super_scientist/providers/storage/integrity_records.py src/super_scientist/providers/storage/repositories.py src/super_scientist/application/workspace_integrity.py src/super_scientist/application/workspace_exchange.py tests/integration/application/test_workspace_integrity.py tests/integration/application/test_workspace_exchange.py tests/integration/application/test_harness_eval_service.py
git commit -m "fix: verify released rule and harness workspace state"
```

### Task 2: Extract the Reusable Append-Only Repository Primitive

**Files:**
- Create: `src/super_scientist/providers/storage/append_only.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py:236`
- Test: `tests/integration/storage/test_repositories.py`
- Test: `tests/property/test_database_append_only.py`

**Interfaces:**
- Consumes: the exact private `_StrictFrozenStorageRecord`, `_AppendOnlyRecordRepository`, `_ReferencedAppendOnlyRecordRepository`, ordered-reference binding, strict decode, and content-hash behavior.
- Produces: `StrictFrozenStorageRecord`, `AppendOnlyRecordRepository[RecordT]`, `ReferencedAppendOnlyRecordRepository[RecordT]`, and `OrderedReferenceBinding` with unchanged SQL and error semantics.

- [ ] **Step 1: Add characterization tests for strict decode and rollback**

```python
def test_append_only_repository_rejects_unknown_payload_field(repository: FixtureRepository) -> None:
    repository.insert_raw_payload('{"record_id":"record-1","unknown":true}')
    with pytest.raises(StorageIntegrityError, match="stored record is invalid"):
        repository.list_all()


def test_append_only_add_rolls_back_record_and_references(runtime: StorageRuntime) -> None:
    with pytest.raises(IntegrityError):
        with runtime.uow() as uow:
            runtime.referenced_repository(uow.connection).add(runtime.invalid_reference_record())
    assert runtime.count("fixture_records") == 0
    assert runtime.count("fixture_record_references") == 0
```

- [ ] **Step 2: Run the characterizations before moving code**

Run: `python -m pytest tests/integration/storage/test_repositories.py tests/property/test_database_append_only.py -v`

Expected: PASS against the private implementation; capture this output as the compatibility baseline.

- [ ] **Step 3: Move and rename the primitive without changing behavior**

```python
class StrictFrozenStorageRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class AppendOnlyRecordRepository[RecordT: BaseModel]:
    def get(self, record_id: str) -> RecordT | None:
        row = self._connection.execute(
            select(self._table).where(self._id_column == record_id)
        ).mappings().one_or_none()
        return None if row is None else self._decode_row(row)

    def list_all(self) -> tuple[RecordT, ...]:
        rows = self._connection.execute(
            select(self._table).order_by(self._id_column)
        ).mappings()
        return tuple(self._decode_row(row) for row in rows)

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        normalized = self._model_type.model_validate(record)
        values = self._storage_values(record_id, normalized, created_at)
        self._connection.execute(self._table.insert().values(**values))
```

Move the existing implementation verbatim first. Update existing repositories to import the public names. Do not change table shapes, serialization, content hashing, derived columns, ordering, exception text, or transaction behavior in this task.

- [ ] **Step 4: Re-run all storage and append-only properties**

Run: `python -m pytest tests/integration/storage tests/property/test_database_append_only.py tests/property/test_progress_trail_append_only.py tests/property/test_rule_append_only.py tests/property/test_hypothesis_primitive_append_only.py tests/property/test_harness_eval_append_only.py -v`

Expected: PASS with the same record JSON and hashes as Step 2.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/providers/storage/append_only.py src/super_scientist/providers/storage/domain_records.py tests/integration/storage/test_repositories.py && python -m mypy src`

```bash
git add src/super_scientist/providers/storage/append_only.py src/super_scientist/providers/storage/domain_records.py tests/integration/storage/test_repositories.py tests/property/test_database_append_only.py
git commit -m "refactor: expose append-only storage primitive"
```

### Task 3: Implement Task-Conditioned Capability Grounding and Cohort Diversity

**Files:**
- Create: `src/super_scientist/domain/cognition/__init__.py`
- Create: `src/super_scientist/domain/cognition/models.py`
- Create: `src/super_scientist/domain/cognition/grounding.py`
- Create: `src/super_scientist/domain/cognition/diversity.py`
- Test: `tests/unit/cognition/test_grounding.py`
- Test: `tests/unit/cognition/test_cohorts.py`
- Test: `tests/unit/cognition/test_diversity.py`
- Test: `tests/unit/domain/test_strict_parsing.py`

**Interfaces:**
- Produces: `CapabilityProfile`, `CapabilityProfileReceiptRef`, `CapabilityRequirement`, `CapabilityAssessment`, `CohortRequest`, `CohortPlan`, `CohortPlanReceiptRef`, `DiversityFingerprint`, `DiversityAssessment`, `ErrorCorrelationRecord`, `assess_capability()`, `build_cohort()`, and `assess_diversity()`.
- Consumed by: Tasks 4, 5, 8, 11, and 17.

- [ ] **Step 1: Write failing grounding and tie tests**

```python
def test_self_reported_capability_is_not_satisfied() -> None:
    assessment = assess_capability(self_reported_profile(), required_capability())
    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.evidence_status is CapabilityEvidenceStatus.SELF_REPORTED


def test_cohort_tie_is_recorded_then_broken_by_actor_id() -> None:
    plan = build_cohort(cohort_request(max_members=1), (profile("peer-b"), profile("peer-a")))
    assert plan.tie_sets == (("peer-a", "peer-b"),)
    assert tuple(member.actor_id for member in plan.members) == ("peer-a",)
```

- [ ] **Step 2: Run the new unit tests**

Run: `python -m pytest tests/unit/cognition tests/unit/domain/test_strict_parsing.py -v`

Expected: FAIL during collection because the cognition package does not exist.

- [ ] **Step 3: Add strict contracts and pure selection**

```python
class CapabilityEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SELF_REPORTED = "SELF_REPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class CapabilityDisposition(StrEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


class AcceptedCognitiveReceiptRef(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    proposal_id: StableIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: StableIdentifier
    audit_event_hash: Sha256Hex


class CapabilityProfileReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["CAPABILITY_PROFILE"] = "CAPABILITY_PROFILE"


class CohortPlanReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["COHORT_PLAN"] = "COHORT_PLAN"


def assess_capability(
    profile: CapabilityProfile,
    requirement: CapabilityRequirement,
) -> CapabilityAssessment:
    matching = tuple(
        item
        for item in profile.assertions
        if item.capability_id == requirement.capability_id
        and item.task_family_id == requirement.task_family_id
    )
    verified = tuple(
        item
        for item in matching
        if item.status is CapabilityEvidenceStatus.VERIFIED
        and item.evidence_snapshot_hash == requirement.evidence_snapshot_hash
    )
    return CapabilityAssessment.from_matches(profile, requirement, matching, verified)
```

`DiversityFingerprint` must include model family/version, scale class, provider, adapter/configuration hashes, prompt strategy, methodological prior, tools, evidence partitions, modalities, previous error clusters, and prior task specializations. `DiversityAssessment` records `DIFFERENT`, `SAME`, or `UNKNOWN` per axis. Unknown values remain unknown. `ErrorCorrelationRecord` uses `KNOWN`, `INSUFFICIENT_DATA`, or `NOT_COMPARABLE` and stores no invented coefficient.

`CapabilityProfile` must record actor/model/provider/adapter/configuration identity, allowed tools, modalities, supported schemas, execution constraints, known failure categories, and sorted capability assertions. Each assertion records task-family scope, evidence status, evidence IDs, validator identity/version for verified evidence, and an evidence-snapshot hash. `CapabilityAssessment` records the exact requirement, matched assertion IDs, disposition, and missing/failed dimensions.

- [ ] **Step 4: Prove diversity is diagnostic only**

```python
def test_same_model_different_prompts_are_diverse_but_not_independent() -> None:
    left, right = same_model_profiles_with_different_prompts()
    diversity = assess_diversity(cohort(left, right), (left, right), ())
    assert diversity.axes["prompt_strategy"] is DiversityAxisStatus.DIFFERENT
    assert are_independent(left.actor, right.actor) is False
    assert "is_independent" not in DiversityAssessment.model_fields
```

Run: `python -m pytest tests/unit/cognition tests/unit/domain/test_identity.py tests/adversarial/test_reviewer_authority.py -v`

Expected: PASS.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/domain/cognition tests/unit/cognition && python -m mypy src`

```bash
git add src/super_scientist/domain/cognition tests/unit/cognition tests/unit/domain/test_strict_parsing.py
git commit -m "feat: add grounded cognitive cohorts"
```

### Task 4: Implement Bounded Deterministic Peer Collaboration

**Files:**
- Create: `src/super_scientist/domain/collaboration/__init__.py`
- Create: `src/super_scientist/domain/collaboration/models.py`
- Create: `src/super_scientist/domain/collaboration/engine.py`
- Test: `tests/unit/collaboration/test_engine.py`
- Test: `tests/unit/collaboration/test_topology.py`
- Test: `tests/unit/collaboration/test_termination.py`

**Interfaces:**
- Consumes: `CohortPlan`, `ResourceBudget`, `ResourceUsage`, `ArtifactRef`, and fixed actor/tool identities.
- Produces: `CollaborationBudget`, `CollaborationSession`, `PeerRequest`, `PeerContribution`, `TopologySnapshot`, `TopologyEvent`, `CollaborationState`, `CollaborationTerminationReason`, `next_peer()`, `apply_topology_event()`, and `advance_collaboration()`.

- [ ] **Step 1: Write failing deterministic routing and bound tests**

```python
def test_next_peer_uses_canonical_eligible_actor_order() -> None:
    state = initial_state(session_with_peers("peer-c", "peer-a", "peer-b"))
    assert next_peer(state.session, state) == "peer-a"


@pytest.mark.parametrize(
    ("state", "reason"),
    (
        (hop_exhausted_state(), CollaborationTerminationReason.MAX_HOPS_REACHED),
        (repeated_state(), CollaborationTerminationReason.REPEATED_STATE_LOOP),
        (topology_churn_state(), CollaborationTerminationReason.TOPOLOGY_CHURN),
        (monopoly_state(), CollaborationTerminationReason.CONTRIBUTION_MONOPOLY),
    ),
)
def test_collaboration_terminates_fail_closed(
    state: CollaborationState,
    reason: CollaborationTerminationReason,
) -> None:
    assert evaluate_termination(state).reason is reason
```

- [ ] **Step 2: Run the collaboration tests**

Run: `python -m pytest tests/unit/collaboration -v`

Expected: FAIL during collection because the collaboration package does not exist.

- [ ] **Step 3: Implement one-transition-at-a-time collaboration**

```python
class CollaborationTerminationReason(StrEnum):
    COMPLETED = "COMPLETED"
    MAX_HOPS_REACHED = "MAX_HOPS_REACHED"
    MAX_CONTRIBUTIONS_REACHED = "MAX_CONTRIBUTIONS_REACHED"
    PER_PEER_LIMIT_REACHED = "PER_PEER_LIMIT_REACHED"
    TOPOLOGY_CHANGE_LIMIT_REACHED = "TOPOLOGY_CHANGE_LIMIT_REACHED"
    NO_ELIGIBLE_PEER = "NO_ELIGIBLE_PEER"
    REPEATED_STATE_LOOP = "REPEATED_STATE_LOOP"
    TOPOLOGY_CHURN = "TOPOLOGY_CHURN"
    CONTRIBUTION_MONOPOLY = "CONTRIBUTION_MONOPOLY"


def advance_collaboration(
    session: CollaborationSession,
    state: CollaborationState,
    request: PeerRequest,
    contribution: PeerContribution,
    usage: ResourceUsage,
) -> CollaborationState:
    require_expected_peer(session, state, request.recipient_id)
    require_declared_parent_and_artifacts(session, state, request, contribution)
    require_usage_within_collaboration_budget(session.budget, usage)
    return append_checked_transition(session, state, request, contribution, usage)
```

The engine must bound peers, hops, contributions, per-peer contributions, topology changes, tokens, time, cost, tool calls, and recursive parent depth. A topology event may enable/disable only a declared edge or activate/deactivate only a declared peer. The state hash covers topology, requests, contributions, usage, and scheduling position.

- [ ] **Step 4: Add transient-topology and no-recursive-delegation tests**

Run: `python -m pytest tests/unit/collaboration -v`

Expected: PASS for loop, storm, churn, monopoly, unauthorized tool, undeclared peer, budget, and parent-depth cases. Confirm no collaboration model imports application, storage, network, subprocess, or transaction modules.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/domain/collaboration tests/unit/collaboration && python -m mypy src`

```bash
git add src/super_scientist/domain/collaboration tests/unit/collaboration
git commit -m "feat: add bounded peer collaboration"
```

### Task 5: Compile Candidate Methods into Statically Validated Procedures

**Files:**
- Create: `src/super_scientist/domain/procedures/__init__.py`
- Create: `src/super_scientist/domain/procedures/models.py`
- Create: `src/super_scientist/domain/procedures/compiler.py`
- Create: `src/super_scientist/domain/procedures/progress_binding.py`
- Test: `tests/unit/procedures/test_compiler.py`
- Test: `tests/unit/procedures/test_validation.py`
- Test: `tests/unit/procedures/test_progress_binding.py`
- Test: `tests/property/test_progress_dependencies.py`

**Interfaces:**
- Consumes: `CapabilityAssessment`, `BudgetReserves`, `ArtifactRef`, existing `ProgressPlan`, and existing `ProgressSubtask`.
- Produces: `CandidateMethod`, `ProcedureStep`, `ExecutableProcedure`, `ProcedureCompilationRequest`, `ProcedureCompilationResult`, `OpaqueProcedureCompilationEnvelope`, `ProcedureCompilationRecord`, `ProcedureValidationReport`, `ProcedureCompilationReceiptRef`, `CompiledProgressPlanBinding`, `MethodDirectionStatus`, `MethodDirectionOutcome`, `parse_untrusted_procedure_compilation_envelope()`, `compile_method()`, and `procedure_to_progress_plan()`.

- [ ] **Step 1: Write the static-validation matrix first**

```python
@pytest.mark.parametrize(
    ("request_factory", "code"),
    (
        (request_with_cycle, ProcedureFindingCode.DEPENDENCY_CYCLE),
        (request_with_missing_input, ProcedureFindingCode.MISSING_ARTIFACT),
        (request_with_undefined_output, ProcedureFindingCode.UNDEFINED_OUTPUT),
        (request_with_unavailable_tool, ProcedureFindingCode.TOOL_UNAVAILABLE),
        (request_with_unauthorized_tool, ProcedureFindingCode.TOOL_UNAUTHORIZED),
        (request_without_completion_criteria, ProcedureFindingCode.MISSING_COMPLETION_CRITERIA),
        (request_with_invalid_validator, ProcedureFindingCode.INVALID_VALIDATOR_BINDING),
        (request_over_budget, ProcedureFindingCode.BUDGET_EXCEEDED),
        (request_requiring_governance_write, ProcedureFindingCode.IMPOSSIBLE_AUTHORITY),
    ),
)
def test_invalid_method_preserves_exact_finding(request_factory, code) -> None:
    result = compile_method(request_factory())
    assert result.report.status is ProcedureValidationStatus.INVALID
    assert code in tuple(item.code for item in result.report.findings)
    assert result.procedure is not None
```

- [ ] **Step 2: Run compiler tests and observe missing contracts**

Run: `python -m pytest tests/unit/procedures tests/property/test_progress_dependencies.py -v`

Expected: FAIL during collection because the procedures package does not exist.

- [ ] **Step 3: Implement the closed procedure IR and compiler**

```python
class ProcedureOperation(StrEnum):
    INSPECT_DECLARED_ARTIFACT = "INSPECT_DECLARED_ARTIFACT"
    DERIVE_STRUCTURED_CANDIDATE = "DERIVE_STRUCTURED_CANDIDATE"
    RUN_REGISTERED_DETERMINISTIC_FIXTURE = "RUN_REGISTERED_DETERMINISTIC_FIXTURE"
    EVALUATE_WITH_REGISTERED_VALIDATOR = "EVALUATE_WITH_REGISTERED_VALIDATOR"
    RECORD_DECLARED_OUTPUT = "RECORD_DECLARED_OUTPUT"


def compile_method(request: ProcedureCompilationRequest) -> ProcedureCompilationResult:
    procedure = compile_declared_stages(request)
    findings = tuple(sorted(validate_procedure(request, procedure), key=finding_sort_key))
    status = validation_status(findings)
    return ProcedureCompilationResult(
        schema_version=1,
        compiler_id=request.compiler_id,
        compiler_version=request.compiler_version,
        request_hash=canonical_model_hash(request),
        procedure=procedure,
        report=ProcedureValidationReport(status=status, findings=findings),
    )
```

Every `ProcedureStep` includes objective, inputs, outputs, dependencies, allowed registered tools, preconditions, completion criteria, evidence requirements, validator ID/version, failure signals, one bounded recovery target or terminal outcome, progress budget category, and `ResourceBudget`. Validators check all 16 requirements from specification section 9.5 in deterministic order.

- [ ] **Step 4: Map only valid procedures to existing progress types**

```python
def procedure_to_progress_plan(
    result: ProcedureCompilationResult,
    *,
    run_id: StableIdentifier,
    plan_version_id: StableIdentifier,
    version: int,
    created_at: UtcTimestamp,
    governing_policy_hash: Sha256Hex,
) -> ProgressPlan:
    if result.report.status is not ProcedureValidationStatus.VALID:
        raise ValueError("only a valid procedure can produce a progress plan")
    return ProgressPlan(
        plan_version_id=plan_version_id,
        run_id=run_id,
        version=version,
        subtasks=tuple(progress_subtask(step, plan_version_id) for step in result.procedure.steps),
        created_at=created_at,
        governing_policy_hash=governing_policy_hash,
    )
```

Run: `python -m pytest tests/unit/procedures tests/unit/progress tests/property/test_progress_dependencies.py -v`

Expected: PASS; invalid/inconclusive results raise the fixed error and valid mappings pass existing topological progress calculations.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/domain/procedures tests/unit/procedures && python -m mypy src`

```bash
git add src/super_scientist/domain/procedures tests/unit/procedures tests/property/test_progress_dependencies.py
git commit -m "feat: compile methods into progress procedures"
```

### Task 6: Add Guidance-Gradient and Model-by-Harness Analysis

**Files:**
- Create: `src/super_scientist/domain/harness_eval/guidance.py`
- Create: `src/super_scientist/domain/harness_eval/matrix.py`
- Modify: `src/super_scientist/domain/harness_eval/__init__.py`
- Test: `tests/unit/harness_eval/test_guidance.py`
- Test: `tests/unit/harness_eval/test_model_harness_matrix.py`
- Test: `tests/unit/harness_eval/test_campaigns.py`

**Interfaces:**
- Consumes: existing `EvaluationBudget`, `HarnessPartition`, `AssessmentOutcome`, `ResourceUsage`, and exact task/model/harness/verifier identities.
- Produces: `GuidanceCondition`, `EvaluationMetricVector`, `GuidanceEvaluationProtocol`, `GuidanceEvaluationCell`, `GuidanceComparison`, `ModelHarnessProtocol`, `ModelHarnessCell`, `ModelHarnessAnalysis`, `compare_guidance_cells()`, and `analyze_model_harness()`.

- [ ] **Step 1: Write held-constant and no-scalar tests**

```python
def test_guidance_comparison_rejects_task_drift() -> None:
    comparison = compare_guidance_cells(cell(task_hash="a" * 64), cell(task_hash="b" * 64))
    assert comparison.comparable is False
    assert EvaluationConfoundCode.TASK_INPUT_MISMATCH in comparison.confounds


def test_metric_vector_has_no_composite_score() -> None:
    assert "composite_score" not in EvaluationMetricVector.model_fields
    assert "task_score" in EvaluationMetricVector.model_fields
    assert "resource_usage" in EvaluationMetricVector.model_fields
```

- [ ] **Step 2: Run the focused tests**

Run: `python -m pytest tests/unit/harness_eval/test_guidance.py tests/unit/harness_eval/test_model_harness_matrix.py -v`

Expected: FAIL because the extension modules do not exist.

- [ ] **Step 3: Implement exact guidance conditions and matching**

```python
class GuidanceCondition(StrEnum):
    FULL_PROCEDURE_GUIDANCE = "FULL_PROCEDURE_GUIDANCE"
    METHOD_ONLY = "METHOD_ONLY"
    OBJECTIVE_AND_DATA_ONLY = "OBJECTIVE_AND_DATA_ONLY"
    OBJECTIVE_DATA_WITH_DISTRACTORS = "OBJECTIVE_DATA_WITH_DISTRACTORS"


def compare_guidance_cells(
    left: GuidanceEvaluationCell,
    right: GuidanceEvaluationCell,
) -> GuidanceComparison:
    confounds = guidance_identity_confounds(left, right)
    return GuidanceComparison(
        comparable=not confounds and left.condition is not right.condition,
        left_cell_id=left.cell_id,
        right_cell_id=right.cell_id,
        component_deltas=metric_component_deltas(left.metrics, right.metrics),
        confounds=confounds,
    )
```

The matched identity includes objective, task input/data, required output schema, model, harness, verifier/checker, artifacts, seed, and `EvaluationBudget`. The distractor condition adds only declared distractor artifact IDs.

`EvaluationMetricVector` stores task score, procedure-compilation status, method-selection result, execution-failure events, recovery-attempt events, `ResourceUsage`, and final validation separately. Missing values require typed missingness reasons. No canonical function may collapse these fields into one score.

- [ ] **Step 4: Implement matrix comparisons without causal overclaim**

```python
class ModelHarnessComparisonKind(StrEnum):
    MODEL_HELD_CONSTANT = "MODEL_HELD_CONSTANT"
    HARNESS_HELD_CONSTANT = "HARNESS_HELD_CONSTANT"
    INTERACTION_DESCRIPTIVE = "INTERACTION_DESCRIPTIVE"
    TRAIN_TEST_TRANSFER = "TRAIN_TEST_TRANSFER"


def analyze_model_harness(
    protocol: ModelHarnessProtocol,
    cells: tuple[ModelHarnessCell, ...],
) -> ModelHarnessAnalysis:
    confounds = validate_complete_matched_grid(protocol, cells)
    comparisons = () if confounds else build_declared_comparisons(protocol, cells)
    return ModelHarnessAnalysis(
        protocol_id=protocol.protocol_id,
        cell_ids=tuple(cell.cell_id for cell in canonical_cells(cells)),
        comparisons=comparisons,
        confounds=confounds,
        causal_claim_permitted=False,
    )
```

Run: `python -m pytest tests/unit/harness_eval/test_guidance.py tests/unit/harness_eval/test_model_harness_matrix.py tests/unit/harness_eval/test_campaigns.py -v`

Expected: PASS; budget mismatch blocks matching, discovery and transfer stay separate, and existing `HarnessCampaign` tests are unchanged.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/domain/harness_eval tests/unit/harness_eval && python -m mypy src`

```bash
git add src/super_scientist/domain/harness_eval tests/unit/harness_eval
git commit -m "feat: add guidance and model harness analysis"
```

### Task 7: Add Harness-Native Traces and Reward Validity

**Files:**
- Create: `src/super_scientist/domain/harness_eval/traces.py`
- Create: `src/super_scientist/domain/harness_eval/rewards.py`
- Modify: `src/super_scientist/domain/harness_eval/__init__.py`
- Test: `tests/unit/harness_eval/test_traces.py`
- Test: `tests/unit/harness_eval/test_rewards.py`
- Test: `tests/adversarial/test_protected_holdout_leakage.py`

**Interfaces:**
- Consumes: evaluation protocols, `ArtifactRef`, hashes, verifier/checker identities, and observable tool/environment results.
- Produces: `MetadataAvailability`, `AvailableValue[T]`, `ContextTransformation`, `ToolObservation`, `EnvironmentEvent`, `HarnessExecutionTrace`, `TraceFreshness`, `RewardObservation`, `RewardHackingFinding`, `RewardValidityAssessment`, `trace_freshness()`, `assess_reward_validity()`, and `valid_reward_evidence()`.

- [ ] **Step 1: Write availability and fail-closed reward tests**

```python
def test_unavailable_log_probabilities_cannot_carry_values() -> None:
    with pytest.raises(ValidationError):
        AvailableValue[tuple[Decimal, ...]](
            status=MetadataAvailability.UNAVAILABLE,
            value=(Decimal("0.5"),),
            evidence_id=None,
        )


def test_high_invalid_reward_is_excluded() -> None:
    trace, expectation, inventory, verification, diagnostic_coverage, findings = stale_reward_bundle()
    freshness = trace_freshness(expectation, trace, inventory=inventory)
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=diagnostic_coverage,
        inventory=inventory,
    )
    assert freshness.status is TraceFreshnessStatus.STALE
    assert assessment.status is RewardValidityStatus.INVALID
    assert RewardInvalidationReason.STALE_HARNESS_TRACE in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()
```

- [ ] **Step 2: Run the trace and reward tests**

Run: `python -m pytest tests/unit/harness_eval/test_traces.py tests/unit/harness_eval/test_rewards.py tests/adversarial/test_protected_holdout_leakage.py -v`

Expected: FAIL because trace and reward modules do not exist.

- [ ] **Step 3: Implement observable-only trace contracts**

```python
class MetadataAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AvailableValue[ValueT](BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    status: MetadataAvailability
    value: ValueT | None
    evidence_id: StableIdentifier | None

    @model_validator(mode="after")
    def require_truthful_availability(self) -> Self:
        present = self.value is not None and self.evidence_id is not None
        if present != (self.status is MetadataAvailability.AVAILABLE):
            raise ValueError("metadata value and evidence require AVAILABLE status")
        return self
```

`HarnessExecutionTrace` binds exact task, model, harness, procedure, environment, context, validator, artifact, tool, output, observed reward, and provenance hashes. `ContextTransformationKind` includes `CONTEXT_COMPACTION` and `RESERIALIZATION`. Tool observations exclude command strings and protected data. Trace freshness compares hashes; timestamps alone never make a trace current. `traces.py` owns `RewardObservation`; the trace embeds the optional observed reward and an availability-wrapped validity status supplied at capture. A supplied capture status is diagnostic only. The later recomputed `RewardValidityAssessment` is the only status used by `valid_reward_evidence()`, and the trace never references that later assessment.

- [ ] **Step 4: Implement structured reward validity and hacking findings**

```python
class RewardValidityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


def assess_reward_validity(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    *,
    expectation: TraceExpectation,
    verification: VerificationOutcomeEvidence,
    diagnostic_coverage: RewardHackingCoverageAttestation,
    inventory: ResolvedEvidenceInventory,
) -> RewardValidityAssessment:
    freshness = trace_freshness(expectation, trace, inventory=inventory)
    # Strictly validate all inputs, resolve evidence receipts through inventory, then build the
    # canonical assessment using freshness, verification, diagnostic_coverage, and inventory.
    ...
```

Run: `python -m pytest tests/unit/harness_eval/test_traces.py tests/unit/harness_eval/test_rewards.py tests/adversarial/test_protected_holdout_leakage.py -v`

Expected: PASS for environment crash, incomplete execution, verifier mismatch/failure, corrupt artifacts, protected leakage, reward hacking, evaluator failure, stale trace, runtime mismatch, unknown evidence, and valid reward.

- [ ] **Step 5: Run the Phase A gate, two reviews, and commit**

Run: `python -m pytest tests/unit/cognition tests/unit/collaboration tests/unit/procedures tests/unit/harness_eval tests/unit/progress tests/unit/domain/test_strict_parsing.py tests/adversarial/test_reviewer_authority.py tests/adversarial/test_protected_holdout_leakage.py -v`

Run: `python -m ruff check src/super_scientist/domain tests/unit tests/adversarial/test_protected_holdout_leakage.py && python -m mypy src`

Expected: all commands exit 0. Run one fresh-context specification-compliance review against specification sections 3-14 and one separate code-quality review. Resolve every finding before committing.

```bash
git add src/super_scientist/domain/harness_eval tests/unit/harness_eval tests/adversarial/test_protected_holdout_leakage.py
git commit -m "feat: add harness traces and reward validity"
```

### Task 8: Add the Closed Cognitive and Evaluation Proposal Contracts

**Files:**
- Modify: `src/super_scientist/kernel/transactions/models.py:88`
- Test: `tests/unit/domain/test_strict_parsing.py`
- Test: `tests/unit/admission/test_engine.py`
- Test: `tests/property/test_transaction_replay.py`

**Interfaces:**
- Consumes: all Phase A domain contracts and existing `ProposalBase`.
- Produces: 18 additive proposal classes, their exact `proposal_type` literals, additive rejection codes, the updated discriminated `Proposal` union, and `parse_untrusted_proposal_json()` as the fixed safe serialized-proposal boundary.

- [ ] **Step 1: Write strict union and unknown-field failures**

```python
@pytest.mark.parametrize(
    "proposal",
    (
        record_capability_profile(),
        record_cohort_plan(),
        record_procedure_compilation(),
        record_guidance_protocol(),
        record_reward_assessment(),
    ),
)
def test_new_proposal_round_trips_through_closed_union(proposal: Proposal) -> None:
    encoded = canonical_json_bytes(proposal.model_dump(mode="json"))
    assert PROPOSAL_ADAPTER.validate_json(encoded) == proposal


def test_new_proposal_rejects_unknown_field() -> None:
    payload = record_capability_profile().model_dump(mode="json") | {"authority": "peer"}
    with pytest.raises(ProposalBoundaryValidationError):
        parse_untrusted_proposal_json(canonical_json_bytes(payload))


def test_procedure_proposal_keeps_nested_result_opaque() -> None:
    payload = record_procedure_compilation_with_schema_invalid_result_json()
    parsed = parse_untrusted_proposal_json(canonical_json_bytes(payload))
    assert isinstance(parsed.compilation, OpaqueProcedureCompilationEnvelope)
    with pytest.raises(ProcedureBoundaryValidationError):
        parse_untrusted_procedure_compilation_result(parsed.compilation)
```

- [ ] **Step 2: Run parsing and replay tests**

Run: `python -m pytest tests/unit/domain/test_strict_parsing.py tests/unit/admission/test_engine.py tests/property/test_transaction_replay.py -v`

Expected: FAIL because the new proposal kinds are not registered.

- [ ] **Step 3: Add the exact proposal classes**

```python
from contextlib import suppress


class RecordCohortPlan(ProposalBase):
    proposal_type: Literal["record_cohort_plan"] = "record_cohort_plan"
    request: CohortRequest
    profile_receipts: tuple[CapabilityProfileReceiptRef, ...] = Field(min_length=1)
    plan: CohortPlan


class RecordProcedureCompilation(ProposalBase):
    proposal_type: Literal["record_procedure_compilation"] = "record_procedure_compilation"
    compilation: OpaqueProcedureCompilationEnvelope


class BindCompiledProgressPlan(ProposalBase):
    proposal_type: Literal["bind_compiled_progress_plan"] = "bind_compiled_progress_plan"
    compilation_receipt: ProcedureCompilationReceiptRef
    binding: CompiledProgressPlanBinding
    plan: ProgressPlan


# <!-- task-8-13-trace-contract:start -->
class HarnessTraceRecordMetadata(BaseModel):
    schema_version: Literal[1] = 1
    received_at: UtcTimestamp
    source_id: StableIdentifier


class HarnessExecutionTraceEnvelope(BaseModel):
    metadata: HarnessTraceRecordMetadata
    trace: HarnessExecutionTrace


class RecordHarnessExecutionTrace(ProposalBase):
    proposal_type: Literal["record_harness_execution_trace"] = "record_harness_execution_trace"
    envelope: HarnessExecutionTraceEnvelope


class RecordRewardAssessment(ProposalBase):
    proposal_type: Literal["record_reward_assessment"] = "record_reward_assessment"
    observation: RewardObservation
    findings: tuple[RewardHackingFinding, ...]
    assessment: RewardValidityAssessment
# <!-- task-8-13-trace-contract:end -->


MAX_PROPOSAL_BYTES = 8 * 1_024 * 1_024


def parse_untrusted_proposal_json(value: bytes) -> Proposal:
    proposal: Proposal | None = None
    with suppress(MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        if len(value) <= MAX_PROPOSAL_BYTES and proposal_json_is_within_depth_limit(value):
            proposal = PROPOSAL_ADAPTER.validate_json(value)
    if proposal is None:
        raise ProposalBoundaryValidationError(
            "transaction proposal failed validation"
        ) from None
    return proposal
```

Add these exact kinds: `record_capability_profile`, `record_cohort_plan`, `record_diversity_assessment`, `record_collaboration_session`, `append_peer_request`, `append_peer_contribution`, `append_topology_event`, `record_collaboration_termination`, `record_procedure_compilation`, `record_method_direction_outcome`, `bind_compiled_progress_plan`, `record_guidance_evaluation_protocol`, `append_guidance_evaluation_cell`, `record_model_harness_protocol`, `append_model_harness_cell`, `record_model_harness_analysis`, `record_harness_execution_trace`, and `record_reward_assessment`.

`RecordDiversityAssessment` carries a `CohortPlanReceiptRef`, the same ordered capability-profile receipts, explicit error-correlation records, and the claimed assessment. Handlers resolve accepted receipts and exact hashes; no cohort or diversity proposal trusts caller-supplied duplicate profile payloads.

Add only these rejection codes: `DERIVATION_MISMATCH`, `STALE_REFERENCE`, `COLLABORATION_BOUND_EXCEEDED`, `INVALID_PROCEDURE`, `UNMATCHED_EVALUATION`, and `INVALID_REWARD`. Reuse existing codes where their meaning is exact.

`OpaqueProcedureCompilationEnvelope` contains compilation metadata and bounded,
base64-encoded canonical result JSON bytes. It does not parse a nested
`ProcedureCompilationResult`. `PROPOSAL_ADAPTER` validates only the outer proposal and
opaque envelope contract; Task 12 owns safe result normalization. Do not add a second
opaque envelope type in `kernel.transactions`.

The canonical proposal hash binds the complete normalized envelope, including
compilation ID, created time, governing policy hash, result JSON hash, and encoded result
bytes. Task 12 must compare the normalized policy hash with the active policy. The
durable `ProcedureCompilationRecord.content_hash` independently binds the normalized
metadata and validated typed result after acceptance.

`parse_untrusted_proposal_json()` is the only public parser for serialized untrusted
proposal bytes. It applies an 8 MiB byte limit and the shared iterative depth limit
before `PROPOSAL_ADAPTER.validate_json()`. It converts every Pydantic, JSON, recursion,
overflow, and recoverable memory failure to the fixed `ProposalBoundaryValidationError`
only after the caught-exception scope exits. The public error has no cause, context,
structured error surface, or rejected input. Raw `PROPOSAL_ADAPTER` calls remain trusted
construction and diagnostic operations; application entrypoints must not expose their
exceptions or structured errors. Base64 is transport encoding, not secrecy; the fixed
outer parser must discard any rejected encoded payload before returning control to a
caller.

- [ ] **Step 4: Prove old proposal bytes and hashes are unchanged**

Run: `python -m pytest tests/unit/domain/test_strict_parsing.py tests/unit/admission/test_engine.py tests/property/test_admission_idempotency.py tests/property/test_transaction_replay.py -v`

Expected: PASS. Existing proposal fixtures must produce their pre-task canonical bytes and hashes.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/kernel/transactions/models.py tests/unit/domain/test_strict_parsing.py tests/unit/admission/test_engine.py && python -m mypy src`

```bash
git add src/super_scientist/kernel/transactions/models.py tests/unit/domain/test_strict_parsing.py tests/unit/admission/test_engine.py tests/property/test_transaction_replay.py
git commit -m "feat: add governed cognitive proposal contracts"
```

### Task 9: Add Migration 0007 and Exact SQLAlchemy Schema

**Files:**
- Create: `alembic/versions/0007_governed_cognitive_procedures.py`
- Modify: `src/super_scientist/providers/storage/schema.py:1605`
- Create: `tests/integration/storage/test_migration_0007.py`
- Modify: `tests/integration/storage/test_migrations.py`
- Modify: `tests/integration/storage/test_migration_0006.py`

**Interfaces:**
- Consumes: migration head `0006_handbook_and_harness_evaluation` and the public append-only repository primitive.
- Produces: 18 append-only tables, their reference indexes/constraints, and update/delete rejection triggers. Released tables are untouched.

- [ ] **Step 1: Write migration-shape and legacy-upgrade failures**

```python
EXPECTED_0007_TABLES = {
    "capability_profiles", "cohort_plans", "diversity_assessments",
    "collaboration_sessions", "peer_requests", "peer_contributions",
    "topology_events", "collaboration_terminations", "procedure_compilations",
    "method_direction_outcomes", "compiled_progress_plan_bindings",
    "guidance_protocols", "guidance_cells", "model_harness_protocols",
    "model_harness_cells", "model_harness_analyses",
    "harness_execution_traces", "reward_assessments",
}


def test_0006_workspace_upgrades_without_rewriting_existing_rows(migrated_0006) -> None:
    before = migrated_0006.snapshot_released_rows()
    migrated_0006.upgrade_to_head()
    assert migrated_0006.snapshot_released_rows() == before
    assert EXPECTED_0007_TABLES <= migrated_0006.table_names()
```

- [ ] **Step 2: Run migration tests**

Run: `python -m pytest tests/integration/storage/test_migration_0007.py tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0006.py -v`

Expected: FAIL because revision 0007 and the table metadata do not exist.

- [ ] **Step 3: Define the additive table helper and tables**

```python
def _create_record_table(
    name: str,
    id_column: str,
    relationship_columns: Sequence[sa.Column[object]],
) -> None:
    op.create_table(
        name,
        sa.Column(id_column, sa.String(), primary_key=True),
        *relationship_columns,
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("governing_policy_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint("schema_version = 1"),
        sa.CheckConstraint("length(content_hash) = 64"),
        sa.CheckConstraint("length(governing_policy_hash) = 64"),
    )
    _create_append_only_triggers(name)
```

Give each table a domain ID primary key and indexed parent IDs: session ID for collaboration children, compilation ID for outcomes/bindings, protocol ID for cells/analyses, and trace/observation IDs for reward records. The downgrade drops only 0007 triggers, indexes, and tables in reverse dependency order.

- [ ] **Step 4: Prove append-only behavior and exact migration chain**

Run: `python -m pytest tests/integration/storage/test_migration_0007.py tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0002.py tests/integration/storage/test_migration_0003.py tests/integration/storage/test_migration_0004.py tests/integration/storage/test_migration_0005.py tests/integration/storage/test_migration_0006.py -v`

Expected: PASS; update/delete on every 0007 table raises `IntegrityError`, 0001-0006 fixtures upgrade, and downgrade/upgrade returns the same schema.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check alembic/versions/0007_governed_cognitive_procedures.py src/super_scientist/providers/storage/schema.py tests/integration/storage/test_migration_0007.py && python -m mypy src`

```bash
git add alembic/versions/0007_governed_cognitive_procedures.py src/super_scientist/providers/storage/schema.py tests/integration/storage/test_migration_0007.py tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0006.py
git commit -m "feat: add cognitive procedure storage schema"
```

### Task 10: Implement Strict Cognitive and Evaluation Repositories

**Files:**
- Create: `src/super_scientist/providers/storage/cognitive_records.py`
- Create: `src/super_scientist/providers/storage/evaluation_records.py`
- Create: `src/super_scientist/providers/storage/procedure_sources.py`
- Modify: `src/super_scientist/providers/storage/integrity_records.py`
- Modify: `src/super_scientist/providers/storage/repositories.py:939`
- Create: `tests/integration/storage/test_cognitive_repositories.py`
- Create: `tests/integration/storage/test_evaluation_repositories.py`
- Create: `tests/integration/storage/test_procedure_source_repositories.py`
- Create: `tests/property/test_cognitive_append_only.py`

**Interfaces:**
- Consumes: Task 9 tables, `AppendOnlyRecordRepository`, `EvidenceRepository`,
  `ArtifactStore`, `TransactionRepository`, and `AuditRepository`.
- Produces: one fixed repository per table; focused accepted procedure-source readers;
  `CognitiveIntegritySnapshot`; `EvaluationExtensionIntegritySnapshot`; complete
  `has_durable_state()` coverage.

- [ ] **Step 1: Write strict decode, relationship, and idempotent-read tests**

```python
def test_capability_profile_repository_rejects_payload_id_mismatch(runtime) -> None:
    runtime.insert_raw_capability_row(primary_key="profile-a", payload=profile("profile-b"))
    with pytest.raises(StorageIntegrityError, match="stored derived column mismatch"):
        CapabilityProfileRepository(runtime.connection).get("profile-a")


def test_guidance_cells_are_returned_in_canonical_identity_order(runtime) -> None:
    repository = GuidanceCellRepository(runtime.connection)
    repository.add(cell("cell-b"), transaction_id="tx-b")
    repository.add(cell("cell-a"), transaction_id="tx-a")
    assert tuple(item.cell_id for item in repository.list_for_protocol("protocol-1")) == (
        "cell-a", "cell-b"
    )


def test_procedure_source_reader_rejects_any_receipt_or_snapshot_mismatch(runtime) -> None:
    reference = runtime.accepted_artifact_catalog_reference()
    for forged in runtime.each_single_field_source_forgery(reference):
        assert runtime.procedure_sources.resolve(forged) is None
```

- [ ] **Step 2: Run repository tests**

Run: `python -m pytest tests/integration/storage/test_cognitive_repositories.py tests/integration/storage/test_evaluation_repositories.py tests/integration/storage/test_procedure_source_repositories.py tests/property/test_cognitive_append_only.py -v`

Expected: FAIL because the repository modules do not exist.

- [ ] **Step 3: Add focused model-bound repositories**

```python
class CapabilityProfileRepository(AppendOnlyRecordRepository[CapabilityProfile]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=capability_profiles,
            model_type=CapabilityProfile,
            record_id_column="profile_id",
            model_id_field="profile_id",
            derived_fields=("actor_id", "governing_policy_hash"),
        )


class PeerContributionRepository(AppendOnlyRecordRepository[PeerContribution]):
    def list_for_session(self, session_id: str) -> tuple[PeerContribution, ...]:
        return self._list_by_relationship("session_id", session_id)
```

Do not create a generic repository locator or dynamic model registry. Export explicit repository classes and explicit snapshot fields. Each `add()` receives the coordinator transaction ID and uses the domain record's timestamp/policy hash.

Add these focused read contracts in `procedure_sources.py`:

- `AcceptedProcedureSourceReceiptReader` reconstructs accepted source receipts from
  `TransactionRepository` and `AuditRepository`. It requires one accepted transaction,
  one exact persisted audit event, exact proposal ID/hash, and exact audit event ID/hash.
- `CapabilityProfileRepository` resolves a capability-profile source by profile ID,
  schema version, and canonical content hash. The accepted proposal must be
  `RecordCapabilityProfile` and must contain that exact profile.
- `ArtifactCatalogSnapshotRepository`, `ToolCatalogSnapshotRepository`, and
  `ValidatorCatalogSnapshotRepository` resolve catalog sources from accepted
  `AddEvidence` records whose artifacts contain canonical schema-version-1 catalog JSON.
  Each repository decodes only its fixed catalog kind and requires exact source record
  ID, source content hash, ordered entries, completeness flag, and artifact hash.
- `ProcedureSourceSnapshotRepository` resolves `source_snapshot_id` to one accepted
  `AddEvidence` snapshot artifact, requires its artifact hash to equal
  `source_snapshot_hash`, and exposes `is_current(snapshot_id, snapshot_hash)`. A
  snapshot artifact contains `schema_version`, `snapshot_family_id`, `snapshot_id`, and
  the canonically ordered source record ID/content-hash bindings. A snapshot is current
  only when its accepted audit sequence is the greatest sequence for that
  `snapshot_family_id`; duplicate greatest sequences or duplicate IDs fail resolution.

The catalog source artifact bytes are exactly the canonical JSON object hashed by
`catalog_snapshot_content_hash()`: `catalog_kind`, `entries`, and `complete`. The
artifact's accepted `AddEvidence` proposal and audit event establish acceptance; the
separate accepted snapshot artifact establishes the fixed-snapshot identity and
freshness. These readers are explicit compositions over existing evidence,
transaction, audit, and artifact storage. They do not add a generic repository locator,
a mutable catalog head, or repository authority to the procedure domain.

- [ ] **Step 4: Test every record family and durable-state probe**

Run: `python -m pytest tests/integration/storage/test_cognitive_repositories.py tests/integration/storage/test_evaluation_repositories.py tests/integration/storage/test_procedure_source_repositories.py tests/property/test_cognitive_append_only.py tests/integration/application/test_workspace_integrity.py -v`

Expected: PASS for round trip, unknown field, content-hash corruption, derived-column mismatch, relationship ordering, append-only enforcement, and one-row-only durable-state detection for all 18 tables.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/providers/storage tests/integration/storage/test_cognitive_repositories.py tests/integration/storage/test_evaluation_repositories.py tests/integration/storage/test_procedure_source_repositories.py tests/property/test_cognitive_append_only.py && python -m mypy src`

```bash
git add src/super_scientist/providers/storage tests/integration/storage/test_cognitive_repositories.py tests/integration/storage/test_evaluation_repositories.py tests/integration/storage/test_procedure_source_repositories.py tests/property/test_cognitive_append_only.py
git commit -m "feat: persist cognitive and evaluation records"
```

### Task 11: Add Governed Cognition and Collaboration Handlers

**Files:**
- Create: `src/super_scientist/application/cognition/__init__.py`
- Create: `src/super_scientist/application/cognition/service.py`
- Create: `src/super_scientist/application/collaboration/__init__.py`
- Create: `src/super_scientist/application/collaboration/service.py`
- Create: `src/super_scientist/application/transactions/cognition.py`
- Create: `src/super_scientist/application/transactions/collaboration.py`
- Create: `tests/integration/application/test_cognition_service.py`
- Create: `tests/integration/application/test_collaboration_service.py`

**Interfaces:**
- Consumes: proposal contracts, pure cognition/collaboration functions, and focused repositories.
- Produces: `fixed_cognition_handlers()`, `cognition_capabilities()`, `fixed_collaboration_handlers()`, and `collaboration_capabilities()`.

- [ ] **Step 1: Write recomputation, stale-state, and rollback tests**

```python
def test_cohort_handler_rejects_caller_supplied_selection_mismatch(runtime) -> None:
    proposal = record_cohort_plan(plan=cohort_plan(member_ids=("unqualified-peer",)))
    decision = runtime.coordinator.submit(proposal)
    assert decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH
    assert runtime.repositories.cohort_plans.list_all() == ()


def test_contribution_rolls_back_with_transaction_and_audit(runtime, monkeypatch) -> None:
    monkeypatch.setattr(runtime.contributions, "add", raise_storage_failure)
    with pytest.raises(SQLAlchemyError):
        runtime.coordinator.submit(append_peer_contribution())
    assert runtime.transaction_count() == 0
    assert runtime.audit_count() == 0
```

- [ ] **Step 2: Run the application tests**

Run: `python -m pytest tests/integration/application/test_cognition_service.py tests/integration/application/test_collaboration_service.py -v`

Expected: FAIL because handlers and capability factories do not exist.

- [ ] **Step 3: Implement pure-derivation handlers**

```python
class RecordCohortPlanHandler:
    proposal_type = "record_cohort_plan"

    def decide(self, proposal: RecordCohortPlan, context: CohortContext) -> TransactionDecision:
        expected = build_cohort(proposal.request, context.resolved_profiles)
        if expected != proposal.plan:
            return rejected(proposal, RejectionCode.DERIVATION_MISMATCH)
        if context.existing_plan is not None:
            return rejected(proposal, RejectionCode.ENTITY_ALREADY_EXISTS)
        return accepted(proposal)

    def project(self, proposal, decision, writes) -> None:
        require_accepted(decision)
        cast(CohortWriteCapability, writes).append_cohort_plan(proposal.plan)
```

Capability and diversity handlers validate exact active-policy hashes and recompute all derived fields. No handler calls `are_independent()`. Accepted operational diversity and peer agreement remain append-only observations.

- [ ] **Step 4: Implement history-derived collaboration handlers**

```python
class AppendPeerContributionHandler:
    proposal_type = "append_peer_contribution"

    def build_context(self, proposal, reads) -> CollaborationContext:
        capability = cast(CollaborationReadCapability, reads)
        state = rebuild_collaboration_state(
            capability.get_session(proposal.session_id),
            capability.list_requests(proposal.session_id),
            capability.list_contributions(proposal.session_id),
            capability.list_topology_events(proposal.session_id),
        )
        return CollaborationContext(state=state)

    def decide(self, proposal, context) -> TransactionDecision:
        expected = advance_collaboration_from_proposal(context.state, proposal)
        return exact_derived_decision(proposal, expected, proposal.resulting_state)
```

Run: `python -m pytest tests/integration/application/test_cognition_service.py tests/integration/application/test_collaboration_service.py tests/unit/cognition tests/unit/collaboration -v`

Expected: PASS for exact recomputation, duplicate IDs, policy drift, unknown parents, topology drift, all limits, idempotency, and rollback.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/application/cognition src/super_scientist/application/collaboration src/super_scientist/application/transactions/cognition.py src/super_scientist/application/transactions/collaboration.py tests/integration/application/test_cognition_service.py tests/integration/application/test_collaboration_service.py && python -m mypy src`

```bash
git add src/super_scientist/application/cognition src/super_scientist/application/collaboration src/super_scientist/application/transactions/cognition.py src/super_scientist/application/transactions/collaboration.py tests/integration/application/test_cognition_service.py tests/integration/application/test_collaboration_service.py
git commit -m "feat: govern cognitive collaboration records"
```

### Task 12: Add Procedure Compilation and Atomic Progress Binding Handlers

**Files:**
- Create: `src/super_scientist/application/procedures/__init__.py`
- Create: `src/super_scientist/application/procedures/service.py`
- Create: `src/super_scientist/application/transactions/procedures.py`
- Modify: `src/super_scientist/application/progress/service.py:195`
- Create: `tests/integration/application/test_procedure_service.py`
- Modify: `tests/integration/application/test_progress_service.py`
- Modify: `tests/integration/application/test_progress_integrity.py`

**Interfaces:**
- Consumes: `parse_untrusted_procedure_compilation_envelope()`, `parse_untrusted_procedure_compilation_result()`, `compile_method()`, `procedure_to_progress_plan()`, `RecordProgressPlanHandler`, compilation receipts, the focused Task 10 accepted-source readers, procedure repositories, and progress capabilities.
- Produces: `fixed_procedure_handlers()`, `procedure_capabilities()`, accepted invalid-compilation history, method terminal outcomes, and atomic compiled-plan binding.

- [ ] **Step 1: Write invalid-history and no-plan tests**

```python
def test_invalid_compilation_is_history_but_creates_no_plan(runtime) -> None:
    decision = runtime.coordinator.submit(record_invalid_compilation())
    assert decision.accepted is True
    assert runtime.compilations.get("compilation-invalid") is not None
    assert runtime.progress_plans.list_all() == ()


def test_binding_rejects_inconclusive_compilation(runtime) -> None:
    runtime.coordinator.submit(record_inconclusive_compilation())
    decision = runtime.coordinator.submit(bind_inconclusive_compilation())
    assert decision.reasons[0].code is RejectionCode.INVALID_PROCEDURE
    assert runtime.progress_plans.list_all() == ()
```

- [ ] **Step 2: Run procedure application tests**

Run: `python -m pytest tests/integration/application/test_procedure_service.py tests/integration/application/test_progress_service.py tests/integration/application/test_progress_integrity.py -v`

Expected: FAIL because procedure handlers and capability composition do not exist.

- [ ] **Step 3: Recompute compilation and retain terminal outcomes**

```python
class RecordProcedureCompilationHandler:
    proposal_type = "record_procedure_compilation"

    def decide(self, proposal, context) -> TransactionDecision:
        try:
            envelope = parse_untrusted_procedure_compilation_envelope(
                proposal.compilation
            )
            supplied_result = parse_untrusted_procedure_compilation_result(
                envelope
            )
            supplied_request = supplied_result.parse_request()
        except ProcedureBoundaryValidationError:
            return rejected(proposal, RejectionCode.INVALID_PROCEDURE)
        resolved_sources = resolve_procedure_source_receipts(
            supplied_request,
            accepted_source_receipts=context.accepted_source_receipts,
            capability_profiles=context.capability_profiles,
            artifact_catalog_snapshots=context.artifact_catalog_snapshots,
            tool_catalog_snapshots=context.tool_catalog_snapshots,
            validator_catalog_snapshots=context.validator_catalog_snapshots,
            source_snapshots=context.source_snapshots,
        )
        if resolved_sources is None:
            return rejected(proposal, RejectionCode.STALE_REFERENCE)
        expected = compile_method(supplied_request)
        if expected != supplied_result:
            return rejected(proposal, RejectionCode.DERIVATION_MISMATCH)
        if (
            envelope.governing_policy_hash
            != context.active_policy.policy_hash
        ):
            return rejected(proposal, RejectionCode.STALE_REFERENCE)
        return reject_existing_or_accept(proposal, context.existing_compilation)

    def project(self, proposal, decision, writes) -> None:
        require_accepted(decision)
        record = ProcedureCompilationRecord.build_from_untrusted_envelope(
            proposal.compilation
        )
        cast(ProcedureWriteCapability, writes).append_compilation(record)
```

The handler catches `ProcedureBoundaryValidationError` and returns the existing invalid-
procedure rejection without recording the error object or rejected input. Application
code must not call `ProcedureCompilationResult.model_validate*()` for untrusted input;
those Pydantic constructors remain internal trusted-construction and diagnostic APIs.
The handler calls the public safe parser before compiler recomputation, policy or
repository authority checks, and durable-record construction. The project step uses
only `ProcedureCompilationRecord.build_from_untrusted_envelope()`, which repeats the
same complete-envelope validation before reading compilation ID, time, policy hash, or
result bytes. No handler treats the opaque proposal envelope as a
`ProcedureCompilationRecord`.

`resolve_procedure_source_receipts()` must resolve every `AcceptedSourceReceiptRef` in
the `GroundedCapabilityAssessment.profile_receipt` fields plus the artifact, tool, and
validator catalog receipts. For each reference, it performs these checks before
compiler recomputation, active-policy comparison, duplicate acceptance, projection, or
progress binding:

1. `AcceptedProcedureSourceReceiptReader.resolve(reference)` must return one accepted
   transaction and its one exact persisted audit event. The resolved proposal ID/hash
   and audit event ID/hash must equal the reference. The reference content hash must be
   canonical, and `receipt_id` must resolve uniquely.
2. The focused repository selected by `source_kind` must return one source record. The
   resolved record ID, schema version, and canonical content hash must equal
   `source_record_id`, `source_schema_version`, and `source_content_hash`.
3. `ProcedureSourceSnapshotRepository.resolve_exact(source_snapshot_id,
   source_snapshot_hash)` must return one accepted snapshot artifact, and
   `is_current(source_snapshot_id, source_snapshot_hash)` must be true.
4. A capability source must equal the retained profile, its accepted
   `RecordCapabilityProfile` payload, and the evidence snapshot used by the recomputed
   assessment. A catalog source must equal the request's complete ordered entries and
   completeness flag. The three catalog sources must resolve to the same current
   snapshot ID/hash.

If any receipt is absent, duplicated, stale, wrong-kind, schema-mismatched,
content-mismatched, snapshot-mismatched, proposal-mismatched, or audit-mismatched, the
handler returns `STALE_REFERENCE`. It performs no compiler, policy, acceptance,
projection, or progress action. The coordinator therefore rejects the proposal
atomically without appending a compilation, binding, or progress plan. The coordinator
retains the rejected transaction decision and its audit event together under its
existing semantics. No accept path may bypass this complete resolution.

`RecordMethodDirectionOutcomeHandler` accepts `SUPPORTED`, `UNSUPPORTED`, `INCONCLUSIVE`, or `ABANDONED` only when every referenced compilation, failure, evidence, and existing budget record exists. `SUPPORTED` remains an evidence outcome and has no claim or admission projection.

- [ ] **Step 4: Compose the existing progress handler inside one transaction**

```python
class BindCompiledProgressPlanHandler:
    proposal_type = "bind_compiled_progress_plan"

    def decide(self, proposal, context) -> TransactionDecision:
        try:
            compilation_request = context.compilation.result.parse_request()
        except ProcedureBoundaryValidationError:
            return rejected(proposal, RejectionCode.INVALID_PROCEDURE)
        resolved_sources = resolve_procedure_source_receipts(
            compilation_request,
            accepted_source_receipts=context.accepted_source_receipts,
            capability_profiles=context.capability_profiles,
            artifact_catalog_snapshots=context.artifact_catalog_snapshots,
            tool_catalog_snapshots=context.tool_catalog_snapshots,
            validator_catalog_snapshots=context.validator_catalog_snapshots,
            source_snapshots=context.source_snapshots,
        )
        if resolved_sources is None:
            return rejected(proposal, RejectionCode.STALE_REFERENCE)
        require_current_valid_compilation(context.compilation, proposal.compilation_receipt)
        expected_plan = procedure_to_progress_plan(
            context.compilation.result,
            run_id=proposal.plan.run_id,
            plan_version_id=proposal.plan.plan_version_id,
            version=proposal.plan.version,
            created_at=proposal.plan.created_at,
            governing_policy_hash=context.active_policy.policy_hash,
        )
        if expected_plan != proposal.plan:
            return rejected(proposal, RejectionCode.DERIVATION_MISMATCH)
        return context.progress_handler.decide(context.progress_proposal, context.progress_context)

    def project(self, proposal, decision, writes) -> None:
        capability = cast(ProcedureBindingCapabilities, writes)
        capability.progress_handler.project(capability.progress_proposal, decision, capability.progress)
        capability.append_binding(proposal.binding)
```

The binding handler repeats complete accepted-source resolution against the stored
compilation request before it validates the compilation receipt or calls
`procedure_to_progress_plan()`. A source that became stale after compilation acceptance
therefore cannot produce a progress plan. The synthetic `RecordProgressPlan` uses the
binding proposal's proposal ID, proposer, approval, and exact plan. It does not submit a
nested transaction. Both plan and binding roll back together.

Run: `python -m pytest tests/integration/application/test_procedure_service.py tests/integration/application/test_progress_service.py tests/integration/application/test_progress_integrity.py tests/unit/procedures tests/unit/progress -v`

Expected: PASS; the existing progress authority, dependency, policy, and duplicate checks execute unchanged.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/application/procedures src/super_scientist/application/transactions/procedures.py src/super_scientist/application/progress/service.py tests/integration/application/test_procedure_service.py && python -m mypy src`

```bash
git add src/super_scientist/application/procedures src/super_scientist/application/transactions/procedures.py src/super_scientist/application/progress/service.py tests/integration/application/test_procedure_service.py tests/integration/application/test_progress_service.py tests/integration/application/test_progress_integrity.py
git commit -m "feat: bind valid procedures to progress plans"
```

### Task 13: Add Governed Guidance, Matrix, Trace, and Reward Handlers

**Files:**
- Create: `src/super_scientist/application/harness_eval/extensions.py`
- Create: `src/super_scientist/application/transactions/harness_extensions.py`
- Modify: `src/super_scientist/application/harness_eval/__init__.py`
- Create: `tests/integration/application/test_harness_eval_extensions.py`
- Modify: `tests/integration/application/test_harness_eval_service.py`

**Interfaces:**
- Consumes: evaluation-extension proposals, pure analyses, existing `EvaluationBudget`, evaluation repositories, and protected-result references.
- Produces: `fixed_harness_extension_handlers()` and `harness_extension_capabilities()`.

- [ ] **Step 1: Write stale trace, unmatched cell, and invalid reward tests**

```python
def test_trace_handler_rejects_model_runtime_mismatch(runtime) -> None:
    runtime.record_model_harness_protocol()
    decision = runtime.coordinator.submit(record_trace(model_version="wrong-version"))
    assert decision.reasons[0].code is RejectionCode.STALE_REFERENCE
    assert runtime.traces.list_all() == ()


def test_invalid_reward_is_stored_but_not_positive_cell_evidence(runtime) -> None:
    runtime.record_current_trace()
    decision = runtime.coordinator.submit(record_invalid_reward(value=Decimal("999")))
    assert decision.accepted is True
    assert runtime.rewards.get("reward-assessment-1").assessment.status is RewardValidityStatus.INVALID
    assert runtime.promotion_reward_values() == ()
```

- [ ] **Step 2: Run extension integration tests**

Run: `python -m pytest tests/integration/application/test_harness_eval_extensions.py tests/integration/application/test_harness_eval_service.py -v`

Expected: FAIL because extension handlers do not exist.

- [ ] **Step 3: Implement exact-protocol and cell handlers**

```python
class AppendGuidanceEvaluationCellHandler:
    proposal_type = "append_guidance_evaluation_cell"

    def decide(self, proposal, context) -> TransactionDecision:
        if context.protocol is None:
            return rejected(proposal, RejectionCode.MISSING_ENTITY)
        confounds = guidance_cell_confounds(context.protocol, proposal.cell)
        if confounds:
            return rejected(proposal, RejectionCode.UNMATCHED_EVALUATION)
        return reject_existing_or_accept(proposal, context.existing_cell)
```

Protocol handlers require exact model/harness/verifier/task/budget identities. A dedicated
`HarnessTraceProposalAdapter` is the only actor that receives raw bounded JSON text or exact bytes:
it calls `parse_untrusted_harness_execution_trace()` and constructs a typed
`RecordHarnessExecutionTrace` before the handler runs. The handler consumes only that typed
proposal; direct `HarnessExecutionTrace.model_validate*()` calls are trusted/internal and must not
parse request payloads. Its external reward-value JSON is tagged as either numeric or categorical;
legacy bare strings are rejected so numeric-looking categorical values cannot be misclassified.
Analysis handlers recompute `analyze_model_harness()` and compare
canonically. No handler creates or changes a `HarnessCampaign` decision.

```python
class HarnessTraceProposalAdapter:
    def from_untrusted_payload(
        self,
        payload: str | bytes,
        metadata: HarnessTraceRecordMetadata,
        proposal_id: StableIdentifier,
        idempotency_key: StableIdentifier,
        proposer: ActorIdentity,
    ) -> RecordHarnessExecutionTrace:
        return RecordHarnessExecutionTrace(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            envelope=HarnessExecutionTraceEnvelope(
                metadata=metadata,
                trace=parse_untrusted_harness_execution_trace(payload),
            ),
        )
```

- [ ] **Step 4: Recompute trace freshness and reward validity**

```python
class RecordRewardAssessmentHandler:
    proposal_type = "record_reward_assessment"

    def decide(self, proposal, context) -> TransactionDecision:
        if context.trace is None:
            return rejected(proposal, RejectionCode.MISSING_ENTITY)
        if context.trace.reward_observation != proposal.observation:
            return rejected(proposal, RejectionCode.DERIVATION_MISMATCH)
        assessment_receipt = reward_validity_receipt(proposal.assessment)
        capabilities = context.resolve_reward_assessment_capabilities(
            trace_receipt=EvidenceReceipt(
                record_id=context.trace.trace_id,
                schema_version=context.trace.schema_version,
                content_hash=context.trace.content_hash,
            ),
            assessment_receipt=assessment_receipt,
        )
        if capabilities is None:
            return rejected(proposal, RejectionCode.STALE_REFERENCE)
        freshness = trace_freshness(
            capabilities.expectation,
            context.trace,
            inventory=capabilities.inventory,
        )
        expected = assess_reward_validity(
            proposal.observation,
            context.trace,
            proposal.findings,
            expectation=capabilities.expectation,
            verification=capabilities.verification,
            diagnostic_coverage=capabilities.diagnostic_coverage,
            inventory=capabilities.inventory,
        )
        if expected != proposal.assessment or expected.freshness != freshness:
            return rejected(proposal, RejectionCode.DERIVATION_MISMATCH)
        return reject_existing_or_accept(proposal, context.existing_assessment)
```

After the handler stores the exact assessment, only
`valid_reward_evidence((expected,))` feeds a positive evidence selector. Invalid and inconclusive
assessments remain historical records but contribute an empty tuple to that selector.

Run: `python -m pytest tests/integration/application/test_harness_eval_extensions.py tests/integration/application/test_harness_eval_service.py tests/unit/harness_eval -v`

Expected: PASS. Stale/mismatched traces reject; invalid/inconclusive rewards remain history; only current `VALID` assessments enter positive evidence selectors.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/application/harness_eval src/super_scientist/application/transactions/harness_extensions.py tests/integration/application/test_harness_eval_extensions.py && python -m mypy src`

```bash
git add src/super_scientist/application/harness_eval src/super_scientist/application/transactions/harness_extensions.py tests/integration/application/test_harness_eval_extensions.py tests/integration/application/test_harness_eval_service.py
git commit -m "feat: govern harness native evaluation evidence"
```

### Task 14: Wire Fixed Routing and Add the Non-Authoritative Application Facade

**Files:**
- Modify: `src/super_scientist/application/transactions/coordinator.py:182`
- Create: `src/super_scientist/application/cognitive/__init__.py`
- Create: `src/super_scientist/application/cognitive/service.py`
- Create: `tests/integration/application/test_cognitive_service.py`
- Modify: `tests/integration/application/test_transaction_coordinator.py`
- Modify: `tests/adversarial/test_model_execution_boundary.py`

**Interfaces:**
- Consumes: four fixed handler tuples and four focused capability factories.
- Produces: all 18 routes in `ProposalRouter`, `CognitiveOrchestrationService`, and `ResearchCoordinator.run_declared_slice()`; no new authority.

- [ ] **Step 1: Write route completeness and authority-graph tests**

```python
def test_every_new_proposal_has_one_fixed_route(runtime) -> None:
    for proposal_type in NEW_COGNITIVE_PROPOSAL_TYPES:
        assert runtime.coordinator.router.resolve(proposal_type).proposal_type == proposal_type


def test_research_coordinator_object_graph_has_no_storage_or_execution_authority(runtime) -> None:
    forbidden = {RepositorySet, DatabaseUnitOfWork, Connection, ArtifactStore, ProtectedAnswerReader}
    assert walk_object_graph_types(runtime.research_coordinator).isdisjoint(forbidden)
```

- [ ] **Step 2: Run coordinator/facade tests**

Run: `python -m pytest tests/integration/application/test_cognitive_service.py tests/integration/application/test_transaction_coordinator.py tests/adversarial/test_model_execution_boundary.py -v`

Expected: FAIL because new handlers are not registered and the facade is absent.

- [ ] **Step 3: Register fixed handlers and capability families**

```python
cognitive_handlers = tuple(
    (handler.proposal_type, handler)
    for handler in (
        *fixed_cognition_handlers(),
        *fixed_collaboration_handlers(),
        *fixed_procedure_handlers(),
        *fixed_harness_extension_handlers(),
    )
)
self._router = ProposalRouter((*existing_handlers, *cognitive_handlers))
```

Add explicit `isinstance` groups beside existing progress/harness groups and call only the matching focused capability factory. Do not add dynamic imports, fallback routing, a generic storage capability, or nested coordinator submission.

- [ ] **Step 4: Add sequencing that stops on first rejection**

```python
class CognitiveOrchestrationService:
    __slots__ = ("_coordinator",)

    def __init__(self, coordinator: TransactionCoordinator) -> None:
        if type(coordinator) is not TransactionCoordinator:
            raise TypeError("cognitive service requires the exact transaction coordinator")
        self._coordinator = coordinator

    def submit(self, proposal: Proposal) -> TransactionDecision:
        return self._coordinator.submit(proposal)


class ResearchCoordinator:
    __slots__ = ("_submitter",)

    def __init__(self, submitter: CognitiveOrchestrationService) -> None:
        if type(submitter) is not CognitiveOrchestrationService:
            raise TypeError("research coordinator requires the sealed submit capability")
        self._submitter = submitter

    def run_declared_slice(self, proposals: tuple[Proposal, ...]) -> tuple[TransactionDecision, ...]:
        decisions: list[TransactionDecision] = []
        for proposal in proposals:
            decision = self._submitter.submit(proposal)
            decisions.append(decision)
            if not decision.accepted:
                break
        return tuple(decisions)
```

Run: `python -m pytest tests/integration/application/test_cognitive_service.py tests/integration/application/test_transaction_coordinator.py tests/property/test_admission_idempotency.py tests/property/test_transaction_replay.py tests/adversarial/test_model_execution_boundary.py -v`

Expected: PASS; exact replay appends nothing, batch rollback remains atomic, and the facade cannot weaken a rejected proposal or bypass the coordinator.

- [ ] **Step 5: Run the Phase B gate, two reviews, and commit**

Run: `python -m pytest tests/integration/application tests/integration/storage tests/property/test_cognitive_append_only.py tests/property/test_transaction_replay.py tests/adversarial/test_model_execution_boundary.py -v`

Run: `python -m ruff check src tests/integration/application tests/integration/storage tests/property/test_cognitive_append_only.py && python -m mypy src`

Expected: all commands exit 0. Run one fresh-context specification-compliance review against sections 14-17 and one separate code-quality/least-authority review. Resolve every finding before committing.

```bash
git add src/super_scientist/application/transactions/coordinator.py src/super_scientist/application/cognitive tests/integration/application/test_cognitive_service.py tests/integration/application/test_transaction_coordinator.py tests/adversarial/test_model_execution_boundary.py
git commit -m "feat: route governed cognitive orchestration"
```

### Task 15: Reconstruct, Verify, Export, Import, and Replay Every New Record

**Files:**
- Modify: `src/super_scientist/providers/storage/integrity_records.py`
- Modify: `src/super_scientist/providers/storage/repositories.py:939`
- Modify: `src/super_scientist/application/workspace_integrity.py:621`
- Modify: `src/super_scientist/application/workspace_exchange.py:640`
- Create: `src/super_scientist/application/cognitive/integrity.py`
- Create: `tests/integration/application/test_cognitive_workspace_integrity.py`
- Create: `tests/integration/application/test_cognitive_workspace_exchange.py`
- Modify: `tests/integration/application/test_workspace_exchange.py`

**Interfaces:**
- Consumes: accepted transaction/audit history, `CognitiveIntegritySnapshot`, `EvaluationExtensionIntegritySnapshot`, and coordinator replay.
- Produces: `expected_cognitive_snapshot()`, `expected_evaluation_extension_snapshot()`, complete projection expectations, 0.2/0.3 bundle compatibility, and fail-closed unknown-version behavior.

- [ ] **Step 1: Write tamper, legacy, and round-trip failures**

```python
def test_cognitive_row_tampering_blocks_next_write(runtime) -> None:
    runtime.record_complete_cognitive_slice()
    runtime.tamper_payload("procedure_compilations", "compilation-1")
    with pytest.raises(StorageIntegrityError, match="workspace integrity error"):
        runtime.coordinator.submit(runtime.next_capability_profile())


def test_030_bundle_round_trip_preserves_complete_integrity_snapshot(runtime, empty_target) -> None:
    runtime.record_complete_cognitive_slice()
    exported = export_workspace(runtime.repositories, runtime.connection, runtime.artifacts)
    import_workspace(exported, target=empty_target)
    assert empty_target.integrity_snapshots() == runtime.integrity_snapshots()


def test_020_bundle_import_creates_no_synthetic_0007_records(legacy_bundle, empty_target) -> None:
    import_workspace(legacy_bundle, target=empty_target)
    assert empty_target.cognitive_snapshot().is_empty()
    assert empty_target.evaluation_extension_snapshot().is_empty()
```

- [ ] **Step 2: Run the integrity and exchange tests**

Run: `python -m pytest tests/integration/application/test_cognitive_workspace_integrity.py tests/integration/application/test_cognitive_workspace_exchange.py tests/integration/application/test_workspace_exchange.py -v`

Expected: FAIL because workspace verification does not reconstruct 0007 records.

- [ ] **Step 3: Rebuild snapshots from accepted proposals**

```python
def expected_cognitive_snapshot(
    transactions: tuple[StoredTransaction, ...],
) -> CognitiveIntegritySnapshot:
    accepted = tuple(item.proposal for item in transactions if item.decision.accepted)
    return CognitiveIntegritySnapshot(
        capability_profiles=tuple(
            item.profile for item in accepted if isinstance(item, RecordCapabilityProfile)
        ),
        cohort_plans=tuple(
            item.plan for item in accepted if isinstance(item, RecordCohortPlan)
        ),
        diversity_assessments=tuple(
            item.assessment
            for item in accepted
            if isinstance(item, RecordDiversityAssessment)
        ),
        collaboration_sessions=tuple(
            item.session
            for item in accepted
            if isinstance(item, RecordCollaborationSession)
        ),
        peer_requests=tuple(
            item.request for item in accepted if isinstance(item, AppendPeerRequest)
        ),
        peer_contributions=tuple(
            item.contribution
            for item in accepted
            if isinstance(item, AppendPeerContribution)
        ),
        topology_events=tuple(
            item.event for item in accepted if isinstance(item, AppendTopologyEvent)
        ),
        terminations=tuple(
            item.termination
            for item in accepted
            if isinstance(item, RecordCollaborationTermination)
        ),
        compilations=tuple(
            compilation_record_from_accepted_proposal(item)
            for item in accepted
            if isinstance(item, RecordProcedureCompilation)
        ),
        method_outcomes=tuple(
            item.outcome
            for item in accepted
            if isinstance(item, RecordMethodDirectionOutcome)
        ),
        bindings=tuple(
            item.binding for item in accepted if isinstance(item, BindCompiledProgressPlan)
        ),
    )
```

Use explicit typed extraction functions rather than reflection in production code. Recompute cohort plans, diversity, collaboration states, compiler results, plan mappings, matrix analyses, trace freshness, and reward validity before comparing snapshots. A transaction/audit mismatch is still checked before domain reconstruction.
`compilation_record_from_accepted_proposal()` must call
`ProcedureCompilationRecord.build_from_untrusted_envelope()` and then repeat the Task 12
compiler equality check. The factory fresh-validates every envelope field before it
reads metadata. Integrity code must never insert
`RecordProcedureCompilation.compilation` directly into a typed compilation snapshot.

- [ ] **Step 4: Extend bundle expectations and replay only through the coordinator**

Add one `WorkspaceProjectionExpectation` per append-only record using a stable kind such as `procedure_compilation_record` and the canonical record hash. Keep existing bundle schema decoding backward compatible with bundles that contain no new expectation kinds. Import proposals only with `TransactionCoordinator.submit_intent()`; do not insert 0007 rows directly.

Run: `python -m pytest tests/integration/application/test_cognitive_workspace_integrity.py tests/integration/application/test_cognitive_workspace_exchange.py tests/integration/application/test_workspace_integrity.py tests/integration/application/test_workspace_exchange.py tests/property/test_transaction_replay.py -v`

Expected: PASS for missing row, extra row, changed payload, changed ordering, changed availability, stale reference, artifact tamper, process restart, 0.2 import, and complete 0.3 round trip.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/application/cognitive/integrity.py src/super_scientist/application/workspace_integrity.py src/super_scientist/application/workspace_exchange.py src/super_scientist/providers/storage/integrity_records.py src/super_scientist/providers/storage/repositories.py tests/integration/application/test_cognitive_workspace_integrity.py tests/integration/application/test_cognitive_workspace_exchange.py && python -m mypy src`

```bash
git add src/super_scientist/application/cognitive/integrity.py src/super_scientist/application/workspace_integrity.py src/super_scientist/application/workspace_exchange.py src/super_scientist/providers/storage/integrity_records.py src/super_scientist/providers/storage/repositories.py tests/integration/application/test_cognitive_workspace_integrity.py tests/integration/application/test_cognitive_workspace_exchange.py tests/integration/application/test_workspace_exchange.py
git commit -m "feat: replay cognitive workspace records"
```

### Task 16: Add Read-Only Cognitive Record Inspection

**Files:**
- Create: `src/super_scientist/application/cognitive/reader.py`
- Create: `src/super_scientist/cli/cognitive.py`
- Modify: `src/super_scientist/cli/main.py:15`
- Create: `tests/integration/cli/test_cognitive_cli.py`
- Modify: `tests/property/test_cli_json_envelopes.py`

**Interfaces:**
- Consumes: fixed repositories and existing `emit()` JSON envelope.
- Produces: `CognitiveRecordKind`, `CognitiveRecordReader.get(kind, record_id)`, and `scientist-harness cognitive inspect --root PATH --kind KIND --id ID [--json]`.

- [ ] **Step 1: Write read-only CLI contract tests**

```python
def test_cognitive_inspect_returns_canonical_json(cli, populated_workspace) -> None:
    result = cli.invoke(
        app,
        ["cognitive", "inspect", "--root", str(populated_workspace),
         "--kind", "procedure-compilation", "--id", "compilation-1", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "cognitive inspect"
    assert payload["data"]["record"]["compilation_id"] == "compilation-1"


def test_cognitive_cli_exposes_no_mutation_command(cli) -> None:
    result = cli.invoke(app, ["cognitive", "--help"])
    assert "inspect" in result.stdout
    assert "run" not in result.stdout
    assert "submit" not in result.stdout
```

- [ ] **Step 2: Run CLI tests**

Run: `python -m pytest tests/integration/cli/test_cognitive_cli.py tests/property/test_cli_json_envelopes.py -v`

Expected: FAIL because the command group does not exist.

- [ ] **Step 3: Implement the fixed record-kind reader**

```python
class CognitiveRecordKind(StrEnum):
    CAPABILITY_PROFILE = "capability-profile"
    COHORT_PLAN = "cohort-plan"
    DIVERSITY_ASSESSMENT = "diversity-assessment"
    COLLABORATION_SESSION = "collaboration-session"
    PEER_REQUEST = "peer-request"
    PEER_CONTRIBUTION = "peer-contribution"
    TOPOLOGY_EVENT = "topology-event"
    COLLABORATION_TERMINATION = "collaboration-termination"
    PROCEDURE_COMPILATION = "procedure-compilation"
    METHOD_DIRECTION_OUTCOME = "method-direction-outcome"
    COMPILED_PROGRESS_PLAN_BINDING = "compiled-progress-plan-binding"
    GUIDANCE_PROTOCOL = "guidance-protocol"
    GUIDANCE_CELL = "guidance-cell"
    MODEL_HARNESS_PROTOCOL = "model-harness-protocol"
    MODEL_HARNESS_CELL = "model-harness-cell"
    MODEL_HARNESS_ANALYSIS = "model-harness-analysis"
    HARNESS_TRACE = "harness-trace"
    REWARD_ASSESSMENT = "reward-assessment"


def get(self, kind: CognitiveRecordKind, record_id: str) -> BaseModel | None:
    repository = self._fixed_readers[kind]
    return repository.get(record_id)
```

Build `_fixed_readers` from a source-controlled mapping. The CLI opens the workspace read-only, validates integrity, emits canonical model JSON, and returns `MISSING_ENTITY` for an unknown ID. It accepts no provider, command, import, tool, model, or execution option.

- [ ] **Step 4: Prove help, error, and envelope stability**

Run: `python -m pytest tests/integration/cli/test_cognitive_cli.py tests/integration/cli tests/property/test_cli_json_envelopes.py -v`

Expected: PASS in text and JSON modes; the existing command path inventory remains valid after adding `("cognitive", "inspect")`.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/super_scientist/application/cognitive/reader.py src/super_scientist/cli/cognitive.py src/super_scientist/cli/main.py tests/integration/cli/test_cognitive_cli.py tests/property/test_cli_json_envelopes.py && python -m mypy src`

```bash
git add src/super_scientist/application/cognitive/reader.py src/super_scientist/cli/cognitive.py src/super_scientist/cli/main.py tests/integration/cli/test_cognitive_cli.py tests/property/test_cli_json_envelopes.py
git commit -m "feat: inspect cognitive records read only"
```

### Task 17: Build the Deterministic Offline Vertical Slice

**Files:**
- Create: `examples/governed_cognitive_procedure_vertical_slice.py`
- Create: `docs/examples/governed-cognitive-procedure-vertical-slice.md`
- Create: `tests/e2e/test_governed_cognitive_procedure_vertical_slice.py`
- Modify: `tests/unit/quality/test_wheel_smoke.py`

**Interfaces:**
- Consumes: public domain/application interfaces, a temporary workspace, fixed peers, deterministic fixtures, and exact checker results.
- Produces: `run_example(workspace_root: Path) -> dict[str, object]` and stable model-free JSON output.

- [ ] **Step 1: Write the end-to-end output contract**

```python
def test_example_is_deterministic_and_exercises_rejection_and_replay(tmp_path) -> None:
    first = run_example(tmp_path / "first")
    second = run_example(tmp_path / "second")
    assert first == second
    assert first["invalid_compilation"]["status"] == "INVALID"
    assert first["valid_binding"]["accepted"] is True
    assert first["invalid_reward"]["promotion_evidence"] is False
    assert first["workspace"]["verified"] is True
    assert first["workspace"]["import_verified"] is True
```

- [ ] **Step 2: Run the e2e test**

Run: `python -m pytest tests/e2e/test_governed_cognitive_procedure_vertical_slice.py -v`

Expected: FAIL because the example does not exist.

- [ ] **Step 3: Implement the fixed model-free sequence**

```python
def run_example(workspace_root: Path) -> dict[str, object]:
    runtime = create_local_runtime(workspace_root, fixed_policy(), FixedClock())
    submitter = CognitiveOrchestrationService(runtime.coordinator)
    decisions = ResearchCoordinator(submitter).run_declared_slice(
        declared_fixture_proposals(runtime)
    )
    verification = verify_workspace(runtime.repositories(), runtime.artifact_store)
    imported = round_trip_into_fresh_workspace(runtime)
    return canonical_example_summary(decisions, verification, imported)
```

The declared proposals must demonstrate verified/self-reported/unknown capabilities, same-model prompt diversity without independence, a topology update, bounded challenge, one invalid and one valid procedure, one bound `ProgressPlan`, all four guidance conditions, a two-model-by-two-harness grid, available/unavailable metadata, a high invalid reward, verification, export, import, and replay.

- [ ] **Step 4: Prove offline packaging behavior**

Run: `python -m pytest tests/e2e/test_governed_cognitive_procedure_vertical_slice.py tests/unit/quality/test_wheel_smoke.py -v`

Run: `python examples/governed_cognitive_procedure_vertical_slice.py --json`

Expected: tests pass; the script exits 0 and emits one stable JSON object with no network, model API, GPU, subprocess, or optional dependency.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check examples/governed_cognitive_procedure_vertical_slice.py tests/e2e/test_governed_cognitive_procedure_vertical_slice.py tests/unit/quality/test_wheel_smoke.py && python -m mypy src`

```bash
git add examples/governed_cognitive_procedure_vertical_slice.py docs/examples/governed-cognitive-procedure-vertical-slice.md tests/e2e/test_governed_cognitive_procedure_vertical_slice.py tests/unit/quality/test_wheel_smoke.py
git commit -m "feat: demonstrate governed cognitive procedures"
```

### Task 18: Prove Authority, Immutability, Determinism, and Threat Mitigations

**Files:**
- Create: `tests/adversarial/test_cognitive_authority.py`
- Create: `tests/adversarial/test_procedure_escalation.py`
- Create: `tests/adversarial/test_trace_reward_tampering.py`
- Modify: `tests/property/test_cognitive_append_only.py`
- Modify: `tests/property/test_transaction_replay.py`
- Modify: `tests/property/test_artifact_immutability.py`
- Modify: `tests/adversarial/test_reviewer_authority.py`

**Interfaces:**
- Consumes: the complete vertical slice.
- Produces: executable evidence for every threat and no-authority claim in specification sections 19-21.

- [ ] **Step 1: Add the authority and consensus attacks**

```python
def test_peer_majority_cannot_transition_claim(runtime) -> None:
    runtime.record_unanimous_peer_contributions()
    decision = runtime.coordinator.submit(runtime.claim_transition_without_evidence())
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE


def test_diversity_assessment_cannot_satisfy_reviewer_independence(runtime) -> None:
    assessment = runtime.same_model_prompt_diversity()
    assert assessment.axes["prompt_strategy"] is DiversityAxisStatus.DIFFERENT
    assert runtime.try_dependent_approval(assessment).accepted is False
```

- [ ] **Step 2: Add capability, topology, and procedure attacks**

Cover capability spoofing, self-report promotion, peer collusion, correlated consensus, routing loops, peer storms, topology manipulation, malicious delegation, recursive delegation, method anchoring, procedure-input prompt injection, undeclared artifacts, arbitrary commands, dynamic imports, unauthorized tools, and impossible governance/protected-evaluator authority.

```python
@pytest.mark.parametrize("operation", ("python_import", "shell_command", "provider_call"))
def test_compiler_rejects_executable_operation(operation: str) -> None:
    with pytest.raises(ValidationError):
        ProcedureStep.model_validate(forbidden_step_payload(operation))


def test_hidden_reasoning_and_forbidden_runtime_imports_are_absent(source_tree) -> None:
    assert source_tree.find_schema_field("chain_of_thought") == ()
    assert source_tree.forbidden_imports(
        roots=("domain/cognition", "domain/collaboration", "domain/procedures"),
        names=(
            "subprocess",
            "socket",
            "requests",
            "to" + "rch",
            "trans" + "formers",
        ),
    ) == ()
```

- [ ] **Step 3: Add trace, evaluator, and reward attacks**

Cover protected-answer leakage, evaluator leakage, trace field tampering, fabricated token IDs/log probabilities, context-hash mismatch, environment reward spoofing, verifier failure, reward-channel manipulation, proxy gaming, cherry-picking, premature termination, resource evasion, and partition contamination.

```python
def test_fabricated_generation_metadata_is_rejected_before_persistence(runtime) -> None:
    decision = runtime.coordinator.submit(runtime.trace_with_unavailable_logprobs_and_value())
    assert decision.accepted is False
    assert runtime.traces.list_all() == ()
```

- [ ] **Step 4: Run adversarial and property suites**

Run: `python -m pytest tests/adversarial tests/property/test_cognitive_append_only.py tests/property/test_transaction_replay.py tests/property/test_artifact_immutability.py -v`

Expected: PASS. Hypothesis permutations must produce stable hashes/order; every update/delete fails; exact replay appends nothing; every authority attack leaves claims, policies, harness heads, and progress heads unchanged unless an existing governed proposal independently authorizes the change.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check tests/adversarial tests/property && python -m mypy src`

```bash
git add tests/adversarial tests/property/test_cognitive_append_only.py tests/property/test_transaction_replay.py tests/property/test_artifact_immutability.py
git commit -m "test: harden cognitive orchestration authority"
```

### Task 19: Update Sources, Architecture, Security, Reproducibility, Handbook, and Version

**Files:**
- Modify: `pyproject.toml:10`
- Modify: `src/super_scientist/__init__.py`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `GOVERNANCE.md`
- Modify: `CLAIM_LEDGER.md`
- Modify: `SECURITY.md`
- Modify: `THREAT_MODEL.md`
- Modify: `REPRODUCIBILITY.md`
- Modify: `docs/governed-adaptation.md`
- Modify: `docs/harness-evolution-evaluation.md`
- Modify: `docs/long-horizon-execution.md`
- Modify: `docs/hypothesis-model-checker-loop.md`
- Modify: `docs/research-inspirations.md`
- Modify: `docs/sources/source-register.yaml`
- Modify: `docs/behavior-handbook.md`
- Modify: `tests/unit/docs/test_source_register.py`
- Modify: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: implemented behavior and exact S30-S35 metadata from the approved spec.
- Produces: package version 0.3.0, exact source register entries, and normative documentation with test/static-check mappings. Controlled handbook artifacts and `docs/USER_MANUAL.md` are intentionally deferred to Task 20 so both can bind the exact implementation baseline.

- [ ] **Step 1: Write source/version/doc-control tests first**

```python
def test_source_register_has_exact_new_ids_and_versions(register) -> None:
    assert tuple(item.id for item in register.sources[-6:]) == (
        "S30", "S31", "S32", "S33", "S34", "S35"
    )
    assert register.by_id("S30").version_consulted == "arXiv:2608.11924v1"
    assert register.by_id("S35").reproduction_status == "not_reproduced"


def test_package_version_is_030() -> None:
    assert super_scientist.__version__ == "0.3.0"
```

- [ ] **Step 2: Run source, handbook, and package tests**

Run: `python -m pytest tests/unit/docs/test_source_register.py tests/unit/test_package.py -v`

Expected: FAIL because S30-S35 and version 0.3.0 are absent.

- [ ] **Step 3: Add exact S30-S35 entries and research boundaries**

Use the source-register schema unchanged. Record the exact authors, 2026, canonical arXiv locator/version, access date `2026-08-23`, verified repository/commit/license from specification section 5, proposal, evidence, adaptation, original synthesis, `adoption_status`, `reproduction_status: not_reproduced`, and limitations. S34 records that code is unavailable until acceptance. No entry claims reproduction or code reuse.

```yaml
  - id: S30
    title: "Spark-to-Paper: End-to-End Research Paper Generation as a Composable Skill"
    year: 2026
    canonical_locator: "https://arxiv.org/abs/2608.11924v1"
    version_consulted: "arXiv:2608.11924v1"
    accessed_at: "2026-08-23"
    repository: "https://github.com/Spark-To-Paper-Skills/spark-to-paper-skills/commit/c17149def034bc777462de612926c8e3b6d01b8c"
    license: "paper: arXiv non-exclusive distribution; repository: MIT"
    adoption_status: adapted
    reproduction_status: not_reproduced
```

- [ ] **Step 4: Update architecture, behavior, security, and reproducibility docs**

Document the control/cognitive plane split, procedure lifecycle, evaluation matching, trace availability, reward validity, threats, replay, failure statuses, and limitations. Every normative statement names its actor, condition, action, object, observable result, and verification test/command. Add claims to `CLAIM_LEDGER.md` only when tests support them. Update the actual nine-check quality inventory. Defer changes to controlled handbook manifests and generated handbook files until Task 20 captures the Task 19 commit.

Run: `python -m pytest tests/unit/docs/test_source_register.py tests/unit/test_package.py -v`

Expected: all tests pass and all non-manual public documentation agrees on version 0.3.0 and capability status.

- [ ] **Step 5: Run documentation precision checks and commit the implementation baseline**

Run: `git diff --check && rg -n "TO[D]O|TB[D]|as[ ]appropriate|when[ ]possible|peer consensus.*truth|diversity.*independence" README.md ARCHITECTURE.md GOVERNANCE.md CLAIM_LEDGER.md SECURITY.md THREAT_MODEL.md REPRODUCIBILITY.md docs`

Review every match. Resolve all precision `BLOCK` findings and every `WARN` that can change implementation, safety, or observable behavior.

```bash
git add pyproject.toml src/super_scientist/__init__.py README.md ARCHITECTURE.md GOVERNANCE.md CLAIM_LEDGER.md SECURITY.md THREAT_MODEL.md REPRODUCIBILITY.md docs/governed-adaptation.md docs/harness-evolution-evaluation.md docs/long-horizon-execution.md docs/hypothesis-model-checker-loop.md docs/research-inspirations.md docs/sources/source-register.yaml docs/behavior-handbook.md tests/unit/docs/test_source_register.py tests/unit/test_package.py
git commit -m "docs: document governed cognitive orchestration"
```

### Task 20: Bind and Verify the 0.3.0 User Manual

**Files:**
- Modify: `docs/USER_MANUAL.md`
- Modify: `docs/handbook/behaviors.json`
- Modify: `docs/handbook/handbook.json`
- Modify: `docs/handbook/handbook.md`
- Create: `tests/unit/docs/test_user_manual.py`
- Test: `tests/unit/docs/test_source_register.py`
- Test: `tests/integration/handbook/test_repository_handbook.py`

**Interfaces:**
- Consumes: the exact implementation baseline commit produced by Task 19.
- Produces: controlled manual and behavior-handbook baseline bindings, updated role/assignment/security/troubleshooting/glossary/source sections, regenerated handbook artifacts, and one cohesive `MAN-16` workflow.

- [ ] **Step 1: Capture the real baseline before editing the manual**

Run: `git status --short`

Expected: no tracked changes.

Run: `git rev-parse HEAD`

Expected: one 40-character Git object ID. Store it in a task-specific variable named `SSOH_IMPLEMENTATION_BASELINE`; do not use a placeholder or predict the documentation commit ID.

- [ ] **Step 2: Add failing manual-map assertions**

```python
def test_manual_maps_cognitive_workflow_and_exact_baseline(manual_text, git_repository) -> None:
    baseline = extract_document_control_baseline(manual_text)
    baseline_pyproject = git_repository.show_text(baseline, "pyproject.toml")
    assert "MAN-16 — Cognitive Cohorts and Procedure Compilation" in manual_text
    assert 'version = "0.3.0"' in baseline_pyproject
    assert "Operational diversity does not satisfy reviewer independence." in manual_text
    assert "Hidden chain-of-thought is not persisted." in manual_text
```

Run: `python -m pytest tests/unit/docs/test_user_manual.py -v`

Expected: FAIL because the manual still describes 0.2.0.

- [ ] **Step 3: Update the controlled manual sections**

Update `MAN-01`, `MAN-03`, `MAN-05`, `MAN-06`, `MAN-11`, `MAN-13`, `MAN-14`, and `MAN-15`. Add `MAN-16` with these ordered actions: declare capability requirements; record grounded profiles; select a bounded cohort; inspect diversity separately from independence; open a collaboration session; compile a candidate method; inspect invalid/inconclusive findings; bind only a valid procedure; record matched evaluations/traces/reward validity; inspect records; verify/export/import/replay.

For Research Coordinator, Capability Grounder, Peer Reasoner, Procedure Compiler, Procedure Validator, Cohort/Diversity Auditor, and Harness Trace Recorder, include capability status, purpose, recommended actor/model type, required capabilities, authority, independence requirement, inputs, outputs, common failures, resolution, unsuitable model types, and exact source/code references. Update every affected `docs/handbook/behaviors.json` source binding to `SSOH_IMPLEMENTATION_BASELINE` and the exact source hash, then regenerate the handbook.

- [ ] **Step 4: Run manual, source, and handbook verification**

Run: `scientist-harness handbook build --root . --repository . --manifest docs/handbook/behaviors.json --output-dir docs/handbook`

Run: `scientist-harness handbook verify --root . --repository . --manifest docs/handbook/behaviors.json`

Run: `python -m pytest tests/unit/docs/test_user_manual.py tests/unit/docs/test_source_register.py tests/integration/handbook/test_repository_handbook.py -v`

Expected: all commands exit 0. `MAN-01` names version 0.3.0 and exactly `SSOH_IMPLEMENTATION_BASELINE`, not the later manual commit.

- [ ] **Step 5: Run the Phase C documentation gate, two reviews, and commit**

Run: `git diff --check && rg -n "TO[D]O|TB[D]|as[ ]appropriate|when[ ]possible|eight checks|nine checks" docs/USER_MANUAL.md`

Resolve all precision findings. Run a fresh-context specification-compliance review against sections 18-26 and a separate documentation/code-quality review.

```bash
git add docs/USER_MANUAL.md docs/handbook/behaviors.json docs/handbook/handbook.json docs/handbook/handbook.md tests/unit/docs/test_user_manual.py tests/unit/docs/test_source_register.py tests/integration/handbook/test_repository_handbook.py
git commit -m "docs: bind 0.3.0 user manual"
```

### Task 21: Run the Complete Quality Gate and Independent Final Review

**Files:**
- Modify only files required to resolve verified failures; add a regression test with every behavioral fix.
- Create: `docs/reviews/0.3.0-spec-compliance.md`
- Create: `docs/reviews/0.3.0-code-quality.md`
- Create: `docs/reviews/0.3.0-authority-and-security.md`

**Interfaces:**
- Consumes: the complete 0.3.0 candidate.
- Produces: reproducible verification evidence and a reviewed branch ready for the user's chosen integration workflow.

- [ ] **Step 1: Install the declared development environment**

Run inside the isolated worktree's Python 3.12 environment: `python -m pip install -e ".[dev]"`

Expected: exit 0 without adding or changing dependency declarations.

- [ ] **Step 2: Run focused structural gates**

Run:

```bash
python -m pytest tests/integration/storage/test_migration_0007.py tests/integration/application/test_cognitive_workspace_integrity.py tests/integration/application/test_cognitive_workspace_exchange.py tests/e2e/test_governed_cognitive_procedure_vertical_slice.py -v
python -m ruff format --check .
python -m ruff check .
python -m mypy src
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Run the full automated suite and fixed quality gate**

Run: `python -m pytest`

Run: `scientist-harness quality-gate`

Expected: both commands exit 0. Record the exact passed/skipped counts and all nine quality-check results in the review documents. Do not write “all tests pass” until both outputs have been inspected.

- [ ] **Step 4: Prove deterministic example and bundle behavior twice**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from examples.governed_cognitive_procedure_vertical_slice import run_example

with TemporaryDirectory() as left, TemporaryDirectory() as right:
    first = run_example(Path(left))
    second = run_example(Path(right))
    assert first == second
    assert first["workspace"]["verified"] is True
    assert first["workspace"]["import_verified"] is True
```

Run the snippet with `python -`. Expected: exit 0 with no output.

- [ ] **Step 5: Run independent reviews and close every block**

Use `superpowers:requesting-code-review` for a fresh-context diff review. Run separate reviews for specification compliance, code quality/maintainability, and authority/security. Each review must inspect proposal authority, focused capabilities, progress reuse, harness reuse, independence separation, trace privacy, reward filtering, migration compatibility, replay, public docs, and the final diff against `fa3124a`.

Record each finding with severity, location, consequence, correction, and verification. Resolve every `BLOCK` and all material warnings. Re-run the affected focused suite after each fix, then rerun Step 3.

- [ ] **Step 6: Commit review evidence and verify the final tree**

```bash
git add docs/reviews/0.3.0-spec-compliance.md docs/reviews/0.3.0-code-quality.md docs/reviews/0.3.0-authority-and-security.md
git commit -m "docs: record 0.3.0 verification reviews"
git status --short
git log --oneline --decorate -20
```

Expected: the tracked worktree is clean and the branch contains only reviewed 0.3.0 work after `fa3124a`. Use `superpowers:verification-before-completion` before reporting results and `superpowers:finishing-a-development-branch` to present integration options; do not push, merge, or deploy without user authorization.

---

## Execution Sequence

1. Create the isolated worktree and verify commit identity and the full 0.2.0 baseline.
2. Execute Tasks 1-7; stop after the Phase A specification and code-quality reviews.
3. Execute Tasks 8-14; stop after the Phase B specification and least-authority reviews.
4. Execute Tasks 15-20; stop after the Phase C documentation and compatibility reviews.
5. Execute Task 21 and present the verified integration options.

Do not collapse tasks across a review boundary. A later task may consume only interfaces listed under its dependencies, and every commit must leave its focused suite passing.

## Specification Coverage Map

| Specification area | Implemented and verified by |
| --- | --- |
| Control plane versus cognitive plane | Tasks 8, 11-15, 18, 19-20 |
| Capability grounding and deterministic ties | Tasks 3, 11, 17-18 |
| Operational diversity versus independence | Tasks 3, 11, 17-20 |
| Bounded collaboration and transient topology | Tasks 4, 11, 15, 17-18 |
| Method compilation and all static checks | Tasks 5, 12, 15, 17-18 |
| Bounded revision and terminal method outcomes | Tasks 5, 12, 17, 19-20 |
| Existing progress-plan reuse | Tasks 5 and 12 |
| Four-condition guidance gradient | Tasks 6, 13, 15, 17 |
| Model-held/harness-held/interaction/transfer analysis | Tasks 6, 13, 15, 17 |
| Harness-native trace fidelity and unavailable metadata | Tasks 7, 13, 15, 17-18 |
| Reward value/validity separation and hacking diagnostics | Tasks 7, 13, 15, 17-20 |
| Fixed proposals and narrow capabilities | Tasks 8 and 11-14 |
| Migration, append-only persistence, and legacy compatibility | Tasks 1-2 and 9-10 |
| Integrity, replay, export, and import | Tasks 1 and 15 |
| Read-only application/CLI surface | Tasks 14 and 16 |
| Offline deterministic example | Task 17 |
| Threat model and no new authority | Tasks 18-21 |
| S30-S35 attribution and capability status | Tasks 19-20 |
| Version 0.3.0, User Manual, and controlled baseline | Tasks 19-20 |
| Full tests, nine-check quality gate, reviews, and verification | Task 21 |
