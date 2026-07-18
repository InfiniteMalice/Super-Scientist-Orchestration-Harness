from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    MetricObservation,
    PerformanceTrajectoryPoint,
    ResourceBudget,
    ResourceUsage,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.research_runs.models import ResearchRun, RunBudgetAllocation
from super_scientist.kernel.transactions.models import (
    Approval,
    ProposeGovernancePolicyTransition,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    EvaluatorAuditRepository,
    ResearchRunRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.repositories import TransactionRepository

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 7, 18, 12, 0, 1, tzinfo=UTC)


def test_v2_cannot_authorize_its_own_transition_without_independent_human(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "self-authorized").model_copy(
        update={"approval": None}
    )

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


def test_governance_transition_requires_passed_independent_evaluator_audit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "failed-audit")
    failed_audit = proposal.evaluator_audit.model_copy(update={"result": AssessmentOutcome.FAILED})
    proposal = proposal.model_copy(
        update={
            "evaluator_audit": failed_audit,
            "measurement": proposal.measurement.model_copy(
                update={"evaluator_audit_id": failed_audit.evaluator_audit_id}
            ),
        }
    )

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


@pytest.mark.integration
def test_measurement_backed_v1_to_v2_transition_is_atomic_and_attributed_to_v1(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "accepted")

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is True
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).get(proposal.research_run.run_id) == (
            proposal.research_run
        )
        assert (
            EvaluatorAuditRepository(connection).get(proposal.evaluator_audit.evaluator_audit_id)
            == proposal.evaluator_audit
        )
        assert (
            SelfImprovementMeasurementRepository(connection).get(
                proposal.measurement.measurement_id
            )
            == proposal.measurement
        )
        assert tuple(
            snapshot.policy.schema_version for snapshot in repositories.policies.list_all()
        ) == (
            1,
            2,
        )
        assert repositories.policies.get_active() == runtime.candidate
        assert len(repositories.transactions.list_all()) == 1
        event = repositories.audit.list_all()[0]
        assert event.payload["policy_hash"] == runtime.prior.policy_hash
        assert event.payload["prior_policy_hash"] == runtime.prior.policy_hash
        assert event.payload["candidate_policy_hash"] == runtime.candidate.policy_hash
        assert event.payload["rollback_policy_hash"] == runtime.prior.policy_hash
    runtime.engine.dispose()


@pytest.mark.integration
def test_unexpected_fault_after_transition_projection_rolls_back_every_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "fault")

    def fail_transaction_append(
        self: TransactionRepository, *args: object, **kwargs: object
    ) -> None:
        del self, args, kwargs
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(TransactionRepository, "add", fail_transaction_append)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        runtime.coordinator.submit(proposal)

    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).list_all() == ()
        assert EvaluatorAuditRepository(connection).list_all() == ()
        assert SelfImprovementMeasurementRepository(connection).list_all() == ()
        assert repositories.policies.get_active() == runtime.prior
        assert repositories.policies.list_all() == (runtime.prior,)
        assert repositories.transactions.list_all() == ()
        assert repositories.audit.list_all() == ()
    runtime.engine.dispose()


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Runtime:
    def __init__(
        self,
        coordinator: TransactionCoordinator,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        engine: Engine,
        prior: PolicySnapshot,
        candidate: PolicySnapshot,
    ) -> None:
        self.coordinator = coordinator
        self.uow_factory = uow_factory
        self.engine = engine
        self.prior = prior
        self.candidate = candidate


def _runtime(tmp_path: Path) -> _Runtime:
    prior_policy = GovernancePolicy(required_claim_checks=("source_exists",))
    prior = PolicySnapshot(policy_hash=policy_hash(prior_policy), policy=prior_policy)
    candidate_policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"governance_change"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.GOVERNANCE_POLICY,
                persistence=PersistenceScope.GOVERNANCE_POLICY,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )
    candidate = PolicySnapshot(
        policy_hash=policy_hash(candidate_policy),
        policy=candidate_policy,
    )
    database_url = f"sqlite:///{(tmp_path / 'transition.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(prior, NOW)
    coordinator = TransactionCoordinator(
        uow_factory,
        prior,
        _Clock(),
        FileArtifactStore(tmp_path / "artifacts"),
    )
    return _Runtime(coordinator, uow_factory, engine, prior, candidate)


def _transition(
    prior: PolicySnapshot,
    candidate: PolicySnapshot,
    prefix: str,
) -> ProposeGovernancePolicyTransition:
    proposer = _model_actor(f"{prefix}-proposer")
    approver = _human_actor(f"{prefix}-approver")
    evaluator = _model_actor(f"{prefix}-evaluator")
    auditor = _human_actor(f"{prefix}-auditor")
    classification = ChangeClassification(
        target=ChangeTarget.GOVERNANCE_POLICY,
        loop_closure=LoopClosure.HUMAN_IN_LOOP,
        persistence=PersistenceScope.GOVERNANCE_POLICY,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )
    budget = _budget(10)
    run = ResearchRun(
        run_id=f"{prefix}-run",
        charter="Independently measure a candidate governance policy",
        scope=("offline constitutional transition",),
        creator=proposer,
        created_at=NOW,
        active_governance_policy_hash=prior.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=approver,
        final_validator_version="human-v1",
        environment_snapshot_id=f"{prefix}-environment",
    )
    audit = EvaluatorAuditRecord(
        evaluator_audit_id=f"{prefix}-audit",
        auditor=auditor,
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        proposer=proposer,
        candidate_producer=proposer,
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=(f"{prefix}-protected-evidence",),
        checks_run=(f"{prefix}-audit-check",),
        assumptions=("policy fixtures are canonical",),
        limitations=("offline deterministic coverage",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=prior.policy_hash,
    )
    measurement = SelfImprovementMeasurementRecord(
        measurement_id=f"{prefix}-measurement",
        change_id=f"{prefix}-change",
        run_id=run.run_id,
        classification=classification,
        proposer=proposer,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        evaluator_tier="protected-external",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        baseline_version_id=prior.policy_hash,
        candidate_version_id=candidate.policy_hash,
        protected_metrics=(_metric(f"{prefix}-protected", protected=True),),
        countermetrics=(_metric(f"{prefix}-countermetric", protected=False),),
        trajectory=(_point(prefix, 0), _point(prefix, 1)),
        attempted_changes=(f"{prefix}-admitted", f"{prefix}-rejected"),
        admitted_changes=(f"{prefix}-admitted",),
        rejected_changes=(f"{prefix}-rejected",),
        regressions=("one retained countermetric regression",),
        rollback_events=(f"{prefix}-rollback-drill",),
        execution_budget=_budget(10),
        search_budget=_budget(20),
        evaluation_budget=_budget(30),
        judging_budget=_budget(40),
        human_budget=_budget(50),
        usage=_usage(),
        failures=("one failed candidate retained",),
        rollback_target_id=prior.policy_hash,
        evaluator_audit_id=audit.evaluator_audit_id,
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=approver,
        decided_at=DECIDED_AT,
        governing_policy_hash=prior.policy_hash,
    )
    return ProposeGovernancePolicyTransition(
        proposal_id=f"{prefix}-transition",
        idempotency_key=f"{prefix}-transition-key",
        proposer=proposer,
        approval=Approval(approver=approver, approved_at=NOW),
        research_run=run,
        evaluator_audit=audit,
        measurement=measurement,
        candidate_policy_snapshot=candidate,
        prior_policy_hash=prior.policy_hash,
        rollback_policy_hash=prior.policy_hash,
        classification=classification,
    )


def _assert_rejected_transition_is_durable_without_projection(runtime: _Runtime) -> None:
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).list_all() == ()
        assert EvaluatorAuditRepository(connection).list_all() == ()
        assert SelfImprovementMeasurementRepository(connection).list_all() == ()
        assert repositories.policies.get_active() == runtime.prior
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1


def _point(prefix: str, index: int) -> PerformanceTrajectoryPoint:
    candidate_id = f"{prefix}-trajectory-change-{index}"
    return PerformanceTrajectoryPoint(
        step_index=index,
        metrics=(_metric(f"{prefix}-trajectory-{index}", protected=False),),
        attempted_change_ids=(candidate_id,),
        admitted_change_ids=(candidate_id,),
        rejected_change_ids=(),
        regressions=(),
        rollback_event_ids=(),
        usage=_usage(),
    )


def _metric(identifier: str, *, protected: bool) -> MetricObservation:
    return MetricObservation(
        metric_id=identifier,
        value=0.5,
        source_id=f"{identifier}-source",
        protected=protected,
        external=True,
    )


def _budget(value: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=1.0,
        compute_units=1.0,
        tokens=1,
        elapsed_seconds=1.0,
        tool_calls=1,
        human_interventions=1,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, "provider", identifier, None, NOW)
