from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from super_scientist.application.evidence_verification import (
    verified_artifact_bytes,
    verify_artifact_binding,
)
from super_scientist.application.representations.records import (
    primitive_evaluation_to_storage,
    primitive_version_from_storage,
    primitive_version_to_storage,
)
from super_scientist.application.representations.service import (
    primitive_use_rejection,
    projected_primitive_status,
)
from super_scientist.application.rules.service import (
    ConsolidateBehavioralRuleHandler,
    ImportReviewerAssessmentHandler,
    ProposeBehavioralRuleHandler,
    RecordRuleIncidentHandler,
    RuleMutationProposal,
    rule_consolidation_decision,
)
from super_scientist.application.trails.receipts import (
    AcceptedProposalReceipt,
    accepted_proposal_receipts,
)
from super_scientist.application.trails.service import (
    FIXED_TRAIL_CLASSIFICATION,
    trail_authority_rejection,
    trail_receipt_rejection,
)
from super_scientist.application.transactions.contracts import HandlerWriteCapability
from super_scientist.application.transactions.hypotheses import (
    AdmitHypothesisHandler,
    HypothesisReceipt,
    ProposeHypothesisVersionHandler,
    RecordCounterexampleHandler,
    RecordSimulationResultHandler,
    RecordVerificationResultHandler,
    RegisterExecutableModelHandler,
    RegisterVerificationMechanismHandler,
    ReviseHypothesisHandler,
    counterexample_to_storage,
    hypothesis_receipts,
    hypothesis_to_storage,
    mechanism_to_storage,
    model_to_storage,
    revision_to_storage,
    simulation_to_storage,
    verification_to_storage,
)
from super_scientist.application.transactions.representations import (
    AdmitPrimitiveVersionHandler,
    ProposePrimitiveVersionHandler,
    RecordPrimitiveEvaluationHandler,
    RepresentationReceipt,
    representation_receipts,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ReviewerAssessment,
    RuleAction,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.evidence_trails.authority import (
    canonical_node_set_hash,
    parse_external_grounding,
)
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    RetainedEvidenceSource,
    TrailAssessment,
    TrailCheckResult,
    TrailOutcome,
    TrailReceiptRef,
    TrailValidationInputs,
)
from super_scientist.domain.evidence_trails.validation import (
    validate_report_binding,
    validate_trail,
)
from super_scientist.domain.hypotheses.models import (
    AcceptedHypothesisReceiptRef,
    ExecutableModelSpec,
    HypothesisSpec,
    RevisionRecord,
    SimulationResult,
    VerificationMechanismSpec,
    VerificationResult,
)
from super_scientist.domain.hypotheses.models import (
    CounterexampleRecord as DomainCounterexampleRecord,
)
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.classification import (
    ExternalGrounding,
    is_authoritative_verification,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import Sha256Hex, canonical_json_bytes
from super_scientist.domain.progress.calculations import (
    calculate_progress,
    current_progress_plan,
    detect_false_finish,
    event_advances_progress_head,
    has_unused_budget,
    is_canonical_artifact_ref,
    remaining_budget,
    replay_pending_dependency_ids,
    select_checkpoint_budget,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    FalseFinishResult,
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
    TerminationReason,
    progress_actors_are_independent,
)
from super_scientist.domain.representations.models import (
    AcceptedPrimitiveReceiptRef,
    PrimitiveEvaluation,
    PrimitiveStatus,
    PrimitiveUse,
    PrimitiveVersion,
    PrimitiveVersionReceiptRef,
)
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome
from super_scientist.kernel.audit.models import (
    AuditEvent,
    AuditVerification,
    json_compatible_payload,
)
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AdmitHypothesis,
    AdmitPrimitiveVersion,
    AppendProgressEvent,
    AppendResearchRunEvent,
    BindReportSentence,
    ConsolidateBehavioralRule,
    CreateResearchRun,
    DecideCompletion,
    DecideEvaluatorSuccession,
    ImportReviewerAssessment,
    Proposal,
    ProposeBehavioralRule,
    ProposeClaim,
    ProposeEvaluatorVersion,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    ProposeGovernancePolicyTransition,
    ProposeHypothesisVersion,
    ProposePrimitiveVersion,
    RecordConfigurationVersion,
    RecordCounterexample,
    RecordEvaluatorAudit,
    RecordEvidenceTrailVersion,
    RecordPrimitiveEvaluation,
    RecordProgressPlan,
    RecordRuleIncident,
    RecordRunBudget,
    RecordRunCheckpoint,
    RecordSelfImprovementMeasurement,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.domain_records import (
    CounterexampleRecord,
    ExecutableModelSpecRecord,
    HypothesisAdmissionDecisionRecord,
    HypothesisAdmissionStatus,
    HypothesisRevisionRecord,
    HypothesisVersionRecord,
    PrimitiveEvaluationRecord,
    PrimitiveVersionRecord,
    SimulationResultRecord,
    VerificationMechanismSpecRecord,
    VerificationResultRecord,
)
from super_scientist.providers.storage.domain_records import (
    PrimitiveStatus as StoredPrimitiveStatus,
)
from super_scientist.providers.storage.integrity_records import (
    AdaptationIntegritySnapshot,
    HypothesisIntegritySnapshot,
    ProgressIntegritySnapshot,
    RepresentationIntegritySnapshot,
    RuleIntegritySnapshot,
    TrailIntegritySnapshot,
)
from super_scientist.providers.storage.repositories import (
    RepositorySet,
    StorageIntegrityError,
    StoredTransaction,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


@dataclass(frozen=True)
class _ValidatedAuditRecord:
    event: AuditEvent
    proposal: Proposal
    decision: TransactionDecision
    intent_fingerprint: str | None
    transaction_persisted: bool
    governing_policy_hash: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class _RuleReplayCapability:
    active_policy: PolicySnapshot
    evidence: Mapping[str, EvidenceRecord]
    artifact_store: ArtifactStore
    incidents: Mapping[str, RuleIncident]
    rules: Mapping[str, BehavioralRuleVersion]
    assessments: Mapping[str, ReviewerAssessment]
    decisions: Mapping[str, RuleConsolidationDecision]
    measurements: Mapping[str, SelfImprovementMeasurementRecord]
    evaluator_audits: Mapping[str, EvaluatorAuditRecord]
    heads: Mapping[str, tuple[str, str, RuleStatus]]
    reviewed_proposals: Mapping[str, ProposeBehavioralRule]

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_incident(self, incident_id: str) -> RuleIncident | None:
        return self.incidents.get(incident_id)

    def get_retained_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        record = self.evidence.get(evidence_id)
        if record is not None:
            verify_artifact_binding(record, self.artifact_store)
        return record

    def get_rule(self, rule_version_id: str) -> BehavioralRuleVersion | None:
        return self.rules.get(rule_version_id)

    def list_rules(self) -> tuple[BehavioralRuleVersion, ...]:
        return tuple(
            sorted(
                self.rules.values(),
                key=lambda item: (item.created_at, item.rule_version_id),
            )
        )

    def get_assessment(self, assessment_id: str) -> ReviewerAssessment | None:
        return self.assessments.get(assessment_id)

    def reviewed_rule_proposal(self, proposal_id: str) -> ProposeBehavioralRule | None:
        return self.reviewed_proposals.get(proposal_id)

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
        return self.heads.get(rule_id)

    def list_heads(self) -> tuple[tuple[str, str, str, RuleStatus], ...]:
        return tuple(
            (rule_id, rule_version_id, semantic_version, status)
            for rule_id, (rule_version_id, semantic_version, status) in sorted(self.heads.items())
        )


@dataclass(frozen=True)
class _RepresentationReplayCapability:
    active_policy: PolicySnapshot
    artifact_store: ArtifactStore
    receipts: Mapping[str, RepresentationReceipt]
    versions: dict[str, PrimitiveVersionRecord]
    evaluations: dict[str, PrimitiveEvaluationRecord]
    staged_proposals: dict[str, ProposePrimitiveVersion]
    verification_results: Mapping[str, VerificationResultRecord]
    verification_mechanisms: Mapping[str, VerificationMechanismSpecRecord]
    evidence: Mapping[str, EvidenceRecord]
    measurements: Mapping[str, SelfImprovementMeasurementRecord]
    evaluator_audits: Mapping[str, EvaluatorAuditRecord]
    heads: dict[str, tuple[str, str, PrimitiveStatus]]

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def resolve_receipt(
        self,
        reference: AcceptedPrimitiveReceiptRef,
    ) -> RepresentationReceipt | None:
        receipt = self.receipts.get(reference.proposal_id)
        return receipt if receipt is not None and receipt.reference == reference else None

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self.versions.get(version_id)

    def list_staged_versions(self) -> tuple[PrimitiveVersion, ...]:
        return tuple(item.primitive_version for item in self.staged_proposals.values())

    def get_evaluation(self, evaluation_id: str) -> PrimitiveEvaluationRecord | None:
        return self.evaluations.get(evaluation_id)

    def get_result(self, result_id: str) -> VerificationResultRecord | None:
        return self.verification_results.get(result_id)

    def get_mechanism(self, mechanism_id: str) -> VerificationMechanismSpecRecord | None:
        return self.verification_mechanisms.get(mechanism_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        item = self.evidence.get(evidence_id)
        if item is not None:
            verify_artifact_binding(item, self.artifact_store)
        return item

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(measurement_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self.evaluator_audits.get(audit_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        return self.heads.get(primitive_id)

    def append_version(self, primitive: PrimitiveVersion) -> None:
        if primitive.primitive_version_id in self.versions:
            return
        status = projected_primitive_status(primitive, self.list_staged_versions())
        record = primitive_version_to_storage(
            primitive,
            status=StoredPrimitiveStatus(status.value),
        )
        _add_stable(
            self.versions,
            record.primitive_version_id,
            record,
            "primitive version projection",
        )

    def append_evaluation(self, evaluation: PrimitiveEvaluation) -> None:
        record = primitive_evaluation_to_storage(evaluation)
        _add_stable(
            self.evaluations,
            record.primitive_evaluation_id,
            record,
            "primitive evaluation projection",
        )

    def set_head_from_candidate_receipt(self, reference: PrimitiveVersionReceiptRef) -> None:
        receipt = self.resolve_receipt(reference)
        candidate_proposal = None if receipt is None else receipt.proposal
        if not isinstance(candidate_proposal, ProposePrimitiveVersion):
            raise ValueError("accepted primitive admission lost its candidate receipt")
        candidate = candidate_proposal.primitive_version
        stored = self.versions.get(candidate.primitive_version_id)
        if (
            stored is None
            or primitive_version_to_storage(candidate, status=stored.status) != stored
        ):
            raise ValueError("accepted primitive admission lost its retained candidate")
        retained = primitive_version_from_storage(stored)
        self.heads[retained.primitive_id] = (
            retained.primitive_version_id,
            retained.semantic_version,
            PrimitiveStatus(stored.status.value),
        )


@dataclass(frozen=True)
class _ReplayPrimitiveResolver:
    versions: Mapping[str, PrimitiveVersionRecord]
    heads: Mapping[str, tuple[str, str, PrimitiveStatus]]

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self.versions.get(version_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        return self.heads.get(primitive_id)


@dataclass(frozen=True)
class _HypothesisReplayCapability:
    active_policy: PolicySnapshot
    artifact_store: ArtifactStore
    receipts: Mapping[str, HypothesisReceipt]
    versions: dict[str, HypothesisVersionRecord]
    models: dict[str, ExecutableModelSpecRecord]
    mechanisms: dict[str, VerificationMechanismSpecRecord]
    simulations: dict[str, SimulationResultRecord]
    results: dict[str, VerificationResultRecord]
    counterexamples: dict[str, CounterexampleRecord]
    revisions: dict[str, HypothesisRevisionRecord]
    admissions: dict[str, HypothesisAdmissionDecisionRecord]
    heads: dict[str, tuple[str, int, HypothesisAdmissionStatus]]
    evidence: Mapping[str, EvidenceRecord]
    measurements: Mapping[str, SelfImprovementMeasurementRecord]
    evaluator_audits: Mapping[str, EvaluatorAuditRecord]
    primitive_versions: Mapping[str, PrimitiveVersionRecord]
    primitive_heads: Mapping[str, tuple[str, str, PrimitiveStatus]]

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def resolve_receipt(
        self,
        reference: AcceptedHypothesisReceiptRef,
    ) -> HypothesisReceipt | None:
        receipt = self.receipts.get(reference.proposal_id)
        return receipt if receipt is not None and receipt.reference == reference else None

    def get_hypothesis(self, identifier: str) -> HypothesisVersionRecord | None:
        return self.versions.get(identifier)

    def list_hypotheses(self) -> tuple[HypothesisVersionRecord, ...]:
        return tuple(self.versions.values())

    def get_model(self, identifier: str) -> ExecutableModelSpecRecord | None:
        return self.models.get(identifier)

    def get_mechanism(self, identifier: str) -> VerificationMechanismSpecRecord | None:
        return self.mechanisms.get(identifier)

    def get_simulation(self, identifier: str) -> SimulationResultRecord | None:
        return self.simulations.get(identifier)

    def get_result(self, identifier: str) -> VerificationResultRecord | None:
        return self.results.get(identifier)

    def get_counterexample(self, identifier: str) -> CounterexampleRecord | None:
        return self.counterexamples.get(identifier)

    def list_counterexamples(self) -> tuple[CounterexampleRecord, ...]:
        return tuple(self.counterexamples.values())

    def get_revision(self, identifier: str) -> HypothesisRevisionRecord | None:
        return self.revisions.get(identifier)

    def list_revisions(self) -> tuple[HypothesisRevisionRecord, ...]:
        return tuple(self.revisions.values())

    def get_admission(self, identifier: str) -> HypothesisAdmissionDecisionRecord | None:
        return self.admissions.get(identifier)

    def get_head(
        self,
        hypothesis_id: str,
    ) -> tuple[str, int, HypothesisAdmissionStatus] | None:
        return self.heads.get(hypothesis_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        item = self.evidence.get(evidence_id)
        if item is not None:
            verify_artifact_binding(item, self.artifact_store)
        return item

    def get_measurement(self, identifier: str) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(identifier)

    def get_evaluator_audit(self, identifier: str) -> EvaluatorAuditRecord | None:
        return self.evaluator_audits.get(identifier)

    def primitive_use_code(self, primitive_version_id: str) -> RejectionCode | None:
        return primitive_use_rejection(
            primitive_version_id,
            resolver=_ReplayPrimitiveResolver(self.primitive_versions, self.primitive_heads),
            use=PrimitiveUse.PUBLIC_CONCLUSION,
        )

    def append_hypothesis(self, hypothesis: HypothesisSpec) -> None:
        record = hypothesis_to_storage(hypothesis)
        _add_stable(
            self.versions,
            record.hypothesis_version_id,
            record,
            "hypothesis version projection",
        )

    def append_model(self, model: ExecutableModelSpec) -> None:
        record = model_to_storage(model)
        _add_stable(self.models, record.model_spec_id, record, "model specification projection")

    def append_mechanism(self, mechanism: VerificationMechanismSpec) -> None:
        record = mechanism_to_storage(mechanism)
        _add_stable(
            self.mechanisms,
            record.mechanism_spec_id,
            record,
            "verification mechanism projection",
        )

    def append_simulation(self, simulation: SimulationResult) -> None:
        record = simulation_to_storage(simulation)
        _add_stable(
            self.simulations,
            record.simulation_result_id,
            record,
            "simulation result projection",
        )

    def append_result(self, result: VerificationResult) -> None:
        model = None if result.model_spec_id is None else self.models.get(result.model_spec_id)
        record = verification_to_storage(result, model)
        _add_stable(
            self.results,
            record.verification_result_id,
            record,
            "verification result projection",
        )

    def append_counterexample(self, counterexample: DomainCounterexampleRecord) -> None:
        model = (
            None
            if counterexample.model_spec_id is None
            else self.models.get(counterexample.model_spec_id)
        )
        record = counterexample_to_storage(counterexample, model)
        _add_stable(
            self.counterexamples,
            record.counterexample_id,
            record,
            "counterexample projection",
        )

    def append_revision(self, hypothesis: HypothesisSpec, revision: RevisionRecord) -> None:
        self.append_hypothesis(hypothesis)
        record = revision_to_storage(revision)
        _add_stable(self.revisions, record.revision_id, record, "hypothesis revision projection")

    def admit_hypothesis(self, decision: object) -> None:
        if not isinstance(decision, HypothesisAdmissionDecisionRecord):
            raise TypeError("hypothesis replay requires a fixed admission decision")
        _add_stable(
            self.admissions,
            decision.admission_decision_id,
            decision,
            "hypothesis admission projection",
        )
        self.heads[decision.hypothesis_id] = (
            decision.hypothesis_version_id,
            decision.version,
            decision.admission_status,
        )


def verify_workspace(
    repositories: RepositorySet,
    artifact_store: ArtifactStore,
) -> AuditVerification:
    events: tuple[AuditEvent, ...] = ()
    try:
        active_policy = repositories.policies.get_active()
        policies = repositories.policies.list_all()
        evidence = repositories.evidence.list_all()
        heads = repositories.claims.list_heads()
        adaptation = repositories.adaptation_integrity_snapshot()
        progress = repositories.progress_integrity_snapshot()
        trails = repositories.trail_integrity_snapshot()
        rules = repositories.rule_integrity_snapshot()
        representations = repositories.representation_integrity_snapshot()
        hypotheses = repositories.hypothesis_integrity_snapshot()
        transactions = repositories.transactions.list_all()
        events = repositories.audit.list_all()
        _require(
            active_policy is not None or not repositories.has_durable_state(),
            "durable workspace state requires an active registered policy",
        )
        audit_records = _validated_audit_records(events, repositories)
        _require_transaction_audit_consistency(transactions, audit_records)
        _require_projection_consistency(
            repositories,
            audit_records,
            evidence,
            heads,
            adaptation,
            progress,
            trails,
            rules,
            representations,
            hypotheses,
            policies,
            active_policy,
            artifact_store,
            transactions,
            events,
        )
        _require_artifact_consistency(evidence, artifact_store)
        _require_claim_evidence_consistency(repositories, heads, evidence)
    except (KeyError, OSError, SQLAlchemyError, TypeError, ValueError) as error:
        return AuditVerification(
            valid=False,
            checked_events=len(events),
            reason=f"workspace integrity error: {error}",
        )
    return AuditVerification(valid=True, checked_events=len(events))


def require_workspace_integrity(
    repositories: RepositorySet,
    artifact_store: ArtifactStore,
) -> None:
    result = verify_workspace(repositories, artifact_store)
    if not result.valid:
        raise StorageIntegrityError(result.reason or "workspace integrity verification failed")


def _validated_audit_records(
    events: tuple[AuditEvent, ...],
    repositories: RepositorySet,
) -> tuple[_ValidatedAuditRecord, ...]:
    records: list[_ValidatedAuditRecord] = []
    for event in events:
        _require(event.event_type == "transaction_decision", "unexpected audit event type")
        payload = json_compatible_payload(event.payload)
        proposal = PROPOSAL_ADAPTER.validate_json(
            canonical_json_bytes(_mapping_value(payload, "proposal"))
        )
        decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(_mapping_value(payload, "decision"))
        )
        governing_hash = SHA256_ADAPTER.validate_python(_mapping_value(payload, "policy_hash"))
        _require(
            repositories.policies.get(governing_hash) is not None,
            "audit event governing policy is not registered",
        )
        configured_hash = _optional_policy_hash(payload, "configured_policy_hash")
        stored_hash = _optional_policy_hash(payload, "stored_policy_hash")
        intent_fingerprint = _optional_policy_hash(payload, "intent_fingerprint")
        transaction_persisted = _strict_bool(payload, "transaction_persisted")
        if configured_hash is not None:
            _require(
                repositories.policies.get(configured_hash) is not None,
                "audit event configured policy is not registered",
            )
        if stored_hash is not None:
            _require(
                repositories.policies.get(stored_hash) is not None,
                "audit event stored policy is not registered",
            )
            _require(
                stored_hash == governing_hash,
                "audit event governing and stored policies do not match",
            )
        _require(
            proposal.proposal_id == decision.proposal_id,
            "audit proposal and decision identifiers do not match",
        )
        if isinstance(proposal, ProposeGovernancePolicyTransition):
            _require(
                _optional_policy_hash(payload, "prior_policy_hash") == proposal.prior_policy_hash,
                "transition audit prior policy hash does not match proposal",
            )
            _require(
                _optional_policy_hash(payload, "candidate_policy_hash")
                == proposal.candidate_policy_snapshot.policy_hash,
                "transition audit candidate policy hash does not match proposal",
            )
            _require(
                _optional_policy_hash(payload, "rollback_policy_hash")
                == proposal.rollback_policy_hash,
                "transition audit rollback policy hash does not match proposal",
            )
        records.append(
            _ValidatedAuditRecord(
                event=event,
                proposal=proposal,
                decision=decision,
                intent_fingerprint=intent_fingerprint,
                transaction_persisted=transaction_persisted,
                governing_policy_hash=governing_hash,
                payload=payload,
            )
        )
    return tuple(records)


def _require_transaction_audit_consistency(
    transactions: tuple[StoredTransaction, ...],
    audit_records: tuple[_ValidatedAuditRecord, ...],
) -> None:
    def key(
        proposal: Proposal,
        decision: TransactionDecision,
        intent_fingerprint: str | None,
    ) -> tuple[bytes, bytes, str | None]:
        return (
            canonical_json_bytes(proposal.model_dump(mode="json")),
            canonical_json_bytes(decision.model_dump(mode="json")),
            intent_fingerprint,
        )

    transaction_keys = {
        key(transaction.proposal, transaction.decision, transaction.intent_fingerprint)
        for transaction in transactions
    }
    persisted_audit_counts = Counter(
        key(record.proposal, record.decision, record.intent_fingerprint)
        for record in audit_records
        if record.transaction_persisted
    )
    for transaction in transactions:
        transaction_key = key(
            transaction.proposal,
            transaction.decision,
            transaction.intent_fingerprint,
        )
        _require(
            persisted_audit_counts[transaction_key] == 1,
            "transaction does not have one exact audit decision",
        )
    for record in audit_records:
        exact_transaction_exists = (
            key(record.proposal, record.decision, record.intent_fingerprint) in transaction_keys
        )
        _require(
            exact_transaction_exists == record.transaction_persisted,
            "audit transaction persistence does not match stored transactions",
        )
        _require(
            not record.decision.accepted or record.transaction_persisted,
            "accepted audit decision has no stored transaction",
        )


def _require_projection_consistency(
    repositories: RepositorySet,
    audit_records: tuple[_ValidatedAuditRecord, ...],
    evidence: tuple[EvidenceRecord, ...],
    heads: tuple[AtomicClaim, ...],
    adaptation: AdaptationIntegritySnapshot,
    progress: ProgressIntegritySnapshot,
    trails: TrailIntegritySnapshot,
    rules: RuleIntegritySnapshot,
    representations: RepresentationIntegritySnapshot,
    hypotheses: HypothesisIntegritySnapshot,
    policies: tuple[PolicySnapshot, ...],
    active_policy: PolicySnapshot | None,
    artifact_store: ArtifactStore,
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...],
) -> None:
    expected_evidence: dict[str, EvidenceRecord] = {}
    expected_claims: dict[tuple[str, int], AtomicClaim] = {}
    expected_runs: dict[str, ResearchRun] = {}
    expected_run_events: dict[str, ResearchRunEvent] = {}
    expected_configurations: dict[str, ConfigurationVersion] = {}
    expected_audits: dict[str, EvaluatorAuditRecord] = {}
    expected_measurements: dict[str, SelfImprovementMeasurementRecord] = {}
    expected_evaluator_versions: dict[str, EvaluatorVersion] = {}
    expected_succession_decisions: dict[str, EvaluatorSuccessionDecision] = {}
    expected_run_heads: dict[str, str] = {}
    expected_evaluator_head: str | None = None
    expected_progress_plans: dict[str, ProgressPlan] = {}
    expected_progress_subtasks: dict[str, ProgressSubtask] = {}
    expected_progress_events: dict[str, ProgressValidationEvent] = {}
    expected_budgets: dict[str, BudgetAllocation] = {}
    expected_checkpoints: dict[str, RunCheckpoint] = {}
    expected_completion_decisions: dict[str, CompletionDecision] = {}
    expected_progress_heads: dict[str, tuple[str, str]] = {}
    expected_trail_versions: dict[str, EvidenceTrailVersion] = {}
    expected_trail_nodes: dict[str, EvidenceTrailNode] = {}
    expected_trail_relations: dict[str, EvidenceTrailRelation] = {}
    expected_trail_checks: dict[str, TrailCheckResult] = {}
    expected_trail_assessments: dict[str, TrailAssessment] = {}
    expected_report_bindings: dict[str, ReportSentenceBinding] = {}
    expected_trail_heads: dict[str, tuple[str, int]] = {}
    expected_rule_incidents: dict[str, RuleIncident] = {}
    expected_rule_versions: dict[str, BehavioralRuleVersion] = {}
    expected_rule_assessments: dict[str, ReviewerAssessment] = {}
    expected_rule_decisions: dict[str, RuleConsolidationDecision] = {}
    expected_rule_regressions: dict[str, RuleRegressionCase] = {}
    expected_rule_heads: dict[str, tuple[str, str, RuleStatus]] = {}
    accepted_rule_proposals: dict[str, ProposeBehavioralRule] = {}
    expected_primitive_versions: dict[str, PrimitiveVersionRecord] = {}
    expected_primitive_evaluations: dict[str, PrimitiveEvaluationRecord] = {}
    expected_primitive_heads: dict[str, tuple[str, str, PrimitiveStatus]] = {}
    accepted_primitive_proposals: dict[str, ProposePrimitiveVersion] = {}
    expected_hypothesis_versions: dict[str, HypothesisVersionRecord] = {}
    expected_models: dict[str, ExecutableModelSpecRecord] = {}
    expected_hypothesis_mechanisms: dict[str, VerificationMechanismSpecRecord] = {}
    expected_simulations: dict[str, SimulationResultRecord] = {}
    expected_hypothesis_results: dict[str, VerificationResultRecord] = {}
    expected_counterexamples: dict[str, CounterexampleRecord] = {}
    expected_revisions: dict[str, HypothesisRevisionRecord] = {}
    expected_admissions: dict[str, HypothesisAdmissionDecisionRecord] = {}
    expected_hypothesis_heads: dict[str, tuple[str, int, HypothesisAdmissionStatus]] = {}
    accepted_evaluator_succession = False
    transitions: list[tuple[ProposeGovernancePolicyTransition, str]] = []
    receipt_index = accepted_proposal_receipts(transactions, events)
    representation_receipt_index = representation_receipts(transactions, events)
    available_representation_receipts: dict[str, RepresentationReceipt] = {}
    hypothesis_receipt_index = hypothesis_receipts(transactions, events)
    available_hypothesis_receipts: dict[str, HypothesisReceipt] = {}
    verification_results = {
        record.verification_result_id: record for record in representations.verification_results
    }
    verification_mechanisms = {
        record.mechanism_spec_id: record for record in representations.verification_mechanisms
    }
    policy_by_hash = {snapshot.policy_hash: snapshot for snapshot in policies}
    _require_historical_policy_sequence(audit_records, policy_by_hash, active_policy)
    transactions_by_id = {
        transaction.proposal.proposal_id: transaction for transaction in transactions
    }
    for audit_record in audit_records:
        if not (audit_record.transaction_persisted and audit_record.decision.accepted):
            continue
        proposal = audit_record.proposal
        transaction = transactions_by_id.get(proposal.proposal_id)
        _require(transaction is not None, "accepted audit has no exact transaction")
        if transaction is None:  # pragma: no cover - fail-closed narrowing
            raise StorageIntegrityError("accepted audit transaction is unavailable")
        historical_policy = policy_by_hash[audit_record.governing_policy_hash]
        if isinstance(proposal, AddEvidence):
            projected = proposal.evidence.model_copy(
                update={"verification_state": VerificationState.HASH_VERIFIED}
            )
            _add_unique(expected_evidence, projected.evidence_id, projected, "evidence projection")
        elif isinstance(proposal, ProposeClaim):
            _add_unique(
                expected_claims,
                (proposal.claim.claim_id, proposal.claim.version),
                proposal.claim,
                "claim projection",
            )
        elif isinstance(proposal, TransitionClaim):
            _add_unique(
                expected_claims,
                (proposal.next_claim.claim_id, proposal.next_claim.version),
                proposal.next_claim,
                "claim projection",
            )
        elif isinstance(proposal, CreateResearchRun):
            _require_governing_hash(
                proposal.run.active_governance_policy_hash,
                audit_record.governing_policy_hash,
                "research run",
            )
            _add_unique(expected_runs, proposal.run.run_id, proposal.run, "research run projection")
        elif isinstance(proposal, AppendResearchRunEvent):
            _require_governing_hash(
                proposal.event.governing_policy_hash,
                audit_record.governing_policy_hash,
                "research run event",
            )
            _require(
                proposal.event.run_id in expected_runs,
                "research run event transaction precedes its run",
            )
            _add_unique(
                expected_run_events,
                proposal.event.run_event_id,
                proposal.event,
                "research run event projection",
            )
            expected_run_heads[proposal.event.run_id] = proposal.event.run_event_id
        elif isinstance(proposal, RecordProgressPlan):
            plan = proposal.plan
            _require_governing_hash(
                plan.governing_policy_hash,
                audit_record.governing_policy_hash,
                "progress plan",
            )
            _require(plan.run_id in expected_runs, "progress plan transaction precedes its run")
            prior_versions = tuple(
                item.version
                for item in expected_progress_plans.values()
                if item.run_id == plan.run_id
            )
            _require(
                plan.version == max(prior_versions, default=0) + 1,
                "progress plan does not continue replay-derived run history",
            )
            calculate_progress(plan, ())
            _add_unique(
                expected_progress_plans,
                plan.plan_version_id,
                plan,
                "progress plan projection",
            )
            for subtask in plan.subtasks:
                _require(
                    subtask.plan_version_id == plan.plan_version_id,
                    "progress subtask does not belong to its enclosing plan",
                )
                _add_unique(
                    expected_progress_subtasks,
                    subtask.subtask_id,
                    subtask,
                    "progress subtask projection",
                )
        elif isinstance(proposal, AppendProgressEvent):
            event = proposal.event
            _require_governing_hash(
                event.governing_policy_hash,
                audit_record.governing_policy_hash,
                "progress event",
            )
            progress_plan = expected_progress_plans.get(event.plan_version_id)
            progress_subtask = expected_progress_subtasks.get(event.subtask_id)
            _require(
                event.run_id in expected_runs
                and progress_plan is not None
                and progress_plan.run_id == event.run_id,
                "progress event references an unprojected run or plan",
            )
            _require(
                progress_subtask is not None
                and progress_subtask.plan_version_id == event.plan_version_id,
                "progress event subtask does not belong to its plan",
            )
            current_head = expected_progress_heads.get(event.run_id)
            current_head_event = (
                None if current_head is None else expected_progress_events.get(current_head[1])
            )
            _require(
                event_advances_progress_head(
                    event,
                    tuple(expected_progress_plans.values()),
                    current_head_event,
                ),
                "progress event does not monotonically advance replay-derived history",
            )
            _add_unique(
                expected_progress_events,
                event.event_id,
                event,
                "progress event projection",
            )
            expected_progress_heads[event.run_id] = (
                event.plan_version_id,
                event.event_id,
            )
        elif isinstance(proposal, RecordRunBudget):
            budget = proposal.budget
            _require_governing_hash(
                budget.governing_policy_hash,
                audit_record.governing_policy_hash,
                "run budget",
            )
            budget_plan = expected_progress_plans.get(budget.plan_version_id)
            _require(
                budget.run_id in expected_runs
                and budget_plan is not None
                and budget_plan.run_id == budget.run_id,
                "run budget references an unprojected run or plan",
            )
            _add_unique(
                expected_budgets,
                budget.budget_id,
                budget,
                "run budget projection",
            )
        elif isinstance(proposal, RecordRunCheckpoint):
            checkpoint = proposal.checkpoint
            _require_governing_hash(
                checkpoint.governing_policy_hash,
                audit_record.governing_policy_hash,
                "run checkpoint",
            )
            checkpoint_plan = expected_progress_plans.get(checkpoint.plan_version_id)
            _require(
                checkpoint.run_id in expected_runs
                and checkpoint_plan is not None
                and checkpoint_plan.run_id == checkpoint.run_id,
                "run checkpoint references an unprojected run or plan",
            )
            if checkpoint_plan is None:  # pragma: no cover - narrowed by fail-closed check above
                raise StorageIntegrityError("run checkpoint plan is unavailable")
            _require(
                current_progress_plan(
                    tuple(expected_progress_plans.values()),
                    checkpoint.run_id,
                )
                == checkpoint_plan,
                "run checkpoint does not target the replay-derived current plan",
            )
            summary = calculate_progress(
                checkpoint_plan,
                tuple(
                    event
                    for event in expected_progress_events.values()
                    if event.plan_version_id == checkpoint.plan_version_id
                ),
            )
            _require(
                checkpoint.validated_subtask_ids == summary.validated_subtask_ids,
                "run checkpoint does not match replay-derived progress",
            )
            _require(
                checkpoint.pending_dependency_ids
                == replay_pending_dependency_ids(
                    checkpoint_plan,
                    summary.validated_subtask_ids,
                ),
                "run checkpoint pending dependencies do not match replay-derived progress",
            )
            checkpoint_budget = select_checkpoint_budget(
                checkpoint,
                tuple(expected_budgets.values()),
            )
            _require(
                checkpoint_budget is not None,
                "run checkpoint has no replay-derived applicable budget",
            )
            if checkpoint_budget is None:  # pragma: no cover - narrowed by fail-closed check above
                raise StorageIntegrityError("run checkpoint budget is unavailable")
            _require(
                checkpoint.remaining_budget == remaining_budget(checkpoint_budget)
                and checkpoint.telemetry == checkpoint_budget.telemetry,
                "run checkpoint does not reconcile its replay-derived budget",
            )
            _require(
                all(
                    is_canonical_artifact_ref(reference)
                    for reference in (
                        *checkpoint.artifact_refs,
                        *checkpoint.raw_log_refs,
                        *checkpoint.raw_transaction_refs,
                    )
                ),
                "run checkpoint contains a noncanonical artifact reference",
            )
            _add_unique(
                expected_checkpoints,
                checkpoint.checkpoint_id,
                checkpoint,
                "run checkpoint projection",
            )
        elif isinstance(proposal, DecideCompletion):
            completion = proposal.completion_proposal
            decision = proposal.completion_decision
            _require_governing_hash(
                completion.governing_policy_hash,
                audit_record.governing_policy_hash,
                "completion proposal",
            )
            _require_governing_hash(
                decision.governing_policy_hash,
                audit_record.governing_policy_hash,
                "completion decision",
            )
            completion_plan = expected_progress_plans.get(completion.plan_version_id)
            _require(
                completion.run_id in expected_runs
                and completion_plan is not None
                and completion_plan.run_id == completion.run_id,
                "completion references an unprojected run or plan",
            )
            _require(
                any(
                    budget.run_id == completion.run_id
                    and budget.plan_version_id == completion.plan_version_id
                    for budget in expected_budgets.values()
                ),
                "completion transaction precedes its run budget",
            )
            _require(
                decision.run_id == completion.run_id
                and decision.plan_version_id == completion.plan_version_id
                and decision.completion_proposal_id == completion.completion_proposal_id
                and decision.checklist == completion.checklist
                and decision.final_validator_result == completion.final_validation.result
                and decision.termination_reason == completion.termination_reason
                and completion.proposer == proposal.proposer,
                "completion decision is not bound to its completion proposal",
            )
            completion_run = expected_runs.get(completion.run_id)
            if completion_run is None:  # pragma: no cover - narrowed by fail-closed check above
                raise StorageIntegrityError("completion run is unavailable")
            final_validation = completion.final_validation
            _require(
                final_validation.actor == completion_run.final_validator
                and final_validation.actor_version == completion_run.final_validator_version
                and decision.decision_authority == final_validation.actor
                and progress_actors_are_independent(
                    final_validation.actor,
                    completion_run.creator,
                )
                and progress_actors_are_independent(
                    final_validation.actor,
                    completion.proposer,
                )
                and completion.relationship_to_run_creator is ActorRelationship.INDEPENDENT
                and completion.relationship_to_completion_proposer is ActorRelationship.INDEPENDENT
                and completion.are_independent
                and is_authoritative_verification(final_validation.category),
                "completion does not retain replay-derived independent final authority",
            )
            required_evidence_ids = (
                *(
                    evidence_id
                    for item in completion.checklist
                    if item.completed
                    for evidence_id in item.evidence_ids
                ),
                *final_validation.evidence_ids,
            )
            _require(
                bool(final_validation.evidence_ids)
                and all(
                    not item.completed or bool(item.evidence_ids) for item in completion.checklist
                )
                and all(evidence_id in expected_evidence for evidence_id in required_evidence_ids),
                "completion evidence is not nonempty retained replay history",
            )
            if completion_plan is None:  # pragma: no cover - narrowed by fail-closed check above
                raise StorageIntegrityError("completion plan is unavailable")
            completion_summary = calculate_progress(
                completion_plan,
                tuple(
                    event
                    for event in expected_progress_events.values()
                    if event.plan_version_id == completion.plan_version_id
                ),
            )
            completion_budgets = tuple(
                budget
                for budget in expected_budgets.values()
                if budget.plan_version_id == completion.plan_version_id
            )
            _require(
                bool(completion_budgets),
                "completion transaction precedes its replay-derived budget",
            )
            latest_completion_budget = max(
                completion_budgets,
                key=lambda budget: (budget.recorded_at, budget.budget_id),
            )
            false_finish = detect_false_finish(
                voluntary_termination=completion.voluntary_termination,
                claims_completion=completion.claims_completion,
                final_validator_result=final_validation.result,
                validated_weight=completion_summary.official_weight,
                unused_budget=has_unused_budget(latest_completion_budget),
            )
            _require(
                decision.false_finish == false_finish
                and false_finish.result is not FalseFinishResult.FALSE_FINISH,
                "completion false-finish finding does not match replay-derived state",
            )
            successful_completion = (
                completion.claims_completion
                and completion.termination_reason is TerminationReason.SUCCESS
                and all(item.completed for item in completion.checklist)
                and final_validation.result is AssessmentOutcome.PASSED
            )
            _require(
                decision.accepted is successful_completion
                and (not completion.claims_completion or successful_completion),
                "completion decision does not match replay-derived finalization gates",
            )
            _add_unique(
                expected_completion_decisions,
                decision.completion_decision_id,
                decision,
                "completion decision projection",
            )
        elif isinstance(proposal, ProposeEvidenceTrailNodes):
            _require_node_stage_replay(
                proposal,
                historical_policy,
                receipt_index,
                transaction,
                audit_record.event.sequence,
            )
        elif isinstance(proposal, ProposeEvidenceTrailRelations):
            _require_relation_stage_replay(
                proposal,
                historical_policy,
                receipt_index,
                transaction,
                audit_record.event.sequence,
            )
        elif isinstance(proposal, RecordEvidenceTrailVersion):
            version = proposal.trail_version
            _require_governing_hash(
                version.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evidence trail version",
            )
            trail_head = expected_trail_heads.get(version.trail_id)
            if trail_head is None:
                _require(
                    version.version == 1 and version.parent_trail_version_id is None,
                    "evidence trail history must begin at version 1",
                )
            else:
                _require(
                    version.version == trail_head[1] + 1
                    and version.parent_trail_version_id == trail_head[0],
                    "evidence trail version does not continue its replay-derived head",
                )
                parent = expected_trail_versions.get(trail_head[0])
                child_claim = next(
                    (
                        claim
                        for claim in expected_claims.values()
                        if f"{claim.claim_id}:{claim.version}" == version.claim_version_id
                    ),
                    None,
                )
                _require(
                    parent is not None
                    and parent.trail_id == version.trail_id
                    and child_claim is not None
                    and child_claim.parent_version_id == parent.claim_version_id,
                    "evidence trail version reparented or broke claim lineage",
                )
            snapshot = proposal.snapshot()
            validation_inputs = _trail_validation_inputs(
                snapshot,
                expected_claims,
                expected_evidence,
                artifact_store,
            )
            validation = validate_trail(snapshot, validation_inputs)
            _require(
                validation.outcome is version.status
                and validation.outcome is not TrailOutcome.INVALID_TRAIL,
                "evidence trail transaction fails semantic replay validation",
            )
            provenance = version.source_first_provenance
            source_receipts = tuple(
                _resolve_receipt(receipt_index, reference)
                for reference in provenance.source_receipts
            )
            node_receipt = _resolve_receipt(
                receipt_index,
                provenance.node_stage_receipt,
            )
            relation_receipt = _resolve_receipt(
                receipt_index,
                provenance.relation_stage_receipt,
            )
            claim_receipt = _resolve_receipt(
                receipt_index,
                provenance.claim_stage_receipt,
            )
            prior_snapshot = (
                None
                if version.parent_trail_version_id is None
                else _expected_trail_snapshot(
                    version.parent_trail_version_id,
                    expected_trail_versions,
                    expected_trail_nodes,
                    expected_trail_relations,
                    expected_trail_checks,
                    expected_trail_assessments,
                )
            )
            receipt_rejection = trail_receipt_rejection(
                proposal,
                validation_inputs=validation_inputs,
                prior_snapshot=prior_snapshot,
                source_receipts=source_receipts,
                node_stage_receipt=node_receipt,
                relation_stage_receipt=relation_receipt,
                claim_stage_receipt=claim_receipt,
                final_transaction_key=(
                    transaction.created_at,
                    transaction.proposal.proposal_id,
                ),
                final_audit_sequence=audit_record.event.sequence,
            )
            _require(
                receipt_rejection is None,
                "evidence trail receipt replay validation failed",
            )
            authority_rejection = trail_authority_rejection(
                proposal,
                historical_policy,
                trail=snapshot,
                retained=validation_inputs,
                authority_actors=_receipt_authority_actors(
                    (*source_receipts, node_receipt, relation_receipt, claim_receipt)
                ),
            )
            _require(
                authority_rejection is None,
                "evidence trail historical authority validation failed",
            )
            _add_unique(
                expected_trail_versions,
                version.trail_version_id,
                version,
                "evidence trail version projection",
            )
            for node in proposal.nodes:
                _add_unique(
                    expected_trail_nodes,
                    node.node_id,
                    node,
                    "evidence trail node projection",
                )
            for relation in proposal.relations:
                _add_unique(
                    expected_trail_relations,
                    relation.relation_id,
                    relation,
                    "evidence trail relation projection",
                )
            for check in proposal.checks:
                _add_unique(
                    expected_trail_checks,
                    check.check_id,
                    check,
                    "evidence trail check projection",
                )
            for assessment in proposal.assessments:
                _add_unique(
                    expected_trail_assessments,
                    assessment.assessment_id,
                    assessment,
                    "evidence trail assessment projection",
                )
            expected_trail_heads[version.trail_id] = (
                version.trail_version_id,
                version.version,
            )
        elif isinstance(proposal, BindReportSentence):
            binding = proposal.binding
            _require_governing_hash(
                binding.governing_policy_hash,
                audit_record.governing_policy_hash,
                "report sentence binding",
            )
            snapshot = _expected_trail_snapshot(
                binding.trail_version_id,
                expected_trail_versions,
                expected_trail_nodes,
                expected_trail_relations,
                expected_trail_checks,
                expected_trail_assessments,
            )
            validation_inputs = _trail_validation_inputs(
                snapshot,
                expected_claims,
                expected_evidence,
                artifact_store,
            )
            _require(
                not validate_report_binding(binding, snapshot, validation_inputs),
                "report sentence binding fails semantic replay validation",
            )
            provenance = snapshot.version.source_first_provenance
            binding_receipts = (
                *(
                    _resolve_receipt(receipt_index, reference)
                    for reference in provenance.source_receipts
                ),
                _resolve_receipt(receipt_index, provenance.node_stage_receipt),
                _resolve_receipt(receipt_index, provenance.relation_stage_receipt),
                _resolve_receipt(receipt_index, provenance.claim_stage_receipt),
            )
            _require(
                all(receipt is not None for receipt in binding_receipts),
                "report binding trail receipts do not resolve",
            )
            authority_rejection = trail_authority_rejection(
                proposal,
                historical_policy,
                trail=snapshot,
                retained=validation_inputs,
                authority_actors=_receipt_authority_actors(binding_receipts),
            )
            _require(
                authority_rejection is None,
                "report binding historical authority validation failed",
            )
            _add_unique(
                expected_report_bindings,
                binding.binding_id,
                binding,
                "report sentence binding projection",
            )
        elif isinstance(
            proposal,
            (
                ProposePrimitiveVersion,
                RecordPrimitiveEvaluation,
                AdmitPrimitiveVersion,
            ),
        ):
            representation_capability = _RepresentationReplayCapability(
                active_policy=historical_policy,
                artifact_store=artifact_store,
                receipts=available_representation_receipts,
                versions=expected_primitive_versions,
                evaluations=expected_primitive_evaluations,
                staged_proposals=accepted_primitive_proposals,
                verification_results=verification_results,
                verification_mechanisms=verification_mechanisms,
                evidence=expected_evidence,
                measurements=expected_measurements,
                evaluator_audits=expected_audits,
                heads=expected_primitive_heads,
            )
            representation_writes = cast(
                HandlerWriteCapability,
                representation_capability,
            )
            if isinstance(proposal, ProposePrimitiveVersion):
                stage_handler = ProposePrimitiveVersionHandler()
                representation_decision = stage_handler.decide(
                    proposal,
                    stage_handler.build_context(proposal, representation_capability),
                )
                _require(
                    representation_decision.accepted,
                    "primitive-stage historical authority validation failed",
                )
                stage_handler.project(
                    proposal,
                    representation_decision,
                    representation_writes,
                )
                accepted_primitive_proposals[proposal.primitive_version.primitive_version_id] = (
                    proposal
                )
            elif isinstance(proposal, RecordPrimitiveEvaluation):
                evaluation_handler = RecordPrimitiveEvaluationHandler()
                representation_decision = evaluation_handler.decide(
                    proposal,
                    evaluation_handler.build_context(proposal, representation_capability),
                )
                _require(
                    representation_decision.accepted,
                    "primitive-evaluation historical authority validation failed",
                )
                evaluation_handler.project(
                    proposal,
                    representation_decision,
                    representation_writes,
                )
            else:
                admission_handler = AdmitPrimitiveVersionHandler()
                representation_decision = admission_handler.decide(
                    proposal,
                    admission_handler.build_context(proposal, representation_capability),
                )
                _require(
                    representation_decision.accepted,
                    "primitive-admission historical authority validation failed",
                )
                admission_handler.project(
                    proposal,
                    representation_decision,
                    representation_writes,
                )
        elif isinstance(
            proposal,
            (
                ProposeHypothesisVersion,
                RegisterExecutableModel,
                RegisterVerificationMechanism,
                RecordSimulationResult,
                RecordVerificationResult,
                RecordCounterexample,
                ReviseHypothesis,
                AdmitHypothesis,
            ),
        ):
            hypothesis_capability = _HypothesisReplayCapability(
                active_policy=historical_policy,
                artifact_store=artifact_store,
                receipts=available_hypothesis_receipts,
                versions=expected_hypothesis_versions,
                models=expected_models,
                mechanisms=expected_hypothesis_mechanisms,
                simulations=expected_simulations,
                results=expected_hypothesis_results,
                counterexamples=expected_counterexamples,
                revisions=expected_revisions,
                admissions=expected_admissions,
                heads=expected_hypothesis_heads,
                evidence=expected_evidence,
                measurements=expected_measurements,
                evaluator_audits=expected_audits,
                primitive_versions=expected_primitive_versions,
                primitive_heads=expected_primitive_heads,
            )
            hypothesis_decision = _replay_hypothesis_proposal(
                proposal,
                hypothesis_capability,
            )
            _require(
                hypothesis_decision.accepted,
                "hypothesis historical authority validation failed",
            )
        elif isinstance(
            proposal,
            (
                RecordRuleIncident,
                ProposeBehavioralRule,
                ImportReviewerAssessment,
                ConsolidateBehavioralRule,
            ),
        ):
            rule_capability = _RuleReplayCapability(
                active_policy=historical_policy,
                evidence=expected_evidence,
                artifact_store=artifact_store,
                incidents=expected_rule_incidents,
                rules=expected_rule_versions,
                assessments=expected_rule_assessments,
                decisions=expected_rule_decisions,
                measurements=expected_measurements,
                evaluator_audits=expected_audits,
                heads=expected_rule_heads,
                reviewed_proposals=accepted_rule_proposals,
            )
            rule_decision = _rule_replay_decision(proposal, rule_capability)
            _require(
                rule_decision.accepted,
                "behavioral-rule historical authority validation failed",
            )
            if isinstance(proposal, RecordRuleIncident):
                _add_stable(
                    expected_rule_incidents,
                    proposal.incident.incident_id,
                    proposal.incident,
                    "rule incident projection",
                )
            elif isinstance(proposal, ProposeBehavioralRule):
                _add_stable(
                    expected_rule_versions,
                    proposal.rule_version.rule_version_id,
                    proposal.rule_version,
                    "behavioral rule projection",
                )
                accepted_rule_proposals[proposal.proposal_id] = proposal
            elif isinstance(proposal, ImportReviewerAssessment):
                _add_stable(
                    expected_rule_assessments,
                    proposal.assessment.assessment_id,
                    proposal.assessment,
                    "reviewer assessment projection",
                )
            else:
                consolidation = proposal.consolidation
                candidate = consolidation.candidate_rule
                _add_unique(
                    expected_rule_decisions,
                    consolidation.consolidation_decision_id,
                    rule_consolidation_decision(proposal),
                    "rule consolidation decision projection",
                )
                if consolidation.action not in {
                    RuleAction.REJECT,
                    RuleAction.ESCALATE_TO_HUMAN,
                }:
                    _add_unique(
                        expected_rule_versions,
                        candidate.rule_version_id,
                        candidate,
                        "behavioral rule projection",
                    )
                    for regression_case in consolidation.regression_cases:
                        _add_unique(
                            expected_rule_regressions,
                            regression_case.regression_case_id,
                            regression_case,
                            "rule regression projection",
                        )
                    expected_rule_heads[candidate.rule_id] = (
                        candidate.rule_version_id,
                        candidate.semantic_version,
                        candidate.status,
                    )
        elif isinstance(proposal, RecordConfigurationVersion):
            configuration = proposal.configuration_version
            _require_governing_hash(
                configuration.governing_policy_hash,
                audit_record.governing_policy_hash,
                "configuration version",
            )
            _add_unique(
                expected_configurations,
                configuration.configuration_version_id,
                configuration,
                "configuration projection",
            )
        elif isinstance(proposal, RecordEvaluatorAudit):
            evaluator_audit = proposal.evaluator_audit
            _require_governing_hash(
                evaluator_audit.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator audit",
            )
            _add_unique(
                expected_audits,
                evaluator_audit.evaluator_audit_id,
                evaluator_audit,
                "evaluator audit projection",
            )
        elif isinstance(proposal, RecordSelfImprovementMeasurement):
            _add_expected_measurement(
                proposal.measurement,
                audit_record.governing_policy_hash,
                expected_runs,
                expected_audits,
                expected_measurements,
            )
        elif isinstance(proposal, ProposeEvaluatorVersion):
            evaluator_version = proposal.evaluator_version
            _require_governing_hash(
                evaluator_version.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator version",
            )
            _add_unique(
                expected_evaluator_versions,
                evaluator_version.evaluator_version_id,
                evaluator_version,
                "evaluator version projection",
            )
        elif isinstance(proposal, DecideEvaluatorSuccession):
            succession = proposal.succession_decision
            _require_governing_hash(
                succession.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator succession",
            )
            _require(
                succession.predecessor_evaluator_version_id in expected_evaluator_versions
                and succession.candidate_evaluator_version_id in expected_evaluator_versions,
                "evaluator succession references an unprojected evaluator version",
            )
            _add_unique(
                expected_succession_decisions,
                succession.evaluator_succession_decision_id,
                succession,
                "evaluator succession projection",
            )
            if succession.accepted:
                if accepted_evaluator_succession:
                    _require(
                        succession.predecessor_evaluator_version_id == expected_evaluator_head,
                        "evaluator succession does not continue the replay-derived head",
                    )
                else:
                    root_ids = {
                        evaluator_version_id
                        for evaluator_version_id, evaluator_version in (
                            expected_evaluator_versions.items()
                        )
                        if evaluator_version.predecessor_evaluator_version_id is None
                    }
                    _require(
                        root_ids == {succession.predecessor_evaluator_version_id},
                        "first evaluator succession must start from the unique root evaluator",
                    )
                expected_evaluator_head = succession.candidate_evaluator_version_id
                accepted_evaluator_succession = True
        elif isinstance(proposal, ProposeGovernancePolicyTransition):
            _require(
                audit_record.governing_policy_hash == proposal.prior_policy_hash,
                "accepted transition audit must be governed by its prior policy",
            )
            _require_governing_hash(
                proposal.research_run.active_governance_policy_hash,
                proposal.prior_policy_hash,
                "transition research run",
            )
            _require_governing_hash(
                proposal.evaluator_audit.governing_policy_hash,
                proposal.prior_policy_hash,
                "transition evaluator audit",
            )
            _add_unique(
                expected_runs,
                proposal.research_run.run_id,
                proposal.research_run,
                "research run projection",
            )
            _add_unique(
                expected_audits,
                proposal.evaluator_audit.evaluator_audit_id,
                proposal.evaluator_audit,
                "evaluator audit projection",
            )
            _add_expected_measurement(
                proposal.measurement,
                proposal.prior_policy_hash,
                expected_runs,
                expected_audits,
                expected_measurements,
            )
            transitions.append((proposal, audit_record.governing_policy_hash))
        if isinstance(
            proposal,
            (
                ProposePrimitiveVersion,
                RecordPrimitiveEvaluation,
                RecordEvaluatorAudit,
                RecordSelfImprovementMeasurement,
            ),
        ):
            representation_receipt = representation_receipt_index.get(proposal.proposal_id)
            _require(
                representation_receipt is not None,
                "accepted representation support transaction has no exact receipt",
            )
            if representation_receipt is not None:
                available_representation_receipts[proposal.proposal_id] = representation_receipt
        if isinstance(
            proposal,
            (
                ProposeHypothesisVersion,
                RegisterExecutableModel,
                RegisterVerificationMechanism,
                RecordSimulationResult,
                RecordVerificationResult,
                RecordCounterexample,
                ReviseHypothesis,
                RecordEvaluatorAudit,
                RecordSelfImprovementMeasurement,
            ),
        ):
            hypothesis_receipt = hypothesis_receipt_index.get(proposal.proposal_id)
            _require(
                hypothesis_receipt is not None,
                "accepted hypothesis support transaction has no exact receipt",
            )
            if hypothesis_receipt is not None:
                available_hypothesis_receipts[proposal.proposal_id] = hypothesis_receipt

    actual_evidence = {record.evidence_id: record for record in evidence}
    _require(actual_evidence == expected_evidence, "evidence projections do not match transactions")

    actual_claims: dict[tuple[str, int], AtomicClaim] = {}
    for head in heads:
        for claim in repositories.claims.history(head.claim_id):
            _add_unique(
                actual_claims,
                (claim.claim_id, claim.version),
                claim,
                "stored claim version",
            )
    _require(actual_claims == expected_claims, "claim projections do not match transactions")
    _require(
        {record.run_id: record for record in adaptation.research_runs} == expected_runs,
        "research run projections do not match accepted transactions",
    )
    _require(
        {record.run_event_id: record for record in adaptation.research_run_events}
        == expected_run_events,
        "research run event projections do not match accepted transactions",
    )
    _require(
        {record.configuration_version_id: record for record in adaptation.configuration_versions}
        == expected_configurations,
        "configuration projections do not match accepted transactions",
    )
    _require(
        {record.evaluator_audit_id: record for record in adaptation.evaluator_audits}
        == expected_audits,
        "evaluator audit projections do not match accepted transactions",
    )
    _require(
        {record.measurement_id: record for record in adaptation.measurements}
        == expected_measurements,
        "measurement projections do not match accepted transactions",
    )
    _require(
        {record.evaluator_version_id: record for record in adaptation.evaluator_versions}
        == expected_evaluator_versions,
        "evaluator version projections do not match accepted transactions",
    )
    _require(
        {
            record.evaluator_succession_decision_id: record
            for record in adaptation.evaluator_succession_decisions
        }
        == expected_succession_decisions,
        "evaluator succession projections do not match accepted transactions",
    )
    _require(
        not adaptation.evaluator_collapse_records,
        "evaluator collapse records have no accepted transaction projection",
    )
    _require(
        dict(adaptation.research_run_heads) == expected_run_heads,
        "research run heads do not match accepted event transactions",
    )
    _require(
        {record.plan_version_id: record for record in progress.plans} == expected_progress_plans,
        "progress plan projections do not match accepted transactions",
    )
    _require(
        {record.subtask_id: record for record in progress.subtasks} == expected_progress_subtasks,
        "progress subtask projections do not match accepted transactions",
    )
    _require(
        {record.event_id: record for record in progress.events} == expected_progress_events,
        "progress event projections do not match accepted transactions",
    )
    _require(
        {record.budget_id: record for record in progress.budgets} == expected_budgets,
        "run budget projections do not match accepted transactions",
    )
    _require(
        {record.checkpoint_id: record for record in progress.checkpoints} == expected_checkpoints,
        "run checkpoint projections do not match accepted transactions",
    )
    _require(
        {record.completion_decision_id: record for record in progress.completion_decisions}
        == expected_completion_decisions,
        "completion decision projections do not match accepted transactions",
    )
    _require(
        {
            run_id: (plan_version_id, event_id)
            for run_id, plan_version_id, event_id in progress.heads
        }
        == expected_progress_heads,
        "progress heads do not match accepted event transactions",
    )
    _require(
        {record.trail_version_id: record for record in trails.versions} == expected_trail_versions,
        "evidence trail version projections do not match accepted transactions",
    )
    _require(
        {record.node_id: record for record in trails.nodes} == expected_trail_nodes,
        "evidence trail node projections do not match accepted transactions",
    )
    _require(
        {record.relation_id: record for record in trails.relations} == expected_trail_relations,
        "evidence trail relation projections do not match accepted transactions",
    )
    _require(
        {record.check_id: record for record in trails.checks} == expected_trail_checks,
        "evidence trail check projections do not match accepted transactions",
    )
    _require(
        {record.assessment_id: record for record in trails.assessments}
        == expected_trail_assessments,
        "evidence trail assessment projections do not match accepted transactions",
    )
    _require(
        {record.binding_id: record for record in trails.bindings} == expected_report_bindings,
        "report sentence binding projections do not match accepted transactions",
    )
    _require(
        {
            trail_id: (trail_version_id, version)
            for trail_id, trail_version_id, version in trails.heads
        }
        == expected_trail_heads,
        "evidence trail heads do not match accepted transactions",
    )
    _require(
        {record.incident_id: record for record in rules.incidents} == expected_rule_incidents,
        "rule incident projections do not match accepted transactions",
    )
    _require(
        {record.rule_version_id: record for record in rules.versions} == expected_rule_versions,
        "behavioral rule projections do not match accepted transactions",
    )
    _require(
        {record.assessment_id: record for record in rules.assessments} == expected_rule_assessments,
        "reviewer assessment projections do not match accepted transactions",
    )
    _require(
        {record.consolidation_decision_id: record for record in rules.decisions}
        == expected_rule_decisions,
        "rule consolidation projections do not match accepted transactions",
    )
    _require(
        {record.regression_case_id: record for record in rules.regressions}
        == expected_rule_regressions,
        "rule regression projections do not match accepted transactions",
    )
    _require(
        {
            rule_id: (rule_version_id, semantic_version, status)
            for rule_id, rule_version_id, semantic_version, status in rules.heads
        }
        == expected_rule_heads,
        "behavioral rule heads do not match accepted transactions",
    )
    _require(
        {record.primitive_version_id: record for record in representations.versions}
        == expected_primitive_versions,
        "primitive version projections do not match accepted transactions",
    )
    _require(
        {record.primitive_evaluation_id: record for record in representations.evaluations}
        == expected_primitive_evaluations,
        "primitive evaluation projections do not match accepted transactions",
    )
    _require(
        {
            primitive_id: (
                primitive_version_id,
                semantic_version,
                PrimitiveStatus(status.value),
            )
            for primitive_id, primitive_version_id, semantic_version, status in (
                representations.heads
            )
        }
        == expected_primitive_heads,
        "primitive heads do not match accepted admission transactions",
    )
    hypothesis_ids = {item.hypothesis_id for item in expected_hypothesis_versions.values()}
    hypothesis_version_ids = set(expected_hypothesis_versions)
    _require(
        {
            record.hypothesis_version_id: record
            for record in hypotheses.versions
            if record.hypothesis_id in hypothesis_ids
        }
        == expected_hypothesis_versions,
        "hypothesis version projections do not match accepted transactions",
    )
    _require(
        {
            record.model_spec_id: record
            for record in hypotheses.models
            if record.hypothesis_version_id in hypothesis_version_ids
        }
        == expected_models,
        "model specification projections do not match accepted transactions",
    )
    _require(
        {
            record.mechanism_spec_id: record
            for record in hypotheses.mechanisms
            if record.hypothesis_version_id in hypothesis_version_ids
        }
        == expected_hypothesis_mechanisms,
        "hypothesis mechanism projections do not match accepted transactions",
    )
    _require(
        {
            record.simulation_result_id: record
            for record in hypotheses.simulations
            if record.hypothesis_version_id in hypothesis_version_ids
        }
        == expected_simulations,
        "simulation projections do not match accepted transactions",
    )
    _require(
        {
            record.verification_result_id: record
            for record in hypotheses.results
            if record.hypothesis_version_id in hypothesis_version_ids
        }
        == expected_hypothesis_results,
        "hypothesis verification projections do not match accepted transactions",
    )
    _require(
        {
            record.counterexample_id: record
            for record in hypotheses.counterexamples
            if record.hypothesis_version_id in hypothesis_version_ids
        }
        == expected_counterexamples,
        "counterexample projections do not match accepted transactions",
    )
    _require(
        {
            record.revision_id: record
            for record in hypotheses.revisions
            if record.hypothesis_id in hypothesis_ids
        }
        == expected_revisions,
        "hypothesis revision projections do not match accepted transactions",
    )
    _require(
        {
            record.admission_decision_id: record
            for record in hypotheses.admissions
            if record.hypothesis_id in hypothesis_ids
        }
        == expected_admissions,
        "hypothesis admission projections do not match accepted transactions",
    )
    _require(
        {
            hypothesis_id: (version_id, version, status)
            for hypothesis_id, version_id, version, status in hypotheses.heads
            if hypothesis_id in hypothesis_ids
        }
        == expected_hypothesis_heads,
        "hypothesis heads do not match accepted admission transactions",
    )
    if accepted_evaluator_succession:
        _require(
            adaptation.evaluator_head == expected_evaluator_head,
            "evaluator head does not match accepted succession transactions",
        )
    else:
        root_ids = {
            evaluator_version_id
            for evaluator_version_id, evaluator_version in expected_evaluator_versions.items()
            if evaluator_version.predecessor_evaluator_version_id is None
        }
        allowed_baseline_heads: set[str | None] = {None}
        if len(root_ids) == 1:
            allowed_baseline_heads.update(root_ids)
        _require(
            adaptation.evaluator_head in allowed_baseline_heads,
            "evaluator head is neither empty nor the unique root evaluator",
        )
    _require_policy_projection_consistency(
        policies,
        active_policy,
        tuple(transitions),
    )


def _replay_hypothesis_proposal(
    proposal: (
        ProposeHypothesisVersion
        | RegisterExecutableModel
        | RegisterVerificationMechanism
        | RecordSimulationResult
        | RecordVerificationResult
        | RecordCounterexample
        | ReviseHypothesis
        | AdmitHypothesis
    ),
    capability: _HypothesisReplayCapability,
) -> TransactionDecision:
    writes = cast(HandlerWriteCapability, capability)
    if isinstance(proposal, ProposeHypothesisVersion):
        stage_handler = ProposeHypothesisVersionHandler()
        decision = stage_handler.decide(
            proposal,
            stage_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            stage_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, RegisterExecutableModel):
        model_handler = RegisterExecutableModelHandler()
        decision = model_handler.decide(
            proposal,
            model_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            model_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, RegisterVerificationMechanism):
        mechanism_handler = RegisterVerificationMechanismHandler()
        decision = mechanism_handler.decide(
            proposal,
            mechanism_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            mechanism_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, RecordSimulationResult):
        simulation_handler = RecordSimulationResultHandler()
        decision = simulation_handler.decide(
            proposal,
            simulation_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            simulation_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, RecordVerificationResult):
        result_handler = RecordVerificationResultHandler()
        decision = result_handler.decide(
            proposal,
            result_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            result_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, RecordCounterexample):
        counterexample_handler = RecordCounterexampleHandler()
        decision = counterexample_handler.decide(
            proposal,
            counterexample_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            counterexample_handler.project(proposal, decision, writes)
        return decision
    if isinstance(proposal, ReviseHypothesis):
        revision_handler = ReviseHypothesisHandler()
        decision = revision_handler.decide(
            proposal,
            revision_handler.build_context(proposal, capability),
        )
        if decision.accepted:
            revision_handler.project(proposal, decision, writes)
        return decision
    admission_handler = AdmitHypothesisHandler()
    decision = admission_handler.decide(
        proposal,
        admission_handler.build_context(proposal, capability),
    )
    if decision.accepted:
        admission_handler.project(proposal, decision, writes)
    return decision


def _rule_replay_decision(
    proposal: RuleMutationProposal,
    capability: _RuleReplayCapability,
) -> TransactionDecision:
    if isinstance(proposal, RecordRuleIncident):
        handler = RecordRuleIncidentHandler()
        return handler.decide(proposal, handler.build_context(proposal, capability))
    if isinstance(proposal, ProposeBehavioralRule):
        rule_handler = ProposeBehavioralRuleHandler()
        return rule_handler.decide(
            proposal,
            rule_handler.build_context(proposal, capability),
        )
    if isinstance(proposal, ImportReviewerAssessment):
        assessment_handler = ImportReviewerAssessmentHandler()
        return assessment_handler.decide(
            proposal,
            assessment_handler.build_context(proposal, capability),
        )
    consolidation_handler = ConsolidateBehavioralRuleHandler()
    return consolidation_handler.decide(
        proposal,
        consolidation_handler.build_context(proposal, capability),
    )


def _expected_trail_snapshot(
    trail_version_id: str,
    versions: dict[str, EvidenceTrailVersion],
    nodes: dict[str, EvidenceTrailNode],
    relations: dict[str, EvidenceTrailRelation],
    checks: dict[str, TrailCheckResult],
    assessments: dict[str, TrailAssessment],
) -> EvidenceTrailSnapshot:
    version = versions.get(trail_version_id)
    _require(version is not None, "report binding references an unprojected trail version")
    if version is None:  # pragma: no cover - narrowed by the fail-closed check above
        raise StorageIntegrityError("report binding trail version is unavailable")
    return EvidenceTrailSnapshot(
        version=version,
        nodes=tuple(item for item in nodes.values() if item.trail_version_id == trail_version_id),
        relations=tuple(
            item for item in relations.values() if item.trail_version_id == trail_version_id
        ),
        checks=tuple(item for item in checks.values() if item.trail_version_id == trail_version_id),
        assessments=tuple(
            item for item in assessments.values() if item.trail_version_id == trail_version_id
        ),
    )


def _trail_validation_inputs(
    snapshot: EvidenceTrailSnapshot,
    claims: dict[tuple[str, int], AtomicClaim],
    evidence: dict[str, EvidenceRecord],
    artifact_store: ArtifactStore,
) -> TrailValidationInputs:
    version = snapshot.version
    matching_claims = tuple(
        claim
        for claim in claims.values()
        if f"{claim.claim_id}:{claim.version}" == version.claim_version_id
    )
    _require(
        len(matching_claims) == 1,
        "evidence trail references an unprojected or ambiguous claim version",
    )
    retained_sources: list[RetainedEvidenceSource] = []
    for source_id in version.source_ids:
        evidence_ids = {node.evidence_id for node in snapshot.nodes if node.source_id == source_id}
        _require(
            len(evidence_ids) == 1,
            "evidence trail source must resolve to exactly one retained evidence record",
        )
        evidence_id = next(iter(evidence_ids))
        retained_evidence = evidence.get(evidence_id)
        _require(
            retained_evidence is not None,
            "evidence trail references an unprojected evidence record",
        )
        if retained_evidence is None:  # pragma: no cover - fail-closed narrowing
            raise StorageIntegrityError("evidence trail retained evidence is unavailable")
        retained_sources.append(
            RetainedEvidenceSource(
                source_id=source_id,
                evidence=retained_evidence,
                artifact_bytes=verified_artifact_bytes(retained_evidence, artifact_store),
            )
        )
    return TrailValidationInputs(
        claim=matching_claims[0],
        sources=tuple(retained_sources),
    )


def _resolve_receipt(
    receipts: dict[str, AcceptedProposalReceipt],
    reference: TrailReceiptRef,
) -> AcceptedProposalReceipt | None:
    receipt = receipts.get(reference.proposal_id)
    return receipt if receipt is not None and receipt.reference == reference else None


def _receipt_precedes(
    receipt: AcceptedProposalReceipt,
    transaction: StoredTransaction,
    audit_sequence: int,
) -> bool:
    return (
        receipt.transaction_created_at,
        receipt.proposal.proposal_id,
    ) < (
        transaction.created_at,
        transaction.proposal.proposal_id,
    ) and receipt.audit_sequence < audit_sequence


def _receipt_authority_actors(
    receipts: tuple[AcceptedProposalReceipt | None, ...],
) -> tuple[ActorIdentity, ...]:
    return tuple(receipt.proposal.proposer for receipt in receipts if receipt is not None)


def _require_node_stage_replay(
    proposal: ProposeEvidenceTrailNodes,
    policy: PolicySnapshot,
    receipts: dict[str, AcceptedProposalReceipt],
    transaction: StoredTransaction,
    audit_sequence: int,
) -> None:
    source_receipts = tuple(
        _resolve_receipt(receipts, reference) for reference in proposal.source_receipts
    )
    _require(
        all(receipt is not None for receipt in source_receipts),
        "node stage source receipts do not resolve exactly",
    )
    resolved = tuple(receipt for receipt in source_receipts if receipt is not None)
    source_proposals = tuple(
        receipt.proposal for receipt in resolved if isinstance(receipt.proposal, AddEvidence)
    )
    source_pairs = tuple(
        dict.fromkeys((node.source_id, node.evidence_id) for node in proposal.nodes)
    )
    _require(
        proposal.classification == FIXED_TRAIL_CLASSIFICATION
        and len(source_proposals) == len(resolved)
        and len({receipt.reference.proposal_id for receipt in resolved}) == len(resolved)
        and all(
            receipt.governing_policy_hash == policy.policy_hash
            and _receipt_precedes(receipt, transaction, audit_sequence)
            for receipt in resolved
        )
        and len({node.node_id for node in proposal.nodes}) == len(proposal.nodes)
        and tuple(evidence_id for _, evidence_id in source_pairs)
        == tuple(item.evidence.evidence_id for item in source_proposals)
        and all(node.trail_version_id == proposal.trail_version_id for node in proposal.nodes)
        and all(
            parse_external_grounding(item.evidence) is ExternalGrounding.PRIMARY_SOURCE
            for item in source_proposals
        ),
        "node stage fails exact historical receipt and graph validation",
    )
    authority_rejection = trail_authority_rejection(
        proposal,
        policy,
        authority_actors=tuple(item.proposer for item in source_proposals),
        authority_actor_ids=frozenset(
            item.evidence.ingestion_actor_id for item in source_proposals
        ),
    )
    _require(
        authority_rejection is None,
        "node stage fails historical policy or approval authority",
    )


def _require_relation_stage_replay(
    proposal: ProposeEvidenceTrailRelations,
    policy: PolicySnapshot,
    receipts: dict[str, AcceptedProposalReceipt],
    transaction: StoredTransaction,
    audit_sequence: int,
) -> None:
    node_receipt = _resolve_receipt(receipts, proposal.node_stage_receipt)
    _require(
        node_receipt is not None and isinstance(node_receipt.proposal, ProposeEvidenceTrailNodes),
        "relation stage node receipt does not resolve exactly",
    )
    if node_receipt is None or not isinstance(
        node_receipt.proposal,
        ProposeEvidenceTrailNodes,
    ):  # pragma: no cover - narrowed by fail-closed check
        raise StorageIntegrityError("relation stage node receipt is unavailable")
    node_stage = node_receipt.proposal
    source_receipts = tuple(
        _resolve_receipt(receipts, reference) for reference in node_stage.source_receipts
    )
    _require(
        all(receipt is not None for receipt in source_receipts),
        "relation stage source receipts do not resolve exactly",
    )
    resolved_sources = tuple(receipt for receipt in source_receipts if receipt is not None)
    source_proposals = tuple(
        receipt.proposal
        for receipt in resolved_sources
        if isinstance(receipt.proposal, AddEvidence)
    )
    node_ids = tuple(node.node_id for node in node_stage.nodes)
    node_id_set = set(node_ids)
    _require(
        proposal.classification == FIXED_TRAIL_CLASSIFICATION
        and node_receipt.governing_policy_hash == policy.policy_hash
        and _receipt_precedes(node_receipt, transaction, audit_sequence)
        and proposal.trail_id == node_stage.trail_id
        and proposal.trail_version_id == node_stage.trail_version_id
        and proposal.proposer == node_stage.proposer
        and proposal.node_ids == node_ids
        and proposal.nodes_hash == canonical_node_set_hash(node_stage.nodes)
        and len({item.relation_id for item in proposal.relations}) == len(proposal.relations)
        and all(
            relation.trail_version_id == proposal.trail_version_id
            and relation.source_node_id in node_id_set
            and relation.target_node_id in node_id_set
            for relation in proposal.relations
        )
        and len(source_proposals) == len(resolved_sources)
        and all(
            receipt.governing_policy_hash == policy.policy_hash
            and _receipt_precedes(receipt, transaction, audit_sequence)
            for receipt in resolved_sources
        )
        and all(
            parse_external_grounding(item.evidence) is ExternalGrounding.PRIMARY_SOURCE
            for item in source_proposals
        ),
        "relation stage fails exact historical receipt and graph validation",
    )
    authority_rejection = trail_authority_rejection(
        proposal,
        policy,
        authority_actors=(
            *(item.proposer for item in source_proposals),
            node_stage.proposer,
        ),
        authority_actor_ids=frozenset(
            item.evidence.ingestion_actor_id for item in source_proposals
        ),
    )
    _require(
        authority_rejection is None,
        "relation stage fails historical policy or approval authority",
    )


def _add_expected_measurement(
    measurement: SelfImprovementMeasurementRecord,
    governing_policy_hash: str,
    expected_runs: dict[str, ResearchRun],
    expected_audits: dict[str, EvaluatorAuditRecord],
    expected_measurements: dict[str, SelfImprovementMeasurementRecord],
) -> None:
    _require_governing_hash(
        measurement.governing_policy_hash,
        governing_policy_hash,
        "measurement",
    )
    _require(
        measurement.run_id in expected_runs and measurement.evaluator_audit_id in expected_audits,
        "measurement references an unprojected run or evaluator audit",
    )
    _add_unique(
        expected_measurements,
        measurement.measurement_id,
        measurement,
        "measurement projection",
    )


def _require_policy_projection_consistency(
    policies: tuple[PolicySnapshot, ...],
    active_policy: PolicySnapshot | None,
    transitions: tuple[tuple[ProposeGovernancePolicyTransition, str], ...],
) -> None:
    actual_policies: dict[str, PolicySnapshot] = {}
    for snapshot in policies:
        _add_unique(actual_policies, snapshot.policy_hash, snapshot, "governance policy")
    if not transitions:
        _require(
            (not actual_policies and active_policy is None)
            or (
                active_policy is not None
                and actual_policies.get(active_policy.policy_hash) == active_policy
            ),
            "active policy pointer does not name a registered governance policy",
        )
        return
    first_transition = transitions[0][0]
    initial = actual_policies.get(first_transition.prior_policy_hash)
    if initial is None:
        raise StorageIntegrityError("transition prior policy is not registered")
    expected_policies = {initial.policy_hash: initial}
    replay_active_hash = initial.policy_hash
    for proposal, governing_policy_hash in transitions:
        _require(
            proposal.prior_policy_hash == replay_active_hash
            and governing_policy_hash == replay_active_hash,
            "governance transition does not continue the replay-derived active policy",
        )
        _require(
            proposal.rollback_policy_hash in expected_policies,
            "governance transition rollback policy is not in prior accepted history",
        )
        candidate = proposal.candidate_policy_snapshot
        prior_candidate = expected_policies.get(candidate.policy_hash)
        _require(
            prior_candidate is None or prior_candidate == candidate,
            "governance candidate hash is reused with different policy content",
        )
        expected_policies[candidate.policy_hash] = candidate
        replay_active_hash = candidate.policy_hash
    _require(
        actual_policies == expected_policies,
        "governance policies do not match accepted transition transactions",
    )
    _require(
        active_policy is not None and active_policy.policy_hash == replay_active_hash,
        "active policy pointer does not match accepted transition replay",
    )


def _require_historical_policy_sequence(
    audit_records: tuple[_ValidatedAuditRecord, ...],
    policies: dict[str, PolicySnapshot],
    active_policy: PolicySnapshot | None,
) -> None:
    if not audit_records:
        return
    replay_active_hash = audit_records[0].governing_policy_hash
    _require(
        replay_active_hash in policies,
        "initial audit governing policy is not registered",
    )
    for record in audit_records:
        _require(
            record.governing_policy_hash == replay_active_hash,
            "audit event does not use the replay-derived active policy",
        )
        if (
            record.transaction_persisted
            and record.decision.accepted
            and isinstance(record.proposal, ProposeGovernancePolicyTransition)
        ):
            _require(
                record.proposal.prior_policy_hash == replay_active_hash,
                "accepted policy transition does not use the active prior policy",
            )
            candidate = record.proposal.candidate_policy_snapshot
            _require(
                policies.get(candidate.policy_hash) == candidate,
                "accepted policy transition candidate is not registered exactly",
            )
            replay_active_hash = candidate.policy_hash
    _require(
        active_policy is not None and active_policy.policy_hash == replay_active_hash,
        "active policy pointer does not match historical audit replay",
    )


def _require_governing_hash(actual: str, expected: str, label: str) -> None:
    _require(actual == expected, f"{label} does not name its transaction governing policy")


def _require_artifact_consistency(
    evidence: tuple[EvidenceRecord, ...],
    artifact_store: ArtifactStore,
) -> None:
    for record in evidence:
        _require(
            record.verification_state is VerificationState.HASH_VERIFIED,
            f"authoritative evidence {record.evidence_id} is not hash verified",
        )
        verify_artifact_binding(record, artifact_store)


def _require_claim_evidence_consistency(
    repositories: RepositorySet,
    heads: tuple[AtomicClaim, ...],
    evidence: tuple[EvidenceRecord, ...],
) -> None:
    evidence_by_id = {record.evidence_id: record for record in evidence}
    for head in heads:
        for claim in repositories.claims.history(head.claim_id):
            if claim.status is ClaimStatus.PROPOSED:
                continue
            if claim.status is ClaimStatus.WITHDRAWN and not claim.evidence_links:
                continue
            checks = run_deterministic_checks(claim, evidence_by_id)
            _require(
                all(check.outcome is CheckOutcome.PASS_DETERMINISTIC for check in checks),
                f"claim {claim.claim_id}:{claim.version} has invalid evidence links",
            )


def _mapping_value(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise StorageIntegrityError(f"audit payload is missing {key}")
    return mapping[key]


def _optional_policy_hash(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return SHA256_ADAPTER.validate_python(value)


def _strict_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = _mapping_value(mapping, key)
    if type(value) is not bool:
        raise StorageIntegrityError(f"audit payload {key} must be a boolean")
    return value


def _add_unique[KeyT, ValueT](
    values: dict[KeyT, ValueT],
    key: KeyT,
    value: ValueT,
    label: str,
) -> None:
    _require(key not in values, f"duplicate {label}")
    values[key] = value


def _add_stable[KeyT, ValueT](
    values: dict[KeyT, ValueT],
    key: KeyT,
    value: ValueT,
    label: str,
) -> None:
    existing = values.get(key)
    _require(existing is None or existing == value, f"changed stable-key {label}")
    values[key] = value


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(detail)
