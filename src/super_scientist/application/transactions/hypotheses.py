from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol, TypeGuard, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import Connection

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.application.hypothesis_testing.service import (
    hypothesis_mutation_authority_rejection,
)
from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.application.representations.service import primitive_use_rejection
from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
    ProposalHandler,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.evidence_trails.authority import parse_external_grounding
from super_scientist.domain.hypotheses.models import (
    AcceptedHypothesisReceiptRef,
    AdmissionOutcome,
    CounterexampleReceiptRef,
    DeterministicCheckerSpec,
    DeterministicCheckResult,
    EvaluatorAuditReceiptRef,
    ExecutableModelSpec,
    ExecutionMode,
    FormalVerificationResult,
    FormalVerifierSpec,
    HypothesisReceiptRef,
    HypothesisRevisionReceiptRef,
    HypothesisSpec,
    HypothesisVersionReceiptRef,
    ImportedPatternStatus,
    LearnedJudgeResult,
    LearnedJudgeSpec,
    ModelSpecReceiptRef,
    RevisionRecord,
    SelfImprovementMeasurementReceiptRef,
    SimulationResult,
    SimulationResultReceiptRef,
    VerificationMechanismReceiptRef,
    VerificationMechanismSpec,
    VerificationResult,
    VerificationResultReceiptRef,
)
from super_scientist.domain.hypotheses.models import (
    CounterexampleRecord as DomainCounterexampleRecord,
)
from super_scientist.domain.hypotheses.models import (
    VerificationOutcome as DomainVerificationOutcome,
)
from super_scientist.domain.identity import ActorIdentity, are_independent
from super_scientist.domain.improvement.classification import ExternalGrounding, VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import (
    Sha256Hex,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.representations.models import PrimitiveStatus, PrimitiveUse
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AdmitHypothesis,
    Proposal,
    ProposeHypothesisVersion,
    RecordCounterexample,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.domain_records import (
    AdmissionDecisionOutcome,
    BuiltinSimulatorId,
    CounterexampleRecord,
    CounterexampleRecordRepository,
    EvaluatorAuditRepository,
    ExecutableModelSpecRecord,
    ExecutableModelSpecRepository,
    HypothesisAdmissionDecisionRecord,
    HypothesisAdmissionDecisionRepository,
    HypothesisAdmissionStatus,
    HypothesisHeadRepository,
    HypothesisRevisionRecord,
    HypothesisRevisionRepository,
    HypothesisVersionRecord,
    HypothesisVersionRepository,
    ModelExecutionMode,
    PrimitiveHeadRepository,
    PrimitiveVersionRecord,
    PrimitiveVersionRepository,
    SelfImprovementMeasurementRepository,
    SimulationResultRecord,
    SimulationResultRepository,
    VerificationMechanismCategory,
    VerificationMechanismSpecRecord,
    VerificationMechanismSpecRepository,
    VerificationOutcome,
    VerificationResultCategory,
    VerificationResultRecord,
    VerificationResultRepository,
)
from super_scientist.providers.storage.domain_records import (
    ModelType as StoredModelType,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    EvidenceRepository,
    StoredTransaction,
    TransactionRepository,
)

type FixedHypothesisHandler = ProposalHandler[BaseModel, BaseModel]
type HypothesisReceiptProposal = (
    ProposeHypothesisVersion
    | RegisterExecutableModel
    | RegisterVerificationMechanism
    | RecordSimulationResult
    | RecordVerificationResult
    | RecordCounterexample
    | ReviseHypothesis
    | RecordEvaluatorAudit
    | RecordSelfImprovementMeasurement
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


class HypothesisReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    reference: HypothesisReceiptRef
    proposal: HypothesisReceiptProposal
    transaction_created_at: UtcTimestamp
    audit_sequence: int
    audit_occurred_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class HypothesisReceiptReader:
    """Resolve an exact accepted transaction through its one durable audit event."""

    def __init__(self, connection: Connection) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)

    def resolve(self, reference: AcceptedHypothesisReceiptRef) -> HypothesisReceipt | None:
        transaction = self._transactions.get_by_proposal_id(reference.proposal_id)
        if transaction is None or not transaction.decision.accepted:
            return None
        proposal = transaction.proposal
        if not _is_receipt_proposal(proposal):
            return None
        matches = tuple(
            event
            for event in self._audit.list_all()
            if _audit_matches(event, proposal, transaction)
        )
        if len(matches) != 1:
            return None
        event = matches[0]
        expected = _receipt_reference(proposal, transaction, event)
        if expected != reference:
            return None
        policy_hash = _audit_policy_hash(event)
        if policy_hash is None:
            return None
        return HypothesisReceipt(
            reference=expected,
            proposal=proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=policy_hash,
        )


def hypothesis_receipts(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...],
) -> dict[str, HypothesisReceipt]:
    """Build the live receipt index from immutable transaction and audit history."""

    receipts: dict[str, HypothesisReceipt] = {}
    for transaction in transactions:
        proposal = transaction.proposal
        if not transaction.decision.accepted or not _is_receipt_proposal(proposal):
            continue
        matches = tuple(event for event in events if _audit_matches(event, proposal, transaction))
        if len(matches) != 1:
            continue
        event = matches[0]
        policy_hash = _audit_policy_hash(event)
        if policy_hash is None:
            continue
        receipts[proposal.proposal_id] = HypothesisReceipt(
            reference=_receipt_reference(proposal, transaction, event),
            proposal=proposal,
            transaction_created_at=transaction.created_at,
            audit_sequence=event.sequence,
            audit_occurred_at=event.occurred_at,
            governing_policy_hash=policy_hash,
        )
    return receipts


@dataclass(frozen=True, slots=True)
class StoredHypothesisPrimitiveResolver:
    _versions: PrimitiveVersionRepository
    _heads: PrimitiveHeadRepository

    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None:
        return self._versions.get(version_id)

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        head = self._heads.get(primitive_id)
        return None if head is None else (head[0], head[1], PrimitiveStatus(head[2].value))


@dataclass(frozen=True)
class RetainedHypothesisEvidenceReader:
    _evidence: EvidenceRepository
    _artifacts: ArtifactStore

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        evidence = self._evidence.get(evidence_id)
        if evidence is not None:
            verify_artifact_binding(evidence, self._artifacts)
        return evidence


@dataclass(frozen=True, slots=True)
class HypothesisReader:
    active_policy: PolicySnapshot
    receipts: HypothesisReceiptReader
    versions: HypothesisVersionRepository
    models: ExecutableModelSpecRepository
    mechanisms: VerificationMechanismSpecRepository
    simulations: SimulationResultRepository
    results: VerificationResultRepository
    counterexamples: CounterexampleRecordRepository
    revisions: HypothesisRevisionRepository
    admissions: HypothesisAdmissionDecisionRepository
    heads: HypothesisHeadRepository
    evidence: RetainedHypothesisEvidenceReader
    measurements: SelfImprovementMeasurementRepository
    evaluator_audits: EvaluatorAuditRepository
    primitive_resolver: StoredHypothesisPrimitiveResolver

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def resolve_receipt(self, reference: AcceptedHypothesisReceiptRef) -> HypothesisReceipt | None:
        return self.receipts.resolve(reference)

    def get_hypothesis(self, identifier: str) -> HypothesisVersionRecord | None:
        return self.versions.get(identifier)

    def list_hypotheses(self) -> tuple[HypothesisVersionRecord, ...]:
        return self.versions.list_all()

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
        return self.counterexamples.list_all()

    def get_revision(self, identifier: str) -> HypothesisRevisionRecord | None:
        return self.revisions.get(identifier)

    def list_revisions(self) -> tuple[HypothesisRevisionRecord, ...]:
        return self.revisions.list_all()

    def get_admission(self, identifier: str) -> HypothesisAdmissionDecisionRecord | None:
        return self.admissions.get(identifier)

    def get_head(self, hypothesis_id: str) -> tuple[str, int, HypothesisAdmissionStatus] | None:
        return self.heads.get(hypothesis_id)

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.evidence.get(evidence_id)

    def get_measurement(self, identifier: str) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(identifier)

    def get_evaluator_audit(self, identifier: str) -> EvaluatorAuditRecord | None:
        return self.evaluator_audits.get(identifier)

    def primitive_use_code(self, primitive_version_id: str) -> RejectionCode | None:
        return primitive_use_rejection(
            primitive_version_id,
            resolver=self.primitive_resolver,
            use=PrimitiveUse.PUBLIC_CONCLUSION,
        )


@dataclass(frozen=True, slots=True)
class HypothesisCapabilitySet:
    reads: HandlerReadCapability
    writes: HandlerWriteCapability


@dataclass(frozen=True, slots=True)
class HypothesisAppender:
    _versions: HypothesisVersionRepository

    def append_hypothesis(self, hypothesis: HypothesisSpec) -> None:
        record = hypothesis_to_storage(hypothesis)
        retained = self._versions.get(record.hypothesis_version_id)
        if retained == record:
            return
        self._versions.add(record.hypothesis_version_id, record, record.created_at)


@dataclass(frozen=True, slots=True)
class ModelAppender:
    _models: ExecutableModelSpecRepository

    def append_model(self, model: ExecutableModelSpec) -> None:
        record = model_to_storage(model)
        retained = self._models.get(record.model_spec_id)
        if retained == record:
            return
        self._models.add(record.model_spec_id, record, record.created_at)


@dataclass(frozen=True, slots=True)
class MechanismAppender:
    _mechanisms: VerificationMechanismSpecRepository

    def append_mechanism(self, mechanism: VerificationMechanismSpec) -> None:
        record = mechanism_to_storage(mechanism)
        retained = self._mechanisms.get(record.mechanism_spec_id)
        if retained == record:
            return
        self._mechanisms.add(record.mechanism_spec_id, record, record.created_at)


@dataclass(frozen=True, slots=True)
class SimulationAppender:
    _simulations: SimulationResultRepository

    def append_simulation(self, simulation: SimulationResult) -> None:
        record = simulation_to_storage(simulation)
        retained = self._simulations.get(record.simulation_result_id)
        if retained == record:
            return
        self._simulations.add(record.simulation_result_id, record, record.completed_at)


@dataclass(frozen=True, slots=True)
class VerificationAppender:
    _results: VerificationResultRepository
    _models: ExecutableModelSpecRepository

    def append_result(self, result: VerificationResult) -> None:
        model = None if result.model_spec_id is None else self._models.get(result.model_spec_id)
        record = verification_to_storage(result, model)
        retained = self._results.get(record.verification_result_id)
        if retained == record:
            return
        self._results.add(record.verification_result_id, record, record.completed_at)


@dataclass(frozen=True, slots=True)
class CounterexampleAppender:
    _counterexamples: CounterexampleRecordRepository
    _models: ExecutableModelSpecRepository

    def append_counterexample(self, counterexample: DomainCounterexampleRecord) -> None:
        model = (
            None
            if counterexample.model_spec_id is None
            else self._models.get(counterexample.model_spec_id)
        )
        record = counterexample_to_storage(counterexample, model)
        retained = self._counterexamples.get(record.counterexample_id)
        if retained == record:
            return
        self._counterexamples.add(record.counterexample_id, record, record.discovered_at)


@dataclass(frozen=True, slots=True)
class RevisionAppender:
    _versions: HypothesisVersionRepository
    _revisions: HypothesisRevisionRepository

    def append_revision(self, hypothesis: HypothesisSpec, revision: RevisionRecord) -> None:
        hypothesis_record = hypothesis_to_storage(hypothesis)
        retained_hypothesis = self._versions.get(hypothesis_record.hypothesis_version_id)
        if retained_hypothesis is None:
            self._versions.add(
                hypothesis_record.hypothesis_version_id,
                hypothesis_record,
                hypothesis_record.created_at,
            )
        elif retained_hypothesis != hypothesis_record:
            raise ValueError("accepted revision conflicts with retained hypothesis version")
        revision_record = revision_to_storage(revision)
        retained_revision = self._revisions.get(revision_record.revision_id)
        if retained_revision == revision_record:
            return
        self._revisions.add(
            revision_record.revision_id,
            revision_record,
            revision_record.revised_at,
        )


@dataclass(frozen=True, slots=True)
class HypothesisAdmissionWriter:
    _admissions: HypothesisAdmissionDecisionRepository
    _heads: HypothesisHeadRepository

    def admit_hypothesis(self, decision: object) -> None:
        if not isinstance(decision, HypothesisAdmissionDecisionRecord):
            raise TypeError("admission projection requires the fixed storage decision")
        retained = self._admissions.get(decision.admission_decision_id)
        if retained is None:
            self._admissions.add(decision.admission_decision_id, decision, decision.decided_at)
        elif retained != decision:
            raise ValueError("accepted admission conflicts with retained decision")
        self._heads.set(
            decision.hypothesis_id,
            decision.hypothesis_version_id,
            decision.version,
            decision.admission_status,
        )


def hypothesis_to_storage(hypothesis: HypothesisSpec) -> HypothesisVersionRecord:
    return HypothesisVersionRecord(
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        hypothesis_id=hypothesis.hypothesis_id,
        version=hypothesis.version,
        statement=hypothesis.statement,
        assumptions=hypothesis.assumptions,
        scope=hypothesis.scope,
        variables=hypothesis.variables,
        predictions=hypothesis.predictions,
        falsification_conditions=hypothesis.falsification_conditions,
        primitive_version_ids=hypothesis.primitive_version_ids,
        evidence_ids=hypothesis.evidence_ids,
        admission_status=HypothesisAdmissionStatus(hypothesis.imported_pattern_status.value),
        proposer_id=hypothesis.proposer.actor_id,
        created_at=hypothesis.created_at,
        governing_policy_hash=hypothesis.governing_policy_hash,
    )


def model_to_storage(model: ExecutableModelSpec) -> ExecutableModelSpecRecord:
    return ExecutableModelSpecRecord(
        model_spec_id=model.model_spec_id,
        hypothesis_version_id=model.hypothesis_version_id,
        model_type=StoredModelType(model.model_type.value),
        execution_mode=ModelExecutionMode(model.execution_mode.value),
        artifact_hash=model.artifact_hash,
        artifact_media_type=model.artifact_media_type,
        artifact_size_bytes=model.artifact_size_bytes,
        artifact_name=model.artifact_name,
        builtin_simulator_id=(
            None
            if model.builtin_simulator_id is None
            else BuiltinSimulatorId(model.builtin_simulator_id)
        ),
        input_schema_id=model.input_schema_id,
        output_schema_id=model.output_schema_id,
        deterministic_seed=model.deterministic_seed,
        max_steps=model.max_steps,
        max_state_bytes=model.max_state_bytes,
        registered_by=model.registered_by.actor_id,
        created_at=model.created_at,
        governing_policy_hash=model.governing_policy_hash,
    )


def mechanism_to_storage(
    mechanism: VerificationMechanismSpec,
) -> VerificationMechanismSpecRecord:
    if isinstance(mechanism, FormalVerifierSpec):
        category = VerificationMechanismCategory.FORMAL_VERIFIER
    elif isinstance(mechanism, DeterministicCheckerSpec):
        category = VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER
    else:
        category = VerificationMechanismCategory.LEARNED_JUDGE
    return VerificationMechanismSpecRecord(
        mechanism_spec_id=mechanism.mechanism_spec_id,
        hypothesis_version_id=mechanism.hypothesis_version_id,
        mechanism_category=category,
        name=mechanism.name,
        description=mechanism.description,
        specification_hash=mechanism.specification_hash,
        input_schema_id=mechanism.input_schema_id,
        output_schema_id=mechanism.output_schema_id,
        created_by=mechanism.created_by.actor_id,
        created_at=mechanism.created_at,
        governing_policy_hash=mechanism.governing_policy_hash,
    )


def simulation_to_storage(simulation: SimulationResult) -> SimulationResultRecord:
    return SimulationResultRecord(
        simulation_result_id=simulation.simulation_result_id,
        hypothesis_version_id=simulation.hypothesis_version_id,
        model_spec_id=simulation.model_spec_id,
        execution_mode=ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
        input_hash=sha256_hex(canonical_json_bytes(simulation.model_input.model_dump(mode="json"))),
        output_hash=sha256_hex(
            canonical_json_bytes(simulation.model_output.model_dump(mode="json"))
        ),
        deterministic_seed=simulation.deterministic_seed,
        steps=simulation.model_output.steps,
        state_bytes=simulation.model_output.state_bytes,
        completed_at=simulation.completed_at,
        governing_policy_hash=simulation.governing_policy_hash,
    )


def verification_to_storage(
    result: VerificationResult,
    model: ExecutableModelSpecRecord | None,
) -> VerificationResultRecord:
    if isinstance(result, FormalVerificationResult):
        mechanism_category = VerificationMechanismCategory.FORMAL_VERIFIER
        result_category = VerificationResultCategory.FORMAL_VERIFICATION_RESULT
    elif isinstance(result, DeterministicCheckResult):
        mechanism_category = VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER
        result_category = VerificationResultCategory.DETERMINISTIC_CHECK_RESULT
    else:
        mechanism_category = VerificationMechanismCategory.LEARNED_JUDGE
        result_category = VerificationResultCategory.LEARNED_JUDGE_RESULT
    return VerificationResultRecord(
        verification_result_id=result.verification_result_id,
        hypothesis_version_id=result.hypothesis_version_id,
        mechanism_spec_id=result.mechanism_spec_id,
        mechanism_category=mechanism_category,
        result_category=result_category,
        model_spec_id=result.model_spec_id,
        model_execution_mode=None if model is None else model.execution_mode,
        simulation_result_ids=result.simulation_result_ids,
        outcome=VerificationOutcome(result.outcome.value),
        findings=result.findings,
        verified_by=result.provenance.actor.actor_id,
        completed_at=result.provenance.assessed_at,
        governing_policy_hash=result.provenance.governing_policy_hash,
    )


def counterexample_to_storage(
    counterexample: DomainCounterexampleRecord,
    model: ExecutableModelSpecRecord | None,
) -> CounterexampleRecord:
    return CounterexampleRecord(
        counterexample_id=counterexample.counterexample_id,
        hypothesis_version_id=counterexample.hypothesis_version_id,
        model_spec_id=counterexample.model_spec_id,
        model_execution_mode=None if model is None else model.execution_mode,
        simulation_result_ids=counterexample.simulation_result_ids,
        verification_result_ids=counterexample.verification_result_ids,
        evidence_ids=counterexample.evidence_ids,
        description=counterexample.description,
        input_hash=counterexample.input_hash,
        observed_output_hash=counterexample.observed_output_hash,
        expected_output_hash=counterexample.expected_output_hash,
        discovered_by=counterexample.discovered_by.actor_id,
        discovered_at=counterexample.discovered_at,
        governing_policy_hash=counterexample.governing_policy_hash,
    )


def revision_to_storage(revision: RevisionRecord) -> HypothesisRevisionRecord:
    return HypothesisRevisionRecord(
        revision_id=revision.revision_id,
        hypothesis_id=revision.hypothesis_id,
        prior_hypothesis_version_id=revision.prior_hypothesis_version_id,
        prior_version=revision.prior_version,
        resulting_hypothesis_version_id=revision.resulting_hypothesis_version_id,
        resulting_version=revision.resulting_version,
        triggering_verification_result_ids=revision.triggering_verification_result_ids,
        considered_counterexample_ids=revision.considered_counterexample_ids,
        assumptions_added=revision.assumptions_added,
        assumptions_removed=revision.assumptions_removed,
        assumptions_changed=revision.assumptions_changed,
        variables_added=revision.variables_added,
        variables_removed=revision.variables_removed,
        variables_changed=revision.variables_changed,
        mechanism_changes=revision.mechanism_changes,
        preserved_elements=revision.preserved_elements,
        changed_predictions=revision.changed_predictions,
        changed_falsification_conditions=revision.changed_falsification_conditions,
        author_id=revision.author.actor_id,
        revised_at=revision.revised_at,
        governing_policy_hash=revision.governing_policy_hash,
    )


def admission_to_storage(
    decision: object,
) -> HypothesisAdmissionDecisionRecord:
    from super_scientist.domain.hypotheses.models import HypothesisAdmissionDecision

    if not isinstance(decision, HypothesisAdmissionDecision):
        raise TypeError("admission projection requires a typed decision")
    return HypothesisAdmissionDecisionRecord(
        admission_decision_id=decision.admission_decision_id,
        hypothesis_version_id=decision.hypothesis_version_id,
        hypothesis_id=decision.hypothesis_id,
        version=decision.version,
        admission_status=HypothesisAdmissionStatus(decision.imported_pattern_status.value),
        model_spec_ids=decision.model_spec_ids,
        verification_result_ids=decision.verification_result_ids,
        counterexample_ids=decision.counterexample_ids,
        revision_ids=decision.revision_ids,
        outcome=AdmissionDecisionOutcome(decision.outcome.value),
        rationale=decision.rationale,
        decided_by=decision.decided_by.actor_id,
        decided_at=decision.decided_at,
        governing_policy_hash=decision.governing_policy_hash,
    )


class _HypothesisReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def resolve_receipt(
        self, reference: AcceptedHypothesisReceiptRef
    ) -> HypothesisReceipt | None: ...

    def get_hypothesis(self, identifier: str) -> HypothesisVersionRecord | None: ...

    def list_hypotheses(self) -> tuple[HypothesisVersionRecord, ...]: ...

    def get_model(self, identifier: str) -> ExecutableModelSpecRecord | None: ...

    def get_mechanism(self, identifier: str) -> VerificationMechanismSpecRecord | None: ...

    def get_simulation(self, identifier: str) -> SimulationResultRecord | None: ...

    def get_result(self, identifier: str) -> VerificationResultRecord | None: ...

    def get_counterexample(self, identifier: str) -> CounterexampleRecord | None: ...

    def list_counterexamples(self) -> tuple[CounterexampleRecord, ...]: ...

    def get_revision(self, identifier: str) -> HypothesisRevisionRecord | None: ...

    def list_revisions(self) -> tuple[HypothesisRevisionRecord, ...]: ...

    def get_admission(self, identifier: str) -> HypothesisAdmissionDecisionRecord | None: ...

    def get_head(self, hypothesis_id: str) -> tuple[str, int, HypothesisAdmissionStatus] | None: ...

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...

    def get_measurement(self, identifier: str) -> SelfImprovementMeasurementRecord | None: ...

    def get_evaluator_audit(self, identifier: str) -> EvaluatorAuditRecord | None: ...

    def primitive_use_code(self, primitive_version_id: str) -> RejectionCode | None: ...


class _HypothesisWriteCapability(Protocol):
    def append_hypothesis(self, hypothesis: HypothesisSpec) -> None: ...


class _ModelWriteCapability(Protocol):
    def append_model(self, model: ExecutableModelSpec) -> None: ...


class _MechanismWriteCapability(Protocol):
    def append_mechanism(self, mechanism: VerificationMechanismSpec) -> None: ...


class _SimulationWriteCapability(Protocol):
    def append_simulation(self, simulation: SimulationResult) -> None: ...


class _VerificationWriteCapability(Protocol):
    def append_result(self, result: VerificationResult) -> None: ...


class _CounterexampleWriteCapability(Protocol):
    def append_counterexample(self, counterexample: DomainCounterexampleRecord) -> None: ...


class _RevisionWriteCapability(Protocol):
    def append_revision(self, hypothesis: HypothesisSpec, revision: RevisionRecord) -> None: ...


class _AdmissionWriteCapability(Protocol):
    def admit_hypothesis(self, decision: object) -> None: ...


class _StageContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: HypothesisVersionRecord | None
    retained: tuple[HypothesisVersionRecord, ...]
    evidence: tuple[EvidenceRecord | None, ...]
    head: tuple[str, int, HypothesisAdmissionStatus] | None


class _ModelContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    stored_candidate: HypothesisVersionRecord | None
    existing: ExecutableModelSpecRecord | None


class _MechanismContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    stored_candidate: HypothesisVersionRecord | None
    existing: VerificationMechanismSpecRecord | None


class _SimulationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    model_receipt: HypothesisReceipt | None
    stored_candidate: HypothesisVersionRecord | None
    stored_model: ExecutableModelSpecRecord | None
    existing: SimulationResultRecord | None


class _VerificationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    mechanism_receipt: HypothesisReceipt | None
    model_receipt: HypothesisReceipt | None
    simulation_receipts: tuple[HypothesisReceipt | None, ...]
    stored_candidate: HypothesisVersionRecord | None
    stored_mechanism: VerificationMechanismSpecRecord | None
    stored_model: ExecutableModelSpecRecord | None
    stored_simulations: tuple[SimulationResultRecord | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]
    existing: VerificationResultRecord | None


class _CounterexampleContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    model_receipt: HypothesisReceipt | None
    simulation_receipts: tuple[HypothesisReceipt | None, ...]
    result_receipts: tuple[HypothesisReceipt | None, ...]
    stored_candidate: HypothesisVersionRecord | None
    stored_model: ExecutableModelSpecRecord | None
    stored_simulations: tuple[SimulationResultRecord | None, ...]
    stored_results: tuple[VerificationResultRecord | None, ...]
    evidence: tuple[EvidenceRecord | None, ...]
    existing: CounterexampleRecord | None


class _RevisionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    prior_receipt: HypothesisReceipt | None
    result_receipts: tuple[HypothesisReceipt | None, ...]
    counterexample_receipts: tuple[HypothesisReceipt | None, ...]
    stored_prior: HypothesisVersionRecord | None
    stored_results: tuple[VerificationResultRecord | None, ...]
    stored_counterexamples: tuple[CounterexampleRecord | None, ...]
    existing_resulting: HypothesisVersionRecord | None
    existing_revision: HypothesisRevisionRecord | None
    head: tuple[str, int, HypothesisAdmissionStatus] | None


class _AdmissionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    candidate_receipt: HypothesisReceipt | None
    model_receipts: tuple[HypothesisReceipt | None, ...]
    result_receipts: tuple[HypothesisReceipt | None, ...]
    search_receipts: tuple[HypothesisReceipt | None, ...]
    revision_receipts: tuple[HypothesisReceipt | None, ...]
    audit_receipt: HypothesisReceipt | None
    measurement_receipt: HypothesisReceipt | None
    stored_candidate: HypothesisVersionRecord | None
    stored_models: tuple[ExecutableModelSpecRecord | None, ...]
    stored_results: tuple[VerificationResultRecord | None, ...]
    stored_search_results: tuple[VerificationResultRecord | None, ...]
    stored_revisions: tuple[HypothesisRevisionRecord | None, ...]
    all_revisions: tuple[HypothesisRevisionRecord, ...]
    counterexamples: tuple[CounterexampleRecord, ...]
    mechanisms: tuple[VerificationMechanismSpecRecord | None, ...]
    measurement: SelfImprovementMeasurementRecord | None
    evaluator_audit: EvaluatorAuditRecord | None
    existing: HypothesisAdmissionDecisionRecord | None
    head: tuple[str, int, HypothesisAdmissionStatus] | None
    primitive_rejections: tuple[RejectionCode | None, ...]


class ProposeHypothesisVersionHandler:
    proposal_type = "propose_hypothesis_version"

    def build_context(
        self,
        proposal: ProposeHypothesisVersion,
        reads: HandlerReadCapability,
    ) -> _StageContext:
        capability = cast(_HypothesisReadCapability, reads)
        hypothesis = proposal.hypothesis
        return _StageContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_hypothesis(hypothesis.hypothesis_version_id),
            retained=capability.list_hypotheses(),
            evidence=tuple(capability.get_evidence(item) for item in hypothesis.evidence_ids),
            head=capability.get_head(hypothesis.hypothesis_id),
        )

    def decide(
        self,
        proposal: ProposeHypothesisVersion,
        context: _StageContext,
    ) -> TransactionDecision:
        hypothesis = proposal.hypothesis
        authority = hypothesis_mutation_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        if hypothesis.proposer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "hypothesis proposer must match the proposal actor",
            )
        policy = _policy_bound_rejection(
            proposal.proposal_id,
            hypothesis.governing_policy_hash,
            context.active_policy,
            "hypothesis version",
        )
        if policy is not None:
            return policy
        expected = hypothesis_to_storage(hypothesis)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "hypothesis version")
        if (
            hypothesis.version != 1
            or context.head is not None
            or any(item.hypothesis_id == hypothesis.hypothesis_id for item in context.retained)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "only the first version of a new hypothesis may use the proposal stage",
            )
        grounding = _evidence_rejection(
            proposal.proposal_id,
            hypothesis.evidence_ids,
            context.evidence,
        )
        if grounding is not None:
            return grounding
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposeHypothesisVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_HypothesisWriteCapability, writes).append_hypothesis(proposal.hypothesis)


class RegisterExecutableModelHandler:
    proposal_type = "register_executable_model"

    def build_context(
        self,
        proposal: RegisterExecutableModel,
        reads: HandlerReadCapability,
    ) -> _ModelContext:
        capability = cast(_HypothesisReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        candidate = _candidate_from_receipt(candidate_receipt)
        return _ModelContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            existing=capability.get_model(proposal.model_spec.model_spec_id),
        )

    def decide(
        self,
        proposal: RegisterExecutableModel,
        context: _ModelContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        if candidate is None or context.candidate_receipt is None:
            return _receipt_rejection(proposal.proposal_id, "hypothesis candidate")
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=_other_actors(proposal.proposer, candidate.proposer),
        )
        if authority is not None:
            return authority
        model = proposal.model_spec
        if model.registered_by != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "model registrar")
        lineage = _candidate_binding_rejection(
            proposal.proposal_id,
            candidate,
            context.stored_candidate,
            model.hypothesis_version_id,
            model.governing_policy_hash,
            context.candidate_receipt,
            context.active_policy,
        )
        if lineage is not None:
            return lineage
        try:
            expected = model_to_storage(model)
            if model.execution_mode is ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR:
                SimulatorRegistry().resolve(cast(str, model.builtin_simulator_id))
        except (KeyError, TypeError, ValueError):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "model must use inert metadata or one source-controlled simulator",
            )
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "model specification")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RegisterExecutableModel,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_ModelWriteCapability, writes).append_model(proposal.model_spec)


class RegisterVerificationMechanismHandler:
    proposal_type = "register_verification_mechanism"

    def build_context(
        self,
        proposal: RegisterVerificationMechanism,
        reads: HandlerReadCapability,
    ) -> _MechanismContext:
        capability = cast(_HypothesisReadCapability, reads)
        receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        candidate = _candidate_from_receipt(receipt)
        return _MechanismContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=receipt,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            existing=capability.get_mechanism(proposal.mechanism_spec.mechanism_spec_id),
        )

    def decide(
        self,
        proposal: RegisterVerificationMechanism,
        context: _MechanismContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        if candidate is None or context.candidate_receipt is None:
            return _receipt_rejection(proposal.proposal_id, "hypothesis candidate")
        mechanism = proposal.mechanism_spec
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=_other_actors(proposal.proposer, candidate.proposer),
        )
        if authority is not None:
            return authority
        if mechanism.created_by != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "verification mechanism creator")
        lineage = _candidate_binding_rejection(
            proposal.proposal_id,
            candidate,
            context.stored_candidate,
            mechanism.hypothesis_version_id,
            mechanism.governing_policy_hash,
            context.candidate_receipt,
            context.active_policy,
        )
        if lineage is not None:
            return lineage
        expected = mechanism_to_storage(mechanism)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "verification mechanism")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RegisterVerificationMechanism,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_MechanismWriteCapability, writes).append_mechanism(proposal.mechanism_spec)


class RecordSimulationResultHandler:
    proposal_type = "record_simulation_result"

    def build_context(
        self,
        proposal: RecordSimulationResult,
        reads: HandlerReadCapability,
    ) -> _SimulationContext:
        capability = cast(_HypothesisReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        model_receipt = capability.resolve_receipt(proposal.model_receipt)
        candidate = _candidate_from_receipt(candidate_receipt)
        model = _model_from_receipt(model_receipt)
        return _SimulationContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            model_receipt=model_receipt,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            stored_model=(None if model is None else capability.get_model(model.model_spec_id)),
            existing=capability.get_simulation(proposal.simulation_result.simulation_result_id),
        )

    def decide(
        self,
        proposal: RecordSimulationResult,
        context: _SimulationContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        model = _model_from_receipt(context.model_receipt)
        if (
            candidate is None
            or model is None
            or context.candidate_receipt is None
            or context.model_receipt is None
        ):
            return _receipt_rejection(proposal.proposal_id, "simulation lineage")
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=_other_actors(
                proposal.proposer,
                candidate.proposer,
                model.registered_by,
            ),
        )
        if authority is not None:
            return authority
        if model.registered_by != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "simulation model registrar")
        lineage = _candidate_binding_rejection(
            proposal.proposal_id,
            candidate,
            context.stored_candidate,
            model.hypothesis_version_id,
            model.governing_policy_hash,
            context.candidate_receipt,
            context.active_policy,
        )
        if lineage is not None:
            return lineage
        result = proposal.simulation_result
        if (
            context.stored_model != model_to_storage(model)
            or result.hypothesis_version_id != candidate.hypothesis_version_id
            or result.model_spec_id != model.model_spec_id
            or result.governing_policy_hash != context.active_policy.policy_hash
            or result.execution_mode is not ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR
            or model.execution_mode is not ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR
            or result.model_input.schema_id != model.input_schema_id
            or result.model_output.schema_id != model.output_schema_id
            or result.deterministic_seed != model.deterministic_seed
            or result.model_input.deterministic_seed != model.deterministic_seed
            or result.model_output.steps > model.max_steps
            or result.model_output.state_bytes > model.max_state_bytes
            or context.candidate_receipt.audit_sequence >= context.model_receipt.audit_sequence
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "simulation must exactly bind the retained candidate, model, schemas, and bounds",
            )
        try:
            expected_output = SimulatorRegistry().execute(
                model,
                result.model_input,
                output_id=result.model_output.model_output_id,
            )
        except (KeyError, TypeError, ValueError):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "simulation could not be reproduced by the fixed registry",
            )
        if expected_output != result.model_output:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "simulation output does not reproduce under the retained bounded input",
            )
        expected = simulation_to_storage(result)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "simulation result")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordSimulationResult,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_SimulationWriteCapability, writes).append_simulation(proposal.simulation_result)


class RecordVerificationResultHandler:
    proposal_type = "record_verification_result"

    def build_context(
        self,
        proposal: RecordVerificationResult,
        reads: HandlerReadCapability,
    ) -> _VerificationContext:
        capability = cast(_HypothesisReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        mechanism_receipt = capability.resolve_receipt(proposal.mechanism_receipt)
        model_receipt = (
            None
            if proposal.model_receipt is None
            else capability.resolve_receipt(proposal.model_receipt)
        )
        simulation_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.simulation_receipts
        )
        candidate = _candidate_from_receipt(candidate_receipt)
        mechanism = _mechanism_from_receipt(mechanism_receipt)
        model = _model_from_receipt(model_receipt)
        result = proposal.verification_result
        return _VerificationContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            mechanism_receipt=mechanism_receipt,
            model_receipt=model_receipt,
            simulation_receipts=simulation_receipts,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            stored_mechanism=(
                None if mechanism is None else capability.get_mechanism(mechanism.mechanism_spec_id)
            ),
            stored_model=(None if model is None else capability.get_model(model.model_spec_id)),
            stored_simulations=tuple(
                capability.get_simulation(item.simulation_result_id) if item is not None else None
                for item in (_simulation_from_receipt(receipt) for receipt in simulation_receipts)
            ),
            evidence=tuple(
                capability.get_evidence(item) for item in result.provenance.evidence_ids
            ),
            existing=capability.get_result(result.verification_result_id),
        )

    def decide(
        self,
        proposal: RecordVerificationResult,
        context: _VerificationContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        mechanism = _mechanism_from_receipt(context.mechanism_receipt)
        model = _model_from_receipt(context.model_receipt)
        simulations = tuple(
            _simulation_from_receipt(receipt) for receipt in context.simulation_receipts
        )
        result = proposal.verification_result
        if (
            candidate is None
            or mechanism is None
            or context.candidate_receipt is None
            or context.mechanism_receipt is None
            or any(item is None for item in simulations)
            or (proposal.model_receipt is None) != (result.model_spec_id is None)
            or (proposal.model_receipt is not None and model is None)
        ):
            return _receipt_rejection(proposal.proposal_id, "verification lineage")
        authority_actors = _other_actors(
            proposal.proposer,
            candidate.proposer,
            mechanism.created_by,
            *(() if model is None else (model.registered_by,)),
        )
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=authority_actors,
        )
        if authority is not None:
            return authority
        if result.provenance.actor != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "verification actor")
        lineage = _candidate_binding_rejection(
            proposal.proposal_id,
            candidate,
            context.stored_candidate,
            result.hypothesis_version_id,
            result.provenance.governing_policy_hash,
            context.candidate_receipt,
            context.active_policy,
        )
        if lineage is not None:
            return lineage
        retained_simulations = cast(tuple[SimulationResult, ...], simulations)
        expected_simulation_ids = tuple(item.simulation_result_id for item in retained_simulations)
        expected_model = None if model is None else model_to_storage(model)
        if (
            context.stored_mechanism != mechanism_to_storage(mechanism)
            or context.stored_model != expected_model
            or tuple(context.stored_simulations)
            != tuple(simulation_to_storage(item) for item in retained_simulations)
            or result.mechanism_spec_id != mechanism.mechanism_spec_id
            or result.model_spec_id != (None if model is None else model.model_spec_id)
            or result.simulation_result_ids != expected_simulation_ids
            or any(
                item.hypothesis_version_id != candidate.hypothesis_version_id
                for item in retained_simulations
            )
            or context.candidate_receipt.audit_sequence >= context.mechanism_receipt.audit_sequence
            or any(
                context.mechanism_receipt.audit_sequence >= receipt.audit_sequence
                for receipt in context.simulation_receipts
                if receipt is not None
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "verification must exactly bind retained candidate, mechanism, model, and runs",
            )
        quality = _verification_quality_rejection(
            proposal.proposal_id,
            result,
            mechanism,
            candidate,
            context.evidence,
            context.active_policy,
        )
        if quality is not None:
            return quality
        expected = verification_to_storage(result, expected_model)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "verification result")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordVerificationResult,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_VerificationWriteCapability, writes).append_result(proposal.verification_result)


class RecordCounterexampleHandler:
    proposal_type = "record_counterexample"

    def build_context(
        self,
        proposal: RecordCounterexample,
        reads: HandlerReadCapability,
    ) -> _CounterexampleContext:
        capability = cast(_HypothesisReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        model_receipt = (
            None
            if proposal.model_receipt is None
            else capability.resolve_receipt(proposal.model_receipt)
        )
        simulation_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.simulation_receipts
        )
        result_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.verification_result_receipts
        )
        candidate = _candidate_from_receipt(candidate_receipt)
        model = _model_from_receipt(model_receipt)
        simulations = tuple(_simulation_from_receipt(receipt) for receipt in simulation_receipts)
        results = tuple(_verification_from_receipt(receipt) for receipt in result_receipts)
        counterexample = proposal.counterexample
        return _CounterexampleContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            model_receipt=model_receipt,
            simulation_receipts=simulation_receipts,
            result_receipts=result_receipts,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            stored_model=(None if model is None else capability.get_model(model.model_spec_id)),
            stored_simulations=tuple(
                None if item is None else capability.get_simulation(item.simulation_result_id)
                for item in simulations
            ),
            stored_results=tuple(
                None if item is None else capability.get_result(item.verification_result_id)
                for item in results
            ),
            evidence=tuple(capability.get_evidence(item) for item in counterexample.evidence_ids),
            existing=capability.get_counterexample(counterexample.counterexample_id),
        )

    def decide(
        self,
        proposal: RecordCounterexample,
        context: _CounterexampleContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        model = _model_from_receipt(context.model_receipt)
        simulations = tuple(
            _simulation_from_receipt(receipt) for receipt in context.simulation_receipts
        )
        results = tuple(_verification_from_receipt(receipt) for receipt in context.result_receipts)
        if (
            candidate is None
            or context.candidate_receipt is None
            or any(item is None for item in simulations)
            or any(item is None for item in results)
            or (proposal.model_receipt is None) != (proposal.counterexample.model_spec_id is None)
            or (proposal.model_receipt is not None and model is None)
        ):
            return _receipt_rejection(proposal.proposal_id, "counterexample lineage")
        retained_results = cast(tuple[VerificationResult, ...], results)
        retained_simulations = cast(tuple[SimulationResult, ...], simulations)
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=_other_actors(
                proposal.proposer,
                candidate.proposer,
                *(item.provenance.actor for item in retained_results),
            ),
        )
        if authority is not None:
            return authority
        counterexample = proposal.counterexample
        if counterexample.discovered_by != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "counterexample discoverer")
        lineage = _candidate_binding_rejection(
            proposal.proposal_id,
            candidate,
            context.stored_candidate,
            counterexample.hypothesis_version_id,
            counterexample.governing_policy_hash,
            context.candidate_receipt,
            context.active_policy,
        )
        if lineage is not None:
            return lineage
        expected_model = None if model is None else model_to_storage(model)
        if (
            context.stored_model != expected_model
            or tuple(context.stored_simulations)
            != tuple(simulation_to_storage(item) for item in retained_simulations)
            or tuple(context.stored_results)
            != tuple(verification_to_storage(item, expected_model) for item in retained_results)
            or counterexample.model_spec_id != (None if model is None else model.model_spec_id)
            or counterexample.simulation_result_ids
            != tuple(item.simulation_result_id for item in retained_simulations)
            or counterexample.verification_result_ids
            != tuple(item.verification_result_id for item in retained_results)
            or any(
                item.hypothesis_version_id != candidate.hypothesis_version_id
                or item.outcome is not DomainVerificationOutcome.FAIL
                or not item.counterexample_search_performed
                or not item.counterexample_found
                for item in retained_results
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "counterexample must bind failed retained search results for one candidate",
            )
        chronology = _strict_stage_order(
            context.candidate_receipt,
            *(() if context.model_receipt is None else (context.model_receipt,)),
            *(receipt for receipt in context.simulation_receipts if receipt is not None),
            *(receipt for receipt in context.result_receipts if receipt is not None),
        )
        if not chronology:
            return _chronology_rejection(proposal.proposal_id, "counterexample receipts")
        grounding = _evidence_rejection(
            proposal.proposal_id,
            counterexample.evidence_ids,
            context.evidence,
        )
        if grounding is not None:
            return grounding
        expected = counterexample_to_storage(counterexample, expected_model)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "counterexample")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordCounterexample,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_CounterexampleWriteCapability, writes).append_counterexample(proposal.counterexample)


class ReviseHypothesisHandler:
    proposal_type = "revise_hypothesis"

    def build_context(
        self,
        proposal: ReviseHypothesis,
        reads: HandlerReadCapability,
    ) -> _RevisionContext:
        capability = cast(_HypothesisReadCapability, reads)
        prior_receipt = capability.resolve_receipt(proposal.prior_hypothesis_receipt)
        result_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.triggering_result_receipts
        )
        counterexample_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.counterexample_receipts
        )
        prior = _candidate_from_receipt(prior_receipt)
        results = tuple(_verification_from_receipt(receipt) for receipt in result_receipts)
        counterexamples = tuple(
            _counterexample_from_receipt(receipt) for receipt in counterexample_receipts
        )
        resulting = proposal.resulting_hypothesis
        revision = proposal.revision
        return _RevisionContext(
            active_policy=capability.policy_snapshot(),
            prior_receipt=prior_receipt,
            result_receipts=result_receipts,
            counterexample_receipts=counterexample_receipts,
            stored_prior=(
                None if prior is None else capability.get_hypothesis(prior.hypothesis_version_id)
            ),
            stored_results=tuple(
                None if item is None else capability.get_result(item.verification_result_id)
                for item in results
            ),
            stored_counterexamples=tuple(
                None if item is None else capability.get_counterexample(item.counterexample_id)
                for item in counterexamples
            ),
            existing_resulting=capability.get_hypothesis(resulting.hypothesis_version_id),
            existing_revision=capability.get_revision(revision.revision_id),
            head=(None if prior is None else capability.get_head(prior.hypothesis_id)),
        )

    def decide(
        self,
        proposal: ReviseHypothesis,
        context: _RevisionContext,
    ) -> TransactionDecision:
        prior = _candidate_from_receipt(context.prior_receipt)
        results = tuple(_verification_from_receipt(item) for item in context.result_receipts)
        counterexamples = tuple(
            _counterexample_from_receipt(item) for item in context.counterexample_receipts
        )
        if (
            prior is None
            or context.prior_receipt is None
            or any(item is None for item in results)
            or any(item is None for item in counterexamples)
        ):
            return _receipt_rejection(proposal.proposal_id, "hypothesis revision lineage")
        retained_results = cast(tuple[VerificationResult, ...], results)
        retained_counterexamples = cast(tuple[DomainCounterexampleRecord, ...], counterexamples)
        revision = proposal.revision
        resulting = proposal.resulting_hypothesis
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=_other_actors(
                proposal.proposer,
                prior.proposer,
                resulting.proposer,
                *(item.provenance.actor for item in retained_results),
                *(item.discovered_by for item in retained_counterexamples),
            ),
        )
        if authority is not None:
            return authority
        if revision.author != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "hypothesis revision author")
        if (
            context.stored_prior != hypothesis_to_storage(prior)
            or context.head
            != (
                prior.hypothesis_version_id,
                prior.version,
                HypothesisAdmissionStatus(prior.imported_pattern_status.value),
            )
            or not all(
                _verification_record_matches(stored, item)
                for stored, item in zip(
                    context.stored_results,
                    retained_results,
                    strict=True,
                )
            )
            or not all(
                _counterexample_record_matches(stored, item)
                for stored, item in zip(
                    context.stored_counterexamples,
                    retained_counterexamples,
                    strict=True,
                )
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "revision must bind the exact admitted predecessor and retained failures",
            )
        if any(
            item.hypothesis_version_id != prior.hypothesis_version_id
            or item.outcome is DomainVerificationOutcome.PASS
            for item in retained_results
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "revision triggers must be failed or abstained checks of the prior version",
            )
        if any(
            item.hypothesis_version_id != prior.hypothesis_version_id
            for item in retained_counterexamples
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "revision counterexamples must belong to the prior version",
            )
        if (
            revision.hypothesis_id != prior.hypothesis_id
            or revision.prior_hypothesis_version_id != prior.hypothesis_version_id
            or revision.prior_version != prior.version
            or revision.resulting_hypothesis_version_id != resulting.hypothesis_version_id
            or revision.resulting_version != resulting.version
            or resulting.hypothesis_id != prior.hypothesis_id
            or resulting.version != prior.version + 1
            or resulting.governing_policy_hash != context.active_policy.policy_hash
            or revision.governing_policy_hash != context.active_policy.policy_hash
            or revision.triggering_verification_result_ids
            != tuple(item.verification_result_id for item in retained_results)
            or revision.considered_counterexample_ids
            != tuple(item.counterexample_id for item in retained_counterexamples)
            or resulting.predictions == prior.predictions
            or resulting.falsification_conditions == prior.falsification_conditions
            or revision.changed_predictions != resulting.predictions
            or revision.changed_falsification_conditions != resulting.falsification_conditions
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "revision must be contiguous and explicitly change predictions and falsification",
            )
        if not _strict_stage_order(
            context.prior_receipt,
            *(item for item in context.result_receipts if item is not None),
            *(item for item in context.counterexample_receipts if item is not None),
        ):
            return _chronology_rejection(proposal.proposal_id, "hypothesis revision receipts")
        expected_hypothesis = hypothesis_to_storage(resulting)
        expected_revision = revision_to_storage(revision)
        if context.existing_resulting is not None or context.existing_revision is not None:
            if (
                context.existing_resulting == expected_hypothesis
                and context.existing_revision == expected_revision
            ):
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "hypothesis revision")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ReviseHypothesis,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_RevisionWriteCapability, writes).append_revision(
            proposal.resulting_hypothesis,
            proposal.revision,
        )


class AdmitHypothesisHandler:
    proposal_type = "admit_hypothesis"

    def build_context(
        self,
        proposal: AdmitHypothesis,
        reads: HandlerReadCapability,
    ) -> _AdmissionContext:
        capability = cast(_HypothesisReadCapability, reads)
        candidate_receipt = capability.resolve_receipt(proposal.hypothesis_receipt)
        model_receipts = tuple(capability.resolve_receipt(item) for item in proposal.model_receipts)
        result_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.verification_result_receipts
        )
        search_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.counterexample_search_receipts
        )
        revision_receipts = tuple(
            capability.resolve_receipt(item) for item in proposal.revision_receipts
        )
        audit_receipt = capability.resolve_receipt(proposal.evaluator_audit_receipt)
        measurement_receipt = capability.resolve_receipt(proposal.measurement_receipt)
        candidate = _candidate_from_receipt(candidate_receipt)
        models = tuple(_model_from_receipt(item) for item in model_receipts)
        results = tuple(_verification_from_receipt(item) for item in result_receipts)
        searches = tuple(_verification_from_receipt(item) for item in search_receipts)
        revisions = tuple(_revision_from_receipt(item) for item in revision_receipts)
        audit = _audit_from_receipt(audit_receipt)
        measurement = _measurement_from_receipt(measurement_receipt)
        return _AdmissionContext(
            active_policy=capability.policy_snapshot(),
            candidate_receipt=candidate_receipt,
            model_receipts=model_receipts,
            result_receipts=result_receipts,
            search_receipts=search_receipts,
            revision_receipts=revision_receipts,
            audit_receipt=audit_receipt,
            measurement_receipt=measurement_receipt,
            stored_candidate=(
                None
                if candidate is None
                else capability.get_hypothesis(candidate.hypothesis_version_id)
            ),
            stored_models=tuple(
                None if item is None else capability.get_model(item.model_spec_id)
                for item in models
            ),
            stored_results=tuple(
                None if item is None else capability.get_result(item.verification_result_id)
                for item in results
            ),
            stored_search_results=tuple(
                None if item is None else capability.get_result(item.verification_result_id)
                for item in searches
            ),
            stored_revisions=tuple(
                None if item is None else capability.get_revision(item.revision_id)
                for item in revisions
            ),
            all_revisions=capability.list_revisions(),
            counterexamples=capability.list_counterexamples(),
            mechanisms=tuple(
                None if item is None else capability.get_mechanism(item.mechanism_spec_id)
                for item in results
            ),
            measurement=(
                None
                if measurement is None
                else capability.get_measurement(measurement.measurement_id)
            ),
            evaluator_audit=(
                None if audit is None else capability.get_evaluator_audit(audit.evaluator_audit_id)
            ),
            existing=capability.get_admission(proposal.admission_decision.admission_decision_id),
            head=(None if candidate is None else capability.get_head(candidate.hypothesis_id)),
            primitive_rejections=(
                ()
                if candidate is None
                else tuple(
                    capability.primitive_use_code(item) for item in candidate.primitive_version_ids
                )
            ),
        )

    def decide(
        self,
        proposal: AdmitHypothesis,
        context: _AdmissionContext,
    ) -> TransactionDecision:
        candidate = _candidate_from_receipt(context.candidate_receipt)
        models = tuple(_model_from_receipt(item) for item in context.model_receipts)
        results = tuple(_verification_from_receipt(item) for item in context.result_receipts)
        searches = tuple(_verification_from_receipt(item) for item in context.search_receipts)
        revisions = tuple(_revision_from_receipt(item) for item in context.revision_receipts)
        audit = _audit_from_receipt(context.audit_receipt)
        measurement = _measurement_from_receipt(context.measurement_receipt)
        if (
            candidate is None
            or context.candidate_receipt is None
            or any(item is None for item in models)
            or any(item is None for item in results)
            or any(item is None for item in searches)
            or any(item is None for item in revisions)
            or audit is None
            or measurement is None
            or context.audit_receipt is None
            or context.measurement_receipt is None
        ):
            return _receipt_rejection(proposal.proposal_id, "hypothesis admission lineage")
        retained_models = cast(tuple[ExecutableModelSpec, ...], models)
        retained_results = cast(tuple[VerificationResult, ...], results)
        retained_searches = cast(tuple[VerificationResult, ...], searches)
        retained_revisions = cast(tuple[RevisionRecord, ...], revisions)
        actors = _other_actors(
            proposal.proposer,
            candidate.proposer,
            *(item.registered_by for item in retained_models),
            *(item.provenance.actor for item in retained_results),
            audit.auditor,
            audit.evaluator,
        )
        authority = hypothesis_mutation_authority_rejection(
            proposal,
            context.active_policy,
            authority_actors=actors,
        )
        if authority is not None:
            return authority
        decision = proposal.admission_decision
        if decision.decided_by != proposal.proposer:
            return _actor_rejection(proposal.proposal_id, "hypothesis integrator")
        approval = proposal.approval
        if approval is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "hypothesis admission requires independent human approval",
            )
        retained_model_records = tuple(model_to_storage(item) for item in retained_models)
        retained_result_records = tuple(
            verification_to_storage(
                item,
                next(
                    (
                        model_record
                        for model_record in retained_model_records
                        if model_record.model_spec_id == item.model_spec_id
                    ),
                    None,
                ),
            )
            for item in retained_results
        )
        retained_search_records = tuple(
            verification_to_storage(
                item,
                next(
                    (
                        model_record
                        for model_record in retained_model_records
                        if model_record.model_spec_id == item.model_spec_id
                    ),
                    None,
                ),
            )
            for item in retained_searches
        )
        if (
            context.stored_candidate != hypothesis_to_storage(candidate)
            or tuple(context.stored_models) != retained_model_records
            or tuple(context.stored_results) != retained_result_records
            or tuple(context.stored_search_results) != retained_search_records
            or tuple(context.stored_revisions)
            != tuple(revision_to_storage(item) for item in retained_revisions)
            or any(item is None for item in context.mechanisms)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "admission receipts must resolve to exact retained hypothesis records",
            )
        lineage = _admission_lineage_rejection(
            proposal,
            context,
            candidate,
            retained_models,
            retained_results,
            retained_searches,
            retained_revisions,
        )
        if lineage is not None:
            return lineage
        support = _admission_support_rejection(
            proposal,
            context,
            candidate,
            retained_results,
            audit,
            measurement,
            approval.approver,
        )
        if support is not None:
            return support
        if any(item is not None for item in context.primitive_rejections):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED,
                "hypothesis admission requires exact admitted primitive heads",
            )
        receipts = (
            context.candidate_receipt,
            *cast(tuple[HypothesisReceipt, ...], context.model_receipts),
            *cast(tuple[HypothesisReceipt, ...], context.result_receipts),
            context.audit_receipt,
            context.measurement_receipt,
        )
        if not _strict_stage_order(*receipts):
            return _chronology_rejection(proposal.proposal_id, "hypothesis admission receipts")
        expected = admission_to_storage(decision)
        if context.existing is not None:
            if context.existing == expected:
                return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            return _stable_conflict(proposal.proposal_id, "hypothesis admission")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: AdmitHypothesis,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        cast(_AdmissionWriteCapability, writes).admit_hypothesis(
            admission_to_storage(proposal.admission_decision)
        )


def fixed_hypothesis_handlers() -> tuple[FixedHypothesisHandler, ...]:
    return (  # type: ignore[return-value]
        ProposeHypothesisVersionHandler(),
        RegisterExecutableModelHandler(),
        RegisterVerificationMechanismHandler(),
        RecordSimulationResultHandler(),
        RecordVerificationResultHandler(),
        RecordCounterexampleHandler(),
        ReviseHypothesisHandler(),
        AdmitHypothesisHandler(),
    )


def hypothesis_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    artifact_store: ArtifactStore,
) -> HypothesisCapabilitySet:
    reader = HypothesisReader(
        active_policy=active_policy,
        receipts=HypothesisReceiptReader(connection),
        versions=HypothesisVersionRepository(connection),
        models=ExecutableModelSpecRepository(connection),
        mechanisms=VerificationMechanismSpecRepository(connection),
        simulations=SimulationResultRepository(connection),
        results=VerificationResultRepository(connection),
        counterexamples=CounterexampleRecordRepository(connection),
        revisions=HypothesisRevisionRepository(connection),
        admissions=HypothesisAdmissionDecisionRepository(connection),
        heads=HypothesisHeadRepository(connection),
        evidence=RetainedHypothesisEvidenceReader(
            EvidenceRepository(connection),
            artifact_store,
        ),
        measurements=SelfImprovementMeasurementRepository(connection),
        evaluator_audits=EvaluatorAuditRepository(connection),
        primitive_resolver=StoredHypothesisPrimitiveResolver(
            PrimitiveVersionRepository(connection),
            PrimitiveHeadRepository(connection),
        ),
    )
    writes: object
    if isinstance(proposal, ProposeHypothesisVersion):
        writes = HypothesisAppender(reader.versions)
    elif isinstance(proposal, RegisterExecutableModel):
        writes = ModelAppender(reader.models)
    elif isinstance(proposal, RegisterVerificationMechanism):
        writes = MechanismAppender(reader.mechanisms)
    elif isinstance(proposal, RecordSimulationResult):
        writes = SimulationAppender(reader.simulations)
    elif isinstance(proposal, RecordVerificationResult):
        writes = VerificationAppender(reader.results, reader.models)
    elif isinstance(proposal, RecordCounterexample):
        writes = CounterexampleAppender(reader.counterexamples, reader.models)
    elif isinstance(proposal, ReviseHypothesis):
        writes = RevisionAppender(reader.versions, reader.revisions)
    elif isinstance(proposal, AdmitHypothesis):
        writes = HypothesisAdmissionWriter(reader.admissions, reader.heads)
    else:
        raise TypeError(f"no fixed hypothesis capability for proposal: {type(proposal)!r}")
    return HypothesisCapabilitySet(
        reads=cast(HandlerReadCapability, reader),
        writes=cast(HandlerWriteCapability, writes),
    )


def _candidate_from_receipt(receipt: HypothesisReceipt | None) -> HypothesisSpec | None:
    if receipt is None:
        return None
    proposal = receipt.proposal
    if isinstance(proposal, ProposeHypothesisVersion):
        return proposal.hypothesis
    if isinstance(proposal, ReviseHypothesis):
        return proposal.resulting_hypothesis
    return None


def _model_from_receipt(receipt: HypothesisReceipt | None) -> ExecutableModelSpec | None:
    if receipt is None or not isinstance(receipt.proposal, RegisterExecutableModel):
        return None
    return receipt.proposal.model_spec


def _mechanism_from_receipt(
    receipt: HypothesisReceipt | None,
) -> VerificationMechanismSpec | None:
    if receipt is None or not isinstance(receipt.proposal, RegisterVerificationMechanism):
        return None
    return receipt.proposal.mechanism_spec


def _simulation_from_receipt(receipt: HypothesisReceipt | None) -> SimulationResult | None:
    if receipt is None or not isinstance(receipt.proposal, RecordSimulationResult):
        return None
    return receipt.proposal.simulation_result


def _verification_from_receipt(receipt: HypothesisReceipt | None) -> VerificationResult | None:
    if receipt is None or not isinstance(receipt.proposal, RecordVerificationResult):
        return None
    return receipt.proposal.verification_result


def _counterexample_from_receipt(
    receipt: HypothesisReceipt | None,
) -> DomainCounterexampleRecord | None:
    if receipt is None or not isinstance(receipt.proposal, RecordCounterexample):
        return None
    return receipt.proposal.counterexample


def _revision_from_receipt(receipt: HypothesisReceipt | None) -> RevisionRecord | None:
    if receipt is None or not isinstance(receipt.proposal, ReviseHypothesis):
        return None
    return receipt.proposal.revision


def _audit_from_receipt(receipt: HypothesisReceipt | None) -> EvaluatorAuditRecord | None:
    if receipt is None or not isinstance(receipt.proposal, RecordEvaluatorAudit):
        return None
    return receipt.proposal.evaluator_audit


def _measurement_from_receipt(
    receipt: HypothesisReceipt | None,
) -> SelfImprovementMeasurementRecord | None:
    if receipt is None or not isinstance(receipt.proposal, RecordSelfImprovementMeasurement):
        return None
    return receipt.proposal.measurement


def _candidate_binding_rejection(
    proposal_id: str,
    candidate: HypothesisSpec,
    stored_candidate: HypothesisVersionRecord | None,
    hypothesis_version_id: str,
    governing_policy_hash: str,
    receipt: HypothesisReceipt,
    active_policy: PolicySnapshot,
) -> TransactionDecision | None:
    if (
        stored_candidate != hypothesis_to_storage(candidate)
        or hypothesis_version_id != candidate.hypothesis_version_id
        or governing_policy_hash != active_policy.policy_hash
        or receipt.governing_policy_hash != active_policy.policy_hash
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "stage must exactly bind one retained policy-governed hypothesis candidate",
        )
    return None


def _verification_quality_rejection(
    proposal_id: str,
    result: VerificationResult,
    mechanism: VerificationMechanismSpec,
    candidate: HypothesisSpec,
    evidence: tuple[EvidenceRecord | None, ...],
    active_policy: PolicySnapshot,
) -> TransactionDecision | None:
    provenance = result.provenance
    exact_category = (
        (
            isinstance(result, FormalVerificationResult)
            and isinstance(mechanism, FormalVerifierSpec)
            and provenance.category is VerificationLevel.FORMAL_VERIFIER
        )
        or (
            isinstance(result, DeterministicCheckResult)
            and isinstance(mechanism, DeterministicCheckerSpec)
            and provenance.category is VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        )
        or (
            isinstance(result, LearnedJudgeResult)
            and isinstance(mechanism, LearnedJudgeSpec)
            and provenance.category
            in {
                VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
                VerificationLevel.RUBRIC_JUDGE,
            }
        )
    )
    if (
        not exact_category
        or mechanism.created_by != provenance.actor
        or mechanism.hypothesis_version_id != candidate.hypothesis_version_id
        or mechanism.governing_policy_hash != active_policy.policy_hash
        or provenance.governing_policy_hash != active_policy.policy_hash
        or provenance.proposer_relationship is not ActorRelationship.INDEPENDENT
        or not are_independent(candidate.proposer, provenance.actor)
        or provenance.evidence_ids != candidate.evidence_ids
        or mechanism.mechanism_spec_id not in provenance.checks_run
        or (
            isinstance(result, DeterministicCheckResult)
            and (
                not isinstance(mechanism, DeterministicCheckerSpec)
                or not result.counterexample_search_performed
                or result.checked_invariants != mechanism.checked_invariants
            )
        )
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "verification requires exact independently specified provenance and search evidence",
        )
    return _evidence_rejection(proposal_id, provenance.evidence_ids, evidence)


def _admission_lineage_rejection(
    proposal: AdmitHypothesis,
    context: _AdmissionContext,
    candidate: HypothesisSpec,
    models: tuple[ExecutableModelSpec, ...],
    results: tuple[VerificationResult, ...],
    searches: tuple[VerificationResult, ...],
    revisions: tuple[RevisionRecord, ...],
) -> TransactionDecision | None:
    decision = proposal.admission_decision
    candidate_counterexamples = tuple(
        item
        for item in context.counterexamples
        if item.hypothesis_version_id == candidate.hypothesis_version_id
    )
    relevant_revisions = tuple(
        sorted(
            (
                item
                for item in context.all_revisions
                if item.hypothesis_id == candidate.hypothesis_id
                and item.resulting_version <= candidate.version
            ),
            key=lambda item: item.resulting_version,
        )
    )
    if (
        candidate.imported_pattern_status is not ImportedPatternStatus.TRANSFER_VALIDATED
        or decision.imported_pattern_status is not ImportedPatternStatus.TRANSFER_VALIDATED
        or decision.outcome is not AdmissionOutcome.ACCEPT
        or decision.hypothesis_version_id != candidate.hypothesis_version_id
        or decision.hypothesis_id != candidate.hypothesis_id
        or decision.version != candidate.version
        or decision.model_spec_ids != tuple(item.model_spec_id for item in models)
        or decision.verification_result_ids
        != tuple(item.verification_result_id for item in results)
        or decision.counterexample_search_result_ids
        != tuple(item.verification_result_id for item in searches)
        or decision.counterexample_ids
        != tuple(item.counterexample_id for item in candidate_counterexamples)
        or decision.revision_ids != tuple(item.revision_id for item in revisions)
        or decision.rollback_hypothesis_version_id != proposal.rollback_hypothesis_version_id
        or decision.governing_policy_hash != context.active_policy.policy_hash
        or candidate_counterexamples
        or not any(
            item.execution_mode is ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR for item in models
        )
        or any(item.hypothesis_version_id != candidate.hypothesis_version_id for item in models)
        or any(
            item.hypothesis_version_id != candidate.hypothesis_version_id
            or item.outcome is not DomainVerificationOutcome.PASS
            for item in results
        )
        or any(
            not isinstance(item, DeterministicCheckResult)
            or item.outcome is not DomainVerificationOutcome.PASS
            or not item.counterexample_search_performed
            or item.counterexample_found
            for item in searches
        )
        or relevant_revisions != tuple(revision_to_storage(item) for item in revisions)
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "admission requires transfer validation, complete revision history, "
            "and no counterexample",
        )
    if candidate.version == 1:
        if (
            context.head is not None
            or proposal.rollback_hypothesis_version_id is not None
            or revisions
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "first admission cannot replace a head or name revision history",
            )
    else:
        if (
            context.head is None
            or proposal.rollback_hypothesis_version_id != context.head[0]
            or context.head[1] != candidate.version - 1
            or not revisions
            or revisions[-1].resulting_hypothesis_version_id != candidate.hypothesis_version_id
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "successor admission must advance the exact current rollback head",
            )
        for prior, current in pairwise(revisions):
            if (
                prior.resulting_hypothesis_version_id != current.prior_hypothesis_version_id
                or prior.resulting_version != current.prior_version
            ):
                return _rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_LINEAGE,
                    "revision receipts must form one contiguous retained chain",
                )
    return None


def _admission_support_rejection(
    proposal: AdmitHypothesis,
    context: _AdmissionContext,
    candidate: HypothesisSpec,
    results: tuple[VerificationResult, ...],
    audit: EvaluatorAuditRecord,
    measurement: SelfImprovementMeasurementRecord,
    approver: ActorIdentity,
) -> TransactionDecision | None:
    evidence_ids = candidate.evidence_ids
    result_ids = tuple(item.verification_result_id for item in results)
    metric_source_ids = tuple(
        dict.fromkeys(
            item.source_id for item in (*measurement.protected_metrics, *measurement.countermetrics)
        )
    )
    baseline = proposal.rollback_hypothesis_version_id or candidate.hypothesis_version_id
    if (
        context.evaluator_audit != audit
        or context.measurement != measurement
        or measurement.decision is not MeasurementDecision.ACCEPTED
        or measurement.classification != proposal.classification
        or measurement.proposer != candidate.proposer
        or measurement.candidate_version_id != candidate.hypothesis_version_id
        or measurement.baseline_version_id != baseline
        or measurement.rollback_target_id != baseline
        or measurement.evaluator_audit_id != audit.evaluator_audit_id
        or measurement.evaluator != audit.evaluator
        or measurement.evaluator_version != audit.evaluator_version
        or measurement.decision_authority != approver
        or measurement.governing_policy_hash != context.active_policy.policy_hash
        or ExternalGrounding.CONTROLLED_EXPERIMENT not in measurement.grounding
        or audit.result is not AssessmentOutcome.PASSED
        or audit.proposer != candidate.proposer
        or audit.candidate_producer != candidate.proposer
        or audit.evidence_ids != evidence_ids
        or audit.checks_run != result_ids
        or audit.governing_policy_hash != context.active_policy.policy_hash
        or metric_source_ids != evidence_ids
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "admission support must exactly bind the candidate, checks, evidence, and rollback",
        )
    return None


def _verification_record_matches(
    stored: VerificationResultRecord | None,
    result: VerificationResult,
) -> bool:
    if stored is None:
        return False
    if isinstance(result, FormalVerificationResult):
        mechanism_category = VerificationMechanismCategory.FORMAL_VERIFIER
        result_category = VerificationResultCategory.FORMAL_VERIFICATION_RESULT
    elif isinstance(result, DeterministicCheckResult):
        mechanism_category = VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER
        result_category = VerificationResultCategory.DETERMINISTIC_CHECK_RESULT
    else:
        mechanism_category = VerificationMechanismCategory.LEARNED_JUDGE
        result_category = VerificationResultCategory.LEARNED_JUDGE_RESULT
    return bool(
        stored.verification_result_id == result.verification_result_id
        and stored.hypothesis_version_id == result.hypothesis_version_id
        and stored.mechanism_spec_id == result.mechanism_spec_id
        and stored.mechanism_category is mechanism_category
        and stored.result_category is result_category
        and stored.model_spec_id == result.model_spec_id
        and (stored.model_execution_mode is None) == (result.model_spec_id is None)
        and stored.simulation_result_ids == result.simulation_result_ids
        and stored.outcome.value == result.outcome.value
        and stored.findings == result.findings
        and stored.verified_by == result.provenance.actor.actor_id
        and stored.completed_at == result.provenance.assessed_at
        and stored.governing_policy_hash == result.provenance.governing_policy_hash
    )


def _counterexample_record_matches(
    stored: CounterexampleRecord | None,
    counterexample: DomainCounterexampleRecord,
) -> bool:
    return bool(
        stored is not None
        and stored.counterexample_id == counterexample.counterexample_id
        and stored.hypothesis_version_id == counterexample.hypothesis_version_id
        and stored.model_spec_id == counterexample.model_spec_id
        and (stored.model_execution_mode is None) == (counterexample.model_spec_id is None)
        and stored.simulation_result_ids == counterexample.simulation_result_ids
        and stored.verification_result_ids == counterexample.verification_result_ids
        and stored.evidence_ids == counterexample.evidence_ids
        and stored.description == counterexample.description
        and stored.input_hash == counterexample.input_hash
        and stored.observed_output_hash == counterexample.observed_output_hash
        and stored.expected_output_hash == counterexample.expected_output_hash
        and stored.discovered_by == counterexample.discovered_by.actor_id
        and stored.discovered_at == counterexample.discovered_at
        and stored.governing_policy_hash == counterexample.governing_policy_hash
    )


def _evidence_rejection(
    proposal_id: str,
    expected_ids: tuple[str, ...],
    evidence: tuple[EvidenceRecord | None, ...],
) -> TransactionDecision | None:
    if len(expected_ids) != len(evidence) or any(item is None for item in evidence):
        return _rejected(
            proposal_id,
            RejectionCode.MISSING_EVIDENCE,
            "hypothesis stage requires every exact retained evidence record",
        )
    retained = cast(tuple[EvidenceRecord, ...], evidence)
    try:
        valid = all(
            item.evidence_id == identifier
            and item.verification_state is VerificationState.HASH_VERIFIED
            and parse_external_grounding(item) is ExternalGrounding.CONTROLLED_EXPERIMENT
            for identifier, item in zip(expected_ids, retained, strict=True)
        )
    except ValueError:
        valid = False
    if not valid:
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "hypothesis evidence must be hash-verified controlled-experiment evidence",
        )
    return None


def _policy_bound_rejection(
    proposal_id: str,
    governing_policy_hash: str,
    active_policy: PolicySnapshot,
    label: str,
) -> TransactionDecision | None:
    if governing_policy_hash == active_policy.policy_hash:
        return None
    return _rejected(
        proposal_id,
        RejectionCode.POLICY_HASH_MISMATCH,
        f"{label} must name the active policy",
    )


def _other_actors(
    proposer: ActorIdentity,
    *actors: ActorIdentity,
) -> tuple[ActorIdentity, ...]:
    retained: list[ActorIdentity] = []
    for actor in actors:
        if actor == proposer or actor in retained:
            continue
        retained.append(actor)
    return tuple(retained)


def _strict_stage_order(*receipts: HypothesisReceipt) -> bool:
    sequences = tuple(item.audit_sequence for item in receipts)
    return len(sequences) == len(set(sequences)) and sequences == tuple(sorted(sequences))


def _is_receipt_proposal(value: Proposal) -> TypeGuard[HypothesisReceiptProposal]:
    return isinstance(
        value,
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
    )


def _audit_policy_hash(event: AuditEvent) -> Sha256Hex | None:
    payload = json_compatible_payload(event.payload)
    try:
        return SHA256_ADAPTER.validate_python(payload["policy_hash"])
    except (KeyError, ValidationError):
        return None


def _audit_matches(
    event: AuditEvent,
    proposal: HypothesisReceiptProposal,
    transaction: StoredTransaction,
) -> bool:
    payload = json_compatible_payload(event.payload)
    if payload.get("transaction_persisted") is not True:
        return False
    try:
        audited_proposal = PROPOSAL_ADAPTER.validate_json(canonical_json_bytes(payload["proposal"]))
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"])
        )
    except (KeyError, ValidationError):
        return False
    return audited_proposal == proposal and audited_decision == transaction.decision


def _receipt_reference(
    proposal: HypothesisReceiptProposal,
    transaction: StoredTransaction,
    event: AuditEvent,
) -> HypothesisReceiptRef:
    if isinstance(proposal, ProposeHypothesisVersion):
        return HypothesisVersionReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, ReviseHypothesis):
        return HypothesisRevisionReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RegisterExecutableModel):
        return ModelSpecReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RegisterVerificationMechanism):
        return VerificationMechanismReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordSimulationResult):
        return SimulationResultReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordVerificationResult):
        return VerificationResultReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordCounterexample):
        return CounterexampleReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    if isinstance(proposal, RecordEvaluatorAudit):
        return EvaluatorAuditReceiptRef(
            proposal_id=proposal.proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
    return SelfImprovementMeasurementReceiptRef(
        proposal_id=proposal.proposal_id,
        proposal_hash=transaction.proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected hypothesis proposal cannot be projected")


def _actor_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.ENTITY_ID_MISMATCH,
        f"{label} must match the proposal actor",
    )


def _receipt_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.INVALID_LINEAGE,
        f"{label} receipt does not resolve to one accepted audited transaction",
    )


def _chronology_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.INVALID_LINEAGE,
        f"{label} are not in committed audit chronology",
    )


def _stable_conflict(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.IDEMPOTENCY_CONFLICT,
        f"{label} stable key was reused with changed content",
    )


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


__all__ = [
    "AdmitHypothesisHandler",
    "HypothesisReceipt",
    "HypothesisReceiptReader",
    "ProposeHypothesisVersionHandler",
    "RecordCounterexampleHandler",
    "RecordSimulationResultHandler",
    "RecordVerificationResultHandler",
    "RegisterExecutableModelHandler",
    "RegisterVerificationMechanismHandler",
    "ReviseHypothesisHandler",
    "fixed_hypothesis_handlers",
    "hypothesis_capabilities",
    "hypothesis_receipts",
]
