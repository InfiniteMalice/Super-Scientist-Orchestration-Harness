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
    rule_authority_rejection,
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
    BehavioralRuleVersion,
    OverlapClassification,
    RecurrenceRepair,
    RuleAction,
    RuleAuthority,
    RuleStatus,
)
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
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
from super_scientist.providers.storage.repositories import (
    PolicyRepository,
    RepositorySet,
    StorageIntegrityError,
)
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
    artifacts: FileArtifactStore
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
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    coordinator = TransactionCoordinator(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        AdvancingClock(),
        artifacts,
    )
    runtime = RuleRuntime(
        engine=engine,
        policy=snapshot,
        coordinator=coordinator,
        service=RuleService(coordinator),
        artifacts=artifacts,
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
    _seed_evidence("evidence-incident-1", rule_runtime)
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
        assert len(repositories.transactions.list_all()) == 8
        assert len(repositories.audit.list_all()) == 8


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
        assert verify_workspace(RepositorySet(connection), rule_runtime.artifacts).valid
        BehavioralRuleHeadRepository(connection).set(
            "rule-1",
            "rule-1-v1",
            "1.0.0",
            RuleStatus.UNDER_REVIEW,
        )
        tampered = verify_workspace(
            RepositorySet(connection),
            rule_runtime.artifacts,
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
def test_rule_proposal_approval_must_be_independent_of_incident_reporters(
    rule_runtime: RuleRuntime,
) -> None:
    reporter = actor("proposal-incident-reporter")
    _seed_evidence("evidence-incident-authority", rule_runtime)
    retained_incident = incident("incident-authority", reporter=reporter).model_copy(
        update={"governing_policy_hash": rule_runtime.policy.policy_hash}
    )
    assert rule_runtime.service.record_incident(
        _incident_proposal(retained_incident, rule_runtime)
    ).accepted
    candidate = rule(
        "rule-authority-v1",
        rule_id="rule-authority",
        incidents=("incident-authority",),
        statement="Retain authority-bearing incident reporters.",
        creator=actor("proposal-rule-creator"),
    ).model_copy(update={"governing_policy_hash": rule_runtime.policy.policy_hash})
    proposal = _rule_proposal(
        candidate,
        rule_runtime,
        proposal_id="proposal-rule-authority",
    ).model_copy(update={"approval": Approval(approver=reporter, approved_at=NOW)})

    decision = rule_runtime.service.propose_rule(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
def test_consolidation_requires_monotonic_semantic_version(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates={"semantic_version": "0.9.0"},
    )

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_consolidation_rejects_unrelated_non_head_predecessor(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    unrelated = rule(
        "rule-unrelated-v1",
        rule_id="rule-unrelated",
        statement="A disjoint rule must not be smuggled into lineage.",
        triggers=("an unrelated workflow runs",),
    ).model_copy(update={"governing_policy_hash": rule_runtime.policy.policy_hash})
    assert rule_runtime.service.propose_rule(
        _rule_proposal(
            unrelated,
            rule_runtime,
            proposal_id="proposal-rule-unrelated",
        )
    ).accepted
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates={"supersedes_rule_version_ids": ("rule-1-v1", "rule-unrelated-v1")},
    )

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_consolidation_allows_non_decreasing_authority_upgrade(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates={"authority": RuleAuthority.GOVERNANCE},
    )

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is True


@pytest.mark.integration
@pytest.mark.parametrize(
    "candidate_updates",
    (
        {"approved_at": NOW + timedelta(seconds=1)},
        {"created_at": NOW + timedelta(seconds=1)},
        {"created_at": NOW - timedelta(seconds=1)},
    ),
    ids=(
        "approval-timestamp-mismatch",
        "creation-after-approval",
        "creation-before-retained-history",
    ),
)
def test_consolidation_requires_exact_approval_timestamp_and_valid_chronology(
    rule_runtime: RuleRuntime,
    candidate_updates: dict[str, object],
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates=candidate_updates,
    )

    decision = rule_runtime.service.consolidate(
        _consolidation_proposal(rule_runtime, consolidation)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
def test_consolidation_rejects_candidate_created_after_its_measurement(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    after_measurement = NOW + timedelta(seconds=1)
    _, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates={
            "created_at": after_measurement,
            "approved_at": after_measurement,
        },
    )
    consolidation = consolidation.model_copy(update={"integrated_at": after_measurement})
    proposal = _consolidation_proposal(rule_runtime, consolidation).model_copy(
        update={
            "approval": Approval(
                approver=rule_runtime.approver,
                approved_at=after_measurement,
            )
        }
    )

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
@pytest.mark.parametrize(
    "late_artifact",
    ("incident", "rule-version", "review-proposal"),
)
def test_review_rejects_assessment_that_predates_each_reviewed_artifact(
    rule_runtime: RuleRuntime,
    late_artifact: str,
) -> None:
    _seed_rule_for_review_with_late_artifact(rule_runtime, late_artifact)
    assessment = _assessment(five_assessments()[0], rule_runtime)

    decision = rule_runtime.service.import_assessment(
        _assessment_proposal(
            assessment,
            rule_runtime,
            f"proposal-review-predates-{late_artifact}",
        )
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_followup_consolidation_must_advance_the_exact_current_head(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_active_rule(rule_runtime)
    _, proposal = _followup_consolidation(rule_runtime)

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is True
    with rule_runtime.engine.connect() as connection:
        assert BehavioralRuleHeadRepository(connection).get("rule-1") == (
            "rule-1-v3",
            "1.2.0",
            RuleStatus.ACTIVE,
        )


@pytest.mark.integration
def test_followup_consolidation_rejects_stale_predecessor_and_rollback(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_active_rule(rule_runtime)
    _, proposal = _followup_consolidation(
        rule_runtime,
        supersedes=("rule-1-v1",),
        rollback_rule_version_id="rule-1-v1",
    )

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_final_active_registry_rejects_exact_duplicate_candidate(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_active_rule(rule_runtime)
    _, proposal = _followup_consolidation(rule_runtime, exact_duplicate=True)

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.DUPLICATE_RULE


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overlap", "expected_accepted"),
    (
        (None, True),
        (OverlapClassification.NON_REDUNDANT, False),
        (OverlapClassification.EXACT_DUPLICATE, False),
    ),
)
def test_consolidation_overlap_must_match_authoritative_active_registry(
    rule_runtime: RuleRuntime,
    overlap: OverlapClassification | None,
    expected_accepted: bool,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    _, consolidation = _consolidation(rule_runtime, assessments)
    proposal = _consolidation_proposal(
        rule_runtime,
        consolidation.model_copy(update={"overlap": overlap}),
    )

    decision = rule_runtime.service.consolidate(proposal)

    assert decision.accepted is expected_accepted
    if not expected_accepted:
        assert decision.reasons[0].code is RejectionCode.UNRESOLVED_RULE_CONFLICT


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
            rule_runtime.artifacts,
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
            rule_runtime.artifacts,
        )
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_historically_accepted_fabricated_rule_evidence(
    rule_runtime: RuleRuntime,
) -> None:
    record = _incident("incident-fabricated-history", rule_runtime)
    proposal = _incident_proposal(record, rule_runtime)
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
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_historically_accepted_learned_rule_review(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_rule_for_review(rule_runtime)
    valid = _assessment(five_assessments()[0], rule_runtime)
    invalid = valid.model_copy(
        update={
            "provenance": valid.provenance.model_copy(
                update={"deterministic_or_learned": "LEARNED"}
            )
        }
    )
    proposal = _assessment_proposal(
        invalid,
        rule_runtime,
        "proposal-review-learned-history",
    )
    accepted = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    with DatabaseUnitOfWork(rule_runtime.engine) as unit_of_work:
        repositories = unit_of_work.repositories()
        connection = unit_of_work.connection
        assert connection is not None
        ReviewerAssessmentRepository(connection).add(
            invalid.assessment_id,
            invalid,
            invalid.provenance.assessed_at,
        )
        repositories.transactions.add(proposal, accepted, NOW + timedelta(seconds=20))
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
                NOW + timedelta(seconds=21),
            )
        )

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_historically_accepted_stale_rule_head(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_active_rule(rule_runtime)
    candidate, proposal = _followup_consolidation(
        rule_runtime,
        supersedes=("rule-1-v1",),
        rollback_rule_version_id="rule-1-v1",
    )
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
        for regression_case in proposal.consolidation.regression_cases:
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
        repositories.transactions.add(proposal, accepted, NOW + timedelta(seconds=60))
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
                NOW + timedelta(seconds=61),
            )
        )

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)
    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_candidate_created_after_its_measurement(
    rule_runtime: RuleRuntime,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    after_measurement = NOW + timedelta(seconds=1)
    candidate, consolidation = _consolidation(
        rule_runtime,
        assessments,
        candidate_updates={
            "created_at": after_measurement,
            "approved_at": after_measurement,
        },
    )
    consolidation = consolidation.model_copy(update={"integrated_at": after_measurement})
    proposal = _consolidation_proposal(rule_runtime, consolidation).model_copy(
        update={
            "approval": Approval(
                approver=rule_runtime.approver,
                approved_at=after_measurement,
            )
        }
    )
    _persist_forged_accepted_consolidation(rule_runtime, candidate, proposal)

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)

    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_workspace_replay_rejects_assessment_that_predates_reviewed_history(
    rule_runtime: RuleRuntime,
) -> None:
    _seed_rule_for_review(rule_runtime)
    valid = _assessment(five_assessments()[0], rule_runtime)
    invalid = valid.model_copy(
        update={
            "provenance": valid.provenance.model_copy(
                update={"assessed_at": NOW - timedelta(seconds=1)}
            )
        }
    )
    proposal = _assessment_proposal(
        invalid,
        rule_runtime,
        "proposal-review-predates-history",
    )
    accepted = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    with DatabaseUnitOfWork(rule_runtime.engine) as unit_of_work:
        repositories = unit_of_work.repositories()
        connection = unit_of_work.connection
        assert connection is not None
        ReviewerAssessmentRepository(connection).add(
            invalid.assessment_id,
            invalid,
            invalid.provenance.assessed_at,
        )
        repositories.transactions.add(proposal, accepted, NOW + timedelta(seconds=20))
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
                NOW + timedelta(seconds=21),
            )
        )

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)

    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
@pytest.mark.parametrize(
    "forged_overlap",
    (
        OverlapClassification.NON_REDUNDANT,
        OverlapClassification.EXACT_DUPLICATE,
    ),
)
def test_workspace_replay_rejects_caller_mutated_consolidation_overlap(
    rule_runtime: RuleRuntime,
    forged_overlap: OverlapClassification,
) -> None:
    assessments = _seed_complete_review(rule_runtime)
    _seed_measurement(rule_runtime)
    candidate, consolidation = _consolidation(rule_runtime, assessments)
    proposal = _consolidation_proposal(
        rule_runtime,
        consolidation.model_copy(update={"overlap": forged_overlap}),
    )
    _persist_forged_accepted_consolidation(rule_runtime, candidate, proposal)

    with rule_runtime.engine.connect() as connection:
        result = verify_workspace(RepositorySet(connection), rule_runtime.artifacts)

    assert result.valid is False
    assert "behavioral-rule historical authority" in (result.reason or "")


@pytest.mark.integration
def test_rule_incident_requires_retained_hash_verified_primary_evidence(
    rule_runtime: RuleRuntime,
) -> None:
    fabricated = rule_runtime.service.record_incident(
        _incident_proposal(_incident("incident-fabricated", rule_runtime), rule_runtime)
    )
    assert fabricated.accepted is False
    assert fabricated.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING

    _seed_evidence(
        "evidence-incident-non-primary",
        rule_runtime,
        grounding=ExternalGrounding.HUMAN_JUDGMENT,
    )
    non_primary = rule_runtime.service.record_incident(
        _incident_proposal(_incident("incident-non-primary", rule_runtime), rule_runtime)
    )
    assert non_primary.accepted is False
    assert non_primary.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING

    _seed_evidence("evidence-incident-primary", rule_runtime)
    primary = rule_runtime.service.record_incident(
        _incident_proposal(_incident("incident-primary", rule_runtime), rule_runtime)
    )
    assert primary.accepted is True


@pytest.mark.integration
def test_rule_incident_fails_closed_when_retained_artifact_is_tampered(
    rule_runtime: RuleRuntime,
) -> None:
    evidence = _seed_evidence("evidence-incident-tampered", rule_runtime)
    rule_runtime.artifacts.resolve(evidence.artifact).write_bytes(b"tampered evidence")

    with pytest.raises(StorageIntegrityError, match="artifact"):
        rule_runtime.service.record_incident(
            _incident_proposal(_incident("incident-tampered", rule_runtime), rule_runtime)
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "provenance_update",
    (
        {"category": VerificationLevel.SELF_CRITIQUE},
        {"deterministic_or_learned": "LEARNED"},
        {"result": AssessmentOutcome.FAILED},
        {"checks_run": ("untrusted-review-mechanism",)},
        {"evidence_ids": ("evidence-incident-1",)},
    ),
    ids=("self-critique", "learned", "failed", "bad-check-binding", "bad-evidence-binding"),
)
def test_reviewer_import_requires_exact_deterministic_passed_provenance(
    rule_runtime: RuleRuntime,
    provenance_update: dict[str, object],
) -> None:
    _seed_rule_for_review(rule_runtime)
    original = _assessment(five_assessments()[0], rule_runtime)
    invalid = original.model_copy(
        update={
            "provenance": original.provenance.model_copy(update=provenance_update),
        }
    )

    decision = rule_runtime.service.import_assessment(
        _assessment_proposal(invalid, rule_runtime, "proposal-invalid-review")
    )

    assert decision.accepted is False
    assert decision.reasons[0].code in {
        RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        RejectionCode.INSUFFICIENT_GROUNDING,
    }


def test_promotion_only_protected_evaluation_and_rollback_do_not_block_staging(
    rule_runtime: RuleRuntime,
) -> None:
    strict_requirement = (
        _policy()
        .adaptation_requirements[0]
        .model_copy(
            update={
                "protected_evaluation_required": True,
                "rollback_required": True,
            }
        )
    )
    strict_policy = _policy().model_copy(
        update={
            "adaptation_requirements": (
                strict_requirement,
                _policy().adaptation_requirements[1],
            )
        }
    )
    strict_snapshot = PolicySnapshot(
        policy_hash=policy_hash(strict_policy),
        policy=strict_policy,
    )
    incident_record = _incident("incident-staging", rule_runtime)
    staged_proposals = (
        _incident_proposal(incident_record, rule_runtime),
        _rule_proposal(_rule("rule-staging-v1", rule_runtime), rule_runtime),
        _assessment_proposal(
            _assessment(five_assessments()[0], rule_runtime),
            rule_runtime,
            "proposal-review-staging",
        ),
    )

    assert all(
        rule_authority_rejection(proposal, strict_snapshot) is None for proposal in staged_proposals
    )

    assessments = tuple(_assessment(item, rule_runtime) for item in five_assessments())
    _, consolidation = _consolidation(rule_runtime, assessments)
    promotion_rejection = rule_authority_rejection(
        _consolidation_proposal(rule_runtime, consolidation),
        strict_snapshot,
    )
    assert promotion_rejection is not None
    assert promotion_rejection.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


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
        _seed_evidence(f"evidence-{identifier}", runtime)
        assert runtime.service.record_incident(
            _incident_proposal(_incident(identifier, runtime), runtime)
        ).accepted
    assert runtime.service.propose_rule(
        _rule_proposal(_rule("rule-1-v1", runtime), runtime)
    ).accepted


def _seed_rule_for_review_with_late_artifact(
    runtime: RuleRuntime,
    late_artifact: str,
) -> None:
    after_review = NOW + timedelta(seconds=1)
    for identifier in ("incident-1", "incident-2"):
        _seed_evidence(f"evidence-{identifier}", runtime)
        record = _incident(identifier, runtime)
        if late_artifact == "incident" and identifier == "incident-1":
            record = record.model_copy(update={"recorded_at": after_review})
        assert runtime.service.record_incident(_incident_proposal(record, runtime)).accepted
    reviewed_rule = _rule("rule-1-v1", runtime)
    if late_artifact == "rule-version":
        reviewed_rule = reviewed_rule.model_copy(update={"created_at": after_review})
    proposal = _rule_proposal(reviewed_rule, runtime)
    if late_artifact == "review-proposal":
        proposal = proposal.model_copy(
            update={
                "approval": Approval(
                    approver=runtime.approver,
                    approved_at=after_review,
                )
            }
        )
    assert runtime.service.propose_rule(proposal).accepted


def _seed_evidence(
    evidence_id: str,
    runtime: RuleRuntime,
    *,
    grounding: ExternalGrounding = ExternalGrounding.PRIMARY_SOURCE,
) -> EvidenceRecord:
    data = f"Retained primary evidence for {evidence_id}.".encode()
    record = EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="rule-incident-source",
        source_locator=f"fixture://{evidence_id}",
        retrieved_at=NOW,
        artifact=runtime.artifacts.put(data, "text/plain"),
        provenance={
            "collector": "rule-service-test",
            "external_grounding": grounding.value,
        },
        ingestion_actor_id=runtime.integrator.actor_id,
        verification_state=VerificationState.UNVERIFIED,
    )
    decision = runtime.coordinator.submit(
        AddEvidence(
            proposal_id=f"proposal-{evidence_id}",
            idempotency_key=f"intent-{evidence_id}",
            proposer=runtime.integrator,
            evidence=record,
        )
    )
    assert decision.accepted
    return record


def _seed_complete_review(runtime: RuleRuntime):  # type: ignore[no-untyped-def]
    _seed_rule_for_review(runtime)
    values = tuple(_assessment(item, runtime) for item in five_assessments())
    for item in values:
        assert runtime.service.import_assessment(
            _assessment_proposal(item, runtime, f"proposal-review-{item.role.value.lower()}")
        ).accepted
    return values


def _seed_active_rule(runtime: RuleRuntime):  # type: ignore[no-untyped-def]
    assessments = _seed_complete_review(runtime)
    _seed_measurement(runtime)
    candidate, consolidation = _consolidation(runtime, assessments)
    assert runtime.service.consolidate(_consolidation_proposal(runtime, consolidation)).accepted
    return candidate


def _followup_consolidation(
    runtime: RuleRuntime,
    *,
    supersedes: tuple[str, ...] = ("rule-1-v2",),
    rollback_rule_version_id: str = "rule-1-v2",
    exact_duplicate: bool = False,
):  # type: ignore[no-untyped-def]
    _seed_evidence("evidence-incident-3", runtime)
    assert runtime.service.record_incident(
        _incident_proposal(_incident("incident-3", runtime), runtime)
    ).accepted
    followup_approver = actor("followup-rule-approver")
    reviewed = rule(
        "rule-1-review-v3",
        semantic_version="1.1.1",
        incidents=("incident-1", "incident-2", "incident-3"),
        statement="Review a follow-up rule with the newly retained recurrence.",
        creator=actor("followup-rule-author"),
        supersedes=("rule-1-v2",),
    ).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})
    reviewed_proposal = _rule_proposal(
        reviewed,
        runtime,
        proposal_id="proposal-rule-followup",
    ).model_copy(update={"approval": Approval(approver=followup_approver, approved_at=NOW)})
    assert runtime.service.propose_rule(reviewed_proposal).accepted

    assessments = tuple(
        _assessment(item, runtime).model_copy(
            update={
                "assessment_id": f"{item.assessment_id}-followup",
                "proposal_id": reviewed_proposal.proposal_id,
                "rule_version_ids": (reviewed.rule_version_id,),
                "incident_ids": ("incident-1", "incident-2", "incident-3"),
                "regression_test_ids": (
                    "test-incident-1",
                    "test-incident-2",
                    "test-incident-3",
                ),
                "provenance": _assessment(item, runtime).provenance.model_copy(
                    update={
                        "evidence_ids": (
                            "evidence-incident-1",
                            "evidence-incident-2",
                            "evidence-incident-3",
                        )
                    }
                ),
            }
        )
        for item in five_assessments()
    )
    for item in assessments:
        proposal = _assessment_proposal(
            item,
            runtime,
            f"proposal-review-followup-{item.role.value.lower()}",
        ).model_copy(update={"approval": Approval(approver=followup_approver, approved_at=NOW)})
        assert runtime.service.import_assessment(proposal).accepted

    audit = _audit(runtime)
    measurement = _measurement(runtime, audit).model_copy(
        update={
            "measurement_id": "measurement-rule-followup",
            "baseline_version_id": rollback_rule_version_id,
            "candidate_version_id": "rule-1-v3",
            "rollback_target_id": rollback_rule_version_id,
            "decision_authority": followup_approver,
        }
    )
    assert runtime.coordinator.submit(
        RecordSelfImprovementMeasurement(
            proposal_id="proposal-measurement-rules-followup",
            idempotency_key="intent-measurement-rules-followup",
            proposer=measurement.proposer,
            approval=Approval(approver=followup_approver, approved_at=NOW),
            measurement=measurement,
        )
    ).accepted

    candidate = rule(
        "rule-1-v3",
        semantic_version="1.2.0",
        incidents=("incident-1", "incident-2", "incident-3"),
        statement=(
            rule().canonical_statement
            if exact_duplicate
            else "Retain the current head and every newly reviewed recurrence."
        ),
        status=RuleStatus.ACTIVE,
        creator=runtime.integrator,
        approver=followup_approver,
        supersedes=supersedes,
    ).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})
    cases = tuple(
        regression(
            f"regression-followup-{index}",
            incident_id,
            rule_version_id=candidate.rule_version_id,
            creator=runtime.integrator,
        ).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})
        for index, incident_id in enumerate(
            ("incident-1", "incident-2", "incident-3"),
            start=1,
        )
    )
    consolidation = build_candidate_diff(
        consolidation_decision_id="decision-followup",
        review_proposal_id=reviewed_proposal.proposal_id,
        assessments=assessments,
        candidate_rule=candidate,
        regression_cases=cases,
        action=RuleAction.ACCEPT_WITH_REVISION,
        recommendation_dispositions=dispositions(assessments),
        separating_variable=None,
        recurrence_incident_ids=("incident-3",),
        recurrence_repairs=(RecurrenceRepair.SCOPE,),
        integrator=runtime.integrator,
        integrated_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
        prior_incident_ids=("incident-1", "incident-2"),
        overlap=OverlapClassification.SEMANTIC_DUPLICATE,
    )
    return candidate, ConsolidateBehavioralRule(
        proposal_id="proposal-consolidate-followup",
        idempotency_key="intent-consolidate-followup",
        proposer=runtime.integrator,
        approval=Approval(approver=followup_approver, approved_at=NOW),
        classification=FIXED_RULE_CLASSIFICATION,
        consolidation=consolidation,
        measurement_id=measurement.measurement_id,
        evaluator_audit_id=audit.evaluator_audit_id,
        rollback_rule_version_id=rollback_rule_version_id,
    )


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
            "decided_at": NOW,
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )


def _consolidation(
    runtime: RuleRuntime,
    assessments,
    *,
    candidate_updates: dict[str, object] | None = None,
):  # type: ignore[no-untyped-def]
    candidate = rule(
        "rule-1-v2",
        semantic_version="1.1.0",
        incidents=("incident-1", "incident-2"),
        status=RuleStatus.ACTIVE,
        creator=runtime.integrator,
        approver=runtime.approver,
        supersedes=("rule-1-v1",),
    ).model_copy(update={"governing_policy_hash": runtime.policy.policy_hash})
    if candidate_updates:
        candidate = candidate.model_copy(update=candidate_updates)
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
        overlap=None,
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


def _persist_forged_accepted_consolidation(
    runtime: RuleRuntime,
    candidate: BehavioralRuleVersion,
    proposal: ConsolidateBehavioralRule,
) -> None:
    accepted = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    decision = rule_consolidation_decision(proposal)
    with DatabaseUnitOfWork(runtime.engine) as unit_of_work:
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
        for regression_case in proposal.consolidation.regression_cases:
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
                    "policy_hash": runtime.policy.policy_hash,
                    "stored_policy_hash": runtime.policy.policy_hash,
                    "configured_policy_hash": runtime.policy.policy_hash,
                    "transaction_persisted": True,
                },
                NOW + timedelta(seconds=31),
            )
        )
