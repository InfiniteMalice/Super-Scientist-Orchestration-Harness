from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import signature
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, Table, update

from super_scientist.application.improvement.service import (
    DecideEvaluatorSuccessionHandler,
    ProposeEvaluatorVersionHandler,
    RecordSelfImprovementMeasurementHandler,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.configurations.models import (
    AgentConfiguration,
    ConfigurationVersion,
    ControlConfiguration,
    FoundationModelConfiguration,
    MemoryConfiguration,
    PromptConfiguration,
    ScaffoldConfiguration,
    ToolConfiguration,
)
from super_scientist.domain.evaluators.models import (
    CollapseMetrics,
    EvaluationResult,
    EvaluationStage,
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorThreshold,
    EvaluatorVersion,
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
    AssessmentProvenance,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    MetricObservation,
    PerformanceTrajectoryPoint,
    ResourceBudget,
    ResourceUsage,
    ResourceUsageBreakdown,
    SelfImprovementMeasurementRecord,
    TrajectoryObservation,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.research_runs.models import (
    ResearchRun,
    ResearchRunEvent,
    ResearchRunEventType,
    RunBudgetAllocation,
)
from super_scientist.kernel.audit.models import AuditVerification
from super_scientist.kernel.transactions.models import (
    AppendResearchRunEvent,
    Approval,
    CreateResearchRun,
    DecideEvaluatorSuccession,
    ProposeEvaluatorVersion,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    RejectionCode,
)
from super_scientist.providers.storage import domain_records
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    ConfigurationVersionRepository,
    EvaluatorAuditRepository,
    EvaluatorCollapseRepository,
    EvaluatorSuccessionRepository,
    EvaluatorVersionRepository,
    ResearchRunEventRepository,
    ResearchRunRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.schema import (
    configuration_versions,
    evaluator_audits,
    evaluator_succession_decisions,
    evaluator_versions,
    research_run_events,
    research_runs,
    self_improvement_measurements,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
HASH = "a" * 64

REPOSITORIES = (
    ResearchRunRepository,
    ResearchRunEventRepository,
    ConfigurationVersionRepository,
    SelfImprovementMeasurementRepository,
    EvaluatorAuditRepository,
    EvaluatorVersionRepository,
    EvaluatorSuccessionRepository,
    EvaluatorCollapseRepository,
)


def test_eight_public_repositories_have_connection_only_constructors() -> None:
    for repository_type in REPOSITORIES:
        assert tuple(signature(repository_type).parameters) == ("connection",)
        assert repository_type.__name__ in domain_records.__all__
    assert "_AppendOnlyRecordRepository" not in domain_records.__all__
    assert not hasattr(domain_records, "AppendOnlyRecordRepository")


@pytest.mark.integration
def test_fixed_repositories_round_trip_exact_domain_models_in_fk_safe_order(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'adaptation-foundation.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    run = _run()
    event = _run_event()
    configuration = _configuration()
    audit = _audit()
    measurement = _measurement()
    predecessor = _evaluator_version("evaluator-v1", None)
    candidate = _evaluator_version("evaluator-v2", "evaluator-v1")
    succession = _succession()
    collapse = _collapse()

    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = tuple(repository_type(connection) for repository_type in REPOSITORIES)
        # The required FK-safe projection order deliberately differs from repository declaration.
        ResearchRunRepository(connection).add(run.run_id, run, NOW)
        ResearchRunEventRepository(connection).add(event.run_event_id, event, NOW)
        ConfigurationVersionRepository(connection).add(
            configuration.configuration_version_id,
            configuration,
            NOW,
        )
        EvaluatorAuditRepository(connection).add(audit.evaluator_audit_id, audit, NOW)
        SelfImprovementMeasurementRepository(connection).add(
            measurement.measurement_id,
            measurement,
            NOW,
        )
        EvaluatorVersionRepository(connection).add(
            predecessor.evaluator_version_id,
            predecessor,
            NOW,
        )
        EvaluatorVersionRepository(connection).add(
            candidate.evaluator_version_id,
            candidate,
            NOW,
        )
        EvaluatorSuccessionRepository(connection).add(
            succession.evaluator_succession_decision_id,
            succession,
            NOW,
        )
        EvaluatorCollapseRepository(connection).add(
            collapse.evaluator_collapse_record_id,
            collapse,
            NOW,
        )

        expected_by_repository = (
            run,
            event,
            configuration,
            measurement,
            audit,
            (predecessor, candidate),
            succession,
            collapse,
        )
        for repository, expected in zip(repositories, expected_by_repository, strict=True):
            expected_records = expected if isinstance(expected, tuple) else (expected,)
            assert repository.list_all() == expected_records

    engine.dispose()


def test_research_run_rejects_non_utc_creation_time() -> None:
    payload = _run().model_dump(mode="python")
    payload["created_at"] = NOW.replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        ResearchRun.model_validate(payload)


@pytest.mark.integration
def test_v1_fails_closed_for_new_persistent_run_proposal_and_audits_it(
    tmp_path: Path,
) -> None:
    policy = _snapshot(GovernancePolicy(required_claim_checks=("source_exists",)))
    coordinator, uow_factory, engine = _coordinator(tmp_path, policy)
    proposer = _human_actor("run-proposer")
    run = _run().model_copy(
        update={
            "creator": proposer,
            "active_governance_policy_hash": policy.policy_hash,
        }
    )

    decision = coordinator.submit(
        CreateResearchRun(
            proposal_id="create-run-v1",
            idempotency_key="create-run-v1-key",
            proposer=proposer,
            approval=Approval(approver=_human_actor("approver"), approved_at=NOW),
            run=run,
        )
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        assert ResearchRunRepository(unit_of_work.connection).list_all() == ()
        repositories = unit_of_work.repositories()
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1
    engine.dispose()


@pytest.mark.integration
def test_v2_research_run_lifecycle_requires_accepted_final_validation(
    tmp_path: Path,
) -> None:
    policy = _snapshot(
        GovernancePolicyV2(
            required_claim_checks=("source_exists",),
            human_approval_for=frozenset({"governance_change"}),
            adaptation_requirements=(
                AdaptationRequirement(
                    change_target=ChangeTarget.RESEARCH_PROCESS,
                    persistence=PersistenceScope.RUN_LOCAL,
                    minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                    permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                    required_approver_kind=ActorKind.HUMAN,
                    protected_evaluation_required=False,
                    rollback_required=False,
                ),
            ),
        )
    )
    coordinator, uow_factory, engine = _coordinator(tmp_path, policy)
    proposer = _human_actor("run-proposer")
    approval = Approval(approver=_human_actor("approver"), approved_at=NOW)
    run = _run().model_copy(
        update={
            "creator": proposer,
            "active_governance_policy_hash": policy.policy_hash,
        }
    )
    create = CreateResearchRun(
        proposal_id="create-run-v2",
        idempotency_key="create-run-v2-key",
        proposer=proposer,
        approval=approval,
        run=run,
    )
    started = _event_proposal(
        "start-run",
        proposer,
        approval,
        run,
        sequence=1,
        event_type=ResearchRunEventType.STARTED,
    )
    premature_success = _event_proposal(
        "premature-success",
        proposer,
        approval,
        run,
        sequence=2,
        event_type=ResearchRunEventType.SUCCEEDED,
    )
    final_validation = _event_proposal(
        "final-validation",
        proposer,
        approval,
        run,
        sequence=2,
        event_type=ResearchRunEventType.FINAL_VALIDATION_ACCEPTED,
        final_validation=_provenance("validator", human=True),
    )
    success = _event_proposal(
        "success",
        proposer,
        approval,
        run,
        sequence=3,
        event_type=ResearchRunEventType.SUCCEEDED,
    )

    assert coordinator.submit(create).accepted is True
    assert coordinator.submit(started).accepted is True
    false_finish = coordinator.submit(premature_success)
    assert false_finish.accepted is False
    assert false_finish.reasons[0].code is RejectionCode.FALSE_FINISH
    assert coordinator.submit(final_validation).accepted is True
    assert coordinator.submit(success).accepted is True

    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        events = sorted(
            ResearchRunEventRepository(connection).list_all(),
            key=lambda event: event.sequence,
        )
        assert tuple(event.event_type for event in events) == (
            ResearchRunEventType.STARTED,
            ResearchRunEventType.FINAL_VALIDATION_ACCEPTED,
            ResearchRunEventType.SUCCEEDED,
        )
        assert domain_records.ResearchRunHeadRepository(connection).get(run.run_id) == (
            success.event.run_event_id
        )
        assert len(unit_of_work.repositories().transactions.list_all()) == 5
        assert len(unit_of_work.repositories().audit.list_all()) == 5
    engine.dispose()


def test_fixed_router_declares_all_phase_a_adaptation_handlers(tmp_path: Path) -> None:
    policy = _snapshot(GovernancePolicy(required_claim_checks=("source_exists",)))
    coordinator, _, engine = _coordinator(tmp_path, policy)
    proposal_types = (
        "create_research_run",
        "append_research_run_event",
        "record_configuration_version",
        "record_evaluator_audit",
        "record_self_improvement_measurement",
        "propose_evaluator_version",
        "decide_evaluator_succession",
    )

    assert (
        tuple(
            coordinator.router.resolve(proposal_type).proposal_type
            for proposal_type in proposal_types
        )
        == proposal_types
    )
    engine.dispose()


def test_workspace_integrity_rejects_untransactional_task4_record(tmp_path: Path) -> None:
    policy = _phase_a_policy()
    _, uow_factory, engine = _coordinator(tmp_path, policy)
    run = _run().model_copy(update={"active_governance_policy_hash": policy.policy_hash})
    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        ResearchRunRepository(unit_of_work.connection).add(run.run_id, run, run.created_at)

    with uow_factory() as unit_of_work:
        result = verify_workspace(
            unit_of_work.repositories(),
            FileArtifactStore(tmp_path / "artifacts-2"),
        )

    assert result.valid is False
    assert "research run" in (result.reason or "")
    engine.dispose()


@pytest.mark.parametrize(
    ("record_kind", "reason_fragment"),
    (
        ("research_run", "research run"),
        ("research_run_event", "research run event"),
        ("configuration", "configuration"),
        ("evaluator_audit", "evaluator audit"),
        ("measurement", "measurement"),
        ("evaluator_version", "evaluator version"),
        ("evaluator_succession", "evaluator succession"),
        ("evaluator_collapse", "evaluator collapse"),
    ),
)
def test_workspace_integrity_reconstructs_all_eight_task4_record_classes(
    tmp_path: Path,
    record_kind: str,
    reason_fragment: str,
) -> None:
    fixture = _complete_adaptation_workspace(tmp_path)
    if record_kind == "evaluator_collapse":
        collapse = _collapse().model_copy(
            update={"governing_policy_hash": fixture.policy.policy_hash}
        )
        with fixture.uow_factory() as unit_of_work:
            assert unit_of_work.connection is not None
            EvaluatorCollapseRepository(unit_of_work.connection).add(
                collapse.evaluator_collapse_record_id,
                collapse,
                collapse.measured_at,
            )
    else:
        table, identifier_column, identifier, tampered = _tamper_target(
            fixture,
            record_kind,
        )
        _rewrite_authoritative_record(
            fixture,
            table,
            identifier_column,
            identifier,
            tampered,
        )

    result = _verify_adaptation_fixture(fixture)

    assert result.valid is False
    assert reason_fragment in (result.reason or "")
    fixture.engine.dispose()


@pytest.mark.parametrize("head_kind", ("research_run", "evaluator"))
def test_workspace_integrity_reconstructs_both_task4_heads(
    tmp_path: Path,
    head_kind: str,
) -> None:
    fixture = _complete_adaptation_workspace(tmp_path)
    with fixture.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        if head_kind == "research_run":
            domain_records.ResearchRunHeadRepository(unit_of_work.connection).set(
                fixture.run.run_id,
                fixture.first_event.run_event_id,
            )
        else:
            domain_records.EvaluatorHeadRepository(unit_of_work.connection).set(
                fixture.predecessor.evaluator_version_id
            )

    result = _verify_adaptation_fixture(fixture)

    assert result.valid is False
    assert "head" in (result.reason or "")
    fixture.engine.dispose()


@pytest.mark.integration
def test_v2_handlers_project_complete_measurement_and_govern_evaluator_succession(
    tmp_path: Path,
) -> None:
    policy = _phase_a_policy()
    coordinator, uow_factory, engine = _coordinator(tmp_path, policy)
    human_approval = Approval(approver=_human_actor("independent-approver"), approved_at=NOW)
    run_proposer = _human_actor("run-proposer")
    run = _run().model_copy(
        update={
            "creator": run_proposer,
            "active_governance_policy_hash": policy.policy_hash,
        }
    )
    configuration = _configuration().model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    predecessor = _evaluator_version("evaluator-v1", None).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    candidate = _evaluator_version("evaluator-v2", "evaluator-v1").model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    audit = _audit().model_copy(
        update={
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    measurement = _measurement().model_copy(
        update={
            "classification": _classification(ChangeTarget.EVALUATOR),
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "candidate_version_id": candidate.evaluator_version_id,
            "baseline_version_id": predecessor.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    succession = _succession(
        candidate_evaluator=candidate.evaluator,
        governing_policy_hash=policy.policy_hash,
    )

    proposals = (
        CreateResearchRun(
            proposal_id="phase-a-run",
            idempotency_key="phase-a-run-key",
            proposer=run_proposer,
            approval=human_approval,
            run=run,
        ),
        RecordConfigurationVersion(
            proposal_id="phase-a-config",
            idempotency_key="phase-a-config-key",
            proposer=configuration.created_by,
            approval=human_approval,
            configuration_version=configuration,
            classification=_classification(ChangeTarget.PROMPT),
        ),
        ProposeEvaluatorVersion(
            proposal_id="phase-a-evaluator-v1",
            idempotency_key="phase-a-evaluator-v1-key",
            proposer=predecessor.candidate_producer,
            approval=human_approval,
            evaluator_version=predecessor,
            classification=_classification(ChangeTarget.EVALUATOR),
        ),
        RecordEvaluatorAudit(
            proposal_id="phase-a-audit",
            idempotency_key="phase-a-audit-key",
            proposer=audit.auditor,
            approval=human_approval,
            evaluator_audit=audit,
        ),
        RecordSelfImprovementMeasurement(
            proposal_id="phase-a-measurement",
            idempotency_key="phase-a-measurement-key",
            proposer=measurement.proposer,
            approval=Approval(
                approver=measurement.decision_authority,
                approved_at=NOW,
            ),
            measurement=measurement,
        ),
        ProposeEvaluatorVersion(
            proposal_id="phase-a-evaluator-v2",
            idempotency_key="phase-a-evaluator-v2-key",
            proposer=candidate.candidate_producer,
            approval=human_approval,
            evaluator_version=candidate,
            classification=_classification(ChangeTarget.EVALUATOR),
        ),
    )
    for proposal in proposals:
        decision = coordinator.submit(proposal)
        assert decision.accepted is True, decision

    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        heads = domain_records.EvaluatorHeadRepository(unit_of_work.connection)
        assert heads.get() is None
        heads.set(predecessor.evaluator_version_id)

    succession_proposal = DecideEvaluatorSuccession(
        proposal_id="phase-a-succession",
        idempotency_key="phase-a-succession-key",
        proposer=succession.decision_authority,
        approval=human_approval,
        succession_decision=succession,
        classification=_classification(ChangeTarget.EVALUATOR),
    )
    assert coordinator.submit(succession_proposal).accepted is True

    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        assert (
            ConfigurationVersionRepository(connection).get(configuration.configuration_version_id)
            == configuration
        )
        assert EvaluatorAuditRepository(connection).get(audit.evaluator_audit_id) == audit
        assert (
            SelfImprovementMeasurementRepository(connection).get(measurement.measurement_id)
            == measurement
        )
        assert EvaluatorVersionRepository(connection).list_all() == (predecessor, candidate)
        assert (
            EvaluatorSuccessionRepository(connection).get(
                succession.evaluator_succession_decision_id
            )
            == succession
        )
        assert domain_records.EvaluatorHeadRepository(connection).get() == (
            candidate.evaluator_version_id
        )
    engine.dispose()


def test_measurement_rejects_passed_audit_for_different_evaluator_lineage() -> None:
    policy = _phase_a_policy()
    authority = _human_actor("measurement-authority")
    measurement = _measurement().model_copy(
        update={
            "classification": _classification(ChangeTarget.EVALUATOR),
            "decision_authority": authority,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    unrelated_audit = _audit().model_copy(
        update={
            "evaluator": _model_actor("unrelated-evaluator"),
            "evaluator_version": "unrelated-evaluator-v1",
            "proposer": measurement.proposer,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    proposal = RecordSelfImprovementMeasurement(
        proposal_id="unrelated-measurement-audit",
        idempotency_key="unrelated-measurement-audit-key",
        proposer=measurement.proposer,
        approval=Approval(approver=authority, approved_at=NOW),
        measurement=measurement,
    )
    capabilities = _LineageReadCapabilities(
        policy=policy,
        run=_run(),
        audit=unrelated_audit,
    )
    handler = RecordSelfImprovementMeasurementHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.parametrize("overspend_scope", ("measurement", "run"))
def test_measurement_rejects_category_cross_subsidy_and_run_overspend(
    overspend_scope: str,
) -> None:
    policy = _phase_a_policy()
    authority = _human_actor(f"{overspend_scope}-budget-authority")
    measurement = _measurement().model_copy(
        update={
            "classification": _classification(ChangeTarget.EVALUATOR),
            "decision_authority": authority,
            "governing_policy_hash": policy.policy_hash,
            **({"search_budget": _budget(0)} if overspend_scope == "measurement" else {}),
        }
    )
    audit = _audit().model_copy(update={"governing_policy_hash": policy.policy_hash})
    run = _run().model_copy(update={"active_governance_policy_hash": policy.policy_hash})
    if overspend_scope == "run":
        run = run.model_copy(
            update={
                "budget_allocation": run.budget_allocation.model_copy(update={"search": _budget(0)})
            }
        )
    proposal = RecordSelfImprovementMeasurement(
        proposal_id=f"{overspend_scope}-budget-overspend",
        idempotency_key=f"{overspend_scope}-budget-overspend-key",
        proposer=measurement.proposer,
        approval=Approval(approver=authority, approved_at=NOW),
        measurement=measurement,
    )
    capabilities = _LineageReadCapabilities(policy=policy, run=run, audit=audit)
    handler = RecordSelfImprovementMeasurementHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNMATCHED_BUDGETS


def test_evaluator_version_rejects_audit_for_different_candidate_producer() -> None:
    policy = _phase_a_policy()
    predecessor = _evaluator_version("evaluator-v1", None).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    candidate = _evaluator_version("evaluator-v2", predecessor.evaluator_version_id).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    measurement = _measurement().model_copy(
        update={
            "classification": _classification(ChangeTarget.EVALUATOR),
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "baseline_version_id": predecessor.evaluator_version_id,
            "candidate_version_id": candidate.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    unrelated_audit = _audit().model_copy(
        update={
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "proposer": measurement.proposer,
            "candidate_producer": _model_actor("unrelated-producer"),
            "governing_policy_hash": policy.policy_hash,
        }
    )
    proposal = ProposeEvaluatorVersion(
        proposal_id="unrelated-version-audit",
        idempotency_key="unrelated-version-audit-key",
        proposer=candidate.candidate_producer,
        approval=Approval(approver=_human_actor("version-approver"), approved_at=NOW),
        evaluator_version=candidate,
        classification=_classification(ChangeTarget.EVALUATOR),
    )
    capabilities = _LineageReadCapabilities(
        policy=policy,
        audit=unrelated_audit,
        measurement=measurement,
        versions=(predecessor,),
    )
    handler = ProposeEvaluatorVersionHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL


def test_evaluator_succession_rejects_audit_for_different_candidate_producer() -> None:
    policy = _phase_a_policy()
    predecessor = _evaluator_version("evaluator-v1", None).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    candidate = _evaluator_version("evaluator-v2", predecessor.evaluator_version_id).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    unrelated_audit = _audit().model_copy(
        update={
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "candidate_producer": _model_actor("unrelated-producer"),
            "governing_policy_hash": policy.policy_hash,
        }
    )
    succession = _succession(
        candidate_evaluator=candidate.evaluator,
        governing_policy_hash=policy.policy_hash,
    )
    proposal = DecideEvaluatorSuccession(
        proposal_id="unrelated-succession-audit",
        idempotency_key="unrelated-succession-audit-key",
        proposer=succession.decision_authority,
        approval=Approval(approver=_human_actor("succession-approver"), approved_at=NOW),
        succession_decision=succession,
        classification=_classification(ChangeTarget.EVALUATOR),
    )
    capabilities = _LineageReadCapabilities(
        policy=policy,
        audit=unrelated_audit,
        versions=(predecessor, candidate),
        active_head_id=predecessor.evaluator_version_id,
    )
    handler = DecideEvaluatorSuccessionHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL


def test_evaluator_succession_rejects_gate_evidence_unrelated_to_audit() -> None:
    policy = _phase_a_policy()
    predecessor = _evaluator_version("evaluator-v1", None).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    candidate = _evaluator_version("evaluator-v2", predecessor.evaluator_version_id).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    audit = _audit().model_copy(
        update={
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    succession = _succession(
        candidate_evaluator=candidate.evaluator,
        governing_policy_hash=policy.policy_hash,
    )
    external = succession.external_evaluation
    assert external is not None
    unrelated_evidence = ("unrelated-gate-evidence",)
    unrelated_external = EvaluationResult.model_validate(
        external.model_copy(
            update={
                "evidence_ids": unrelated_evidence,
                "provenance": external.provenance.model_copy(
                    update={"evidence_ids": unrelated_evidence}
                ),
            }
        ).model_dump(mode="python")
    )
    succession = succession.model_copy(update={"external_evaluation": unrelated_external})
    proposal = DecideEvaluatorSuccession(
        proposal_id="unrelated-gate-evidence",
        idempotency_key="unrelated-gate-evidence-key",
        proposer=succession.decision_authority,
        approval=Approval(approver=_human_actor("succession-approver"), approved_at=NOW),
        succession_decision=succession,
        classification=_classification(ChangeTarget.EVALUATOR),
    )
    capabilities = _LineageReadCapabilities(
        policy=policy,
        audit=audit,
        versions=(predecessor, candidate),
        active_head_id=predecessor.evaluator_version_id,
    )
    handler = DecideEvaluatorSuccessionHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@dataclass(frozen=True)
class _LineageReadCapabilities:
    policy: PolicySnapshot
    run: ResearchRun | None = None
    audit: EvaluatorAuditRecord | None = None
    measurement: SelfImprovementMeasurementRecord | None = None
    versions: tuple[EvaluatorVersion, ...] = ()
    active_head_id: str | None = None

    def policy_snapshot(self) -> PolicySnapshot:
        return self.policy

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        if self.measurement is None or self.measurement.measurement_id != measurement_id:
            return None
        return self.measurement

    def get_run(self, run_id: str) -> ResearchRun | None:
        if self.run is None or self.run.run_id != run_id:
            return None
        return self.run

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        if self.audit is None or self.audit.evaluator_audit_id != evaluator_audit_id:
            return None
        return self.audit

    def get_evaluator_version(self, evaluator_version_id: str) -> EvaluatorVersion | None:
        return next(
            (
                version
                for version in self.versions
                if version.evaluator_version_id == evaluator_version_id
            ),
            None,
        )

    def measurements_for_candidate(
        self,
        candidate_version_id: str,
    ) -> tuple[SelfImprovementMeasurementRecord, ...]:
        if (
            self.measurement is None
            or self.measurement.candidate_version_id != candidate_version_id
        ):
            return ()
        return (self.measurement,)

    def get_succession_decision(
        self,
        decision_id: str,
    ) -> EvaluatorSuccessionDecision | None:
        del decision_id
        return None

    def active_evaluator_version_id(self) -> str | None:
        return self.active_head_id


class _FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class _CompleteAdaptationFixture:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    artifacts: FileArtifactStore
    policy: PolicySnapshot
    run: ResearchRun
    first_event: ResearchRunEvent
    last_event: ResearchRunEvent
    configuration: ConfigurationVersion
    audit: EvaluatorAuditRecord
    measurement: SelfImprovementMeasurementRecord
    predecessor: EvaluatorVersion
    candidate: EvaluatorVersion
    succession: EvaluatorSuccessionDecision


def _complete_adaptation_workspace(tmp_path: Path) -> _CompleteAdaptationFixture:
    policy = _phase_a_policy()
    coordinator, uow_factory, engine = _coordinator(tmp_path, policy)
    assert isinstance(engine, Engine)
    artifacts = FileArtifactStore(tmp_path / "artifacts-2")
    approval = Approval(approver=_human_actor("fixture-approver"), approved_at=NOW)
    run_proposer = _human_actor("fixture-run-proposer")
    run = _run().model_copy(
        update={
            "creator": run_proposer,
            "active_governance_policy_hash": policy.policy_hash,
        }
    )
    first_event_proposal = _event_proposal(
        "fixture-start",
        run_proposer,
        approval,
        run,
        sequence=1,
        event_type=ResearchRunEventType.STARTED,
    )
    last_event_proposal = _event_proposal(
        "fixture-pause",
        run_proposer,
        approval,
        run,
        sequence=2,
        event_type=ResearchRunEventType.PAUSED,
    )
    configuration = _configuration().model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    predecessor = _evaluator_version("evaluator-v1", None).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    candidate = _evaluator_version("evaluator-v2", predecessor.evaluator_version_id).model_copy(
        update={"governing_policy_hash": policy.policy_hash}
    )
    audit = _audit().model_copy(
        update={
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    measurement = _measurement().model_copy(
        update={
            "classification": _classification(ChangeTarget.EVALUATOR),
            "evaluator": candidate.evaluator,
            "evaluator_version": candidate.evaluator_version_id,
            "baseline_version_id": predecessor.evaluator_version_id,
            "candidate_version_id": candidate.evaluator_version_id,
            "governing_policy_hash": policy.policy_hash,
        }
    )
    succession = _succession(
        candidate_evaluator=candidate.evaluator,
        governing_policy_hash=policy.policy_hash,
    )
    proposals = (
        CreateResearchRun(
            proposal_id="fixture-run",
            idempotency_key="fixture-run-key",
            proposer=run_proposer,
            approval=approval,
            run=run,
        ),
        first_event_proposal,
        last_event_proposal,
        RecordConfigurationVersion(
            proposal_id="fixture-configuration",
            idempotency_key="fixture-configuration-key",
            proposer=configuration.created_by,
            approval=approval,
            configuration_version=configuration,
            classification=_classification(ChangeTarget.PROMPT),
        ),
        ProposeEvaluatorVersion(
            proposal_id="fixture-evaluator-v1",
            idempotency_key="fixture-evaluator-v1-key",
            proposer=predecessor.candidate_producer,
            approval=approval,
            evaluator_version=predecessor,
            classification=_classification(ChangeTarget.EVALUATOR),
        ),
        RecordEvaluatorAudit(
            proposal_id="fixture-audit",
            idempotency_key="fixture-audit-key",
            proposer=audit.auditor,
            approval=approval,
            evaluator_audit=audit,
        ),
        RecordSelfImprovementMeasurement(
            proposal_id="fixture-measurement",
            idempotency_key="fixture-measurement-key",
            proposer=measurement.proposer,
            approval=Approval(
                approver=measurement.decision_authority,
                approved_at=NOW,
            ),
            measurement=measurement,
        ),
        ProposeEvaluatorVersion(
            proposal_id="fixture-evaluator-v2",
            idempotency_key="fixture-evaluator-v2-key",
            proposer=candidate.candidate_producer,
            approval=approval,
            evaluator_version=candidate,
            classification=_classification(ChangeTarget.EVALUATOR),
        ),
    )
    for proposal in proposals:
        decision = coordinator.submit(proposal)
        assert decision.accepted, decision
    with uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        domain_records.EvaluatorHeadRepository(unit_of_work.connection).set(
            predecessor.evaluator_version_id
        )
    succession_proposal = DecideEvaluatorSuccession(
        proposal_id="fixture-succession",
        idempotency_key="fixture-succession-key",
        proposer=succession.decision_authority,
        approval=approval,
        succession_decision=succession,
        classification=_classification(ChangeTarget.EVALUATOR),
    )
    decision = coordinator.submit(succession_proposal)
    assert decision.accepted, decision
    fixture = _CompleteAdaptationFixture(
        engine=engine,
        uow_factory=uow_factory,
        artifacts=artifacts,
        policy=policy,
        run=run,
        first_event=first_event_proposal.event,
        last_event=last_event_proposal.event,
        configuration=configuration,
        audit=audit,
        measurement=measurement,
        predecessor=predecessor,
        candidate=candidate,
        succession=succession,
    )
    verification = _verify_adaptation_fixture(fixture)
    assert verification.valid, verification.reason
    return fixture


def _verify_adaptation_fixture(fixture: _CompleteAdaptationFixture) -> AuditVerification:
    with fixture.uow_factory() as unit_of_work:
        return verify_workspace(unit_of_work.repositories(), fixture.artifacts)


def _tamper_target(
    fixture: _CompleteAdaptationFixture,
    record_kind: str,
) -> tuple[Table, str, str, BaseModel]:
    targets: dict[str, tuple[Table, str, str, BaseModel]] = {
        "research_run": (
            research_runs,
            "run_id",
            fixture.run.run_id,
            fixture.run.model_copy(update={"charter": "tampered research charter"}),
        ),
        "research_run_event": (
            research_run_events,
            "run_event_id",
            fixture.last_event.run_event_id,
            fixture.last_event.model_copy(update={"detail": "tampered event detail"}),
        ),
        "configuration": (
            configuration_versions,
            "configuration_version_id",
            fixture.configuration.configuration_version_id,
            fixture.configuration.model_copy(
                update={"created_at": fixture.configuration.created_at + timedelta(seconds=1)}
            ),
        ),
        "evaluator_audit": (
            evaluator_audits,
            "evaluator_audit_id",
            fixture.audit.evaluator_audit_id,
            fixture.audit.model_copy(update={"limitations": ("tampered audit",)}),
        ),
        "measurement": (
            self_improvement_measurements,
            "measurement_id",
            fixture.measurement.measurement_id,
            fixture.measurement.model_copy(update={"failures": ("tampered failure",)}),
        ),
        "evaluator_version": (
            evaluator_versions,
            "evaluator_version_id",
            fixture.candidate.evaluator_version_id,
            fixture.candidate.model_copy(update={"benchmark_version_ids": ("tampered-benchmark",)}),
        ),
        "evaluator_succession": (
            evaluator_succession_decisions,
            "evaluator_succession_decision_id",
            fixture.succession.evaluator_succession_decision_id,
            fixture.succession.model_copy(update={"rationale": ("tampered rationale",)}),
        ),
    }
    return targets[record_kind]


def _rewrite_authoritative_record(
    fixture: _CompleteAdaptationFixture,
    table: Table,
    identifier_column: str,
    identifier: str,
    record: BaseModel,
) -> None:
    record_json = canonical_json_bytes(record.model_dump(mode="json")).decode("utf-8")
    with fixture.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql(f"DROP TRIGGER {table.name}_no_update")
        unit_of_work.connection.execute(
            update(table)
            .where(table.c[identifier_column] == identifier)
            .values(
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
            )
        )


def _snapshot(policy: GovernancePolicy | GovernancePolicyV2) -> PolicySnapshot:
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _phase_a_policy() -> PolicySnapshot:
    requirements = (
        AdaptationRequirement(
            change_target=ChangeTarget.RESEARCH_PROCESS,
            persistence=PersistenceScope.RUN_LOCAL,
            minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
            required_approver_kind=ActorKind.HUMAN,
            protected_evaluation_required=False,
            rollback_required=False,
        ),
        AdaptationRequirement(
            change_target=ChangeTarget.PROMPT,
            persistence=PersistenceScope.PERSISTENT_SKILL,
            minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
            required_approver_kind=ActorKind.HUMAN,
            protected_evaluation_required=False,
            rollback_required=True,
        ),
        AdaptationRequirement(
            change_target=ChangeTarget.EVALUATOR,
            persistence=PersistenceScope.EVALUATOR_POLICY,
            minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
            required_approver_kind=ActorKind.HUMAN,
            protected_evaluation_required=True,
            rollback_required=True,
        ),
    )
    return _snapshot(
        GovernancePolicyV2(
            required_claim_checks=("source_exists",),
            human_approval_for=frozenset({"governance_change"}),
            adaptation_requirements=requirements,
        )
    )


def _classification(target: ChangeTarget) -> ChangeClassification:
    persistence = {
        ChangeTarget.PROMPT: PersistenceScope.PERSISTENT_SKILL,
        ChangeTarget.EVALUATOR: PersistenceScope.EVALUATOR_POLICY,
    }[target]
    return ChangeClassification(
        target=target,
        loop_closure=LoopClosure.HUMAN_IN_LOOP,
        persistence=persistence,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )


def _coordinator(
    tmp_path: Path,
    policy: PolicySnapshot,
) -> tuple[TransactionCoordinator, Callable[[], DatabaseUnitOfWork], object]:
    database_url = f"sqlite:///{(tmp_path / f'{policy.policy.schema_version}.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    return (
        TransactionCoordinator(
            uow_factory,
            policy,
            _FixedClock(),
            FileArtifactStore(tmp_path / f"artifacts-{policy.policy.schema_version}"),
        ),
        uow_factory,
        engine,
    )


def _event_proposal(
    identifier: str,
    proposer: ActorIdentity,
    approval: Approval,
    run: ResearchRun,
    *,
    sequence: int,
    event_type: ResearchRunEventType,
    final_validation: AssessmentProvenance | None = None,
) -> AppendResearchRunEvent:
    return AppendResearchRunEvent(
        proposal_id=identifier,
        idempotency_key=f"{identifier}-key",
        proposer=proposer,
        approval=approval,
        event=ResearchRunEvent(
            run_event_id=f"{identifier}-event",
            run_id=run.run_id,
            sequence=sequence,
            event_type=event_type,
            actor=proposer,
            detail=f"{event_type.value} lifecycle event",
            final_validation=final_validation,
            occurred_at=NOW,
            governing_policy_hash=run.active_governance_policy_hash,
        ),
    )


def _run() -> ResearchRun:
    budget = _budget(100)
    return ResearchRun(
        run_id="run-1",
        charter="Measure the governed policy transition",
        scope=("offline deterministic fixtures",),
        creator=_model_actor("proposer"),
        created_at=NOW,
        active_governance_policy_hash=HASH,
        model_configuration_version_id="configuration-1",
        scaffold_configuration_version_id="scaffold-1",
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=_human_actor("validator"),
        final_validator_version="validator-v1",
        environment_snapshot_id="environment-1",
    )


def _run_event() -> ResearchRunEvent:
    return ResearchRunEvent(
        run_event_id="run-event-1",
        run_id="run-1",
        sequence=1,
        event_type=ResearchRunEventType.STARTED,
        actor=_human_actor("operator"),
        detail="run started after budget review",
        final_validation=None,
        occurred_at=NOW,
        governing_policy_hash=HASH,
    )


def _configuration() -> ConfigurationVersion:
    scaffold = ScaffoldConfiguration(
        scaffold_configuration_id="scaffold-1",
        prompt=PromptConfiguration(
            prompt_configuration_id="prompt-1",
            template_hash=HASH,
            variable_names=("input",),
        ),
        memory=MemoryConfiguration(
            memory_configuration_id="memory-1",
            schema_hash=HASH,
            cross_run_enabled=False,
        ),
        tools=ToolConfiguration(
            tool_configuration_id="tools-1",
            tool_ids=("read-only",),
            routing_hash=HASH,
        ),
        control=ControlConfiguration(
            control_configuration_id="control-1",
            policy_hash=HASH,
            max_steps=5,
        ),
    )
    return ConfigurationVersion(
        configuration_version_id="configuration-1",
        agent_configuration=AgentConfiguration(
            agent_configuration_id="agent-1",
            foundation_model=FoundationModelConfiguration(
                foundation_model_configuration_id="foundation-1",
                provider_id="provider",
                model_id="model",
                adapter_id=None,
            ),
            scaffold=scaffold,
        ),
        predecessor_configuration_version_id=None,
        rollback_configuration_version_id="configuration-1",
        created_by=_human_actor("config-author"),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _audit() -> EvaluatorAuditRecord:
    return EvaluatorAuditRecord(
        evaluator_audit_id="audit-1",
        auditor=_human_actor("auditor"),
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=_model_actor("evaluator"),
        evaluator_version="evaluator-v1",
        proposer=_model_actor("proposer"),
        candidate_producer=_model_actor("producer"),
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=(
            "protected-eval-evidence",
            "external-eval-evidence",
            "human-review-evidence",
            "canary-evidence",
        ),
        checks_run=("audit-check",),
        assumptions=("identity metadata is accurate",),
        limitations=("fixture coverage",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=HASH,
    )


def _measurement() -> SelfImprovementMeasurementRecord:
    trajectory = (_trajectory(0), _trajectory(1))
    return SelfImprovementMeasurementRecord(
        measurement_id="measurement-1",
        change_id="change-1",
        run_id="run-1",
        classification=ChangeClassification(
            target=ChangeTarget.GOVERNANCE_POLICY,
            loop_closure=LoopClosure.HUMAN_IN_LOOP,
            persistence=PersistenceScope.GOVERNANCE_POLICY,
            verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
            signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
        ),
        proposer=_model_actor("proposer"),
        evaluator=_model_actor("evaluator"),
        evaluator_version="evaluator-v1",
        evaluator_tier="protected",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        baseline_version_id="policy-v1",
        candidate_version_id="policy-v2",
        protected_metrics=(_metric("protected", True),),
        countermetrics=(_metric("countermetric", False),),
        expected_final_index=1,
        trajectory=trajectory,
        peak_observation=_observation(trajectory[1]),
        final_observation=_observation(trajectory[1]),
        attempted_changes=("accepted-change", "rejected-change"),
        admitted_changes=("accepted-change",),
        rejected_changes=("rejected-change",),
        regressions=("retained regression",),
        rollback_events=("rollback-drill",),
        execution_budget=_budget(10),
        search_budget=_budget(20),
        evaluation_budget=_budget(30),
        judging_budget=_budget(40),
        human_budget=_budget(50),
        usage_by_category=_usage_breakdown(
            execution=_usage(),
            search=_usage(),
        ),
        usage=_usage(2),
        failures=("retained failed experiment",),
        rollback_target_id="policy-v1",
        evaluator_audit_id="audit-1",
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=_human_actor("authority"),
        decided_at=NOW,
        governing_policy_hash=HASH,
    )


def _evaluator_version(identifier: str, predecessor: str | None) -> EvaluatorVersion:
    return EvaluatorVersion(
        evaluator_version_id=identifier,
        evaluator=_model_actor(f"{identifier}-actor"),
        configuration_hash=HASH,
        threshold_history=(
            EvaluatorThreshold(
                threshold_id=f"{identifier}-threshold",
                metric_id="accuracy",
                value=0.8,
                effective_at=NOW,
            ),
        ),
        benchmark_version_ids=("benchmark-v1",),
        predecessor_evaluator_version_id=predecessor,
        rollback_evaluator_version_id=predecessor,
        candidate_producer=_model_actor("producer"),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _succession(
    *,
    candidate_evaluator: ActorIdentity | None = None,
    governing_policy_hash: str = HASH,
) -> EvaluatorSuccessionDecision:
    return EvaluatorSuccessionDecision(
        evaluator_succession_decision_id="succession-1",
        predecessor_evaluator_version_id="evaluator-v1",
        candidate_evaluator_version_id="evaluator-v2",
        candidate_evaluator=(
            _model_actor("candidate-evaluator")
            if candidate_evaluator is None
            else candidate_evaluator
        ),
        candidate_producer=_model_actor("producer"),
        change_proposer=_model_actor("proposer"),
        evaluator_audit_id="audit-1",
        evaluator_audit_result=AssessmentOutcome.PASSED,
        protected_evaluation=_evaluation(
            "protected-eval",
            EvaluationStage.PROTECTED,
            governing_policy_hash,
        ),
        external_evaluation=_evaluation(
            "external-eval",
            EvaluationStage.EXTERNAL,
            governing_policy_hash,
        ),
        human_review=_evaluation(
            "human-review",
            EvaluationStage.HUMAN_REVIEW,
            governing_policy_hash,
            human=True,
        ),
        canary_evaluation=_evaluation(
            "canary",
            EvaluationStage.CANARY,
            governing_policy_hash,
        ),
        predecessor_rollback_target_id="evaluator-v1",
        accepted=True,
        rationale=("all gates passed",),
        decision_authority=_human_actor("promotion-authority"),
        decided_at=NOW,
        governing_policy_hash=governing_policy_hash,
    )


def _collapse() -> EvaluatorCollapseRecord:
    return EvaluatorCollapseRecord(
        evaluator_collapse_record_id="collapse-1",
        evaluator_version_id="evaluator-v2",
        metrics=CollapseMetrics(**{field: 0.5 for field in CollapseMetrics.model_fields}),
        evidence_ids=("external-eval",),
        findings=("separate collapse signals retained",),
        measured_at=NOW,
        governing_policy_hash=HASH,
    )


def _provenance(
    identifier: str,
    *,
    human: bool = False,
    evidence_id: str = "evidence-1",
    governing_policy_hash: str = HASH,
) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=_human_actor(identifier) if human else _model_actor(identifier),
        actor_version=f"{identifier}-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="HUMAN" if human else "DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("fixture assumptions",),
        evidence_ids=(evidence_id,),
        checks_run=("check-1",),
        limitations=("fixture coverage",),
        result=AssessmentOutcome.PASSED,
        assessed_at=NOW,
        governing_policy_hash=governing_policy_hash,
    )


def _evaluation(
    identifier: str,
    stage: EvaluationStage,
    governing_policy_hash: str,
    *,
    human: bool = False,
) -> EvaluationResult:
    evidence_id = f"{identifier}-evidence"
    return EvaluationResult(
        evaluation_id=identifier,
        candidate_evaluator_version_id="evaluator-v2",
        stage=stage,
        provenance=_provenance(
            identifier,
            human=human,
            evidence_id=evidence_id,
            governing_policy_hash=governing_policy_hash,
        ),
        grounding=(
            ExternalGrounding.HUMAN_JUDGMENT if human else ExternalGrounding.INDEPENDENT_TEST_SUITE
        ),
        evidence_ids=(evidence_id,),
        passed=True,
        governing_policy_hash=governing_policy_hash,
    )


def _metric(identifier: str, protected: bool) -> MetricObservation:
    return MetricObservation(
        metric_id=identifier,
        value=0.5,
        source_id="evaluation-1",
        protected=protected,
        external=True,
    )


def _trajectory(index: int) -> PerformanceTrajectoryPoint:
    candidate_id = "accepted-change" if index == 0 else "rejected-change"
    usage = _usage()
    return PerformanceTrajectoryPoint(
        step_index=index,
        change_id="change-1",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        metrics=(_metric(f"trajectory-{index}", False),),
        attempted_change_ids=(candidate_id,),
        admitted_change_ids=(candidate_id,) if index == 0 else (),
        rejected_change_ids=() if index == 0 else (candidate_id,),
        regressions=("retained regression",) if index == 0 else (),
        rollback_event_ids=() if index == 0 else ("rollback-drill",),
        usage_by_category=_usage_breakdown(
            execution=usage if index == 0 else None,
            search=usage if index == 1 else None,
        ),
        usage=usage,
    )


def _observation(point: PerformanceTrajectoryPoint) -> TrajectoryObservation:
    return TrajectoryObservation(step_index=point.step_index, metrics=point.metrics)


def _budget(value: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _usage(multiplier: int = 1) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=float(multiplier),
        compute_units=float(multiplier),
        tokens=multiplier,
        elapsed_seconds=float(multiplier),
        tool_calls=multiplier,
        human_interventions=multiplier,
    )


def _zero_usage() -> ResourceUsage:
    return _usage(0)


def _usage_breakdown(
    *,
    execution: ResourceUsage | None = None,
    search: ResourceUsage | None = None,
) -> ResourceUsageBreakdown:
    return ResourceUsageBreakdown(
        execution=execution or _zero_usage(),
        search=search or _zero_usage(),
        evaluation=_zero_usage(),
        judging=_zero_usage(),
        human=_zero_usage(),
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, "provider", identifier, None, NOW)
