from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import Connection, insert

from super_scientist.domain.cognition.models import (
    CapabilityProfile,
    CohortPlan,
    DiversityAssessment,
)
from super_scientist.domain.collaboration.models import (
    CollaborationSession,
    CollaborationTermination,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
)
from super_scientist.domain.primitives import (
    Sha256Hex,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.procedures.models import (
    AcceptedSourceReceiptRef,
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    ProcedureCompilationRecord,
    ProcedureEvidenceSourceKind,
)
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    BindCompiledProgressPlan,
    GovernedProposalBase,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordMethodDirectionOutcome,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
)
from super_scientist.providers.storage.append_only import AppendOnlyRecordRepository
from super_scientist.providers.storage.procedure_sources import (
    AcceptedProcedureSourceReceiptReader,
)
from super_scientist.providers.storage.repositories import (
    StorageIntegrityError,
    TransactionRepository,
)
from super_scientist.providers.storage.schema import (
    capability_profiles,
    cohort_plans,
    collaboration_sessions,
    collaboration_terminations,
    compiled_progress_plan_bindings,
    diversity_assessments,
    method_direction_outcomes,
    peer_contributions,
    peer_requests,
    procedure_compilations,
    topology_events,
)

__all__ = [
    "BoundedStorageIdentifier",
    "CapabilityProfileRepository",
    "CohortPlanRepository",
    "CollaborationSessionRepository",
    "CollaborationSessionStorageEnvelope",
    "CollaborationTerminationRepository",
    "CollaborationTerminationStorageEnvelope",
    "CompiledProgressPlanBindingRepository",
    "DiversityAssessmentRepository",
    "GovernedAppendOnlyRecordRepository",
    "MethodDirectionOutcomeRepository",
    "MethodDirectionOutcomeStorageEnvelope",
    "PeerContributionRepository",
    "PeerRequestRepository",
    "ProcedureCompilationRepository",
    "TopologyEventRepository",
]

_TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)
_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


def _require_canonical_storage_identifier(value: str) -> str:
    if type(value) is not str or value != value.strip() or "\x00" in value:
        raise ValueError("storage envelope identifier must already be canonical")
    return value


BoundedStorageIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=200),
    AfterValidator(_require_canonical_storage_identifier),
]


class _StrictGovernedStorageEnvelope(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


class CollaborationSessionStorageEnvelope(_StrictGovernedStorageEnvelope):
    session_id: BoundedStorageIdentifier
    cohort_plan_id: BoundedStorageIdentifier
    record: CollaborationSession

    @classmethod
    def from_collaboration_session(cls, record: CollaborationSession) -> Self:
        return cls(
            session_id=record.session_id,
            cohort_plan_id=record.cohort_plan.cohort_plan_id,
            record=record,
        )

    @model_validator(mode="after")
    def require_exact_relationships(self) -> Self:
        if (
            self.session_id != self.record.session_id
            or self.cohort_plan_id != self.record.cohort_plan.cohort_plan_id
        ):
            raise ValueError("collaboration session storage relationship mismatch")
        return self


class CollaborationTerminationStorageEnvelope(_StrictGovernedStorageEnvelope):
    session_id: BoundedStorageIdentifier
    record: CollaborationTermination

    @classmethod
    def from_collaboration_termination_proposal(
        cls,
        proposal: RecordCollaborationTermination,
    ) -> Self:
        return cls(session_id=proposal.session_id, record=proposal.termination)


class MethodDirectionOutcomeStorageEnvelope(_StrictGovernedStorageEnvelope):
    outcome_id: BoundedStorageIdentifier
    compilation_id: BoundedStorageIdentifier
    record: MethodDirectionOutcome

    @classmethod
    def from_method_direction_outcome_proposal(
        cls,
        proposal: RecordMethodDirectionOutcome,
    ) -> Self:
        return cls(
            outcome_id=proposal.outcome.outcome_id,
            compilation_id=proposal.compilation_id,
            record=proposal.outcome,
        )

    @model_validator(mode="after")
    def require_exact_outcome_id(self) -> Self:
        if self.outcome_id != self.record.outcome_id:
            raise ValueError("method direction storage identifier mismatch")
        return self


class GovernedAppendOnlyRecordRepository[RecordT: BaseModel](AppendOnlyRecordRepository[RecordT]):
    """Append-only storage for one fixed governed table and model."""

    _record_decoder: Callable[[str], RecordT] | None = None

    def add(  # type: ignore[override]
        self,
        record_id: str,
        record: RecordT,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_storage(isinstance(record, BaseModel), "record must be a Pydantic model")
        try:
            validated = self._model_type.model_validate(
                record.model_dump(mode="python", warnings=False)
            )
            validated_created_at = _TIMESTAMP_ADAPTER.validate_python(created_at)
            validated_transaction_id = _require_canonical_storage_identifier(transaction_id)
            validated_policy_hash = _SHA256_ADAPTER.validate_python(governing_policy_hash)
        except (TypeError, ValueError):
            invalid_record = True
        else:
            invalid_record = False
        if invalid_record:
            _raise_storage_integrity("invalid governed append-only record")
        _require_storage(
            type(record_id) is str,
            "record identifier must be a string",
        )
        _require_storage(
            getattr(validated, self._identifier_field) == record_id,
            f"{self._identifier_field} does not match record",
        )
        schema_version = _record_schema_version(validated)
        _require_storage(schema_version == 1, "governed record schema_version must be 1")
        record_policy_hash = _record_governing_policy_hash(validated)
        if record_policy_hash is not None:
            _require_storage(
                record_policy_hash == validated_policy_hash,
                "governing_policy_hash does not match record",
            )

        record_json = canonical_json_bytes(
            validated.model_dump(mode="json", warnings=False)
        ).decode("utf-8")
        values: dict[str, object] = {
            self._identifier_field: record_id,
            "schema_version": schema_version,
            "record_json": record_json,
            "content_hash": sha256_hex(record_json.encode("utf-8")),
            "transaction_id": validated_transaction_id,
            "governing_policy_hash": validated_policy_hash,
            "created_at": validated_created_at.isoformat(),
        }
        for column_name, field_name in self._relationship_fields.items():
            value = getattr(validated, field_name)
            _require_storage(
                isinstance(value, str),
                f"{field_name} relationship must be a string",
            )
            values[column_name] = _require_canonical_storage_identifier(value)
        derived_values = self._derive_storage_values(validated)
        _require_storage(
            set(derived_values).issubset(self._table.c.keys()),
            "unknown derived relationship column",
        )
        _require_storage(
            not (set(derived_values) & set(values)),
            "derived relationship column overlaps canonical storage",
        )
        values.update(derived_values)
        self._connection.execute(insert(self._table).values(**values))

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        try:
            record_json = _exact_string(row.get("record_json"))
            content_hash = _exact_string(row.get("content_hash"))
            # JSON mode restores explicit wire forms such as tuple arrays and decimals. The
            # fresh strict rebuild and exact canonical-byte comparison below prevent coercive
            # alternatives from becoming accepted storage representations.
            decoded = (
                self._model_type.model_validate_json(record_json, strict=False)
                if self._record_decoder is None
                else self._record_decoder(record_json)
            )
            record = self._model_type.model_validate(
                decoded.model_dump(mode="python", warnings=False)
            )
            canonical_record_json = canonical_json_bytes(
                record.model_dump(mode="json", warnings=False)
            ).decode("utf-8")
            created_at = _exact_string(row.get("created_at"))
            validated_created_at = _TIMESTAMP_ADAPTER.validate_python(
                datetime.fromisoformat(created_at)
            )
        except (TypeError, ValueError):
            invalid_record_json = True
        else:
            invalid_record_json = False
        if invalid_record_json:
            _raise_storage_integrity("invalid record JSON")
        _require_storage(
            validated_created_at.isoformat() == created_at,
            "created_at must use canonical isoformat",
        )
        stored_identifier = _exact_string(row.get(self._identifier_field))
        _require_storage(
            getattr(record, self._identifier_field) == stored_identifier,
            f"{self._identifier_field} does not match record_json",
        )
        _require_storage(
            sha256_hex(record_json.encode("utf-8")) == content_hash,
            "content_hash does not match record_json",
        )
        for column_name, field_name in self._relationship_fields.items():
            relationship = getattr(record, field_name)
            _require_storage(
                isinstance(relationship, str)
                and relationship == _exact_string(row.get(column_name)),
                f"{column_name} does not match record_json",
            )
        self._verify_derived_storage_values(row, record)
        _require_storage(record_json == canonical_record_json, "record_json must be canonical")
        _require_storage(
            type(row.get("schema_version")) is int
            and row["schema_version"] == _record_schema_version(record),
            "schema_version does not match record_json",
        )
        transaction_id = row.get("transaction_id")
        governing_policy_hash = row.get("governing_policy_hash")
        try:
            _require_canonical_storage_identifier(_exact_string(transaction_id))
            _SHA256_ADAPTER.validate_python(governing_policy_hash)
        except (TypeError, ValueError):
            invalid_provenance = True
        else:
            invalid_provenance = False
        if invalid_provenance:
            _raise_storage_integrity("invalid row provenance")
        record_policy_hash = _record_governing_policy_hash(record)
        if record_policy_hash is not None:
            _require_storage(
                governing_policy_hash == record_policy_hash,
                "governing_policy_hash does not match record_json",
            )
        _require_matching_accepted_transaction(
            self._connection,
            _exact_string(transaction_id),
            record,
        )
        return record


def _exact_string(value: object) -> str:
    if type(value) is not str:
        raise StorageIntegrityError(
            "storage integrity error: stored value must be exact text"
        ) from None
    return value


def _record_schema_version(record: BaseModel) -> object:
    if "schema_version" in record.__class__.model_fields:
        return _model_field_value(record, "schema_version")
    nested = getattr(record, "record", None)
    return getattr(nested, "schema_version", None)


def _record_governing_policy_hash(record: BaseModel) -> object:
    if "governing_policy_hash" in record.__class__.model_fields:
        return _model_field_value(record, "governing_policy_hash")
    nested = getattr(record, "record", None)
    if isinstance(nested, BaseModel) and "governing_policy_hash" in nested.__class__.model_fields:
        return _model_field_value(nested, "governing_policy_hash")
    return None


def _model_field_value(record: BaseModel, field_name: str) -> object:
    return getattr(record, field_name)


def _require_storage(condition: bool, detail: str) -> None:
    if not condition:
        _raise_storage_integrity(detail)


def _raise_storage_integrity(detail: str) -> None:
    raise StorageIntegrityError(f"storage integrity error: {detail}") from None


def _require_proposal_transaction(
    proposal: GovernedProposalBase,
    transaction_id: str,
) -> None:
    try:
        proposal.__class__.model_validate(proposal.model_dump(mode="python", warnings=False))
    except (TypeError, ValueError):
        invalid_proposal = True
    else:
        invalid_proposal = False
    if invalid_proposal:
        _raise_storage_integrity("invalid governed proposal")
    _require_storage(
        transaction_id == proposal.proposal_id,
        "transaction_id does not match accepted proposal",
    )


def _require_matching_accepted_transaction(
    connection: Connection,
    transaction_id: str,
    record: BaseModel,
) -> None:
    try:
        transaction = TransactionRepository(connection).get_by_proposal_id(transaction_id)
    except StorageIntegrityError:
        transaction_lookup_failed = True
        transaction = None
    else:
        transaction_lookup_failed = False
    if (
        transaction_lookup_failed
        or transaction is None
        or not transaction.decision.accepted
        or not _proposal_projection_matches(transaction.proposal, record)
    ):
        _raise_storage_integrity("transaction provenance does not match record_json")


def _proposal_projection_matches(proposal: object, record: BaseModel) -> bool:
    if isinstance(proposal, RecordCapabilityProfile):
        return record == proposal.profile
    if isinstance(proposal, RecordCohortPlan):
        return record == proposal.plan
    if isinstance(proposal, RecordDiversityAssessment):
        return record == proposal.assessment
    if isinstance(proposal, RecordCollaborationSession):
        return record == CollaborationSessionStorageEnvelope.from_collaboration_session(
            proposal.session
        )
    if isinstance(proposal, AppendPeerRequest):
        return record == proposal.request
    if isinstance(proposal, AppendPeerContribution):
        return record == proposal.contribution
    if isinstance(proposal, AppendTopologyEvent):
        return record == proposal.event
    if isinstance(proposal, RecordCollaborationTermination):
        return (
            record
            == CollaborationTerminationStorageEnvelope.from_collaboration_termination_proposal(
                proposal
            )
        )
    if isinstance(proposal, RecordProcedureCompilation):
        try:
            expected_compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(
                proposal.compilation
            )
        except (TypeError, ValueError):
            return False
        return record == expected_compilation
    if isinstance(proposal, RecordMethodDirectionOutcome):
        return (
            record
            == MethodDirectionOutcomeStorageEnvelope.from_method_direction_outcome_proposal(
                proposal
            )
        )
    if isinstance(proposal, BindCompiledProgressPlan):
        return record == proposal.binding
    if isinstance(proposal, RecordGuidanceEvaluationProtocol):
        return record == proposal.protocol
    if isinstance(proposal, AppendGuidanceEvaluationCell):
        return record == proposal.cell
    if isinstance(proposal, RecordModelHarnessProtocol):
        return record == proposal.protocol
    if isinstance(proposal, AppendModelHarnessCell):
        return record == proposal.cell
    if isinstance(proposal, RecordModelHarnessAnalysis):
        return record == proposal.analysis
    if isinstance(proposal, RecordHarnessExecutionTrace):
        from super_scientist.providers.storage.evaluation_records import (
            HarnessExecutionTraceStorageEnvelope,
        )

        return record == HarnessExecutionTraceStorageEnvelope.from_harness_execution_trace(
            proposal.envelope.trace
        )
    if isinstance(proposal, RecordRewardAssessment):
        from super_scientist.providers.storage.evaluation_records import (
            RewardAssessmentStorageEnvelope,
        )

        return record == RewardAssessmentStorageEnvelope.from_reward_assessment(proposal.assessment)
    return False


class CapabilityProfileRepository(GovernedAppendOnlyRecordRepository[CapabilityProfile]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=capability_profiles,
            model_type=CapabilityProfile,
            identifier_field="profile_id",
        )

    def add_from_proposal(
        self,
        proposal: RecordCapabilityProfile,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.profile.profile_id,
            proposal.profile,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def resolve(self, reference: AcceptedSourceReceiptRef) -> CapabilityProfile | None:
        receipt = AcceptedProcedureSourceReceiptReader(self._connection).resolve(reference)
        if (
            receipt is None
            or reference.source_kind is not ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
            or reference.source_schema_version != 1
            or not isinstance(receipt.proposal, RecordCapabilityProfile)
            or receipt.proposal.profile.profile_id != reference.source_record_id
            or receipt.proposal.profile.content_hash != reference.source_content_hash
        ):
            return None
        stored = self.get(reference.source_record_id)
        return stored if stored == receipt.proposal.profile else None


class CohortPlanRepository(GovernedAppendOnlyRecordRepository[CohortPlan]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=cohort_plans,
            model_type=CohortPlan,
            identifier_field="cohort_plan_id",
            relationship_fields={"request_id": "request_id"},
        )

    def add_from_proposal(
        self,
        proposal: RecordCohortPlan,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.plan.cohort_plan_id,
            proposal.plan,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_request(self, request_id: str) -> tuple[CohortPlan, ...]:
        return self._list_by_relationship("request_id", request_id)


class DiversityAssessmentRepository(GovernedAppendOnlyRecordRepository[DiversityAssessment]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=diversity_assessments,
            model_type=DiversityAssessment,
            identifier_field="diversity_assessment_id",
            relationship_fields={"cohort_plan_id": "cohort_plan_id"},
        )

    def add_from_proposal(
        self,
        proposal: RecordDiversityAssessment,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.assessment.diversity_assessment_id,
            proposal.assessment,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_cohort_plan(self, cohort_plan_id: str) -> tuple[DiversityAssessment, ...]:
        return self._list_by_relationship("cohort_plan_id", cohort_plan_id)


class _CollaborationSessionEnvelopeRepository(
    GovernedAppendOnlyRecordRepository[CollaborationSessionStorageEnvelope]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=collaboration_sessions,
            model_type=CollaborationSessionStorageEnvelope,
            identifier_field="session_id",
            relationship_fields={"cohort_plan_id": "cohort_plan_id"},
        )


class CollaborationSessionRepository:
    def __init__(self, connection: Connection) -> None:
        self._records = _CollaborationSessionEnvelopeRepository(connection)

    def add_from_proposal(
        self,
        proposal: RecordCollaborationSession,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        envelope = CollaborationSessionStorageEnvelope.from_collaboration_session(proposal.session)
        self._records.add(
            envelope.session_id,
            envelope,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def get(self, record_id: str) -> CollaborationSession | None:
        envelope = self._records.get(record_id)
        return None if envelope is None else envelope.record

    def list_all(self) -> tuple[CollaborationSession, ...]:
        return tuple(item.record for item in self._records.list_all())

    def list_for_cohort_plan(self, cohort_plan_id: str) -> tuple[CollaborationSession, ...]:
        return tuple(
            item.record
            for item in self._records._list_by_relationship("cohort_plan_id", cohort_plan_id)
        )


class PeerRequestRepository(GovernedAppendOnlyRecordRepository[PeerRequest]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=peer_requests,
            model_type=PeerRequest,
            identifier_field="request_id",
            relationship_fields={"session_id": "session_id"},
        )

    def add_from_proposal(
        self,
        proposal: AppendPeerRequest,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.request.request_id,
            proposal.request,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_session(self, session_id: str) -> tuple[PeerRequest, ...]:
        return self._list_by_relationship("session_id", session_id)


class PeerContributionRepository(GovernedAppendOnlyRecordRepository[PeerContribution]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=peer_contributions,
            model_type=PeerContribution,
            identifier_field="contribution_id",
            relationship_fields={"session_id": "session_id", "request_id": "request_id"},
        )

    def add_from_proposal(
        self,
        proposal: AppendPeerContribution,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.contribution.contribution_id,
            proposal.contribution,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_session(self, session_id: str) -> tuple[PeerContribution, ...]:
        return self._list_by_relationship("session_id", session_id)

    def list_for_request(self, request_id: str) -> tuple[PeerContribution, ...]:
        return self._list_by_relationship("request_id", request_id)


class TopologyEventRepository(GovernedAppendOnlyRecordRepository[TopologyEvent]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=topology_events,
            model_type=TopologyEvent,
            identifier_field="event_id",
            relationship_fields={"session_id": "session_id"},
        )

    def add_from_proposal(
        self,
        proposal: AppendTopologyEvent,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.event.event_id,
            proposal.event,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_session(self, session_id: str) -> tuple[TopologyEvent, ...]:
        return self._list_by_relationship("session_id", session_id)


class _CollaborationTerminationEnvelopeRepository(
    GovernedAppendOnlyRecordRepository[CollaborationTerminationStorageEnvelope]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=collaboration_terminations,
            model_type=CollaborationTerminationStorageEnvelope,
            identifier_field="session_id",
        )


class CollaborationTerminationRepository:
    def __init__(self, connection: Connection) -> None:
        self._records = _CollaborationTerminationEnvelopeRepository(connection)

    def add_from_proposal(
        self,
        proposal: RecordCollaborationTermination,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        envelope = CollaborationTerminationStorageEnvelope.from_collaboration_termination_proposal(
            proposal
        )
        self._records.add(
            envelope.session_id,
            envelope,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def get(self, record_id: str) -> CollaborationTermination | None:
        envelope = self._records.get(record_id)
        return None if envelope is None else envelope.record

    def list_all(self) -> tuple[CollaborationTermination, ...]:
        return tuple(item.record for item in self._records.list_all())


class ProcedureCompilationRepository(
    GovernedAppendOnlyRecordRepository[ProcedureCompilationRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=procedure_compilations,
            model_type=ProcedureCompilationRecord,
            identifier_field="compilation_id",
        )

    def add_from_proposal(
        self,
        proposal: RecordProcedureCompilation,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        try:
            record = ProcedureCompilationRecord.build_from_untrusted_envelope(proposal.compilation)
        except (TypeError, ValueError):
            invalid_compilation = True
        else:
            invalid_compilation = False
        if invalid_compilation:
            _raise_storage_integrity("invalid procedure compilation envelope")
        self.add(
            record.compilation_id,
            record,
            created_at,
            transaction_id,
            governing_policy_hash,
        )


class _MethodDirectionOutcomeEnvelopeRepository(
    GovernedAppendOnlyRecordRepository[MethodDirectionOutcomeStorageEnvelope]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=method_direction_outcomes,
            model_type=MethodDirectionOutcomeStorageEnvelope,
            identifier_field="outcome_id",
            relationship_fields={"compilation_id": "compilation_id"},
        )


class MethodDirectionOutcomeRepository:
    def __init__(self, connection: Connection) -> None:
        self._records = _MethodDirectionOutcomeEnvelopeRepository(connection)

    def add_from_proposal(
        self,
        proposal: RecordMethodDirectionOutcome,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        envelope = MethodDirectionOutcomeStorageEnvelope.from_method_direction_outcome_proposal(
            proposal
        )
        self._records.add(
            envelope.outcome_id,
            envelope,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def get(self, record_id: str) -> MethodDirectionOutcome | None:
        envelope = self._records.get(record_id)
        return None if envelope is None else envelope.record

    def list_all(self) -> tuple[MethodDirectionOutcome, ...]:
        return tuple(item.record for item in self._records.list_all())

    def list_for_compilation(self, compilation_id: str) -> tuple[MethodDirectionOutcome, ...]:
        return tuple(
            item.record
            for item in self._records._list_by_relationship("compilation_id", compilation_id)
        )


class CompiledProgressPlanBindingRepository(
    GovernedAppendOnlyRecordRepository[CompiledProgressPlanBinding]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=compiled_progress_plan_bindings,
            model_type=CompiledProgressPlanBinding,
            identifier_field="binding_id",
            relationship_fields={"compilation_id": "compilation_id"},
        )

    def add_from_proposal(
        self,
        proposal: BindCompiledProgressPlan,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.binding.binding_id,
            proposal.binding,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_compilation(
        self,
        compilation_id: str,
    ) -> tuple[CompiledProgressPlanBinding, ...]:
        return self._list_by_relationship("compilation_id", compilation_id)
