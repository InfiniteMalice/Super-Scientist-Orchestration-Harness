from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super_scientist.application.rules.service import (
    FIXED_RULE_CLASSIFICATION,
    RuleService,
    rule_consolidation_decision,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.behavioral_rules.consolidation import build_candidate_diff
from super_scientist.domain.behavioral_rules.models import (
    OverlapClassification,
    RecurrenceRepair,
    RuleAction,
    RuleStatus,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    Approval,
    ConsolidateBehavioralRule,
    CreateResearchRun,
    ImportReviewerAssessment,
    ProposeBehavioralRule,
    RecordEvaluatorAudit,
    RecordRuleIncident,
    RecordSelfImprovementMeasurement,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    BehavioralRuleHeadRepository,
    BehavioralRuleVersionRepository,
    ReviewerAssessmentRepository,
    RuleConsolidationDecisionRepository,
    RuleIncidentRepository,
    RuleRegressionCaseRepository,
)
from super_scientist.providers.storage.repositories import PolicyRepository, RepositorySet
from tests.integration.application.test_adaptation_foundation import (
    _audit as base_audit,
)
from tests.integration.application.test_adaptation_foundation import (
    _measurement as base_measurement,
)
from tests.integration.application.test_adaptation_foundation import (
    _run as base_run,
)
from tests.rule_fixtures import (
    NOW,
    actor,
    dispositions,
    five_assessments,
    incident,
    regression,
    rule,
)


class AdvancingClock:
    def __init__(self) -> None:
        self._next = NOW

    def now(self):  # type: ignore[no-untyped-def]
        value = self._next
        self._next += timedelta(seconds=1)
        return value


@dataclass(frozen=True)
class RuleRuntime:
    engine: Engine
    policy: PolicySnapshot
    coordinator: TransactionCoordinator
    service: RuleService
    approver: ActorIdentity
    integrator: ActorIdentity


@pytest.fixture
def rule_runtime(tmp_path: Path) -> Iterator[RuleRuntime]:
    policy = _policy()
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    database_url = f"sqlite:///{(tmp_path / 'rules.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        PolicyRepository(connection).add_and_activate(snapshot, NOW)
    coordinator = TransactionCoordinator(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        AdvancingClock(),
        FileArtifactStore(tmp_path / "artifacts"),
    )
    runtime = RuleRuntime(
        engine=engine,
        policy=snapshot,
        coordinator=coordinator,
        service=RuleService(coordinator),
        approver=actor("rule-approver"),
        integrator=actor("integrator"),
    )
    try:
        yield runtime
    finally:
        engine.dispose()


@pytest.mark.integration
def test_rule_service_rejects_exact_duplicate_but_routes_semantic_duplicate_to_review(
    rule_runtime: RuleRuntime,
) -> None:
    first_incident = _incident("incident-1", rule_runtime)
    assert rule_runtime.service.record_incident(
        _incident_proposal(first_incident, rule_runtime)
    ).accepted
    baseline = _rule("rule-1-v1", rule_runtime)
    assert rule_runtime.service.propose_rule(_rule_proposal(baseline, rule_runtime)).accepted

    exact = baseline.model_copy(
        update={
            "rule_version_id": "rule-copy-v1",
            "rule_id": "rule-copy",
            "canonical_statement": f"  {baseline.canonical_statement.upper()}  ",
        }
    )
    duplicate = rule_runtime.service.propose_rule(
        _rule_proposal(exact, rule_runtime, proposal_id="proposal-rule-exact")
    )
    assert duplicate.accepted is False
    assert duplicate.reasons[0].code is RejectionCode.DUPLICATE_RULE

    semantic = exact.model_copy(
        update={
            "rule_version_id": "rule-semantic-v1",
            "rule_id": "rule-semantic",
            "canonical_statement": "Do not erase the incident that gave rise to a rule.",
            "status": RuleStatus.UNDER_REVIEW,
        }
    )
    routed = rule_runtime.service.propose_rule(
        _rule_proposal(semantic, rule_runtime, proposal_id="proposal-rule-semantic")
    )
    assert routed.accepted is True
    with rule_runtime.engine.connect() as connection:
        stored = BehavioralRuleVersionRepository(connection).get("rule-semantic-v1")
        assert stored is not None
        assert stored.status is RuleStatus.UNDER_REVIEW


@pytest.mark.integration
def test_assessment_stable_key_is_idempotent_and_changed_content_is_audited_conflict(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_rule_for_review(rule_runtime)
    original = _assessment(five_assessments()[0], rule_runtime)
    first = rule_runtime.service.import_assessment(
        _assessment_proposal(original, rule_runtime, "proposal-review-1")
    )
    exact_reimport = rule_runtime.service.import_assessment(
        _assessment_proposal(original, rule_runtime, "proposal-review-2")
    )
    changed = original.model_copy(update={"findings": ("changed under the same stable key",)})
    conflict = rule_runtime.service.import_assessment(
        _assessment_proposal(changed, rule_runtime, "proposal-review-3")
    )

    assert first.accepted is True
    assert exact_reimport.accepted is True
    assert conflict.accepted is False
    assert conflict.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    with rule_runtime.engine.connect() as connection:
        assert ReviewerAssessmentRepository(connection).list_all() == (original,)
        repositories = RepositorySet(connection)
        assert len(repositories.transactions.list_all()) == 6
        assert len(repositories.audit.list_all()) == 6


@pytest.mark.integration
def test_integrator_consolidates_only_after_five_reviews_and_measurement(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    candidate, consolidation = _consolidation(rule_runtime, assessments)

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is True
    with rule_runtime.engine.connect() as connection:
        assert (
            BehavioralRuleVersionRepository(connection).get(candidate.rule_version_id) == candidate
        )
        stored_decision = RuleConsolidationDecisionRepository(connection).get("decision-1")
        assert stored_decision is not None
        assert stored_decision.preserved_dissent
        assert len(RuleRegressionCaseRepository(connection).list_all()) == 2
        assert BehavioralRuleHeadRepository(connection).get("rule-1") == (
            "rule-1-v2",
            "1.1.0",
            RuleStatus.ACTIVE,
        )
        assert verify_workspace(RepositorySet(connection), FileArtifactStore(Path("unused"))).valid
        BehavioralRuleHeadRepository(connection).set(
            "rule-1",
            "rule-1-v1",
            "1.0.0",
            RuleStatus.UNDER_REVIEW,
        )
        tampered = verify_workspace(
            RepositorySet(connection),
            FileArtifactStore(Path("unused")),
        )
        assert tampered.valid is False
        assert "behavioral rule heads" in (tampered.reason or "")


@pytest.mark.integration
def test_live_consolidation_rejects_pairwise_correlated_reviewers(
    rule_runtime: RuleRuntime,
) -> None:
    independent = _seed_pairwise_correlated_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(rule_runtime, independent)

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNRESOLVED_RULE_CONFLICT


@pytest.mark.integration
def test_integrator_cannot_approve_own_consolidation(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(rule_runtime, assessments)
    proposal = _consolidation_proposal(rule_runtime, consolidation).model_copy(
        update={"approval": Approval(approver=rule_runtime.integrator, approved_at=NOW)}
    )

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
def test_wrong_rule_classification_and_dependent_approval_fail_closed(
    rule_runtime: RuleRuntime,
) -> None:
    record = _incident("incident-1", rule_runtime)
    wrong = FIXED_RULE_CLASSIFICATION.model_copy(update={"persistence": PersistenceScope.RUN_LOCAL})
    wrong_classification = rule_runtime.service.record_incident(
        _incident_proposal(record, rule_runtime).model_copy(update={"classification": wrong})
    )
    dependent = rule_runtime.service.record_incident(
        _incident_proposal(
            record.model_copy(update={"incident_id": "incident-2"}),
            rule_runtime,
            proposal_id="proposal-incident-dependent",
        ).model_copy(update={"approval": Approval(approver=record.reported_by, approved_at=NOW)})
    )

    assert wrong_classification.accepted is False
    assert wrong_classification.reasons[0].code is RejectionCode.PERMISSION_DENIED
    assert dependent.accepted is False
    assert dependent.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
def test_workspace_replay_rejects_historically_accepted_wrong_rule_authority(
    rule_runtime: RuleRuntime,
) -> None:
    record = _incident("incident-1", rule_runtime)
    wrong = FIXED_RULE_CLASSIFICATION.model_copy(update={"persistence": PersistenceScope.RUN_LOCAL})
    proposal = _incident_proposal(record, rule_runtime).model_copy(update={"classification": wrong})
    accepted = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    with DatabaseUnitOfWork(rule_runtime.engine) as unit_of_work:
        repositories = unit_of_work.repositories()
        connection = unit_of_work.connection
        assert connection is not None
        RuleIncidentRepository(connection).add(
            record.incident_id,
            record,
            record.recorded_at,
        )
        repositories.transactions.add(proposal, accepted, NOW)
        repositories.audit.add(
            append_event(
                repositories.audit.last(),
                "transaction_decision",
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "decision": accepted.model_dump(mode="json"),
                    "policy_hash": rule_runtime.policy.policy_hash,
                    "stored_policy_hash": rule_runtime.policy.policy_hash,
                    "configured_policy_hash": rule_runtime.policy.policy_hash,
                    "transaction_persisted": True,
                },
                NOW + timedelta(seconds=1),
            )
        )

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(
            RepositorySet(connection),
            FileArtifactStore(Path("unused")),
        )
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_historically_accepted_correlated_reviewers(
    rule_runtime: RuleRuntime,
) -> None:
    independent = _seed_pairwise_correlated_review(rule_runtime)
    _seed_measurement(rule_runtime)
    candidate, consolidation = _consolidation(rule_runtime, independent)
    proposal = _consolidation_proposal(rule_runtime, consolidation)
    accepted = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    decision = rule_consolidation_decision(proposal)
    with DatabaseUnitOfWork(rule_runtime.engine) as unit_of_work:
        repositories = unit_of_work.repositories()
        connection = unit_of_work.connection
        assert connection is not None
        BehavioralRuleVersionRepository(connection).add(
            candidate.rule_version_id,
            candidate,
            candidate.created_at,
        )
        RuleConsolidationDecisionRepository(connection).add(
            decision.consolidation_decision_id,
            decision,
            decision.decided_at,
        )
        for regression_case in consolidation.regression_cases:
            RuleRegressionCaseRepository(connection).add(
                regression_case.regression_case_id,
                regression_case,
                regression_case.created_at,
            )
        BehavioralRuleHeadRepository(connection).set(
            candidate.rule_id,
            candidate.rule_version_id,
            candidate.semantic_version,
            candidate.status,
        )
        repositories.transactions.add(
            proposal,
            accepted,
            NOW + timedelta(seconds=30),
        )
        repositories.audit.add(
            append_event(
                repositories.audit.last(),
                "transaction_decision",
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "decision": accepted.model_dump(mode="json"),
                    "policy_hash": rule_runtime.policy.policy_hash,
                    "stored_policy_hash": rule_runtime.policy.policy_hash,
                    "configured_policy_hash": rule_runtime.policy.policy_hash,
                    "transaction_persisted": True,
                },
                NOW + timedelta(seconds=31),
            )
        )

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(
            RepositorySet(connection),
            FileArtifactStore(Path("unused")),
        )
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


def _policy() -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists", "evidence_span_exists"),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.BEHAVIORAL_RULE,
                persistence=PersistenceScope.PERSISTENT_RULE,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.PRIMARY_SOURCE}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
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


def _incident(identifier: str, runtime: RuleRuntime):
    return incident(identifier).model_copy(
        update={"governing_policy_hash": runtime.policy.policy_hash}
    )


def _rule(identifier: str, runtime: RuleRuntime):
    return rule(identifier).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})


def _assessment(value, runtime: RuleRuntime):  # type: ignore[no-untyped-def]
    return value.model_copy(
        update={
            "provenance": value.provenance.model_copy(
                update={"governing_policy_hash": runtime.policy.policy_hash}
            )
        }
    )


def _incident_proposal(
    record,
    runtime: RuleRuntime,
    proposal_id: str | None = None,
):  # type: ignore[no-untyped-def]
    identifier = proposal_id or f"proposal-{record.incident_id}"
    return RecordRuleIncident(
        proposal_id=identifier,
        idempotency_key=f"intent-{identifier}",
        proposer=record.reported_by,
        approval=Approval(approver=runtime.approver, approved_at=NOW),
        classification=FIXED_RULE_CLASSIFICATION,
        incident=record,
    )


def _rule_proposal(
    record,
    runtime: RuleRuntime,
    proposal_id: str = "proposal-rule-1",
):  # type: ignore[no-untyped-def]
    return ProposeBehavioralRule(
        proposal_id=proposal_id,
        idempotency_key=f"intent-{proposal_id}",
        proposer=record.creator,
        approval=Approval(approver=runtime.approver, approved_at=NOW),
        classification=FIXED_RULE_CLASSIFICATION,
        rule_version=record,
    )


def _assessment_proposal(
    record,
    runtime: RuleRuntime,
    proposal_id: str,
):  # type: ignore[no-untyped-def]
    return ImportReviewerAssessment(
        proposal_id=proposal_id,
        idempotency_key=f"intent-{proposal_id}",
        proposer=record.provenance.actor,
        approval=Approval(approver=runtime.approver, approved_at=NOW),
        classification=FIXED_RULE_CLASSIFICATION,
        assessment=record,
    )


def _seed_rule_for_review(runtime: RuleRuntime) -> None:
    for identifier in ("incident-1", "incident-2"):
        assert runtime.service.record_incident(
            _incident_proposal(_incident(identifier, runtime), runtime)
        ).accepted
    assert runtime.service.propose_rule(
        _rule_proposal(_rule("rule-1-v1", runtime), runtime)
    ).accepted


def _seed_complete_review(runtime: RuleRuntime):  # type: ignore[no-untyped-def]
    _seed_rule_for_review(runtime)
    values = tuple(_assessment(item, runtime) for item in five_assessments())
    for item in values:
        assert runtime.service.import_assessment(
            _assessment_proposal(item, runtime, f"proposal-review-{item.role.value.lower()}")
        ).accepted
    return values


def _seed_pairwise_correlated_review(
    runtime: RuleRuntime,
):  # type: ignore[no-untyped-def]
    _seed_rule_for_review(runtime)
    independent = tuple(_assessment(item, runtime) for item in five_assessments())
    correlated = list(independent)
    shared_configuration = correlated[0].provenance.actor.configuration_hash
    assert shared_configuration is not None
    correlated[1] = correlated[1].model_copy(
        update={
            "provenance": correlated[1].provenance.model_copy(
                update={
                    "actor": correlated[1].provenance.actor.model_copy(
                        update={"configuration_hash": shared_configuration}
                    )
                }
            )
        }
    )
    for item in correlated:
        assert runtime.service.import_assessment(
            _assessment_proposal(
                item,
                runtime,
                f"proposal-review-{item.role.value.lower()}",
            )
        ).accepted
    return independent


def _seed_measurement(runtime: RuleRuntime) -> None:
    run = _run(runtime)
    assert runtime.coordinator.submit(
        CreateResearchRun(
            proposal_id="proposal-run-rules",
            idempotency_key="intent-run-rules",
            proposer=run.creator,
            approval=Approval(approver=runtime.approver, approved_at=NOW),
            run=run,
        )
    ).accepted
    audit = _audit(runtime)
    assert runtime.coordinator.submit(
        RecordEvaluatorAudit(
            proposal_id="proposal-audit-rules",
            idempotency_key="intent-audit-rules",
            proposer=audit.auditor,
            approval=Approval(approver=runtime.approver, approved_at=NOW),
            evaluator_audit=audit,
        )
    ).accepted
    measurement = _measurement(runtime, audit)
    assert runtime.coordinator.submit(
        RecordSelfImprovementMeasurement(
            proposal_id="proposal-measurement-rules",
            idempotency_key="intent-measurement-rules",
            proposer=measurement.proposer,
            approval=Approval(approver=measurement.decision_authority, approved_at=NOW),
            measurement=measurement,
        )
    ).accepted


def _run(runtime: RuleRuntime) -> ResearchRun:
    baseline = base_run()
    return ResearchRun.model_validate(
        baseline.model_dump(mode="python")
        | {
            "run_id": "run-rules",
            "creator": runtime.integrator,
            "active_governance_policy_hash": runtime.policy.policy_hash,
        }
    )


def _audit(runtime: RuleRuntime) -> EvaluatorAuditRecord:
    baseline = base_audit()
    return EvaluatorAuditRecord.model_validate(
        baseline.model_dump(mode="python")
        | {
            "evaluator_audit_id": "audit-rule-1",
            "proposer": runtime.integrator,
            "candidate_producer": runtime.integrator,
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )


def _measurement(
    runtime: RuleRuntime,
    audit: EvaluatorAuditRecord,
) -> SelfImprovementMeasurementRecord:
    baseline = base_measurement()
    trajectory = tuple(
        point.model_copy(
            update={
                "change_id": "change-rule-1",
                "grounding": (ExternalGrounding.PRIMARY_SOURCE,),
            }
        )
        for point in baseline.trajectory
    )
    return SelfImprovementMeasurementRecord.model_validate(
        baseline.model_dump(mode="python")
        | {
            "measurement_id": "measurement-rule-1",
            "change_id": "change-rule-1",
            "run_id": "run-rules",
            "classification": FIXED_RULE_CLASSIFICATION,
            "proposer": runtime.integrator,
            "evaluator": audit.evaluator,
            "evaluator_version": audit.evaluator_version,
            "grounding": (ExternalGrounding.PRIMARY_SOURCE,),
            "baseline_version_id": "rule-1-v1",
            "candidate_version_id": "rule-1-v2",
            "trajectory": trajectory,
            "peak_observation": baseline.peak_observation.model_copy(
                update={"metrics": trajectory[-1].metrics}
            ),
            "final_observation": baseline.final_observation.model_copy(
                update={"metrics": trajectory[-1].metrics}
            ),
            "rollback_target_id": "rule-1-v1",
            "evaluator_audit_id": audit.evaluator_audit_id,
            "decision_authority": runtime.approver,
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )


def _consolidation(runtime: RuleRuntime, assessments):  # type: ignore[no-untyped-def]
    candidate = rule(
        "rule-1-v2",
        semantic_version="1.1.0",
        incidents=("incident-1", "incident-2"),
        status=RuleStatus.ACTIVE,
        creator=runtime.integrator,
        approver=runtime.approver,
        supersedes=("rule-1-v1",),
    ).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})
    cases = tuple(
        regression(f"regression-{index}", incident_id, creator=runtime.integrator).model_copy(
            update={"governing_policy_hash": runtime.policy.policy_hash}
        )
        for index, incident_id in enumerate(("incident-1", "incident-2"), start=1)
    )
    consolidation = build_candidate_diff(
        consolidation_decision_id="decision-1",
        review_proposal_id="proposal-rule-1",
        assessments=assessments,
        candidate_rule=candidate,
        regression_cases=cases,
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable=None,
        recurrence_incident_ids=("incident-2",),
        recurrence_repairs=(RecurrenceRepair.SCOPE,),
        integrator=runtime.integrator,
        integrated_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
        prior_incident_ids=("incident-1",),
        overlap=OverlapClassification.PARTIAL_OVERLAP,
    )
    return candidate, consolidation


def _consolidation_proposal(
    runtime: RuleRuntime,
    consolidation,
):  # type: ignore[no-untyped-def]
    return ConsolidateBehavioralRule(
        proposal_id="proposal-consolidate-1",
        idempotency_key="intent-consolidate-1",
        proposer=runtime.integrator,
        approval=Approval(approver=runtime.approver, approved_at=NOW),
        classification=FIXED_RULE_CLASSIFICATION,
        consolidation=consolidation,
        measurement_id="measurement-rule-1",
        evaluator_audit_id="audit-rule-1",
        rollback_rule_version_id="rule-1-v1",
    )
