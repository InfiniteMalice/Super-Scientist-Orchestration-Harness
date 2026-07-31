from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.rules.service import (
    ConsolidateBehavioralRuleHandler,
    ImportReviewerAssessmentHandler,
    ProposeBehavioralRuleHandler,
    RecordRuleIncidentHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ReviewerAssessment,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.kernel.transactions.models import (
    ConsolidateBehavioralRule,
    ImportReviewerAssessment,
    ProposeBehavioralRule,
    RecordRuleIncident,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.domain_records import (
    BehavioralRuleHeadRepository,
    BehavioralRuleVersionRepository,
    EvaluatorAuditRepository,
    ReviewerAssessmentRepository,
    RuleConsolidationDecisionRepository,
    RuleIncidentRepository,
    RuleRegressionCaseRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.repositories import (
    EvidenceRepository,
    TransactionRepository,
)

type FixedRuleHandler = ProposalHandler[BaseModel, BaseModel]


@dataclass(frozen=True)
class RetainedRuleEvidenceReader:
    """Narrow, read-only access to artifact-verified retained evidence."""

    _evidence: EvidenceRepository
    _artifacts: ArtifactStore

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        record = self._evidence.get(evidence_id)
        if record is not None:
            verify_artifact_binding(record, self._artifacts)
        return record


@dataclass(frozen=True)
class RuleReviewerReadFacade:
    """Read authority available while importing an independent review."""

    _incidents: RuleIncidentRepository
    _rules: BehavioralRuleVersionRepository
    _assessments: ReviewerAssessmentRepository
    _transactions: TransactionRepository
    _evidence: RetainedRuleEvidenceReader

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self._incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self._rules.get(rule_version_id)

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self._assessments.get(assessment_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self._evidence.get_retained_evidence(evidence_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        stored = self._transactions.get_by_proposal_id(proposal_id)
        if (
            stored is None
            or not stored.decision.accepted
            or not isinstance(stored.proposal, ProposeBehavioralRule)
        ):
            return None
        return stored.proposal


@dataclass(frozen=True)
class ReviewerAssessmentWriter:
    """The reviewer's only durable write authority."""

    _assessments: ReviewerAssessmentRepository

    def append_assessment(self, record: ReviewerAssessment) -> None:
        if self._assessments.get(record.assessment_id) == record:
            return
        self._assessments.add(
            record.assessment_id,
            record,
            record.provenance.assessed_at,
        )


@dataclass(frozen=True)
class RuleIntegratorReadFacade:
    """Read-only rule registry, review, measurement, and history authority."""

    _incidents: RuleIncidentRepository
    _rules: BehavioralRuleVersionRepository
    _assessments: ReviewerAssessmentRepository
    _decisions: RuleConsolidationDecisionRepository
    _measurements: SelfImprovementMeasurementRepository
    _evaluator_audits: EvaluatorAuditRepository
    _transactions: TransactionRepository
    _head: BehavioralRuleHeadRepository
    _evidence: RetainedRuleEvidenceReader

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self._incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self._rules.get(rule_version_id)

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]:
        return self._rules.list_all()

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self._assessments.get(assessment_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self._evidence.get_retained_evidence(evidence_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        stored = self._transactions.get_by_proposal_id(proposal_id)
        if (
            stored is None
            or not stored.decision.accepted
            or not isinstance(stored.proposal, ProposeBehavioralRule)
        ):
            return None
        return stored.proposal

    def get_decision(self, decision_id: str) -> RuleConsolidationDecision | None:
        return self._decisions.get(decision_id)

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        return self._measurements.get(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self._evaluator_audits.get(audit_id)

    def get_head(self, rule_id: str) -> tuple[str, str, RuleStatus] | None:
        return self._head.get(rule_id)

    def list_heads(self) -> tuple[tuple[str, str, str, RuleStatus], ...]:
        return self._head.list_all()


@dataclass(frozen=True)
class RuleIntegratorWriter:
    """Role-specific canonical rule writes; no repository is exposed."""

    _rules: BehavioralRuleVersionRepository
    _decisions: RuleConsolidationDecisionRepository
    _regressions: RuleRegressionCaseRepository
    _head: BehavioralRuleHeadRepository

    def append_rule(self, record: BehavioralRuleVersion) -> None:
        self._rules.add(record.rule_version_id, record, record.created_at)

    def append_decision(self, record: RuleConsolidationDecision) -> None:
        self._decisions.add(
            record.consolidation_decision_id,
            record,
            record.decided_at,
        )

    def append_regression(self, record: RuleRegressionCase) -> None:
        self._regressions.add(
            record.regression_case_id,
            record,
            record.created_at,
        )

    def set_rule_head(self, record: BehavioralRuleVersion) -> None:
        self._head.set(
            record.rule_id,
            record.rule_version_id,
            record.semantic_version,
            record.status,
        )


@dataclass(frozen=True)
class RuleIncidentCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository
    evidence: RetainedRuleEvidenceReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.evidence.get_retained_evidence(evidence_id)

    def append_incident(self, record: RuleIncident) -> None:
        if self.incidents.get(record.incident_id) == record:
            return
        self.incidents.add(record.incident_id, record, record.recorded_at)


@dataclass(frozen=True)
class RuleProposalCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository
    rules: BehavioralRuleVersionRepository
    evidence: RetainedRuleEvidenceReader

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.rules.get(rule_version_id)

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]:
        return self.rules.list_all()

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.evidence.get_retained_evidence(evidence_id)

    def append_rule_proposal(self, record: BehavioralRuleVersion) -> None:
        if self.rules.get(record.rule_version_id) == record:
            return
        self.rules.add(record.rule_version_id, record, record.created_at)


@dataclass(frozen=True)
class RuleReviewerImportCapabilities:
    active_policy: PolicySnapshot
    reader: RuleReviewerReadFacade
    writer: ReviewerAssessmentWriter

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.reader.get_incident(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.reader.get_rule(rule_version_id)

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self.reader.get_assessment(assessment_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.reader.get_retained_evidence(evidence_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        return self.reader.reviewed_rule_proposal(proposal_id)

    def append_assessment(self, record: ReviewerAssessment) -> None:
        self.writer.append_assessment(record)


@dataclass(frozen=True)
class RuleIntegratorCapabilities:
    active_policy: PolicySnapshot
    reader: RuleIntegratorReadFacade
    writer: RuleIntegratorWriter

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.reader.get_incident(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.reader.get_rule(rule_version_id)

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]:
        return self.reader.list_rules()

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self.reader.get_assessment(assessment_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.reader.get_retained_evidence(evidence_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        return self.reader.reviewed_rule_proposal(proposal_id)

    def get_decision(self, decision_id: str) -> RuleConsolidationDecision | None:
        return self.reader.get_decision(decision_id)

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        return self.reader.get_measurement(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self.reader.get_evaluator_audit(audit_id)

    def get_head(self, rule_id: str) -> tuple[str, str, RuleStatus] | None:
        return self.reader.get_head(rule_id)

    def list_heads(self) -> tuple[tuple[str, str, str, RuleStatus], ...]:
        return self.reader.list_heads()

    def append_rule_version(self, record: BehavioralRuleVersion) -> None:
        self.writer.append_rule(record)

    def append_decision(self, record: RuleConsolidationDecision) -> None:
        self.writer.append_decision(record)

    def append_regression(self, record: RuleRegressionCase) -> None:
        self.writer.append_regression(record)

    def set_rule_head(self, record: BehavioralRuleVersion) -> None:
        self.writer.set_rule_head(record)


def fixed_rule_handlers() -> tuple[FixedRuleHandler, ...]:
    return (  # type: ignore[return-value]
        RecordRuleIncidentHandler(),
        ProposeBehavioralRuleHandler(),
        ImportReviewerAssessmentHandler(),
        ConsolidateBehavioralRuleHandler(),
    )


def rule_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    artifact_store: ArtifactStore,
) -> (
    RuleIncidentCapabilities
    | RuleProposalCapabilities
    | RuleReviewerImportCapabilities
    | RuleIntegratorCapabilities
):
    evidence = RetainedRuleEvidenceReader(
        _evidence=EvidenceRepository(connection),
        _artifacts=artifact_store,
    )
    if isinstance(proposal, RecordRuleIncident):
        return RuleIncidentCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
            evidence=evidence,
        )
    if isinstance(proposal, ProposeBehavioralRule):
        return RuleProposalCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
            rules=BehavioralRuleVersionRepository(connection),
            evidence=evidence,
        )
    if isinstance(proposal, ImportReviewerAssessment):
        incidents = RuleIncidentRepository(connection)
        rules = BehavioralRuleVersionRepository(connection)
        assessments = ReviewerAssessmentRepository(connection)
        return RuleReviewerImportCapabilities(
            active_policy=active_policy,
            reader=RuleReviewerReadFacade(
                _incidents=incidents,
                _rules=rules,
                _assessments=assessments,
                _transactions=TransactionRepository(connection),
                _evidence=evidence,
            ),
            writer=ReviewerAssessmentWriter(_assessments=assessments),
        )
    if isinstance(proposal, ConsolidateBehavioralRule):
        incidents = RuleIncidentRepository(connection)
        rules = BehavioralRuleVersionRepository(connection)
        assessments = ReviewerAssessmentRepository(connection)
        decisions = RuleConsolidationDecisionRepository(connection)
        regressions = RuleRegressionCaseRepository(connection)
        head = BehavioralRuleHeadRepository(connection)
        return RuleIntegratorCapabilities(
            active_policy=active_policy,
            reader=RuleIntegratorReadFacade(
                _incidents=incidents,
                _rules=rules,
                _assessments=assessments,
                _decisions=decisions,
                _measurements=SelfImprovementMeasurementRepository(connection),
                _evaluator_audits=EvaluatorAuditRepository(connection),
                _transactions=TransactionRepository(connection),
                _head=head,
                _evidence=evidence,
            ),
            writer=RuleIntegratorWriter(
                _rules=rules,
                _decisions=decisions,
                _regressions=regressions,
                _head=head,
            ),
        )
    raise TypeError(f"no fixed behavioral-rule capability for proposal: {type(proposal)!r}")
