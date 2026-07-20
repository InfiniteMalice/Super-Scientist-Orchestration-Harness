from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

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
from super_scientist.providers.storage.repositories import TransactionRepository

type FixedRuleHandler = ProposalHandler[BaseModel, BaseModel]


@dataclass(frozen=True)
class RuleIncidentCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, RuleIncident):
            raise TypeError(f"unsupported rule incident record: {type(record)!r}")
        if self.incidents.get(record.incident_id) == record:
            return
        self.incidents.add(record.incident_id, record, record.recorded_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("rule incidents have no mutable projection")


@dataclass(frozen=True)
class RuleProposalCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository
    rules: BehavioralRuleVersionRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.rules.get(rule_version_id)

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]:
        return self.rules.list_all()

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, BehavioralRuleVersion):
            raise TypeError(f"unsupported behavioral rule proposal: {type(record)!r}")
        if self.rules.get(record.rule_version_id) == record:
            return
        self.rules.add(record.rule_version_id, record, record.created_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("only the rule integrator may update rule heads")


@dataclass(frozen=True)
class RuleReviewerImportCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository
    rules: BehavioralRuleVersionRepository
    assessments: ReviewerAssessmentRepository
    transactions: TransactionRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.rules.get(rule_version_id)

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self.assessments.get(assessment_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        stored = self.transactions.get_by_proposal_id(proposal_id)
        if (
            stored is None
            or not stored.decision.accepted
            or not isinstance(stored.proposal, ProposeBehavioralRule)
        ):
            return None
        return stored.proposal

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, ReviewerAssessment):
            raise TypeError(f"unsupported reviewer assessment record: {type(record)!r}")
        if self.assessments.get(record.assessment_id) == record:
            return
        self.assessments.add(
            record.assessment_id,
            record,
            record.provenance.assessed_at,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("reviewers cannot update canonical rule heads")


@dataclass(frozen=True)
class RuleIntegratorCapabilities:
    active_policy: PolicySnapshot
    incidents: RuleIncidentRepository
    rules: BehavioralRuleVersionRepository
    assessments: ReviewerAssessmentRepository
    decisions: RuleConsolidationDecisionRepository
    regressions: RuleRegressionCaseRepository
    head: BehavioralRuleHeadRepository
    measurements: SelfImprovementMeasurementRepository
    evaluator_audits: EvaluatorAuditRepository
    transactions: TransactionRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.rules.get(rule_version_id)

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self.assessments.get(assessment_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        stored = self.transactions.get_by_proposal_id(proposal_id)
        if (
            stored is None
            or not stored.decision.accepted
            or not isinstance(stored.proposal, ProposeBehavioralRule)
        ):
            return None
        return stored.proposal

    def get_decision(self, decision_id: str) -> RuleConsolidationDecision | None:
        return self.decisions.get(decision_id)

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self.evaluator_audits.get(audit_id)

    def get_head(self, rule_id: str) -> tuple[str, str, RuleStatus] | None:
        return self.head.get(rule_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, BehavioralRuleVersion):
            self.rules.add(record.rule_version_id, record, record.created_at)
            return
        if isinstance(record, RuleConsolidationDecision):
            self.decisions.add(
                record.consolidation_decision_id,
                record,
                record.decided_at,
            )
            return
        if isinstance(record, RuleRegressionCase):
            self.regressions.add(
                record.regression_case_id,
                record,
                record.created_at,
            )
            return
        raise TypeError(f"unsupported rule integrator record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, BehavioralRuleVersion):
            raise TypeError(f"unsupported rule head projection: {type(record)!r}")
        self.head.set(
            record.rule_id,
            record.rule_version_id,
            record.semantic_version,
            record.status,
        )


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
) -> (
    RuleIncidentCapabilities
    | RuleProposalCapabilities
    | RuleReviewerImportCapabilities
    | RuleIntegratorCapabilities
):
    if isinstance(proposal, RecordRuleIncident):
        return RuleIncidentCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
        )
    if isinstance(proposal, ProposeBehavioralRule):
        return RuleProposalCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
            rules=BehavioralRuleVersionRepository(connection),
        )
    if isinstance(proposal, ImportReviewerAssessment):
        return RuleReviewerImportCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
            rules=BehavioralRuleVersionRepository(connection),
            assessments=ReviewerAssessmentRepository(connection),
            transactions=TransactionRepository(connection),
        )
    if isinstance(proposal, ConsolidateBehavioralRule):
        return RuleIntegratorCapabilities(
            active_policy=active_policy,
            incidents=RuleIncidentRepository(connection),
            rules=BehavioralRuleVersionRepository(connection),
            assessments=ReviewerAssessmentRepository(connection),
            decisions=RuleConsolidationDecisionRepository(connection),
            regressions=RuleRegressionCaseRepository(connection),
            head=BehavioralRuleHeadRepository(connection),
            measurements=SelfImprovementMeasurementRepository(connection),
            evaluator_audits=EvaluatorAuditRepository(connection),
            transactions=TransactionRepository(connection),
        )
    raise TypeError(f"no fixed behavioral-rule capability for proposal: {type(proposal)!r}")
