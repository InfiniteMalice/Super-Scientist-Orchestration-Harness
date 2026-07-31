# Governed Adaptation and Harness Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a backward-compatible 0.2.0 candidate that adds governed adaptation, independently validated long-horizon progress, source-bound evidence trails, behavioral-rule consolidation, behavior-to-code navigation, safe hypothesis/model/checker loops, evaluator succession, and matched-budget harness evaluation without granting runtime code-execution or self-promotion authority.

**Architecture:** Keep one transactional modular monolith. `KernelService` remains the compatibility facade while a fixed, build-time proposal router delegates new proposal kinds to focused handlers with capability-scoped repositories; the coordinator alone owns `BEGIN IMMEDIATE`, idempotency, policy attribution, projection, transaction append, audit append, commit, and rollback. Authoritative records are append-only, mutable heads are rebuildable projections, large bytes are content addressed, and protected evaluation data lives in a physically separate store.

**Tech Stack:** Python 3.12.13+, Pydantic 2.11+, SQLAlchemy 2.x, SQLite, Alembic, Typer 0.19.2, Click 8.3.3, pytest 9, Hypothesis 6, Ruff, strict mypy, branch coverage, Bandit, pip-audit, hatchling/build, twine, and the Python standard-library `ast` module.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-18-governed-adaptation-and-harness-evolution-design.md` exactly.
- Preserve every v0.1.0 proposal, rejection code, transaction row, audit envelope, policy hash, artifact, CLI command, exit status, exact replay behavior, and migration path.
- Leave `alembic/versions/0001_epistemic_kernel.py` byte-for-byte unchanged.
- Use strict, frozen Pydantic models with `extra="forbid"`; never use a generic `verified: bool`.
- Models, tools, reviewers, and humans may propose; only deterministic kernel code may admit and commit.
- No runtime record may grant dynamic import, `eval`, `exec`, subprocess, shell, network, filesystem, GPU, model-SDK, or arbitrary provider authority.
- Core and test dependencies remain unchanged; Torch, Transformers, PEFT, model SDKs, and benchmark packages are prohibited.
- Do not add benchmark-specific adapters, loaders, schemas, commands, examples, tests, dependencies, spatial/color/cell/grid assumptions, or hidden task contracts.
- Every persistent adaptive change needs non-`NONE` grounding, a `SelfImprovementMeasurementRecord`, a passed independent `EvaluatorAuditRecord`, rollback metadata, and the authority required by active policy.
- Progress is diagnostic only and cannot authorize claims, training data, adapters, harness changes, governance changes, or final success.
- Protected expected outputs remain in a separate SQLite database and artifact root and may not enter ordinary repositories, object graphs, exports, logs, exceptions, audit payloads, or candidate-facing schemas.
- Reviewers submit immutable assessments; only the governed integrator may propose a canonical rule diff.
- Confidence, likelihood, self-consistency, textual agreement, and correlated unanimity never substitute for evidence or independent verification.
- Use red-green-refactor TDD for every behavior. Run the focused test first, then the phase suite, then commit.
- Each task ends at an independently testable, fresh-reviewer-capable gate; each phase gets separate fresh-context specification-compliance and code-quality reviews before the next phase starts.
- Before Task 1 commits, `git config user.name` and `git config user.email` must both return user-approved values; if either is absent, pause and request them rather than inventing an identity.
- The old eight-check `CHECKS` registry must pass unmodified before the additive wheel-install check can be activated.
- Never weaken or skip Ruff, strict mypy, 90% branch coverage, Bandit, pip-audit, build, Twine, audit verification, or wheel smoke installation.
- All source and research claims must distinguish proposal, evidence, limitation, project adaptation, and project-original synthesis.
- Use no arbitrary shell from scientific runtime code; development commands in this plan are fixed reviewer commands only.
- Do not merge directly to `main`; finish with one draft pull request from `feat/governed-adaptation-and-harness-evolution`.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `src/super_scientist/application/transactions/contracts.py` | Generic handler protocol and capability types; no storage implementation |
| `src/super_scientist/application/transactions/coordinator.py` | Shared atomic submission, replay, policy, projection, transaction, and audit flow |
| `src/super_scientist/application/transactions/router.py` | Fixed proposal-type-to-handler registry; no dynamic imports |
| `src/super_scientist/application/transactions/governance.py` | Measurement-backed V1-to-V2 policy-transition handler |
| `src/super_scientist/application/kernel_service.py` | Backward-compatible facade over the coordinator |
| `src/super_scientist/config/models.py` | Immutable V1/V2 governance policy union and runtime settings |
| `src/super_scientist/config/loader.py` | Version-specific policy parsing and exact canonical hashing |
| `src/super_scientist/domain/improvement/classification.py` | Stable adaptation classification enums shared by policy and measurements |
| `src/super_scientist/domain/improvement/models.py` | Budgets, trajectories, measurements, assessment provenance, evaluator audits |
| `src/super_scientist/domain/research_runs/models.py` | Research-run definitions and lifecycle events |
| `src/super_scientist/domain/configurations/models.py` | Model/scaffold/prompt/memory/tool/control configuration versions and diffs |
| `src/super_scientist/domain/progress/models.py` | Plans, subtasks, validation events, budgets, checkpoints, completion decisions |
| `src/super_scientist/domain/progress/calculations.py` | Official/provisional progress and dependency invalidation calculations |
| `src/super_scientist/domain/evidence_trails/models.py` | Trail versions, nodes, relations, assessments, and report bindings |
| `src/super_scientist/domain/evidence_trails/validation.py` | Exact-span, structure, ordering, relation, scope, temporal, and modality checks |
| `src/super_scientist/domain/behavioral_rules/models.py` | Incidents, rule versions, assessments, decisions, regression cases, links |
| `src/super_scientist/domain/behavioral_rules/consolidation.py` | Duplicate/conflict classification and canonical candidate-diff construction |
| `src/super_scientist/domain/representations/models.py` | Quarantined primitive versions and old/new-frame evaluations |
| `src/super_scientist/domain/hypotheses/models.py` | Hypotheses, model metadata, verification unions, counterexamples, revisions |
| `src/super_scientist/application/hypothesis_testing/simulators.py` | Fixed allowlisted deterministic simulator registry |
| `src/super_scientist/application/hypothesis_testing/service.py` | Bounded model/checker/revision workflow |
| `src/super_scientist/domain/evaluators/models.py` | Evaluator versions, succession decisions, rollback, and collapse metrics |
| `src/super_scientist/domain/harness_eval/models.py` | Campaigns, partitions, budgets, observations, metrics, confounds, decisions |
| `src/super_scientist/application/harness_eval/capabilities.py` | Candidate, coordinator, evaluator, gateway, decision, and auditor interfaces |
| `src/super_scientist/application/harness_eval/service.py` | Matched-budget campaign and admission logic |
| `src/super_scientist/providers/storage/domain_records.py` | Private append-only engine, fixed model-bound domain repositories, and mutable head projections |
| `src/super_scientist/providers/storage/protected_evaluation.py` | Separate protected database/artifact store and result gateway |
| `src/super_scientist/providers/storage/schema.py` | SQLAlchemy metadata for all released tables |
| `src/super_scientist/application/workspace_integrity.py` | Mixed-policy, append-only-history, projection, artifact, and protected-hash reconciliation |
| `src/super_scientist/handbook/models.py` | Strict behavior manifest and verification report contracts |
| `src/super_scientist/handbook/builder.py` | Deterministic JSON/Markdown handbook generation from manifests and AST facts |
| `src/super_scientist/handbook/verification.py` | Commit/path/symbol/hash/staleness and containment checks |
| `src/super_scientist/cli/adaptation.py` | Research-run, governance, improvement, progress, trail, rule, primitive, hypothesis, model, verifier commands |
| `src/super_scientist/cli/handbook.py` | Handbook build and verify commands |
| `src/super_scientist/cli/harness_eval.py` | Harness campaign create, record, and report commands |
| `src/super_scientist/cli/main.py` | Stable grouped Typer surface and schema-version-1 JSON envelopes |
| `src/super_scientist/quality/imported_pattern_firewall.py` | Digest-pinned neutral source-tree policy checker |
| `src/super_scientist/quality/wheel_smoke.py` | Fixed isolated built-wheel installation and CLI smoke check |
| `quality/imported-pattern-firewall-policy.json` | Schema-validated attribution terms and exact allowed paths |
| `docs/handbook/handbook.json` | Deterministic machine-readable generated handbook |
| `docs/handbook/handbook.md` | Deterministic progressive-disclosure generated handbook |
| `docs/governed-adaptation.md` | Classification, measurement, configuration, and evaluator-succession guide |
| `docs/long-horizon-execution.md` | Progress, budget, checkpoint, and finalization guide |
| `docs/evidence-trails.md` | Source-first trail construction and validation guide |
| `docs/behavioral-rules.md` | Incident, reviewer, consolidation, conflict, and redundancy guide |
| `docs/representational-primitives.md` | Primitive quarantine, versioning, and evaluation guide |
| `docs/hypothesis-model-checker-loop.md` | Safe model metadata, checker categories, revision, and transfer guide |
| `docs/behavior-handbook.md` | Manifest authoring, build, staleness, and reverse-navigation guide |
| `docs/harness-evolution-evaluation.md` | Matched-budget, protected-split, transfer, and collapse guide |
| `alembic/versions/0002_governed_adaptation_foundation.py` | Runs, configurations, measurements, evaluator succession |
| `alembic/versions/0003_progress_and_evidence_trails.py` | Progress, budgets, checkpoints, trails, checks |
| `alembic/versions/0004_behavioral_rules.py` | Incidents, rules, assessments, consolidation, regression, heads |
| `alembic/versions/0005_hypotheses_and_representations.py` | Primitives, hypotheses, models, verification, revisions, admissions |
| `alembic/versions/0006_handbook_and_harness_evaluation.py` | Behavior links, handbook verification, campaigns, results, decisions |
| `examples/governed_adaptation_vertical_slice.py` | Complete offline thermal/equipment-incident workflow |
| `docs/examples/governed-adaptation-vertical-slice.md` | Reproducible example instructions and expected decisions |
| `REPRODUCIBILITY.md` | Determinism, environment, budgets, replay, and limitations |
| `THREAT_MODEL.md` | Trust boundaries, attacker capabilities, abuse cases, mitigations |
| `tests/unit/` | Pure contracts, decisions, calculations, deterministic checkers |
| `tests/property/` | Immutability, graph, replay, dependency, and append-only properties |
| `tests/integration/` | SQLite, migrations, application transactions, CLI, store separation |
| `tests/adversarial/` | Authority, leakage, circularity, tampering, imported-pattern isolation |
| `tests/e2e/test_governed_adaptation_vertical_slice.py` | Complete deterministic 0.2.0 proof |

---

### Task 1: Characterize Compatibility and Extract the Shared Transaction Coordinator

**Files:**
- Create: `src/super_scientist/application/transactions/__init__.py`
- Create: `src/super_scientist/application/transactions/contracts.py`
- Create: `src/super_scientist/application/transactions/coordinator.py`
- Create: `src/super_scientist/application/transactions/router.py`
- Modify: `src/super_scientist/application/kernel_service.py:55`
- Test: `tests/integration/application/test_transaction_coordinator.py`
- Test: `tests/property/test_transaction_replay.py`

**Interfaces:**
- Consumes: existing `Proposal`, `TransactionDecision`, `DatabaseUnitOfWork`, `RepositorySet`, `ArtifactStore`, and audit chain.
- Produces: `ProposalHandler`, `HandlerReadCapability`, `HandlerWriteCapability`, `ProposalRouter.resolve(proposal_type)`, and `TransactionCoordinator.submit()` / `submit_intent()`; `KernelService` keeps its public constructor and methods.

- [ ] **Step 1: Add characterization tests before moving behavior**

```python
@pytest.mark.integration
def test_coordinator_preserves_one_decision_and_audit_event_per_new_attempt(
    runtime: Runtime,
) -> None:
    decision = runtime.service.submit(runtime.add_evidence_proposal("proposal-1", "key-1"))
    assert decision.accepted is True
    assert len(runtime.repositories().transactions.list_all()) == 1
    assert len(runtime.repositories().audit.list_all()) == 1


@pytest.mark.integration
def test_exact_replay_does_not_readmit_or_append(runtime: Runtime) -> None:
    proposal = runtime.add_evidence_proposal("proposal-1", "key-1")
    first = runtime.service.submit(proposal)
    second = runtime.service.submit(proposal)
    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})
    assert len(runtime.repositories().transactions.list_all()) == 1
    assert len(runtime.repositories().audit.list_all()) == 1
```

- [ ] **Step 2: Run the characterization tests**

Run: `python -m pytest tests/integration/application/test_transaction_coordinator.py tests/property/test_transaction_replay.py -v`

Expected: the new file initially fails collection because `TransactionCoordinator` is absent; all pre-existing replay tests pass.

- [ ] **Step 3: Add the fixed handler contracts and coordinator**

```python
class HandlerReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...


class HandlerWriteCapability(Protocol):
    def append_authoritative(self, record: BaseModel) -> None: ...
    def update_projection(self, record: BaseModel) -> None: ...


class ProposalHandler[ProposalT: BaseModel, ContextT: BaseModel](Protocol):
    proposal_type: str

    def build_context(self, proposal: ProposalT, reads: HandlerReadCapability) -> ContextT: ...
    def decide(self, proposal: ProposalT, context: ContextT) -> TransactionDecision: ...
    def project(
        self,
        proposal: ProposalT,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None: ...
```

Implement `ProposalRouter` from an immutable constructor-supplied mapping and reject duplicate keys. Implement `TransactionCoordinator` by moving the current `KernelService.submit`, `submit_intent`, `_submit_locked`, `_audit`, normalization, and exact-replay flow without changing ordering. Register one compatibility handler for the existing three proposal types; it may use the characterized legacy repository view. New handlers added later must receive focused capability objects and never `RepositorySet`.

- [ ] **Step 4: Prove compatibility after extraction**

Run: `python -m pytest tests/unit/admission tests/integration/application/test_kernel_service.py tests/integration/application/test_transaction_coordinator.py tests/property/test_admission_idempotency.py tests/property/test_transaction_replay.py -v`

Expected: PASS with the same decisions, hashes, rows, and audit counts as baseline.

- [ ] **Step 5: Run focused static checks and commit**

Run: `python -m ruff check src/super_scientist/application/transactions src/super_scientist/application/kernel_service.py tests/integration/application/test_transaction_coordinator.py && python -m mypy src`

Expected: both commands exit 0.

```bash
git add src/super_scientist/application/transactions src/super_scientist/application/kernel_service.py tests/integration/application/test_transaction_coordinator.py tests/property/test_transaction_replay.py
git commit -m "refactor: extract shared transaction coordinator"
```

### Task 2: Preserve Governance V1 and Add Exact V2 Policy Contracts

**Files:**
- Modify: `src/super_scientist/config/models.py:13`
- Modify: `src/super_scientist/config/loader.py`
- Create: `src/super_scientist/domain/improvement/__init__.py`
- Create: `src/super_scientist/domain/improvement/classification.py`
- Modify: `src/super_scientist/providers/storage/repositories.py:726`
- Modify: `src/super_scientist/application/workspace_integrity.py`
- Test: `tests/unit/config/test_policy_versions.py`
- Test: `tests/integration/storage/test_policy_versions.py`

**Interfaces:**
- Consumes: exact V1 canonical JSON and policy hash behavior.
- Produces: classification enums, `GovernancePolicyV1`, `GovernancePolicyV2`, `AdaptationRequirement`, discriminated `PolicyDocument`, and version-aware `PolicySnapshot`. Task 4 consumes these contracts to implement measurement-backed transition admission after migration 0002 exists.

- [ ] **Step 1: Write exact V1/V2 hashing and mixed-history failures first**

```python
def test_v1_policy_hash_is_unchanged(v1_policy_json: dict[str, object]) -> None:
    legacy = load_policy_document(v1_policy_json)
    assert isinstance(legacy.policy, GovernancePolicyV1)
    assert legacy.policy_hash == "26269abd13de9d63206eb6fe0465deb5b5ef5f99602a9d4ad89ea710cff3e7d9"


def test_v2_policy_hash_uses_its_exact_payload(v2_policy: GovernancePolicyV2) -> None:
    expected = sha256_hex(canonical_json_bytes(v2_policy.model_dump(mode="json")))
    assert policy_hash(v2_policy) == expected
    assert (
        policy_hash(v2_policy) != "26269abd13de9d63206eb6fe0465deb5b5ef5f99602a9d4ad89ea710cff3e7d9"
    )


def test_policy_repository_decodes_mixed_history(policy_repository: PolicyRepository) -> None:
    policy_repository.add_and_activate(v1_snapshot(), utc_timestamp(1))
    policy_repository.add_and_activate(v2_snapshot(), utc_timestamp(2))
    assert [item.policy.schema_version for item in policy_repository.list_all()] == [1, 2]
    assert policy_repository.get_active() == v2_snapshot()
```

- [ ] **Step 2: Run the policy tests and observe the missing V2 contracts**

Run: `python -m pytest tests/unit/config/test_policy_versions.py tests/integration/storage/test_policy_versions.py -v`

Expected: FAIL because the classification module, `GovernancePolicyV2`, and mixed-version decoder do not exist.

- [ ] **Step 3: Implement exact versioned policy contracts**

```python
class GovernancePolicyV1(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Literal[1] = 1
    required_claim_checks: tuple[StableIdentifier, ...] = Field(min_length=1)
    human_approval_for: frozenset[StableIdentifier] = Field(
        default_factory=lambda: frozenset({"governance_change", "adapter_promotion"})
    )


class AdaptationRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    change_target: ChangeTarget
    persistence: PersistenceScope
    minimum_verification: VerificationLevel
    permitted_grounding: frozenset[ExternalGrounding] = Field(min_length=1)
    required_approver_kind: ActorKind
    protected_evaluation_required: bool
    rollback_required: bool


class GovernancePolicyV2(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Literal[2] = 2
    required_claim_checks: tuple[StableIdentifier, ...] = Field(min_length=1)
    human_approval_for: frozenset[StableIdentifier]
    adaptation_requirements: tuple[AdaptationRequirement, ...] = Field(min_length=1)


PolicyDocument = Annotated[
    GovernancePolicyV1 | GovernancePolicyV2, Field(discriminator="schema_version")
]


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    policy_hash: Sha256Hex
    policy: PolicyDocument
```

Define all exact `ChangeTarget`, `LoopClosure`, `PersistenceScope`, `VerificationLevel`, `ExternalGrounding`, and `ImprovementSignal` enum values from design section 11 in `domain/improvement/classification.py`; Task 4 imports them rather than redefining them. Keep V1 validators and serialization byte-equivalent to the current `GovernancePolicy`. Hash each exact validated version, never an upcast model. Repository and workspace verification select the decoder by stored `schema_version`, reject unknown versions/fields/hash mismatches, and preserve historical V1 hashes. Default `init` continues to create V1; this task does not activate V2 or implement transition admission.

- [ ] **Step 4: Test V1, V2, tampering, unknown versions, and mixed history**

Run: `python -m pytest tests/unit/config tests/integration/storage/test_policy_versions.py tests/integration/application/test_workspace_integrity.py tests/integration/cli/test_kernel_cli.py -v`

Expected: PASS; exact V1 hash/CLI initialization remain unchanged, mixed immutable V1/V2 rows decode and verify, and no operation silently activates V2.

- [ ] **Step 5: Commit the policy boundary**

```bash
git add src/super_scientist/config src/super_scientist/domain/improvement src/super_scientist/providers/storage/repositories.py src/super_scientist/application/workspace_integrity.py tests/unit/config/test_policy_versions.py tests/integration/storage/test_policy_versions.py
git commit -m "feat: add versioned governance policy contracts"
```

### Task 3: Add the Governed Adaptation Foundation Migration

**Files:**
- Create: `alembic/versions/0002_governed_adaptation_foundation.py`
- Modify: `src/super_scientist/providers/storage/schema.py`
- Create: `src/super_scientist/providers/storage/domain_records.py`
- Modify: `src/super_scientist/providers/storage/database.py:13`
- Test: `tests/integration/storage/test_migration_0002.py`
- Test: `tests/property/test_adaptation_append_only.py`

**Interfaces:**
- Consumes: migration `0001_epistemic_kernel` and `DatabaseUnitOfWork`.
- Produces: the eight authoritative storage tables, a private reusable append-only engine, and constrained public run/evaluator head projections. Task 4 adds the eight fixed public model-bound repositories after their strict record classes exist.

- [ ] **Step 1: Write migration shape, foreign-key, and trigger tests**

```python
AUTHORITATIVE_0002_TABLES = {
    "research_runs",
    "research_run_events",
    "configuration_versions",
    "self_improvement_measurements",
    "evaluator_audits",
    "evaluator_versions",
    "evaluator_succession_decisions",
    "evaluator_collapse_records",
}


@pytest.mark.integration
def test_0001_upgrades_to_0002_with_append_only_triggers(database_url: str) -> None:
    upgrade_to(database_url, "0001_epistemic_kernel")
    upgrade_to(database_url, "0002_governed_adaptation_foundation")
    assert AUTHORITATIVE_0002_TABLES <= table_names(database_url)
    for table in AUTHORITATIVE_0002_TABLES:
        assert_update_and_delete_raise_append_only(database_url, table)


@pytest.mark.integration
def test_new_foreign_keys_are_enforced(database_url: str) -> None:
    upgrade_to(database_url, "0002_governed_adaptation_foundation")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        insert_run_event_without_run(database_url)
```

- [ ] **Step 2: Run the new migration tests**

Run: `python -m pytest tests/integration/storage/test_migration_0002.py tests/property/test_adaptation_append_only.py -v`

Expected: FAIL because revision `0002_governed_adaptation_foundation` is absent.

- [ ] **Step 3: Create normalized tables and focused repositories**

Every authoritative table stores its stable primary key, relationship keys needed for SQLite foreign keys, `record_json`, `content_hash`, and `created_at`. Every mutable projection stores only its entity key and immutable version key. Add `PRAGMA foreign_keys=ON` through an SQLAlchemy connection event:

```python
@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection: DBAPIConnection, _: ConnectionRecord) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

Implement the private repository engine with the same strict encode/decode and hash-recompute behavior as existing repositories. It is excluded from `__all__`; no public constructor or factory accepts a table, model, identifier, relationship mapping, SQL fragment, or decoder:

```python
class _AppendOnlyRecordRepository[RecordT: BaseModel]:
    def get(self, record_id: str) -> RecordT | None: ...
    def list_all(self) -> tuple[RecordT, ...]: ...
    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None: ...
```

Task 4 constructs fixed public wrappers in this module, each binding one trusted table, exact strict model type, identifier field, and relationship mapping. Callers can never select a table or model dynamically.

- [ ] **Step 4: Verify clean, upgrade, downgrade, append-only, and foreign-key behavior**

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0002.py tests/property/test_database_append_only.py tests/property/test_adaptation_append_only.py -v`

Expected: PASS from both an empty database and a genuine `0001` database.

- [ ] **Step 5: Commit migration 0002**

```bash
git add alembic/versions/0002_governed_adaptation_foundation.py src/super_scientist/providers/storage/schema.py src/super_scientist/providers/storage/domain_records.py src/super_scientist/providers/storage/database.py tests/integration/storage/test_migration_0002.py tests/property/test_adaptation_append_only.py
git commit -m "feat: add adaptation foundation storage"
```

### Task 4: Implement Research Runs, Configuration Separation, Measurements, and Evaluator Succession

**Files:**
- Modify: `src/super_scientist/domain/improvement/__init__.py`
- Create: `src/super_scientist/domain/improvement/models.py`
- Create: `src/super_scientist/domain/research_runs/__init__.py`
- Create: `src/super_scientist/domain/research_runs/models.py`
- Create: `src/super_scientist/domain/configurations/__init__.py`
- Create: `src/super_scientist/domain/configurations/models.py`
- Create: `src/super_scientist/domain/evaluators/__init__.py`
- Create: `src/super_scientist/domain/evaluators/models.py`
- Create: `src/super_scientist/application/improvement/service.py`
- Create: `src/super_scientist/application/research_runs/service.py`
- Create: `src/super_scientist/application/transactions/adaptation.py`
- Create: `src/super_scientist/application/transactions/governance.py`
- Modify: `src/super_scientist/kernel/transactions/models.py:23`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Modify: `docs/sources/source-register.yaml`
- Modify: `docs/research-inspirations.md`
- Create: `docs/governed-adaptation.md`
- Test: `tests/unit/improvement/test_models.py`
- Test: `tests/unit/evaluators/test_succession.py`
- Test: `tests/unit/docs/test_source_register.py`
- Test: `tests/integration/application/test_adaptation_foundation.py`
- Test: `tests/integration/application/test_governance_transition.py`
- Test: `tests/adversarial/test_adaptation_authority.py`

**Interfaces:**
- Consumes: Task 2 classification/V1/V2 contracts, coordinator handler contracts, 0002 repositories, `ActorIdentity`, and `are_independent`.
- Produces: `ResearchRun`, `ResearchRunEvent`, configuration version/diff models, `AssessmentProvenance`, `EvaluatorAuditRecord`, `SelfImprovementMeasurementRecord`, `EvaluatorVersion`, `EvaluatorSuccessionDecision`; fixed `ResearchRunRepository`, `ResearchRunEventRepository`, `ConfigurationVersionRepository`, `SelfImprovementMeasurementRepository`, `EvaluatorAuditRepository`, `EvaluatorVersionRepository`, `EvaluatorSuccessionRepository`, and `EvaluatorCollapseRepository`; focused adaptation handlers; and measurement-backed `ProposeGovernancePolicyTransition` admission.

- [ ] **Step 1: Write exhaustive measurement, transition, and authority tests**

```python
@pytest.mark.parametrize("target", tuple(ChangeTarget))
@pytest.mark.parametrize("persistence", tuple(PersistenceScope))
def test_every_change_classification_round_trips(
    target: ChangeTarget, persistence: PersistenceScope
) -> None:
    value = ChangeClassification(
        target=target,
        loop_closure=LoopClosure.HUMAN_ON_LOOP,
        persistence=persistence,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.PRIMARY_SOURCE,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )
    assert ChangeClassification.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "operation",
    [
        "adapter_self_promotion",
        "closed_loop_governance",
        "evaluator_threshold_rewrite",
        "automatic_evaluator_replacement",
        "failed_experiment_omission",
        "confidence_as_evidence",
    ],
)
def test_prohibited_adaptive_operations_fail_closed(
    authority_fixture: AuthorityFixture, operation: str
) -> None:
    decision = authority_fixture.attempt(operation)
    assert decision.accepted is False
    assert decision.reasons[0].code in {
        RejectionCode.PERMISSION_DENIED,
        RejectionCode.PROHIBITED_CLOSED_LOOP,
        RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
    }


def test_execution_state_is_not_part_of_persistent_configuration(
    config_fixture: ConfigFixture,
) -> None:
    version = config_fixture.configuration_version()
    assert "execution_state" not in version.model_dump()
    assert ConfigurationDiff.between(version, version).changed_layers == ()


def test_fake_trainer_returns_metadata_without_model_runtime(fake_trainer: FakeTrainer) -> None:
    candidate = fake_trainer.train(valid_training_request())
    assert candidate.dataset_lineage_ids
    assert candidate.artifact_hash
    assert candidate.promoted is False


def test_v2_cannot_authorize_its_own_transition(transition_fixture: TransitionFixture) -> None:
    decision = transition_fixture.submit_without_independent_human()
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_governance_transition_requires_passed_measurement_and_evaluator_audit(
    transition_fixture: TransitionFixture,
) -> None:
    decision = transition_fixture.submit_with_failed_evaluator_audit()
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


def test_s21_through_s29_have_complete_non_reproduction_metadata() -> None:
    text = Path("docs/sources/source-register.yaml").read_text(encoding="utf-8")
    blocks = {
        match.group("id"): match.group("body")
        for match in re.finditer(
            r"(?ms)^  - id: (?P<id>S\d{2})\n(?P<body>.*?)(?=^  - id: S\d{2}\n|\Z)",
            text,
        )
    }
    for identifier in (f"S{number}" for number in range(21, 30)):
        body = blocks[identifier]
        for key in (
            "version_consulted",
            "license",
            "source_proposal",
            "source_evidence",
            "project_adaptation",
            "project_original_synthesis",
            "limitations",
        ):
            assert re.search(rf"(?m)^    {key}:($|\s)", body)
        assert "reproduction_status: not_reproduced" in body
```

- [ ] **Step 2: Run the domain and adversarial tests**

Run: `python -m pytest tests/unit/improvement tests/unit/evaluators tests/unit/docs/test_source_register.py tests/integration/application/test_governance_transition.py tests/adversarial/test_adaptation_authority.py -v`

Expected: FAIL because measurement, evaluator-audit, research-run, configuration, evaluator-succession, transition proposal, and adaptation rejection contracts are absent.

- [ ] **Step 3: Implement strict records and monotonic persistence rules**

Use exact enum members from design section 11. Define `ActorRelationship` with `SAME_ACTOR`, `SHARED_MODEL_CONFIGURATION`, `ORGANIZATIONAL_DEPENDENCY`, `UNKNOWN`, and `INDEPENDENT`, and define `AssessmentOutcome` with `PASSED`, `FAILED`, `INCONCLUSIVE`, and `ABSTAINED`. Define assessment provenance once and embed it by value or stable identifier in every verifier/checker/judge result:

```python
class AssessmentProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    actor: ActorIdentity
    actor_version: StableIdentifier
    category: VerificationLevel
    deterministic_or_learned: Literal["DETERMINISTIC", "LEARNED", "HUMAN"]
    proposer_relationship: ActorRelationship
    assumptions: tuple[NonBlankText, ...]
    evidence_ids: tuple[StableIdentifier, ...]
    checks_run: tuple[StableIdentifier, ...] = Field(min_length=1)
    limitations: tuple[NonBlankText, ...] = Field(min_length=1)
    result: AssessmentOutcome
    meaningful_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    assessed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex
```

Extend `RejectionCode` without renaming or reordering existing values. The new stable values are `MISSING_ENTITY`, `INVALID_LINEAGE`, `INSUFFICIENT_GROUNDING`, `PROHIBITED_CLOSED_LOOP`, `UNMATCHED_BUDGETS`, `PROTECTED_DATA_ACCESS`, `STALE_HANDBOOK_MAPPING`, `INVALID_DEPENDENCY`, `FALSE_FINISH`, `CIRCULAR_EVALUATOR_APPROVAL`, `BENCHMARK_SPECIFIC_ADMISSION`, `DUPLICATE_RULE`, `UNRESOLVED_RULE_CONFLICT`, and `EXPERIMENTAL_PRIMITIVE_QUARANTINED`.

Separate persistent layers with these exact top-level contracts: `FoundationModelConfiguration`, `ScaffoldConfiguration`, `PromptConfiguration`, `MemoryConfiguration`, `ToolConfiguration`, `ControlConfiguration`, `AgentConfiguration`, `ConfigurationVersion`, and `ConfigurationDiff`. `ExecutionState` is a distinct transient record and can only be referenced by checkpoints; it is never serialized inside `ConfigurationVersion`. The slow-loop trainer is a deterministic protocol whose implementation returns only dataset lineage, candidate artifact hash, evaluation reference, and rollback metadata:

```python
class FakeTrainer(Protocol):
    def train(self, request: AdapterTrainingRequest) -> AdapterCandidateMetadata: ...


class AdapterCandidateMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    candidate_id: StableIdentifier
    base_model_configuration_id: StableIdentifier
    dataset_lineage_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    artifact_hash: Sha256Hex
    evaluation_id: StableIdentifier | None
    rollback_configuration_id: StableIdentifier
    promoted: Literal[False] = False
```

No live model or training SDK enters the package. `EvaluatorAuditRecord` must reject an auditor equal or non-independent to evaluator, proposer, or candidate producer. `SelfImprovementMeasurementRecord` requires stable `change_id`, full `m_0..m_T` trajectory, separate budgets, failures, regressions, rollback target, and passed `evaluator_audit_id` for durable persistence. Evaluator promotion requires protected/external results, human review, canary result, predecessor rollback target, and an accepted independent audit; it updates only the evaluator-head projection.

In `providers/storage/domain_records.py`, add the eight named public repositories listed in this task's Interfaces block. Each constructor accepts only an active SQLAlchemy `Connection` and internally fixes its table, exact strict model, identifier field, and relationship mapping before delegating to `_AppendOnlyRecordRepository`. Add them to `__all__`; do not expose the private engine or any generic binding factory.

Implement `ProposeGovernancePolicyTransition` only now that 0002 measurement/audit repositories exist. Its handler runs under the prior active policy and applies the non-configurable constitutional rule: independent human approval, non-closed-loop classification, complete accepted measurement, passed independent evaluator audit, prior/candidate hashes, compatibility validation, and rollback hash. An accepted transition appends the V2 snapshot and updates the active-policy projection atomically; its audit event remains attributed to V1 and records both hashes. Rollback is another governed transition. No V2 field or candidate policy may authorize its own activation.

- [ ] **Step 4: Verify foundation behavior through the coordinator**

Run: `python -m pytest tests/unit/improvement tests/unit/evaluators tests/unit/docs/test_source_register.py tests/integration/application/test_adaptation_foundation.py tests/integration/application/test_governance_transition.py tests/property/test_audit_chain.py tests/adversarial/test_adaptation_authority.py -v`

Expected: PASS; rejected operations and transition attempts are durable and audited, an accepted V1-to-V2 transition preserves mixed history, and unexpected faults roll back.

- [ ] **Step 5: Commit the Phase A domain**

```bash
git add src/super_scientist/domain/improvement src/super_scientist/domain/research_runs src/super_scientist/domain/configurations src/super_scientist/domain/evaluators src/super_scientist/application/improvement src/super_scientist/application/research_runs src/super_scientist/application/transactions/adaptation.py src/super_scientist/application/transactions/governance.py src/super_scientist/kernel/transactions/models.py src/super_scientist/providers/storage/domain_records.py docs/sources/source-register.yaml docs/research-inspirations.md docs/governed-adaptation.md tests/unit/improvement tests/unit/evaluators tests/unit/docs/test_source_register.py tests/integration/application/test_adaptation_foundation.py tests/integration/application/test_governance_transition.py tests/property/test_audit_chain.py tests/adversarial/test_adaptation_authority.py
git commit -m "feat: govern adaptation measurements and evaluator succession"
```

### Task 5: Add Progress and Evidence-trail Storage

**Files:**
- Create: `alembic/versions/0003_progress_and_evidence_trails.py`
- Modify: `src/super_scientist/providers/storage/schema.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Test: `tests/integration/storage/test_migration_0003.py`
- Test: `tests/property/test_progress_trail_append_only.py`

**Interfaces:**
- Consumes: 0002 schema and append-only repository base.
- Produces: normalized append-only storage for progress plans/subtasks/events/budgets/checkpoints/completion decisions and trail versions/nodes/relations/checks/assessments/report bindings, plus fixed progress/trail head repositories. Tasks 6 and 7 add the public strict model-bound record repositories after their domain models exist.

- [ ] **Step 1: Define migration assertions before schema code**

```python
AUTHORITATIVE_0003_TABLES = {
    "progress_plans",
    "progress_subtasks",
    "progress_events",
    "run_budgets",
    "run_checkpoints",
    "completion_decisions",
    "evidence_trail_versions",
    "evidence_trail_nodes",
    "evidence_trail_relations",
    "evidence_trail_checks",
    "evidence_trail_assessments",
    "report_sentence_bindings",
}


def test_0003_rejects_orphan_progress_and_trail_records(database_url: str) -> None:
    upgrade_to(database_url, "0003_progress_and_evidence_trails")
    assert_foreign_key_failure(database_url, "progress_events", "missing-subtask")
    assert_foreign_key_failure(database_url, "evidence_trail_nodes", "missing-trail-version")
```

- [ ] **Step 2: Run migration 0003 tests**

Run: `python -m pytest tests/integration/storage/test_migration_0003.py tests/property/test_progress_trail_append_only.py -v`

Expected: FAIL because revision 0003 and its tables are absent.

- [ ] **Step 3: Implement migration, private storage bindings, and fixed head repositories**

Give each trail node and relation a composite uniqueness constraint on `(trail_version_id, node_id)` or `(trail_version_id, relation_id)`. Store dependency edges in immutable subtask JSON while also materializing `(plan_version_id, subtask_id)` uniqueness. Use append-only triggers on all authoritative tables and constrained mutable heads:

```python
progress_heads = Table(
    "progress_heads",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("plan_version_id", String(160), nullable=False),
    Column("last_event_id", String(160), nullable=False),
)

evidence_trail_heads = Table(
    "evidence_trail_heads",
    metadata,
    Column("trail_id", String(128), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
)
```

Keep the existing generic append-only record engine private. Task 5 may add only fixed public repositories for `progress_heads` and `evidence_trail_heads`; no public constructor or factory accepts a table, model, identifier, relationship mapping, decoder, or SQL fragment. Raw-storage and head tests reject mismatched IDs, invalid content hashes, dangling relationships, and corrupt JSON. Tasks 6 and 7 bind the exact strict domain models and add unknown-field decoder tests through fixed record repositories.

- [ ] **Step 4: Verify 0001-to-0003 and clean-to-0003 paths**

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0002.py tests/integration/storage/test_migration_0003.py tests/property/test_database_append_only.py tests/property/test_progress_trail_append_only.py -v`

Expected: PASS with foreign keys enabled on every connection.

- [ ] **Step 5: Commit migration 0003**

```bash
git add alembic/versions/0003_progress_and_evidence_trails.py src/super_scientist/providers/storage/schema.py src/super_scientist/providers/storage/domain_records.py tests/integration/storage/test_migration_0003.py tests/property/test_progress_trail_append_only.py
git commit -m "feat: add progress and evidence trail storage"
```

### Task 6: Implement Independently Validated Progress, Budgets, Checkpoints, and False-finish Detection

**Files:**
- Create: `src/super_scientist/domain/progress/__init__.py`
- Create: `src/super_scientist/domain/progress/models.py`
- Create: `src/super_scientist/domain/progress/calculations.py`
- Create: `src/super_scientist/application/progress/service.py`
- Create: `src/super_scientist/application/transactions/progress.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Create: `docs/long-horizon-execution.md`
- Test: `tests/unit/progress/test_calculations.py`
- Test: `tests/property/test_progress_dependencies.py`
- Test: `tests/integration/application/test_progress_service.py`
- Test: `tests/adversarial/test_false_finish.py`

**Interfaces:**
- Consumes: research-run repositories, V2 policy, `AssessmentProvenance`, `ActorIdentity`, coordinator contracts, and private 0003 progress storage bindings.
- Produces: `ProgressPlan`, `ProgressSubtask`, `ProgressValidationEvent`, `BudgetAllocation`, `RunCheckpoint`, `CompletionProposal`, `CompletionDecision`, `calculate_progress()`, `detect_false_finish()`, and fixed `ProgressPlanRepository`, `ProgressSubtaskRepository`, `ProgressEventRepository`, `RunBudgetRepository`, `RunCheckpointRepository`, and `CompletionDecisionRepository` wrappers. Each public repository accepts only an active `Connection` and binds its exact table, strict model, identifier, and relationships internally.

- [ ] **Step 1: Write dependency, independence, and non-authority tests**

```python
def test_only_independently_validated_subtasks_count(plan_fixture: PlanFixture) -> None:
    plan_fixture.provisionally_complete("collect")
    plan_fixture.validate("analyze", validator=plan_fixture.independent_validator)
    summary = calculate_progress(plan_fixture.plan, plan_fixture.events)
    assert summary.provisional_weight == Decimal("0.40")
    assert summary.official_weight == Decimal("0.00")


def test_dependency_invalidation_removes_dependent_weight(plan_fixture: PlanFixture) -> None:
    plan_fixture.validate_all()
    plan_fixture.invalidate("collect")
    summary = calculate_progress(plan_fixture.plan, plan_fixture.events)
    assert summary.validated_subtask_ids == ()


def test_progress_cannot_authorize_persistent_change(progress_fixture: ProgressFixture) -> None:
    decision = progress_fixture.try_harness_admission_with_progress_only()
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING
```

- [ ] **Step 2: Run the focused progress tests**

Run: `python -m pytest tests/unit/progress tests/property/test_progress_dependencies.py tests/adversarial/test_false_finish.py -v`

Expected: FAIL because progress contracts and calculations are absent.

- [ ] **Step 3: Implement the progress state machine and ordered finalization checklist**

```python
class ProgressValidationEvent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    event_id: StableIdentifier
    run_id: StableIdentifier
    plan_version_id: StableIdentifier
    subtask_id: StableIdentifier
    requested_status: ProgressStatus
    completion_proposer: ActorIdentity
    validator: ActorIdentity
    validator_version: StableIdentifier
    validator_category: VerificationLevel
    relationship_to_run_creator: ActorRelationship
    relationship_to_completion_proposer: ActorRelationship
    are_independent: bool
    evidence_ids: tuple[StableIdentifier, ...]
    checks_run: tuple[StableIdentifier, ...] = Field(min_length=1)
    limitations: tuple[NonBlankText, ...]
    result: AssessmentOutcome
    occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex
```

Validation rejects a false `are_independent` claim by recomputing identity independence. The final validator must be independent of run creator and completion proposer. `calculate_progress` topologically sorts the plan, rejects cycles, counts only current `VALIDATED` nodes whose dependency closure remains valid, and reports provisional weight separately. `detect_false_finish` evaluates the exact ordered checklist from design section 13.4 and returns a typed finding rather than raising.

- [ ] **Step 4: Verify calculations, transactionality, checkpoints, and failure taxonomy**

Run: `python -m pytest tests/unit/progress tests/property/test_progress_dependencies.py tests/integration/application/test_progress_service.py tests/adversarial/test_false_finish.py -v`

Expected: PASS; `TIMEOUT`, `BUDGET_EXHAUSTED`, `HARNESS_ERROR`, `ENVIRONMENT_ERROR`, and `VALIDATOR_ERROR` remain distinct.

- [ ] **Step 5: Commit progress behavior**

```bash
git add src/super_scientist/domain/progress src/super_scientist/application/progress src/super_scientist/application/transactions/progress.py docs/long-horizon-execution.md tests/unit/progress tests/property/test_progress_dependencies.py tests/integration/application/test_progress_service.py tests/adversarial/test_false_finish.py
git commit -m "feat: add independently validated progress ledger"
```

### Task 7: Implement Versioned Natural Evidence Trails

**Files:**
- Create: `src/super_scientist/domain/evidence_trails/__init__.py`
- Create: `src/super_scientist/domain/evidence_trails/models.py`
- Create: `src/super_scientist/domain/evidence_trails/validation.py`
- Create: `src/super_scientist/application/trails/service.py`
- Create: `src/super_scientist/application/transactions/trails.py`
- Modify: `src/super_scientist/application/evidence_verification.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Create: `docs/evidence-trails.md`
- Test: `tests/unit/evidence_trails/test_validation.py`
- Test: `tests/property/test_evidence_trail_graphs.py`
- Test: `tests/integration/application/test_trail_service.py`

**Interfaces:**
- Consumes: immutable `EvidenceRecord`, artifact verification, atomic claim IDs, assessment provenance, and private 0003 trail storage bindings.
- Produces: `EvidenceTrailVersion`, `EvidenceTrailNode`, `EvidenceTrailRelation`, `TrailCheckResult`, `TrailAssessment`, `ReportSentenceBinding`, `validate_trail()`, and fixed `EvidenceTrailVersionRepository`, `EvidenceTrailNodeRepository`, `EvidenceTrailRelationRepository`, `EvidenceTrailCheckRepository`, `EvidenceTrailAssessmentRepository`, and `ReportSentenceBindingRepository` wrappers. Each public repository accepts only an active `Connection` and binds its exact table, strict model, identifier, and relationships internally.

- [ ] **Step 1: Write source fidelity, contradiction, and causality tests**

```python
def test_modified_source_invalidates_exact_span(trail_fixture: TrailFixture) -> None:
    trail_fixture.replace_source_bytes(b"changed source")
    result = validate_trail(trail_fixture.trail(), trail_fixture.sources())
    assert result.outcome is TrailOutcome.INVALID_TRAIL
    assert "CONTENT_HASH_MISMATCH" in result.finding_codes


def test_temporal_order_does_not_authorize_causality(trail_fixture: TrailFixture) -> None:
    trail = trail_fixture.with_relation(RelationType.CAUSES_CANDIDATE, causal_support=())
    result = validate_trail(trail, trail_fixture.sources())
    assert "CAUSAL_OVERCLAIM" in result.finding_codes


def test_contradictions_are_preserved_in_report_binding(trail_fixture: TrailFixture) -> None:
    binding = trail_fixture.bind_report_sentence(outcome=TrailOutcome.CONFLICTED)
    assert binding.opposing_node_ids
    assert binding.uncertainty
```

- [ ] **Step 2: Run trail tests before adding contracts**

Run: `python -m pytest tests/unit/evidence_trails tests/property/test_evidence_trail_graphs.py -v`

Expected: FAIL because evidence-trail modules are absent.

- [ ] **Step 3: Implement strict graph records and deterministic validation**

```python
class EvidenceTrailRelation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    relation_id: StableIdentifier
    trail_version_id: StableIdentifier
    source_node_id: StableIdentifier
    target_node_id: StableIdentifier
    relation_type: RelationType
    evidence_ids: tuple[StableIdentifier, ...]
    modality: ClaimModality


class TrailValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    trail_version_id: StableIdentifier
    outcome: TrailOutcome
    finding_codes: tuple[StableIdentifier, ...]
    required_node_ids: tuple[StableIdentifier, ...]
    opposing_node_ids: tuple[StableIdentifier, ...]
    assessment_ids: tuple[StableIdentifier, ...]
```

Validate source existence, byte hash, exact spans, structural bounds, node uniqueness, relation endpoints, ordering, scope, temporal assertions, and modality deterministically. Independent assessments cover necessity, groundedness, relation fidelity, counterevidence, contamination, rubric fidelity, and answerability. Never collapse `CONFLICTED`, `INSUFFICIENT`, or `UNANSWERABLE` into success.

- [ ] **Step 4: Verify source-first application flow and immutable revisions**

Run: `python -m pytest tests/unit/evidence_trails tests/property/test_evidence_trail_graphs.py tests/integration/application/test_trail_service.py tests/integration/application/test_workspace_integrity.py -v`

Expected: PASS; editing a trail appends a version and leaves earlier nodes, relations, and checks unchanged.

- [ ] **Step 5: Commit natural evidence trails**

```bash
git add src/super_scientist/domain/evidence_trails src/super_scientist/application/trails src/super_scientist/application/transactions/trails.py src/super_scientist/application/evidence_verification.py docs/evidence-trails.md tests/unit/evidence_trails tests/property/test_evidence_trail_graphs.py tests/integration/application/test_trail_service.py
git commit -m "feat: add source-bound evidence trails"
```

### Task 8: Add Behavioral-rule Storage

**Files:**
- Create: `alembic/versions/0004_behavioral_rules.py`
- Modify: `src/super_scientist/providers/storage/schema.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Test: `tests/integration/storage/test_migration_0004.py`
- Test: `tests/property/test_rule_append_only.py`

**Interfaces:**
- Consumes: 0003 schema and repository base.
- Produces: append-only incident, rule-version, reviewer-assessment, consolidation-decision, and regression-case repositories plus mutable rule heads.

- [ ] **Step 1: Specify rule history and projection storage tests**

```python
AUTHORITATIVE_0004_TABLES = {
    "rule_incidents",
    "behavioral_rule_versions",
    "reviewer_assessments",
    "rule_consolidation_decisions",
    "rule_regression_cases",
}


def test_rule_history_cannot_be_updated_or_deleted(database_url: str) -> None:
    upgrade_to(database_url, "0004_behavioral_rules")
    seed_rule_history(database_url)
    for table in AUTHORITATIVE_0004_TABLES:
        assert_update_and_delete_raise_append_only(database_url, table)


def test_rule_head_must_reference_an_immutable_version(database_url: str) -> None:
    upgrade_to(database_url, "0004_behavioral_rules")
    assert_foreign_key_failure(database_url, "behavioral_rule_heads", "missing-version")
```

- [ ] **Step 2: Run migration 0004 tests**

Run: `python -m pytest tests/integration/storage/test_migration_0004.py tests/property/test_rule_append_only.py -v`

Expected: FAIL because 0004 is absent.

- [ ] **Step 3: Implement normalized tables, triggers, constraints, and decoders**

Use stable IDs and content hashes on all authoritative records. Enforce unique `(rule_id, semantic_version)`, unique assessment IDs, incident existence, and head-to-version references. A consolidation decision references every consumed assessment and incident; regression cases reference incidents rather than copying or replacing them.

```python
behavioral_rule_heads = Table(
    "behavioral_rule_heads",
    metadata,
    Column("rule_id", String(128), primary_key=True),
    Column("rule_version_id", String(192), nullable=False),
    Column("semantic_version", String(32), nullable=False),
    Column("status", String(32), nullable=False),
)
```

- [ ] **Step 4: Verify migration chains and append-only enforcement**

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0004.py tests/property/test_rule_append_only.py -v`

Expected: PASS from clean and genuine 0001 databases.

- [ ] **Step 5: Commit migration 0004**

```bash
git add alembic/versions/0004_behavioral_rules.py src/super_scientist/providers/storage/schema.py src/super_scientist/providers/storage/domain_records.py tests/integration/storage/test_migration_0004.py tests/property/test_rule_append_only.py
git commit -m "feat: add behavioral rule storage"
```

### Task 9: Implement Behavioral-rule Review and Governed Consolidation

**Files:**
- Create: `src/super_scientist/domain/behavioral_rules/__init__.py`
- Create: `src/super_scientist/domain/behavioral_rules/models.py`
- Create: `src/super_scientist/domain/behavioral_rules/consolidation.py`
- Create: `src/super_scientist/application/rules/service.py`
- Create: `src/super_scientist/application/transactions/rules.py`
- Create: `docs/behavioral-rules.md`
- Test: `tests/unit/behavioral_rules/test_consolidation.py`
- Test: `tests/property/test_rule_history.py`
- Test: `tests/integration/application/test_rule_service.py`
- Test: `tests/adversarial/test_reviewer_authority.py`

**Interfaces:**
- Consumes: immutable incidents, assessment provenance, 0004 repositories, V2 policy, and coordinator contracts.
- Produces: `RuleIncident`, `BehavioralRuleVersion`, five-role `ReviewerAssessment`, `ConsolidationProposal`, `RuleConsolidationDecision`, `RuleRegressionCase`, `classify_overlap()`, and `build_candidate_diff()`.

- [ ] **Step 1: Write duplicate, contradiction, recurrence, dissent, and authority tests**

```python
def test_exact_duplicate_cannot_create_second_active_rule(rule_fixture: RuleFixture) -> None:
    decision = rule_fixture.propose_exact_duplicate()
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.DUPLICATE_RULE


def test_contradiction_requires_explicit_boundary_and_both_regressions(
    rule_fixture: RuleFixture,
) -> None:
    proposal = rule_fixture.contradictory_consolidation_without_boundary()
    decision = rule_fixture.submit(proposal)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNRESOLVED_RULE_CONFLICT


def test_reviewers_cannot_write_rule_heads(rule_fixture: RuleFixture) -> None:
    capabilities = rule_fixture.reviewer_capabilities()
    assert not hasattr(capabilities, "rule_heads")
    assert not hasattr(capabilities, "governance")
    assert not hasattr(capabilities, "quality_registry")
```

- [ ] **Step 2: Run rule-governance tests**

Run: `python -m pytest tests/unit/behavioral_rules tests/property/test_rule_history.py tests/adversarial/test_reviewer_authority.py -v`

Expected: FAIL because the rule domain and focused capabilities are absent.

- [ ] **Step 3: Implement strict rule and reviewer contracts**

```python
class ReviewerAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    assessment_id: StableIdentifier
    role: ReviewerRole
    provenance: AssessmentProvenance
    proposal_id: StableIdentifier
    rule_version_ids: tuple[StableIdentifier, ...]
    incident_ids: tuple[StableIdentifier, ...]
    overlap: OverlapClassification | None
    conflict: ConflictClassification | None
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    candidate_statement: NonBlankText | None
    scope: tuple[NonBlankText, ...]
    triggers: tuple[NonBlankText, ...]
    exceptions: tuple[NonBlankText, ...]
    counterexamples: tuple[NonBlankText, ...]
    regression_test_ids: tuple[StableIdentifier, ...]
    recommended_action: RuleAction
    uncertainty: tuple[NonBlankText, ...]
```

`build_candidate_diff()` requires assessment roles `SEMANTIC`, `CONFLICT`, `ABSTRACTION`, `ADVERSARIAL`, and `VERIFICATION`, preserves every finding and dissent, and explains each accepted/rejected recommendation. Exact duplicates reject. Semantic duplicates enter review. Contradictions require the separating variable, explicit precondition/exception boundary, both incidents, and regression cases. Recurrence repairs abstraction, trigger, retrieval, enforcement, or scope without deleting either incident.

- [ ] **Step 4: Verify import idempotency and integrator-only projection changes**

Run: `python -m pytest tests/unit/behavioral_rules tests/property/test_rule_history.py tests/integration/application/test_rule_service.py tests/adversarial/test_reviewer_authority.py -v`

Expected: PASS; changed assessment content under an existing stable key is an audited idempotency conflict.

- [ ] **Step 5: Commit behavioral-rule governance**

```bash
git add src/super_scientist/domain/behavioral_rules src/super_scientist/application/rules src/super_scientist/application/transactions/rules.py docs/behavioral-rules.md tests/unit/behavioral_rules tests/property/test_rule_history.py tests/integration/application/test_rule_service.py tests/adversarial/test_reviewer_authority.py
git commit -m "feat: govern behavioral rule consolidation"
```

### Task 10: Add Hypothesis and Representation Storage

**Files:**
- Create: `alembic/versions/0005_hypotheses_and_representations.py`
- Modify: `src/super_scientist/providers/storage/schema.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Test: `tests/integration/storage/test_migration_0005.py`
- Test: `tests/property/test_hypothesis_primitive_append_only.py`

**Interfaces:**
- Consumes: 0004 schema and repository base.
- Produces: append-only primitive versions/evaluations, hypothesis versions, model specs, verification mechanism specs/results, simulation results, counterexamples, revisions, admissions, and mutable primitive/hypothesis heads.

- [ ] **Step 1: Define migration 0005 invariants**

```python
AUTHORITATIVE_0005_TABLES = {
    "primitive_versions",
    "primitive_evaluations",
    "hypothesis_versions",
    "executable_model_specs",
    "verification_mechanism_specs",
    "verification_results",
    "simulation_results",
    "counterexample_records",
    "hypothesis_revisions",
    "hypothesis_admission_decisions",
}


def test_revision_requires_existing_prior_and_resulting_versions(database_url: str) -> None:
    upgrade_to(database_url, "0005_hypotheses_and_representations")
    assert_foreign_key_failure(database_url, "hypothesis_revisions", "missing-prior")
    assert_foreign_key_failure(database_url, "hypothesis_revisions", "missing-result")
```

- [ ] **Step 2: Run storage tests before migration creation**

Run: `python -m pytest tests/integration/storage/test_migration_0005.py tests/property/test_hypothesis_primitive_append_only.py -v`

Expected: FAIL because 0005 is absent.

- [ ] **Step 3: Implement normalized tables and immutable lineage**

Enforce unique `(primitive_id, semantic_version)` and `(hypothesis_id, version)`. Store discriminator columns for verification mechanism/result categories and execution mode so repository queries cannot confuse formal verifiers, deterministic checkers, and learned judges. Store model artifacts only by content hash and metadata; schema has no source text, import path, entry point, command, URL, or executable field.

```python
hypothesis_heads = Table(
    "hypothesis_heads",
    metadata,
    Column("hypothesis_id", String(128), primary_key=True),
    Column("hypothesis_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("admission_status", String(40), nullable=False),
)
```

- [ ] **Step 4: Verify clean/upgrade paths, foreign keys, and append-only triggers**

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0005.py tests/property/test_hypothesis_primitive_append_only.py -v`

Expected: PASS with no change to 0001.

- [ ] **Step 5: Commit migration 0005**

```bash
git add alembic/versions/0005_hypotheses_and_representations.py src/super_scientist/providers/storage/schema.py src/super_scientist/providers/storage/domain_records.py tests/integration/storage/test_migration_0005.py tests/property/test_hypothesis_primitive_append_only.py
git commit -m "feat: add hypothesis and representation storage"
```

### Task 11: Implement the Quarantined Representational Primitive Registry

**Files:**
- Create: `src/super_scientist/domain/representations/__init__.py`
- Create: `src/super_scientist/domain/representations/models.py`
- Create: `src/super_scientist/application/representations/service.py`
- Create: `src/super_scientist/application/transactions/representations.py`
- Create: `docs/representational-primitives.md`
- Test: `tests/unit/representations/test_registry.py`
- Test: `tests/integration/application/test_representation_service.py`
- Test: `tests/adversarial/test_primitive_circularity.py`

**Interfaces:**
- Consumes: assessment provenance, 0005 repositories, V2 policy, and coordinator contracts.
- Produces: `PrimitiveVersion`, `OldFrameEvaluation`, `NewFrameEvaluation`, `PrimitiveEvaluation`, semantic-version validation, and quarantine/admission handlers.

- [ ] **Step 1: Write semantic-version, quarantine, and circularity tests**

```python
def test_incompatible_meaning_requires_major_version(primitive_fixture: PrimitiveFixture) -> None:
    result = primitive_fixture.evaluate_version_change("1.2.0", "1.3.0", meaning_compatible=False)
    assert result.accepted is False
    assert result.code == "INCOMPATIBLE_MEANING_REQUIRES_MAJOR"


def test_experimental_primitive_cannot_enter_claim_schema(
    primitive_fixture: PrimitiveFixture,
) -> None:
    decision = primitive_fixture.try_claim_schema_admission(PrimitiveStatus.EXPERIMENTAL)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED


def test_primitive_and_its_evaluator_cannot_approve_each_other(
    primitive_fixture: PrimitiveFixture,
) -> None:
    decision = primitive_fixture.submit_circular_evaluation()
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
```

- [ ] **Step 2: Run representation tests**

Run: `python -m pytest tests/unit/representations tests/adversarial/test_primitive_circularity.py -v`

Expected: FAIL because the representation registry is absent.

- [ ] **Step 3: Implement primitive contracts and two-frame evaluation**

```python
class PrimitiveVersion(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    primitive_version_id: StableIdentifier
    primitive_id: StableIdentifier
    semantic_version: SemanticVersion
    transformation_kind: TransformationKind
    definition: NonBlankText
    motivation: NonBlankText
    parent_vocabulary: tuple[StableIdentifier, ...]
    contrasts: tuple[NonBlankText, ...]
    examples: tuple[NonBlankText, ...]
    counterexamples: tuple[NonBlankText, ...]
    expected_uses: tuple[NonBlankText, ...]
    dependencies: tuple[StableIdentifier, ...]
    falsification_tests: tuple[StableIdentifier, ...] = Field(min_length=1)
    ambiguity: tuple[NonBlankText, ...]
    proposer: ActorIdentity
    status: PrimitiveStatus
```

Old-frame evaluation records preserved constraints, established tests, and regressions. New-frame evaluation records novel predictions, independent operationalization, non-circular test construction, and later reuse. Duplicate concepts become `DUPLICATE_SUSPECTED`; experimental concepts remain unavailable to canonical schemas, active evaluators, adapter data, and public conclusions.

- [ ] **Step 4: Verify persistence and policy enforcement**

Run: `python -m pytest tests/unit/representations tests/integration/application/test_representation_service.py tests/adversarial/test_primitive_circularity.py -v`

Expected: PASS; every old version and evaluation remains queryable.

- [ ] **Step 5: Commit the primitive registry**

```bash
git add src/super_scientist/domain/representations src/super_scientist/application/representations src/super_scientist/application/transactions/representations.py docs/representational-primitives.md tests/unit/representations tests/integration/application/test_representation_service.py tests/adversarial/test_primitive_circularity.py
git commit -m "feat: add quarantined representation registry"
```

### Task 12: Implement the Safe Hypothesis, Model, Checker, and Revision Loop

**Files:**
- Create: `src/super_scientist/domain/hypotheses/__init__.py`
- Create: `src/super_scientist/domain/hypotheses/models.py`
- Create: `src/super_scientist/application/hypothesis_testing/__init__.py`
- Create: `src/super_scientist/application/hypothesis_testing/simulators.py`
- Create: `src/super_scientist/application/hypothesis_testing/service.py`
- Create: `src/super_scientist/application/transactions/hypotheses.py`
- Create: `docs/hypothesis-model-checker-loop.md`
- Test: `tests/unit/hypotheses/test_models.py`
- Test: `tests/unit/hypotheses/test_simulators.py`
- Test: `tests/evaluation/test_hypothesis_transfer.py`
- Test: `tests/integration/application/test_hypothesis_service.py`
- Test: `tests/adversarial/test_model_execution_boundary.py`

**Interfaces:**
- Consumes: 0005 repositories, V2 policy, assessment provenance, artifact hashes, and coordinator contracts.
- Produces: `HypothesisSpec`, `ExecutableModelSpec`, `ModelInput`, `ModelOutput`, `SimulationResult`, discriminated `VerificationMechanismSpec` / `VerificationResult`, `CounterexampleRecord`, `RevisionRecord`, `HypothesisAdmissionDecision`, fixed `SimulatorRegistry`, and bounded service operations.

- [ ] **Step 1: Write union precision, immutability, and execution-boundary tests**

```python
@pytest.mark.parametrize(
    "forbidden_field",
    ["import_path", "entry_point", "source_text", "argv", "shell_command", "network_url"],
)
def test_model_spec_rejects_execution_authority(forbidden_field: str) -> None:
    payload = valid_model_spec_payload()
    payload[forbidden_field] = "untrusted"
    with pytest.raises(ValidationError):
        ExecutableModelSpec.model_validate(payload)


def test_learned_judge_cannot_claim_formal_verifier_category() -> None:
    payload = valid_learned_judge_result_payload()
    payload["mechanism_type"] = "FORMAL_VERIFIER"
    with pytest.raises(ValidationError):
        VERIFICATION_RESULT_ADAPTER.validate_python(payload)


def test_revision_preserves_failed_hypothesis(hypothesis_fixture: HypothesisFixture) -> None:
    resulting = hypothesis_fixture.revise_after_failed_check()
    assert hypothesis_fixture.repository.get_version(resulting.prior_version_id) is not None
    assert resulting.changed_predictions
    assert resulting.changed_falsification_conditions


@pytest.mark.parametrize(
    "fixture_id",
    ["thermal-chamber", "exponential-decay", "equipment-incident", "software-maintenance"],
)
def test_generic_loop_transfers_across_independent_fixtures(
    transfer_fixture: TransferFixture,
    fixture_id: str,
) -> None:
    result = transfer_fixture.run(fixture_id)
    assert result.domain_contract_fields == ()
    assert result.imported_code_used is False
    assert result.metrics.correctness is not None
```

- [ ] **Step 2: Run hypothesis and execution-boundary tests**

Run: `python -m pytest tests/unit/hypotheses tests/evaluation/test_hypothesis_transfer.py tests/adversarial/test_model_execution_boundary.py -v`

Expected: FAIL because hypothesis contracts and simulator registry are absent.

- [ ] **Step 3: Implement discriminated verification records and fixed simulators**

```python
class ExecutableModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    model_spec_id: StableIdentifier
    model_type: ModelType
    execution_mode: ExecutionMode
    artifact_hash: Sha256Hex | None
    builtin_simulator_id: StableIdentifier | None
    input_schema_id: StableIdentifier
    output_schema_id: StableIdentifier
    deterministic_seed: int
    max_steps: int = Field(ge=1, le=100_000)
    max_state_bytes: int = Field(ge=1, le=10_000_000)


class SimulatorRegistry:
    def __init__(self) -> None:
        self._simulators: Mapping[str, DeterministicSimulator] = MappingProxyType(
            {
                "thermal-chamber-v1": ThermalChamberSimulator(),
                "exponential-decay-v1": ExponentialDecaySimulator(),
            }
        )

    def resolve(self, simulator_id: str) -> DeterministicSimulator:
        return self._simulators[simulator_id]
```

The two simulators accept strict numeric records, deterministic seeds, explicit bounds, and in-memory state only. `METADATA_ONLY` never executes. Unknown simulator IDs reject. The transfer suite also uses immutable evidence records for a synthetic equipment-incident document and an in-memory file-manifest checker for a simulated software-maintenance task; neither adds execution authority. Matched conditions compare direct deterministic reasoning, ordinary plan-and-execute, retry with checker feedback, and the typed revision loop while retaining correctness, checker accuracy, false admission, diversity, revision utility, unsupported-model, abstention, cost, transfer, and regression metrics separately. Admission requires provenance, assumptions/scope, predictions, falsification conditions, checker results, counterexample search, revision lineage, `TRANSFER_VALIDATED` imported-pattern status, independent review, and active-policy authority.

- [ ] **Step 4: Run transfer fixtures and adversarial execution tests**

Run: `python -m pytest tests/unit/hypotheses tests/evaluation/test_hypothesis_transfer.py tests/integration/application/test_hypothesis_service.py tests/adversarial/test_model_execution_boundary.py -v`

Expected: PASS with no subprocess, dynamic import, filesystem, network, `eval`, or `exec` call path.

- [ ] **Step 5: Commit the governed hypothesis loop**

```bash
git add src/super_scientist/domain/hypotheses src/super_scientist/application/hypothesis_testing src/super_scientist/application/transactions/hypotheses.py docs/hypothesis-model-checker-loop.md tests/unit/hypotheses tests/evaluation/test_hypothesis_transfer.py tests/integration/application/test_hypothesis_service.py tests/adversarial/test_model_execution_boundary.py
git commit -m "feat: add safe hypothesis revision loop"
```

### Task 13: Add Handbook and Harness-evaluation Storage with a Separate Protected Store

**Files:**
- Create: `alembic/versions/0006_handbook_and_harness_evaluation.py`
- Modify: `src/super_scientist/providers/storage/schema.py`
- Modify: `src/super_scientist/providers/storage/domain_records.py`
- Create: `src/super_scientist/providers/storage/protected_evaluation.py`
- Test: `tests/integration/storage/test_migration_0006.py`
- Test: `tests/integration/storage/test_protected_evaluation_store.py`
- Test: `tests/property/test_harness_eval_append_only.py`

**Interfaces:**
- Consumes: 0005 schema, artifact-store containment rules, and repository base.
- Produces: ordinary repositories for behavior links, handbook verification, campaigns, partitions, budgets, candidate observations, metrics, confounds, and decisions; separately produces `ProtectedEvaluationStore`, `ProtectedAnswerReader`, `ProtectedIntegrityAuditor`, evaluator-facing `ProtectedResultValidator`, and coordinator-facing `ProtectedResultGateway`.

- [ ] **Step 1: Write migration and physical-separation tests**

```python
def test_protected_answers_are_not_in_main_database(main_url: str, protected_url: str) -> None:
    upgrade_to(main_url, "0006_handbook_and_harness_evaluation")
    protected = create_protected_store(protected_url)
    protected.add_expected_output("task-1", b"secret-answer")
    assert b"secret-answer" not in Path(sqlite_path(main_url)).read_bytes()
    assert "protected_expected_outputs" not in table_names(main_url)


def test_protected_store_is_absent_from_repository_set(runtime: Runtime) -> None:
    repositories = runtime.repositories()
    assert not hasattr(repositories, "protected")
    assert not hasattr(repositories, "expected_outputs")


def test_result_gateway_schema_cannot_carry_answer_bytes() -> None:
    fields = ProtectedCheckerResult.model_fields
    assert "expected_output" not in fields
    assert "answer_bytes" not in fields
    assert "answer_reference" not in fields
```

- [ ] **Step 2: Run migration and separation tests**

Run: `python -m pytest tests/integration/storage/test_migration_0006.py tests/integration/storage/test_protected_evaluation_store.py tests/property/test_harness_eval_append_only.py -v`

Expected: FAIL because 0006 and the separate store are absent.

- [ ] **Step 3: Implement ordinary 0006 tables and a distinct protected schema**

The main migration creates `behavior_rule_link_versions`, `handbook_verification_records`, `harness_campaigns`, `harness_partition_manifests`, `harness_budgets`, `harness_observations`, `harness_metrics`, `harness_confounds`, and `harness_decisions`, all append only except rebuildable heads. It stores protected content hashes only.

`ProtectedEvaluationStore` creates its own private metadata and engine, rooted under a separately supplied protected path. Its public constructors return only role-specific capabilities:

```python
class ProtectedCheckerResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    result_id: StableIdentifier
    campaign_id: StableIdentifier
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    candidate_output_hash: Sha256Hex
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    outcome: AssessmentOutcome
    metric_values: tuple[MetricValue, ...]
    evaluated_at: UtcTimestamp


class ProtectedResultGateway(Protocol):
    def append_result(self, result: ProtectedCheckerResult) -> None: ...
```

No main-database object owns the protected engine, protected connection, artifact root,
answer reader, or reversible reference. The evaluator-facing result validator is a
spawned capability with no database authority and returns only a strictly validated
`ProtectedCheckerResult`. The result gateway is a coordinator-local adapter over the
caller's supplied active `DatabaseUnitOfWork` connection; it appends through that exact
transaction so campaign creation, result persistence, commit, and rollback remain
atomic and no second SQLite writer is opened. Because that adapter necessarily owns a
main-database connection and repositories, it must not be placed in evaluator or
candidate dependency graphs.

- [ ] **Step 4: Verify migration chain, triggers, object graphs, and store integrity**

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0006.py tests/integration/storage/test_protected_evaluation_store.py tests/property/test_harness_eval_append_only.py -v`

Expected: PASS; protected corruption is detected by the separately privileged auditor
without exposing answer bytes. Real-unit-of-work tests also prove same-transaction
campaign visibility and rollback, shared worker requests are serialized and poison on
protocol loss, concurrent close releases process resources, and expected database or
filesystem failures return fixed non-leaking errors/findings.

- [ ] **Step 5: Commit migration 0006 and protected storage**

```bash
git add alembic/versions/0006_handbook_and_harness_evaluation.py src/super_scientist/providers/storage/schema.py src/super_scientist/providers/storage/domain_records.py src/super_scientist/providers/storage/protected_evaluation.py tests/integration/storage/test_migration_0006.py tests/integration/storage/test_protected_evaluation_store.py tests/property/test_harness_eval_append_only.py
git commit -m "feat: separate protected harness evaluation storage"
```

### Task 14: Build and Verify the Behavior Handbook Deterministically

**Files:**
- Create: `src/super_scientist/handbook/__init__.py`
- Create: `src/super_scientist/handbook/models.py`
- Create: `src/super_scientist/handbook/builder.py`
- Create: `src/super_scientist/handbook/verification.py`
- Create: `docs/handbook/manifest.schema.json`
- Create: `docs/handbook/behaviors.json`
- Create: `docs/handbook/handbook.json`
- Create: `docs/handbook/handbook.md`
- Create: `docs/behavior-handbook.md`
- Test: `tests/unit/handbook/test_builder.py`
- Test: `tests/unit/handbook/test_verification.py`
- Test: `tests/integration/handbook/test_repository_handbook.py`
- Test: `tests/adversarial/test_handbook_paths.py`

**Interfaces:**
- Consumes: human-authored strict manifest, source-controlled behavior-rule links, repository commit, Python AST, and containment utilities.
- Produces: `BehaviorManifest`, `BehaviorEntry`, `SourceBinding`, `HandbookBuildResult`, `HandbookVerificationRecord`, `build_handbook()`, and `verify_handbook()`.

- [ ] **Step 1: Write symbol, hash, staleness, and path-escape tests**

```python
def test_missing_symbol_fails_verification(repository_fixture: RepositoryFixture) -> None:
    manifest = repository_fixture.manifest(symbol="missing_symbol")
    result = verify_handbook(repository_fixture.root, manifest)
    assert result.valid is False
    assert "SYMBOL_NOT_FOUND" in result.finding_codes


def test_source_change_marks_behavior_stale(repository_fixture: RepositoryFixture) -> None:
    built = build_handbook(repository_fixture.root, repository_fixture.manifest())
    repository_fixture.change_bound_source()
    result = verify_handbook(repository_fixture.root, repository_fixture.manifest())
    assert result.valid is False
    assert built.source_tree_hash != result.actual_source_tree_hash


@pytest.mark.parametrize("escape", ["../outside.py", "linked/outside.py"])
def test_manifest_cannot_escape_repository(
    repository_fixture: RepositoryFixture, escape: str
) -> None:
    with pytest.raises(PathContainmentError):
        verify_handbook(repository_fixture.root, repository_fixture.manifest(path=escape))
```

- [ ] **Step 2: Run handbook tests**

Run: `python -m pytest tests/unit/handbook tests/adversarial/test_handbook_paths.py -v`

Expected: FAIL because handbook contracts and deterministic builder are absent.

- [ ] **Step 3: Implement manual-manifest plus AST verification**

```python
class SourceBinding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    repository_commit: Sha256Hex
    relative_path: NonBlankText
    symbol: NonBlankText
    source_hash: Sha256Hex


class BehaviorEntry(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    behavior_id: StableIdentifier
    summary: NonBlankText
    contracts: tuple[NonBlankText, ...]
    dependencies: tuple[StableIdentifier, ...]
    governing_rule_version_ids: tuple[StableIdentifier, ...]
    source_bindings: tuple[SourceBinding, ...] = Field(min_length=1)
```

Resolve every path beneath the declared repository root with symlink/reparse checks. Parse `.py` with `ast.parse`, inventory modules/classes/functions, verify the named symbol and exact source hash, and derive reverse source-to-behavior links. Generate deterministic sorted JSON and Markdown with four disclosure levels: summary; contracts/dependencies/rules; modules/symbols; exact commit/path/hash. Syntax verifies location only and never infers behavioral truth.

- [ ] **Step 4: Build and verify the repository's initial handbook**

Run: `python -m pytest tests/unit/handbook tests/integration/handbook/test_repository_handbook.py tests/adversarial/test_handbook_paths.py -v`

Expected: PASS and two byte-identical builds from unchanged source.

- [ ] **Step 5: Commit handbook generation**

```bash
git add src/super_scientist/handbook docs/handbook docs/behavior-handbook.md tests/unit/handbook tests/integration/handbook tests/adversarial/test_handbook_paths.py
git commit -m "feat: add verified behavior handbook"
```

### Task 15: Implement Matched-budget Harness Evaluation and Collapse Monitoring

**Files:**
- Create: `src/super_scientist/domain/harness_eval/__init__.py`
- Create: `src/super_scientist/domain/harness_eval/models.py`
- Create: `src/super_scientist/application/harness_eval/__init__.py`
- Create: `src/super_scientist/application/harness_eval/capabilities.py`
- Create: `src/super_scientist/application/harness_eval/service.py`
- Create: `src/super_scientist/application/transactions/harness_eval.py`
- Modify: `src/super_scientist/domain/evaluators/models.py`
- Create: `docs/harness-evolution-evaluation.md`
- Test: `tests/unit/harness_eval/test_campaigns.py`
- Test: `tests/unit/evaluators/test_collapse.py`
- Test: `tests/integration/application/test_harness_eval_service.py`
- Test: `tests/adversarial/test_protected_holdout_leakage.py`

**Interfaces:**
- Consumes: main 0006 repositories, protected role capabilities, V2 policy, evaluator audit, measurement, and coordinator contracts.
- Produces: campaign/budget/partition/result/confound/decision contracts, `PublicTaskInputReader`, `CampaignCoordinatorCapability`, `EvaluatorExecutorCapability`, `DecisionAuthorityCapability`, `compare_budgets()`, `decide_campaign()`, and multidimensional collapse reports.

- [ ] **Step 1: Write fairness, transfer, leakage, and collapse tests**

```python
def test_unmatched_budgets_are_incomparable(campaign_fixture: CampaignFixture) -> None:
    result = compare_budgets(
        campaign_fixture.baseline_budget, campaign_fixture.extra_attempt_budget
    )
    assert result.comparable is False
    assert result.mismatches == ("attempts",)


def test_discovery_gain_without_transfer_is_benchmark_specific(
    campaign_fixture: CampaignFixture,
) -> None:
    decision = decide_campaign(campaign_fixture.discovery_gain_transfer_failure())
    assert decision.status is HarnessDecisionStatus.BENCHMARK_SPECIFIC
    assert decision.admitted is False


def test_candidate_graph_contains_no_protected_capability(
    campaign_fixture: CampaignFixture,
) -> None:
    graph_types = campaign_fixture.walk_candidate_object_graph_types()
    assert ProtectedAnswerReader not in graph_types
    assert ProtectedEvaluationStore not in graph_types


def test_no_aggregate_collapse_score_can_promote(evaluator_fixture: EvaluatorFixture) -> None:
    report = evaluator_fixture.high_aggregate_with_catastrophic_regression()
    assert report.catastrophic_regression is True
    assert evaluator_fixture.try_promote_from_report_only(report).accepted is False
```

- [ ] **Step 2: Run harness and leakage tests**

Run: `python -m pytest tests/unit/harness_eval tests/unit/evaluators/test_collapse.py tests/adversarial/test_protected_holdout_leakage.py -v`

Expected: FAIL because campaign contracts and capability boundaries are absent.

- [ ] **Step 3: Implement exact variants, partitions, budgets, and capabilities**

```python
class EvaluationBudget(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    model_id: StableIdentifier
    model_version: StableIdentifier
    adapter_id: StableIdentifier | None
    feedback_mode: FeedbackMode
    tool_ids: tuple[StableIdentifier, ...]
    attempts: int = Field(ge=1)
    token_limit: int = Field(ge=1)
    reasoning_limit: int = Field(ge=1)
    evaluator_call_limit: int = Field(ge=1)
    wall_clock_seconds: Decimal = Field(gt=0)
    cost_limit: Decimal = Field(ge=0)
    human_intervention_limit: int = Field(ge=0)


class PublicTaskInputReader(Protocol):
    def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput: ...


class EvaluatorExecutorCapability(Protocol):
    def evaluate(
        self,
        campaign_id: str,
        task_id: str,
        candidate_output: bytes,
        checker: FixedCheckerConfiguration,
    ) -> ProtectedCheckerResult: ...
```

Partition membership is immutable and exclusive within a campaign version. Candidate code is executed before, and outside, the protected evaluator; the evaluator receives only output bytes and cannot invoke candidate code. Reports preserve all iterations, negatives, confounds, evaluator changes, budget mismatches, rollback, and discovery/validation/transfer/regression/safety metrics separately. Collapse records preserve every dimension from design section 18 with no promotion-producing aggregate.

- [ ] **Step 4: Exercise serialization, exceptions, logs, audit, exports, and indirect references**

Run: `python -m pytest tests/unit/harness_eval tests/unit/evaluators/test_collapse.py tests/integration/application/test_harness_eval_service.py tests/adversarial/test_protected_holdout_leakage.py -v`

Expected: PASS; the literal protected fixture answer is absent from captured logs, errors, main database bytes, audit JSON, campaign export, and candidate object graphs.

- [ ] **Step 5: Commit fair harness evaluation**

```bash
git add src/super_scientist/domain/harness_eval src/super_scientist/domain/evaluators/models.py src/super_scientist/application/harness_eval src/super_scientist/application/transactions/harness_eval.py docs/harness-evolution-evaluation.md tests/unit/harness_eval tests/unit/evaluators/test_collapse.py tests/integration/application/test_harness_eval_service.py tests/adversarial/test_protected_holdout_leakage.py
git commit -m "feat: add fair protected harness evaluation"
```

### Task 16: Add the Stable Grouped CLI Without Expanding Runtime Authority

**Files:**
- Create: `src/super_scientist/cli/adaptation.py`
- Create: `src/super_scientist/cli/handbook.py`
- Create: `src/super_scientist/cli/harness_eval.py`
- Modify: `src/super_scientist/cli/main.py:15`
- Modify: `src/super_scientist/cli/kernel.py:109`
- Test: `tests/integration/cli/test_adaptation_cli.py`
- Test: `tests/integration/cli/test_handbook_cli.py`
- Test: `tests/integration/cli/test_harness_eval_cli.py`
- Test: `tests/property/test_cli_json_envelopes.py`

**Interfaces:**
- Consumes: application services from Tasks 2–15, strict UTF-8 JSON loader, existing `emit()`, and existing exit-code conventions.
- Produces: every grouped command in design section 22; existing commands and schema-version-1 JSON envelopes remain unchanged.

- [ ] **Step 1: Write CLI surface and envelope tests**

```python
@pytest.mark.parametrize(
    "args",
    [
        ["research-run", "create"],
        ["governance", "propose"],
        ["improvement", "classify"],
        ["progress", "add"],
        ["trail", "create"],
        ["rule", "propose"],
        ["primitive", "propose"],
        ["hypothesis", "propose"],
        ["model", "register"],
        ["verifier", "record"],
        ["handbook", "verify"],
        ["harness-eval", "create"],
    ],
)
def test_grouped_commands_are_registered(cli_runner: CliRunner, args: list[str]) -> None:
    result = cli_runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0


def test_malformed_json_uses_stable_error_envelope(cli_runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"{")
    result = cli_runner.invoke(app, ["research-run", "create", "--input", str(path), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["schema_version"] == 1
```

- [ ] **Step 2: Run CLI tests before command registration**

Run: `python -m pytest tests/integration/cli/test_adaptation_cli.py tests/integration/cli/test_handbook_cli.py tests/integration/cli/test_harness_eval_cli.py -v`

Expected: FAIL because the new groups are not registered.

- [ ] **Step 3: Implement strict `--input` commands and safe runtime construction**

Register the exact commands from design section 22. Every mutating command loads a strict UTF-8 JSON object, constructs a trusted `ProposalAttempt` before its proposal factory, submits through the shared coordinator, and disposes engines on every path. Read commands accept only stable IDs. Handbook commands enforce repository/output containment. No option accepts module names, entry points, commands, providers, executables, URLs, or Python source.

```python
research_run_app = typer.Typer(no_args_is_help=True)
governance_app = typer.Typer(no_args_is_help=True)
improvement_app = typer.Typer(no_args_is_help=True)
progress_app = typer.Typer(no_args_is_help=True)
trail_app = typer.Typer(no_args_is_help=True)
rule_app = typer.Typer(no_args_is_help=True)
primitive_app = typer.Typer(no_args_is_help=True)
hypothesis_app = typer.Typer(no_args_is_help=True)
model_app = typer.Typer(no_args_is_help=True)
verifier_app = typer.Typer(no_args_is_help=True)
handbook_app = typer.Typer(no_args_is_help=True)
harness_eval_app = typer.Typer(no_args_is_help=True)
```

- [ ] **Step 4: Verify old and new CLI behavior together**

Run: `python -m pytest tests/integration/cli tests/property/test_cli_json_envelopes.py tests/e2e/test_kernel_vertical_slice_example.py -v`

Expected: PASS; existing human/JSON output and exit statuses 0, 2, 3, and 4 remain unchanged.

- [ ] **Step 5: Commit the grouped CLI**

```bash
git add src/super_scientist/cli tests/integration/cli tests/property/test_cli_json_envelopes.py
git commit -m "feat: expose governed adaptation cli"
```

### Task 17: Complete Attribution, Documentation, Versioning, and the Offline Vertical Slice

**Files:**
- Modify: `docs/sources/source-register.yaml`
- Modify: `docs/research-inspirations.md`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `GOVERNANCE.md`
- Modify: `SECURITY.md`
- Modify: `CLAIM_LEDGER.md`
- Modify: `docs/handbook/handbook.json`
- Modify: `docs/handbook/handbook.md`
- Create: `REPRODUCIBILITY.md`
- Create: `THREAT_MODEL.md`
- Create: `examples/governed_adaptation_vertical_slice.py`
- Create: `docs/examples/governed-adaptation-vertical-slice.md`
- Create: `src/super_scientist/application/workspace_exchange.py`
- Modify: `src/super_scientist/__init__.py`
- Modify: `pyproject.toml:7`
- Modify: `tests/unit/docs/test_source_register.py`
- Create: `tests/e2e/test_governed_adaptation_vertical_slice.py`
- Create: `tests/integration/application/test_workspace_exchange.py`
- Modify: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: all implemented domains, fixed thermal/decay simulators, synthetic SSOH incident sources, CLI, and source metadata S21–S29.
- Produces: accurate attribution, complete operational/security documentation, protected-safe deterministic workspace export/import, package version `0.2.0`, and the deterministic 21-step example from design section 28.

- [ ] **Step 1: Write source-register, version, and end-to-end assertions**

```python
def test_s21_through_s29_have_complete_non_reproduction_metadata() -> None:
    text = Path("docs/sources/source-register.yaml").read_text(encoding="utf-8")
    blocks = {
        match.group("id"): match.group("body")
        for match in re.finditer(
            r"(?ms)^  - id: (?P<id>S\d{2})\n(?P<body>.*?)(?=^  - id: S\d{2}\n|\Z)",
            text,
        )
    }
    for identifier in (f"S{number}" for number in range(21, 30)):
        body = blocks[identifier]
        for key in (
            "version_consulted",
            "license",
            "source_proposal",
            "source_evidence",
            "project_adaptation",
            "project_original_synthesis",
            "limitations",
        ):
            assert re.search(rf"(?m)^    {key}:($|\s)", body)
        assert "reproduction_status: not_reproduced" in body


@pytest.mark.e2e
def test_governed_adaptation_vertical_slice(example_workspace: Path) -> None:
    result = run_example(example_workspace)
    assert result.policy_versions == (1, 2)
    assert result.false_finish_rejected is True
    assert result.failed_hypothesis_preserved is True
    assert result.first_harness_candidate_status == "BENCHMARK_SPECIFIC"
    assert result.second_harness_candidate_status == "ADMITTED"
    assert result.audit_valid is True


def test_workspace_export_import_round_trip(exchange_fixture: ExchangeFixture) -> None:
    exported = exchange_fixture.export_source_workspace()
    imported = exchange_fixture.import_into_empty_workspace(exported)
    assert imported.conflicts == ()
    assert exchange_fixture.export_imported_workspace() == exported
    assert "protected_expected_output" not in exported.model_dump_json()


def test_changed_import_under_existing_identity_is_audited_conflict(
    exchange_fixture: ExchangeFixture,
) -> None:
    exported = exchange_fixture.export_source_workspace()
    changed = exchange_fixture.change_record_without_changing_identity(exported)
    result = exchange_fixture.import_into_empty_workspace(changed)
    assert result.conflicts
    assert exchange_fixture.last_audit_decision().accepted is False
```

- [ ] **Step 2: Run docs and end-to-end tests before the example exists**

Run: `python -m pytest tests/unit/docs/test_source_register.py tests/unit/test_package.py tests/integration/application/test_workspace_exchange.py tests/e2e/test_governed_adaptation_vertical_slice.py -v`

Expected: FAIL because source entries, version 0.2.0, and the example are incomplete.

- [ ] **Step 3: Add precise sources, limitations, documentation, and example**

Record these exact source boundaries: S21 arXiv v1 CC BY 4.0; S22 arXiv v1 plus MIT companion repository at commit `06a48f9beddeb0ff711a3f63be857e3e95709923`; S23 arXiv v2 CC BY 4.0; S24 v1 default arXiv license; S25 v1 default arXiv license; S26 v1 CC BY 4.0 plus unlicensed repository commit `ffd1ba1c2c3e31099264f630b9ed44aec63a86a7` with no code reuse; S27 v1 default arXiv license; S28 v1 CC BY 4.0; S29 public GitHub Pages repository commit `d907a3c18ac97fe6bf7b0bbe43ba938acb023b72` with no license, no peer-reviewed paper, and self-reported public results that are not independently verified.

Implement `WorkspaceExport` as a strict canonical bundle of authoritative records, rebuildable projection expectations, and content-addressed artifact references. It never contains protected answers, protected-store references, live paths, or executable configuration. `export_workspace()` sorts records by stable identity. `import_workspace()` verifies every hash and schema then submits records through stable coordinator intents; identical content replays, while changed content under an existing identity produces an audited conflict.

The example performs all 21 ordered steps in design section 28 using synthetic thermal-chamber records and equipment incidents. It uses no API, model, network, GPU, training, imported code, or arbitrary shell. Rebuild `docs/handbook/handbook.json` and `docs/handbook/handbook.md` after all source changes so their commit/path/symbol hashes are current. Update `pyproject.toml` and `src/super_scientist/__init__.py` to exactly `0.2.0` only after the E2E flow passes.

- [ ] **Step 4: Run documentation, package, old E2E, and new E2E suites**

Run: `python -m pytest tests/unit/docs tests/unit/test_package.py tests/integration/application/test_workspace_exchange.py tests/e2e -v`

Expected: PASS; existing kernel examples remain byte-for-byte compatible where asserted.

- [ ] **Step 5: Commit docs and deterministic proof**

```bash
git add docs/sources/source-register.yaml docs/research-inspirations.md docs/handbook/handbook.json docs/handbook/handbook.md README.md ARCHITECTURE.md GOVERNANCE.md SECURITY.md CLAIM_LEDGER.md REPRODUCIBILITY.md THREAT_MODEL.md examples/governed_adaptation_vertical_slice.py docs/examples/governed-adaptation-vertical-slice.md src/super_scientist/application/workspace_exchange.py src/super_scientist/__init__.py pyproject.toml tests/unit/docs tests/unit/test_package.py tests/integration/application/test_workspace_exchange.py tests/e2e/test_governed_adaptation_vertical_slice.py
git commit -m "docs: prove governed adaptation vertical slice"
```

### Task 18: Govern the Additive Quality Check, Run Independent Reviews, and Open One Draft PR

**Files:**
- Create: `quality/imported-pattern-firewall-policy.json`
- Create: `src/super_scientist/quality/imported_pattern_firewall.py`
- Create: `src/super_scientist/quality/wheel_smoke.py`
- Modify: `src/super_scientist/quality/runner.py:34`
- Modify: `src/super_scientist/application/workspace_integrity.py`
- Create: `tests/unit/quality/test_imported_pattern_firewall.py`
- Create: `tests/unit/quality/test_wheel_smoke.py`
- Modify: `tests/unit/quality/test_runner.py`
- Create: `tests/adversarial/test_imported_pattern_tampering.py`
- Create: `docs/reviews/0.2.0-spec-compliance.md`
- Create: `docs/reviews/0.2.0-code-quality.md`
- Create: `docs/reviews/0.2.0-quality-gate.md`

**Interfaces:**
- Consumes: prior immutable eight-check registry, approved design, all tests, governed `QualityPolicyProposal`, build artifacts, and source-attribution policy.
- Produces: neutral imported-pattern firewall, approved additive `wheel-install` check, complete verification evidence, separate review reports, and one draft pull request.

- [ ] **Step 1: Write firewall tamper and fixed wheel-check tests**

```python
@pytest.mark.parametrize(
    "mutation",
    ["remove_term", "broaden_allowed_path", "modify_policy", "mismatch_digest"],
)
def test_imported_pattern_policy_tampering_fails(
    firewall_fixture: FirewallFixture, mutation: str
) -> None:
    result = firewall_fixture.run_after(mutation)
    assert result.passed is False
    assert result.findings


def test_quality_registry_adds_only_fixed_wheel_install_check() -> None:
    assert tuple(check.name for check in CHECKS[:8]) == (
        "format",
        "lint",
        "types",
        "tests",
        "security",
        "dependencies",
        "build",
        "package",
    )
    assert CHECKS[8].name == "wheel-install"
    assert "--skip" not in CHECKS[8].argv


def test_wheel_smoke_uses_built_distribution_and_fixed_cli_command() -> None:
    plan = build_wheel_smoke_plan((Path("dist/package-0.2.0-py3-none-any.whl"),))
    assert plan.smoke_argv[-3:] == ("scientist-harness", "--help", "--json")
```

- [ ] **Step 2: Demonstrate the unchanged old gate before modifying `CHECKS`**

Run: `scientist-harness quality-gate --json`

Expected: all existing eight checks pass using the prior registry. Save exact command, environment, return codes, and output hashes in `docs/reviews/0.2.0-quality-gate.md`.

- [ ] **Step 3: Implement the governed firewall and additive check**

The plaintext deny terms exist only in `quality/imported-pattern-firewall-policy.json`, whose strict schema requires a policy version, sorted unique term list, and exact allowed attribution paths. The executable module contains the reviewed SHA-256 digest of the whole policy file and exact allowed paths, validates the digest before parsing, then scans source/config/fixtures/examples/commands/dependencies for denied terms and domain assumptions. Its policy digest and path allowlist enter the quality-policy hash.

Submit a `QualityPolicyProposal` recording prior registry hash, proposed registry hash, source diff hash, measurement ID, rationale, regression tests, independent human approval, and rollback commit. Runtime code records but cannot apply source edits. After approval, append exactly one fixed `wheel-install` `QualityCheck`. `wheel_smoke.py` creates an isolated temporary environment, installs only the built wheel through fixed argv, runs the fixed CLI smoke command, reports results, and deletes only the verified temporary directory.

Pause after the old eight-check evidence and proposal payload are complete. Present the exact prior/proposed hashes, source diff, measurement, regression tests, and rollback commit to the user and obtain explicit human approval of that `QualityPolicyProposal` before editing `CHECKS`. Written-spec approval permits proposing this change but is not reused as approval of an unseen quality-policy payload.

- [ ] **Step 4: Run targeted tamper tests and the complete new gate**

Run: `python -m pytest tests/unit/quality tests/adversarial/test_imported_pattern_tampering.py -v`

Expected: PASS, including all four tamper mutations and unchanged first-eight ordering.

Run: `scientist-harness quality-gate --json`

Expected: exit 0; Ruff format/check, strict mypy, pytest with branch coverage at least 90%, Bandit, pip-audit, build, Twine, and wheel installation all pass.

- [ ] **Step 5: Run whole-workspace and migration verification**

Run: `python -m pytest -v`

Expected: all tests pass with only documented environment-dependent skips.

Run: `$verificationRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ssoh-0.2-" + [guid]::NewGuid()); python examples/governed_adaptation_vertical_slice.py --root $verificationRoot; scientist-harness audit verify --root $verificationRoot --json`

Expected: exit 0 with a valid transaction/audit chain, exact mixed V1/V2 policy history, reconciled projections, and valid content-addressed artifacts.

Run: `python -m pytest tests/integration/storage/test_migrations.py tests/integration/storage/test_migration_0002.py tests/integration/storage/test_migration_0003.py tests/integration/storage/test_migration_0004.py tests/integration/storage/test_migration_0005.py tests/integration/storage/test_migration_0006.py -v`

Expected: PASS from clean and genuine 0001 fixtures.

- [ ] **Step 6: Obtain separate fresh-context reviews**

Dispatch one reviewer against the approved specification and one different reviewer against code quality/security. Both reviewers read the full diff and return findings without editing. Record every finding, disposition, evidence, and rerun in:

```text
docs/reviews/0.2.0-spec-compliance.md
docs/reviews/0.2.0-code-quality.md
```

Expected spec-review result: no unresolved requirement gaps. Expected code-review result: no unresolved correctness, security, migration, trust-boundary, or compatibility findings. Fix findings with red-green TDD and repeat the affected review until both are clean.

- [ ] **Step 7: Commit verification evidence**

```bash
git add quality/imported-pattern-firewall-policy.json src/super_scientist/quality src/super_scientist/application/workspace_integrity.py tests/unit/quality tests/adversarial/test_imported_pattern_tampering.py docs/reviews
git commit -m "build: govern the complete release quality gate"
```

- [ ] **Step 8: Inspect the final branch and open one draft PR**

Run: `git status --short`

Expected: no output.

Run: `git log --oneline main..HEAD`

Expected: focused commits corresponding to Tasks 1–18, with no merge commit and no direct commit to `main`.

Run: `git diff --check main...HEAD`

Expected: exit 0 with no output.

Use the `github:yeet` skill to push `feat/governed-adaptation-and-harness-evolution` and open one draft pull request. The PR body must include architecture, migrations, exact test commands/results, S21–S29 attribution, security analysis, protected-data boundary, compatibility evidence, known limitations, deferred work, rollback, and evidence for every acceptance criterion. Do not merge the PR.

---

## Execution Order and Review Gates

1. Execute Tasks 1–4 and run the Phase A focused suite plus an independent review.
2. Execute Tasks 5–7 and run the Phase B focused suite plus an independent review.
3. Execute Tasks 8–9 and run the Phase C focused suite plus independent specification and code-quality reviews.
4. Execute Tasks 10–12 and run the Phase D focused suite plus imported-pattern isolation review.
5. Execute Tasks 13–15 and run the Phase E focused suite plus protected-data and stale-handbook adversarial review.
6. Execute Tasks 16–18 and run the Phase F compatibility suite, prior gate, new full gate, complete audit verification, separate final reviews, and draft-PR handoff.

No phase may start with unresolved blocking or high-severity findings from the prior phase. A failed test or unexpected result triggers `superpowers:systematic-debugging`; do not patch before identifying root cause. Before any completion claim, use `repo-quality-gate` and `superpowers:verification-before-completion` and report exact observed outputs.

## Spec Coverage Matrix

| Approved design sections | Implementing tasks |
| --- | --- |
| 1–7 purpose, baseline, goals, non-goals, S21–S29 provenance, alternatives, architecture | Tasks 1, 4, 17 |
| 8 shared transaction and policy evolution | Tasks 1–2 |
| 9 record authority and external artifacts | Tasks 3, 5, 8, 10, 13, 17 |
| 10 research runs | Tasks 3–4, 16–17 |
| 11 classifications, measurements, evaluator audits, prohibited operations | Tasks 2–4, 15, 18 |
| 12 model/scaffold/execution-state separation and fake adapter training | Task 4 |
| 13 progress, telemetry, budgets, checkpoints, false finishes | Tasks 5–6, 16–17 |
| 14 natural evidence trails | Tasks 5, 7, 16–17 |
| 15 behavioral-rule incidents, reviewers, integrator, contradiction, redundancy | Tasks 8–9, 16–17 |
| 16 representational primitives | Tasks 10–11, 16–17 |
| 17 hypothesis/model/checker/revision and imported-pattern lifecycle | Tasks 10, 12, 16–18 |
| 18 evaluator succession and collapse | Tasks 3–4, 15, 17 |
| 19 handbook and reverse source navigation | Tasks 13–14, 16–18 |
| 20 matched-budget campaigns and protected holdouts | Tasks 13, 15–18 |
| 21 migrations 0002–0006, append-only history, projections, import/export | Tasks 3, 5, 8, 10, 13, 17–18 |
| 22 CLI | Task 16 |
| 23 security design | Every task; adversarial proof in Tasks 4, 6–7, 9, 11–15, 18 |
| 24 errors and idempotent structured imports | Tasks 1, 4, 6–7, 9, 11–17 |
| 25 quality gate and governed additive wheel check | Task 18 |
| 26 test strategy and imported-pattern firewall | Every task; final proof in Task 18 |
| 27 four deterministic transfer cases and matched conditions | Task 12 |
| 28 21-step offline demonstration | Task 17 |
| 29 documentation | Tasks 4, 6–7, 9, 11–12, 14–15, 17–18 |
| 30 phases and independent reviews | Execution Order plus Task 18 |
| 31 acceptance | Tasks 17–18 |
| 32 approved design decision and planning gate | This plan; implementation begins only after execution-mode selection |
