from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Never, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import Connection, insert, select

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
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    BindCompiledProgressPlan,
    GovernedProposalBase,
    Proposal,
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
    TransactionDecision,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.append_only import AppendOnlyRecordRepository
from super_scientist.providers.storage.procedure_sources import (
    AcceptedProcedureSourceReceiptReader,
)
from super_scientist.providers.storage.query_bounds import sqlite_in_chunks
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    StorageIntegrityError,
    StoredTransaction,
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


@dataclass(frozen=True, slots=True)
class _ParsedTransactionAuditBinding:
    proposal: Proposal
    proposal_hash: str
    decision: TransactionDecision
    intent_fingerprint: str | None
    governing_policy_hash: str


@dataclass(frozen=True, slots=True)
class _GovernedProvenanceBinding:
    transaction: StoredTransaction
    exact_audit_policy_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GovernedProvenanceSnapshot:
    bindings: Mapping[str, _GovernedProvenanceBinding]


GovernedProvenanceSnapshot = _GovernedProvenanceSnapshot


class GovernedAppendOnlyRecordRepository[RecordT: BaseModel](AppendOnlyRecordRepository[RecordT]):
    """Append-only storage for one fixed governed table and model."""

    _record_decoder: Callable[[str], RecordT] | None = None

    def get(self, record_id: str) -> RecordT | None:
        row = (
            self._connection.execute(
                select(self._table).where(self._table.c[self._identifier_field] == record_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        exact_row = dict(row)
        snapshot = _load_governed_provenance_snapshot(
            self._connection,
            (_exact_string(exact_row.get("transaction_id")),),
        )
        return self._decode_row_with_provenance(exact_row, snapshot)

    def list_all(self) -> tuple[RecordT, ...]:
        rows = tuple(
            dict(row)
            for row in self._connection.execute(
                select(self._table).order_by(self._table.c[self._identifier_field])
            ).mappings()
        )
        return self._decode_rows_with_new_provenance_snapshot(rows)

    def _list_by_relationship(
        self,
        column_name: str,
        value: str | int,
    ) -> tuple[RecordT, ...]:
        _require_storage(
            column_name in self._relationship_fields,
            "unknown relationship column",
        )
        rows = tuple(
            dict(row)
            for row in self._connection.execute(
                select(self._table)
                .where(self._table.c[column_name] == value)
                .order_by(self._table.c[self._identifier_field])
            ).mappings()
        )
        return self._decode_rows_with_new_provenance_snapshot(rows)

    def _list_by_relationship_values(
        self,
        column_name: str,
        values: tuple[str, ...],
    ) -> tuple[RecordT, ...]:
        _require_storage(
            column_name in self._relationship_fields,
            "unknown relationship column",
        )
        rows: list[dict[str, object]] = []
        for exact_values in sqlite_in_chunks(values):
            rows.extend(
                dict(row)
                for row in self._connection.execute(
                    select(self._table)
                    .where(self._table.c[column_name].in_(exact_values))
                    .order_by(self._table.c[self._identifier_field])
                ).mappings()
            )
        rows.sort(key=lambda row: _exact_string(row.get(self._identifier_field)))
        return self._decode_rows_with_new_provenance_snapshot(tuple(rows))

    def _get_many(self, record_ids: tuple[str, ...]) -> tuple[RecordT, ...]:
        rows: list[dict[str, object]] = []
        for identifiers in sqlite_in_chunks(record_ids):
            rows.extend(
                dict(row)
                for row in self._connection.execute(
                    select(self._table)
                    .where(self._table.c[self._identifier_field].in_(identifiers))
                    .order_by(self._table.c[self._identifier_field])
                ).mappings()
            )
        return self._decode_rows_with_new_provenance_snapshot(tuple(rows))

    def _get_many_with_provenance(
        self,
        record_ids: tuple[str, ...],
        snapshot: _GovernedProvenanceSnapshot,
    ) -> tuple[RecordT, ...]:
        rows: list[dict[str, object]] = []
        for identifiers in sqlite_in_chunks(record_ids):
            rows.extend(
                dict(row)
                for row in self._connection.execute(
                    select(self._table)
                    .where(self._table.c[self._identifier_field].in_(identifiers))
                    .order_by(self._table.c[self._identifier_field])
                ).mappings()
            )
        return tuple(self._decode_row_with_provenance(row, snapshot) for row in rows)

    def _list_all_with_provenance(
        self,
        snapshot: _GovernedProvenanceSnapshot,
    ) -> tuple[RecordT, ...]:
        rows = self._connection.execute(
            select(self._table).order_by(self._table.c[self._identifier_field])
        ).mappings()
        return tuple(self._decode_row_with_provenance(dict(row), snapshot) for row in rows)

    def _decode_rows_with_new_provenance_snapshot(
        self,
        rows: tuple[dict[str, object], ...],
    ) -> tuple[RecordT, ...]:
        if not rows:
            return ()
        transaction_ids = tuple(_exact_string(row.get("transaction_id")) for row in rows)
        snapshot = _load_governed_provenance_snapshot(
            self._connection,
            transaction_ids,
        )
        return tuple(self._decode_row_with_provenance(row, snapshot) for row in rows)

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
        snapshot = _load_governed_provenance_snapshot(
            self._connection,
            (_exact_string(row.get("transaction_id")),),
        )
        return self._decode_row_with_provenance(row, snapshot)

    def _decode_row_with_provenance(
        self,
        row: Mapping[str, object],
        snapshot: _GovernedProvenanceSnapshot,
    ) -> RecordT:
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
            snapshot,
            _exact_string(transaction_id),
            _exact_string(governing_policy_hash),
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


def _raise_storage_integrity(detail: str) -> Never:
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
    snapshot: _GovernedProvenanceSnapshot,
    transaction_id: str,
    governing_policy_hash: str,
    record: BaseModel,
) -> None:
    binding = snapshot.bindings.get(transaction_id)
    if (
        binding is None
        or not binding.transaction.decision.accepted
        or not _proposal_projection_matches(binding.transaction.proposal, record)
    ):
        _raise_storage_integrity("transaction provenance does not match record_json")
    if binding.exact_audit_policy_hashes != (governing_policy_hash,):
        _raise_storage_integrity("governing_policy_hash does not match transaction audit")


def _load_governed_provenance_snapshot(
    connection: Connection,
    transaction_ids: tuple[str, ...] | None = None,
) -> _GovernedProvenanceSnapshot:
    try:
        transaction_repository = TransactionRepository(connection)
        transactions = (
            transaction_repository.list_all()
            if transaction_ids is None
            else transaction_repository.get_many_by_proposal_ids(transaction_ids)
        )
        # Even a targeted record read must verify the complete hash-linked audit
        # chain. Batch readers reuse this operation-local provenance snapshot.
        audit_events = AuditRepository(connection).list_all()
    except StorageIntegrityError:
        invalid_provenance_storage = True
        transactions = ()
        audit_events = ()
    else:
        invalid_provenance_storage = False
    if invalid_provenance_storage:
        _raise_storage_integrity("transaction provenance does not match record_json")

    return build_governed_provenance_snapshot(transactions, audit_events)


def build_governed_provenance_snapshot(
    transactions: tuple[StoredTransaction, ...],
    audit_events: tuple[AuditEvent, ...],
) -> _GovernedProvenanceSnapshot:
    audits_by_proposal_id: dict[str, list[_ParsedTransactionAuditBinding]] = {}
    for event in audit_events:
        parsed = _parse_transaction_audit_binding(event)
        if parsed is not None:
            audits_by_proposal_id.setdefault(parsed.proposal.proposal_id, []).append(parsed)

    bindings: dict[str, _GovernedProvenanceBinding] = {}
    for transaction in transactions:
        exact_audits = tuple(
            audit
            for audit in audits_by_proposal_id.get(transaction.proposal.proposal_id, ())
            if _audit_binding_matches_transaction(audit, transaction)
        )
        bindings[transaction.proposal.proposal_id] = _GovernedProvenanceBinding(
            transaction=transaction,
            exact_audit_policy_hashes=tuple(audit.governing_policy_hash for audit in exact_audits),
        )
    return _GovernedProvenanceSnapshot(bindings=MappingProxyType(bindings))


def _parse_transaction_audit_binding(
    event: AuditEvent,
) -> _ParsedTransactionAuditBinding | None:
    if event.event_type != "transaction_decision":
        return None
    payload = json_compatible_payload(event.payload)
    allowed_keys = {
        "proposal",
        "decision",
        "policy_hash",
        "stored_policy_hash",
        "transaction_persisted",
        "configured_policy_hash",
        "intent_fingerprint",
    }
    if not set(payload).issubset(allowed_keys):
        return None
    try:
        audited_proposal = parse_untrusted_proposal_json(canonical_json_bytes(payload["proposal"]))
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"]),
            strict=True,
        )
        policy_hash = _SHA256_ADAPTER.validate_python(payload["policy_hash"])
        stored_policy_hash = _SHA256_ADAPTER.validate_python(payload["stored_policy_hash"])
        if "configured_policy_hash" in payload:
            _SHA256_ADAPTER.validate_python(payload["configured_policy_hash"])
        audited_intent_fingerprint = (
            None
            if "intent_fingerprint" not in payload
            else _SHA256_ADAPTER.validate_python(payload["intent_fingerprint"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        payload.get("transaction_persisted") is not True
        or stored_policy_hash != policy_hash
        or audited_decision.proposal_id != audited_proposal.proposal_id
    ):
        return None
    return _ParsedTransactionAuditBinding(
        proposal=audited_proposal,
        proposal_hash=sha256_hex(
            canonical_json_bytes(audited_proposal.model_dump(mode="json", warnings=False))
        ),
        decision=audited_decision,
        intent_fingerprint=audited_intent_fingerprint,
        governing_policy_hash=policy_hash,
    )


def _audit_binding_matches_transaction(
    audit: _ParsedTransactionAuditBinding,
    transaction: StoredTransaction,
) -> bool:
    return (
        audit.proposal == transaction.proposal
        and audit.proposal.proposal_id == transaction.proposal.proposal_id
        and audit.proposal_hash == transaction.proposal_hash
        and audit.decision == transaction.decision
        and audit.decision.proposal_id == transaction.proposal.proposal_id
        and audit.intent_fingerprint == transaction.intent_fingerprint
    )


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

    def get_many_with_provenance(
        self,
        record_ids: tuple[str, ...],
        snapshot: GovernedProvenanceSnapshot,
    ) -> tuple[CapabilityProfile, ...]:
        return self._get_many_with_provenance(record_ids, snapshot)


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

    def get_many_with_provenance(
        self,
        record_ids: tuple[str, ...],
        snapshot: GovernedProvenanceSnapshot,
    ) -> tuple[CohortPlan, ...]:
        return self._get_many_with_provenance(record_ids, snapshot)


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

    def _list_all_with_provenance(
        self,
        snapshot: _GovernedProvenanceSnapshot,
    ) -> tuple[CollaborationSession, ...]:
        return tuple(item.record for item in self._records._list_all_with_provenance(snapshot))

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

    def _list_all_with_provenance(
        self,
        snapshot: _GovernedProvenanceSnapshot,
    ) -> tuple[CollaborationTermination, ...]:
        return tuple(item.record for item in self._records._list_all_with_provenance(snapshot))


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

    def _list_all_with_provenance(
        self,
        snapshot: _GovernedProvenanceSnapshot,
    ) -> tuple[MethodDirectionOutcome, ...]:
        return tuple(item.record for item in self._records._list_all_with_provenance(snapshot))

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
