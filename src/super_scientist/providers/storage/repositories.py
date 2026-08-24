from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from super_scientist.config.loader import policy_hash
from super_scientist.config.models import PolicyDocument, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.primitives import (
    Sha256Hex,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.audit.chain import append_event, verify_chain
from super_scientist.kernel.audit.models import AuditEvent
from super_scientist.kernel.transactions.models import (
    GOVERNED_PROPOSAL_CLASSES,
    Proposal,
    TransactionDecision,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.integrity_records import (
    AdaptationIntegritySnapshot,
    CognitiveIntegritySnapshot,
    EvaluationExtensionIntegritySnapshot,
    HandbookIntegritySnapshot,
    HarnessIntegritySnapshot,
    HypothesisIntegritySnapshot,
    ProgressIntegritySnapshot,
    RepresentationIntegritySnapshot,
    RuleIntegritySnapshot,
    TrailIntegritySnapshot,
)
from super_scientist.providers.storage.schema import (
    audit_events,
    behavior_rule_link_versions,
    behavioral_rule_heads,
    behavioral_rule_version_incidents,
    behavioral_rule_version_supersessions,
    behavioral_rule_versions,
    capability_profiles,
    claim_heads,
    claim_versions,
    cohort_plans,
    collaboration_sessions,
    collaboration_terminations,
    compiled_progress_plan_bindings,
    completion_decisions,
    configuration_versions,
    counterexample_records,
    diversity_assessments,
    evaluator_audits,
    evaluator_collapse_records,
    evaluator_heads,
    evaluator_succession_decisions,
    evaluator_versions,
    evidence_records,
    evidence_trail_assessments,
    evidence_trail_checks,
    evidence_trail_heads,
    evidence_trail_nodes,
    evidence_trail_relations,
    evidence_trail_versions,
    executable_model_specs,
    governance_policies,
    governance_state,
    guidance_cells,
    guidance_protocols,
    handbook_verification_records,
    harness_budgets,
    harness_campaign_heads,
    harness_campaigns,
    harness_confounds,
    harness_decisions,
    harness_execution_traces,
    harness_metrics,
    harness_observations,
    harness_partition_manifests,
    hypothesis_admission_decisions,
    hypothesis_heads,
    hypothesis_revisions,
    hypothesis_versions,
    method_direction_outcomes,
    model_harness_analyses,
    model_harness_cells,
    model_harness_protocols,
    peer_contributions,
    peer_requests,
    primitive_evaluations,
    primitive_heads,
    primitive_versions,
    procedure_compilations,
    progress_events,
    progress_heads,
    progress_plans,
    progress_subtasks,
    report_sentence_bindings,
    research_run_events,
    research_run_heads,
    research_runs,
    reviewer_assessment_incidents,
    reviewer_assessment_rule_versions,
    reviewer_assessments,
    reward_assessments,
    rule_consolidation_assessments,
    rule_consolidation_decisions,
    rule_consolidation_incidents,
    rule_incidents,
    rule_regression_case_incidents,
    rule_regression_cases,
    run_budgets,
    run_checkpoints,
    self_improvement_measurements,
    simulation_results,
    topology_events,
    transactions,
    verification_mechanism_specs,
    verification_results,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)
POLICY_DOCUMENT_ADAPTER: TypeAdapter[PolicyDocument] = TypeAdapter(PolicyDocument)
_STORAGE_TYPE_KEY = "__super_scientist_storage_type__"
_STORAGE_ITEMS_KEY = "items"
_STORAGE_ENUMS: dict[str, type[Enum]] = {
    enum_type.__name__: enum_type for enum_type in (ActorKind, ClaimStatus, VerificationState)
}
_STRICT_JSON_PROPOSAL_TYPES = frozenset(
    {
        "create_research_run",
        "append_research_run_event",
        "record_configuration_version",
        "record_evaluator_audit",
        "record_self_improvement_measurement",
        "propose_evaluator_version",
        "decide_evaluator_succession",
        "propose_governance_policy_transition",
        "record_progress_plan",
        "append_progress_event",
        "record_run_budget",
        "record_run_checkpoint",
        "decide_completion",
        "propose_evidence_trail_nodes",
        "propose_evidence_trail_relations",
        "record_evidence_trail_version",
        "bind_report_sentence",
        "record_rule_incident",
        "propose_behavioral_rule",
        "import_reviewer_assessment",
        "consolidate_behavioral_rule",
        "create_harness_campaign",
        "record_harness_iteration",
        "record_harness_protected_result",
        "record_harness_confound",
        "decide_harness_campaign",
        "propose_primitive_version",
        "record_primitive_evaluation",
        "admit_primitive_version",
        "propose_hypothesis_version",
        "register_executable_model",
        "register_verification_mechanism",
        "record_simulation_result",
        "record_verification_result",
        "record_counterexample",
        "revise_hypothesis",
        "admit_hypothesis",
    }
)
_GOVERNED_PROPOSAL_TYPES = frozenset(
    proposal_type.model_fields["proposal_type"].default
    for proposal_type in GOVERNED_PROPOSAL_CLASSES
)
_STRICT_JSON_PROPOSAL_TYPES |= _GOVERNED_PROPOSAL_TYPES


class StorageIntegrityError(ValueError):
    """Raised when durable repository state contradicts its canonical record."""


def _require_integrity(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(f"storage integrity error: {detail}")


def _stored_str(row: Mapping[str, object], column: str) -> str:
    value = row[column]
    if not isinstance(value, str):
        raise StorageIntegrityError(f"storage integrity error: {column} must be a string")
    return value


def _stored_int(row: Mapping[str, object], column: str) -> int:
    value = row[column]
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageIntegrityError(f"storage integrity error: {column} must be an integer")
    return value


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, frozenset):
        compatible = [_json_compatible(item) for item in value]
        return sorted(compatible, key=canonical_json_bytes)
    return value


def _serialization_fallback(value: object) -> object:
    compatible = _json_compatible(value)
    if compatible is value:
        raise TypeError(f"unsupported JSON value: {type(value).__name__}")
    return compatible


def _encode_storage_value(value: object) -> object:
    if isinstance(value, Mapping):
        encoded = {key: _encode_storage_value(item) for key, item in value.items()}
        if _STORAGE_TYPE_KEY in value:
            return {
                _STORAGE_TYPE_KEY: "mapping",
                _STORAGE_ITEMS_KEY: [[key, encoded[key]] for key in sorted(encoded)],
            }
        return encoded
    if isinstance(value, (list, tuple)):
        return [_encode_storage_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        encoded_items = [_encode_storage_value(item) for item in value]
        return {
            _STORAGE_TYPE_KEY: "frozenset",
            _STORAGE_ITEMS_KEY: sorted(encoded_items, key=canonical_json_bytes),
        }
    if isinstance(value, datetime):
        return {
            _STORAGE_TYPE_KEY: "datetime",
            _STORAGE_ITEMS_KEY: value.isoformat(),
        }
    if isinstance(value, Enum):
        return {
            _STORAGE_TYPE_KEY: "enum",
            _STORAGE_ITEMS_KEY: [value.__class__.__name__, value.value],
        }
    return value


def _decode_storage_value(value: object) -> object:
    if isinstance(value, list):
        return [_decode_storage_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {_STORAGE_TYPE_KEY, _STORAGE_ITEMS_KEY}:
        storage_type = value[_STORAGE_TYPE_KEY]
        items = value[_STORAGE_ITEMS_KEY]
        if storage_type == "frozenset":
            if not isinstance(items, list):
                raise ValueError("invalid frozenset storage envelope")
            return frozenset(_decode_storage_value(item) for item in items)
        if storage_type == "mapping":
            if not isinstance(items, list):
                raise ValueError("invalid mapping storage envelope")
            decoded: dict[str, object] = {}
            for item in items:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or item[0] in decoded
                ):
                    raise ValueError("invalid mapping storage item")
                decoded[item[0]] = _decode_storage_value(item[1])
            return decoded
        if storage_type == "datetime":
            if not isinstance(items, str):
                raise ValueError("invalid datetime storage envelope")
            return datetime.fromisoformat(items)
        if storage_type == "enum":
            if not isinstance(items, list) or len(items) != 2 or not isinstance(items[0], str):
                raise ValueError("invalid enum storage envelope")
            enum_type = _STORAGE_ENUMS.get(items[0])
            if enum_type is None:
                raise ValueError(f"unsupported stored enum: {items[0]}")
            return enum_type(items[1])
    return {key: _decode_storage_value(item) for key, item in value.items()}


def _storage_json(value: object) -> str:
    return canonical_json_bytes(_encode_storage_value(value)).decode("utf-8")


def _load_storage_json(value: str) -> object:
    return _decode_storage_value(json.loads(value))


def _validated_model_json[ModelT: BaseModel](model_type: type[ModelT], value: ModelT) -> str:
    dumped = value.model_dump(mode="python", warnings="none")
    validated = model_type.model_validate(dumped)
    return validated.model_dump_json(warnings="none", fallback=_serialization_fallback)


def _validated_timestamp(value: UtcTimestamp) -> UtcTimestamp:
    return TIMESTAMP_ADAPTER.validate_python(value)


def _proposal_hash(proposal: Proposal) -> str:
    return sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))


def _decode_evidence_row(row: Mapping[str, object]) -> EvidenceRecord:
    evidence_id = _stored_str(row, "evidence_id")
    content_hash = _stored_str(row, "content_hash")
    record_json = _stored_str(row, "record_json")
    created_at = _stored_str(row, "created_at")
    try:
        record = EvidenceRecord.model_validate(_load_storage_json(record_json))
    except (TypeError, ValueError) as error:
        raise StorageIntegrityError(
            "storage integrity error: invalid evidence record JSON"
        ) from error
    _require_integrity(record.evidence_id == evidence_id, "evidence_id does not match record_json")
    _require_integrity(
        record.content_hash == content_hash,
        "content_hash does not match record_json",
    )
    _require_integrity(
        record.retrieved_at.isoformat() == created_at,
        "created_at does not match record_json",
    )
    return record


def _decode_claim_version_row(row: Mapping[str, object]) -> AtomicClaim:
    claim_version_id = _stored_str(row, "claim_version_id")
    claim_id = _stored_str(row, "claim_id")
    version = _stored_int(row, "version")
    status = _stored_str(row, "status")
    record_json = _stored_str(row, "record_json")
    content_hash = _stored_str(row, "content_hash")
    created_at = _stored_str(row, "created_at")
    try:
        claim = AtomicClaim.model_validate_json(record_json)
    except (TypeError, ValueError) as error:
        raise StorageIntegrityError("storage integrity error: invalid claim record JSON") from error
    _require_integrity(
        claim_version_id == f"{claim.claim_id}:{claim.version}",
        "claim_version_id does not match record_json",
    )
    _require_integrity(claim.claim_id == claim_id, "claim_id does not match record_json")
    _require_integrity(claim.version == version, "version does not match record_json")
    _require_integrity(claim.status.value == status, "status does not match record_json")
    _require_integrity(
        sha256_hex(record_json.encode("utf-8")) == content_hash,
        "content_hash does not match record_json",
    )
    _require_integrity(
        claim.created_at.isoformat() == created_at,
        "created_at does not match record_json",
    )
    return claim


def _decode_transaction_row(row: Mapping[str, object]) -> StoredTransaction:
    proposal_id = _stored_str(row, "proposal_id")
    idempotency_key = _stored_str(row, "idempotency_key")
    raw_intent_fingerprint = row["intent_fingerprint"]
    stored_hash = _stored_str(row, "proposal_hash")
    proposal_json = _stored_str(row, "proposal_json")
    decision_json = _stored_str(row, "decision_json")
    created_at = _stored_str(row, "created_at")
    try:
        intent_fingerprint = (
            None
            if raw_intent_fingerprint is None
            else SHA256_ADAPTER.validate_python(raw_intent_fingerprint)
        )
        raw_proposal = json.loads(proposal_json)
        if (
            isinstance(raw_proposal, dict)
            and raw_proposal.get("proposal_type") in _GOVERNED_PROPOSAL_TYPES
        ):
            proposal = parse_untrusted_proposal_json(proposal_json)
        elif (
            isinstance(raw_proposal, dict)
            and raw_proposal.get("proposal_type") in _STRICT_JSON_PROPOSAL_TYPES
        ):
            proposal = PROPOSAL_ADAPTER.validate_json(proposal_json)
        else:
            proposal = PROPOSAL_ADAPTER.validate_python(_decode_storage_value(raw_proposal))
        decision = TransactionDecision.model_validate_json(decision_json)
        validated_created_at = _validated_timestamp(datetime.fromisoformat(created_at))
    except (TypeError, ValueError) as error:
        raise StorageIntegrityError(
            "storage integrity error: invalid transaction record"
        ) from error
    _require_integrity(
        stored_hash == _proposal_hash(proposal),
        "proposal_hash does not match canonical proposal JSON",
    )
    _require_integrity(
        proposal.proposal_id == proposal_id,
        "proposal_id does not match proposal_json",
    )
    _require_integrity(
        proposal.idempotency_key == idempotency_key,
        "idempotency_key does not match proposal_json",
    )
    _require_integrity(
        decision.proposal_id == proposal.proposal_id,
        "decision proposal_id does not match proposal_json",
    )
    return StoredTransaction(
        proposal=proposal,
        proposal_hash=stored_hash,
        decision=decision,
        intent_fingerprint=intent_fingerprint,
        created_at=validated_created_at,
    )


def _decode_audit_row(row: Mapping[str, object]) -> AuditEvent:
    sequence = _stored_int(row, "sequence")
    event_id = _stored_str(row, "event_id")
    previous_hash = _stored_str(row, "previous_hash")
    payload_hash = _stored_str(row, "payload_hash")
    event_hash = _stored_str(row, "event_hash")
    event_json = _stored_str(row, "event_json")
    try:
        event = AuditEvent.model_validate_json(event_json)
    except ValueError as error:
        raise StorageIntegrityError("storage integrity error: invalid audit event JSON") from error
    _require_integrity(event.sequence == sequence, "audit sequence does not match event_json")
    _require_integrity(event.event_id == event_id, "audit event_id does not match event_json")
    _require_integrity(
        event.previous_hash == previous_hash,
        "audit previous_hash does not match event_json",
    )
    _require_integrity(
        event.payload_hash == payload_hash,
        "audit payload_hash does not match event_json",
    )
    _require_integrity(event.event_hash == event_hash, "audit event_hash does not match event_json")
    return event


def _decode_policy_row(
    row: Mapping[str, object],
    *,
    active_policy_hash: str | None = None,
) -> PolicySnapshot:
    stored_policy_hash = _stored_str(row, "policy_hash")
    policy_json = _stored_str(row, "policy_json")
    created_at = _stored_str(row, "created_at")
    try:
        policy = POLICY_DOCUMENT_ADAPTER.validate_json(policy_json)
        _validated_timestamp(datetime.fromisoformat(created_at))
    except (TypeError, ValueError) as error:
        raise StorageIntegrityError(
            "storage integrity error: invalid governance policy row"
        ) from error
    _require_integrity(
        stored_policy_hash == policy_hash(policy),
        "policy_hash does not match policy_json",
    )
    if active_policy_hash is not None:
        _require_integrity(
            active_policy_hash == stored_policy_hash,
            "active_policy_hash does not match governance policy",
        )
    return PolicySnapshot(policy_hash=stored_policy_hash, policy=policy)


class StoredTransaction(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal: Proposal
    proposal_hash: str
    decision: TransactionDecision
    intent_fingerprint: Sha256Hex | None = None
    created_at: UtcTimestamp


class EvidenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        row = (
            self._connection.execute(
                select(
                    evidence_records.c.evidence_id,
                    evidence_records.c.content_hash,
                    evidence_records.c.record_json,
                    evidence_records.c.created_at,
                ).where(evidence_records.c.evidence_id == evidence_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_evidence_row(dict(row))

    def list_all(self) -> tuple[EvidenceRecord, ...]:
        rows = self._connection.execute(
            select(
                evidence_records.c.evidence_id,
                evidence_records.c.content_hash,
                evidence_records.c.record_json,
                evidence_records.c.created_at,
            ).order_by(evidence_records.c.evidence_id)
        ).mappings()
        return tuple(_decode_evidence_row(dict(row)) for row in rows)

    def add(self, record: EvidenceRecord) -> None:
        dumped = record.model_dump(mode="python", warnings="none")
        validated = EvidenceRecord.model_validate(dumped)
        record_json = _storage_json(validated.model_dump(mode="python", warnings="none"))
        self._connection.execute(
            insert(evidence_records).values(
                evidence_id=validated.evidence_id,
                content_hash=validated.content_hash,
                record_json=record_json,
                created_at=validated.retrieved_at.isoformat(),
            )
        )


class ClaimRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_head(self, claim_id: str) -> AtomicClaim | None:
        row = (
            self._connection.execute(
                select(
                    claim_heads.c.claim_id.label("head_claim_id"),
                    claim_heads.c.claim_version_id.label("head_claim_version_id"),
                    claim_heads.c.version.label("head_version"),
                    claim_heads.c.status.label("head_status"),
                    claim_versions.c.claim_version_id.label("claim_version_id"),
                    claim_versions.c.claim_id,
                    claim_versions.c.version,
                    claim_versions.c.status,
                    claim_versions.c.record_json,
                    claim_versions.c.content_hash,
                    claim_versions.c.created_at,
                )
                .select_from(
                    claim_heads.outerjoin(
                        claim_versions,
                        claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
                    )
                )
                .where(claim_heads.c.claim_id == claim_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            _require_integrity(
                not self.history(claim_id),
                "claim versions exist without a head projection",
            )
            return None
        head = self._decode_head_row(dict(row))
        self._require_latest_head(head)
        return head

    def get_head_required(self, claim_id: str) -> AtomicClaim:
        claim = self.get_head(claim_id)
        if claim is None:
            raise KeyError(f"claim does not exist: {claim_id}")
        return claim

    def list_heads(self) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(
                claim_heads.c.claim_id.label("head_claim_id"),
                claim_heads.c.claim_version_id.label("head_claim_version_id"),
                claim_heads.c.version.label("head_version"),
                claim_heads.c.status.label("head_status"),
                claim_versions.c.claim_version_id.label("claim_version_id"),
                claim_versions.c.claim_id,
                claim_versions.c.version,
                claim_versions.c.status,
                claim_versions.c.record_json,
                claim_versions.c.content_hash,
                claim_versions.c.created_at,
            )
            .select_from(
                claim_heads.outerjoin(
                    claim_versions,
                    claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
                )
            )
            .order_by(claim_heads.c.claim_id)
        ).mappings()
        heads = tuple(self._decode_head_row(dict(row)) for row in rows)
        for head in heads:
            self._require_latest_head(head)
        history_claim_ids = set(
            self._connection.execute(select(claim_versions.c.claim_id).distinct()).scalars()
        )
        _require_integrity(
            {head.claim_id for head in heads} == history_claim_ids,
            "claim versions exist without a head projection",
        )
        return heads

    def history(self, claim_id: str) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(
                claim_versions.c.claim_version_id,
                claim_versions.c.claim_id,
                claim_versions.c.version,
                claim_versions.c.status,
                claim_versions.c.record_json,
                claim_versions.c.content_hash,
                claim_versions.c.created_at,
            )
            .where(claim_versions.c.claim_id == claim_id)
            .order_by(claim_versions.c.version)
        ).mappings()
        claims = tuple(_decode_claim_version_row(dict(row)) for row in rows)
        for expected_version, claim in enumerate(claims, start=1):
            _require_integrity(claim.claim_id == claim_id, "claim history has the wrong claim_id")
            _require_integrity(
                claim.version == expected_version,
                "claim history versions must be contiguous from version 1",
            )
            expected_parent = (
                None if expected_version == 1 else f"{claim_id}:{expected_version - 1}"
            )
            _require_integrity(
                claim.parent_version_id == expected_parent,
                "claim history parent linkage is not contiguous",
            )
        return claims

    def _require_latest_head(self, head: AtomicClaim) -> None:
        history = self.history(head.claim_id)
        _require_integrity(bool(history), "claim head has no version history")
        _require_integrity(
            head == history[-1],
            "claim head does not reference the latest history version",
        )

    def add_version(self, claim: AtomicClaim) -> None:
        record_json = _validated_model_json(AtomicClaim, claim)
        validated = AtomicClaim.model_validate_json(record_json)
        version_id = f"{validated.claim_id}:{validated.version}"
        current_head = self.get_head(validated.claim_id)
        if current_head is None:
            _require_integrity(
                validated.version == 1 and validated.parent_version_id is None,
                "a claim without a head must begin at version 1",
            )
        else:
            expected_version = current_head.version + 1
            expected_parent_version_id = f"{current_head.claim_id}:{current_head.version}"
            _require_integrity(
                validated.version == expected_version,
                "claim version must be the exact successor of the current head",
            )
            _require_integrity(
                validated.parent_version_id == expected_parent_version_id,
                "claim parent_version_id must reference the current head",
            )
        self._connection.execute(
            insert(claim_versions).values(
                claim_version_id=version_id,
                claim_id=validated.claim_id,
                version=validated.version,
                status=validated.status.value,
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
                created_at=validated.created_at.isoformat(),
            )
        )
        head_insert = sqlite_insert(claim_heads).values(
            claim_id=validated.claim_id,
            claim_version_id=version_id,
            version=validated.version,
            status=validated.status.value,
        )
        self._connection.execute(
            head_insert.on_conflict_do_update(
                index_elements=[claim_heads.c.claim_id],
                set_={
                    "claim_version_id": version_id,
                    "version": validated.version,
                    "status": validated.status.value,
                },
            )
        )

    @staticmethod
    def _decode_head_row(row: Mapping[str, object]) -> AtomicClaim:
        if row["record_json"] is None:
            raise StorageIntegrityError("storage integrity error: claim head is dangling")
        claim = _decode_claim_version_row(row)
        _require_integrity(
            _stored_str(row, "head_claim_id") == claim.claim_id,
            "claim head claim_id does not match claim version",
        )
        _require_integrity(
            _stored_str(row, "head_claim_version_id") == f"{claim.claim_id}:{claim.version}",
            "claim head claim_version_id does not match claim version",
        )
        _require_integrity(
            _stored_int(row, "head_version") == claim.version,
            "claim head version does not match claim version",
        )
        _require_integrity(
            _stored_str(row, "head_status") == claim.status.value,
            "claim head status does not match claim version",
        )
        return claim


class TransactionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_proposal_id(self, proposal_id: str) -> StoredTransaction | None:
        row = (
            self._connection.execute(
                select(
                    transactions.c.proposal_id,
                    transactions.c.idempotency_key,
                    transactions.c.intent_fingerprint,
                    transactions.c.proposal_json,
                    transactions.c.proposal_hash,
                    transactions.c.decision_json,
                    transactions.c.created_at,
                ).where(transactions.c.proposal_id == proposal_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_transaction_row(dict(row))

    def get_by_idempotency_key(self, key: str) -> StoredTransaction | None:
        row = (
            self._connection.execute(
                select(
                    transactions.c.proposal_id,
                    transactions.c.idempotency_key,
                    transactions.c.intent_fingerprint,
                    transactions.c.proposal_json,
                    transactions.c.proposal_hash,
                    transactions.c.decision_json,
                    transactions.c.created_at,
                ).where(transactions.c.idempotency_key == key)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_transaction_row(dict(row))

    def list_all(self) -> tuple[StoredTransaction, ...]:
        rows = self._connection.execute(
            select(
                transactions.c.proposal_id,
                transactions.c.idempotency_key,
                transactions.c.intent_fingerprint,
                transactions.c.proposal_json,
                transactions.c.proposal_hash,
                transactions.c.decision_json,
                transactions.c.created_at,
            ).order_by(transactions.c.created_at, transactions.c.proposal_id)
        ).mappings()
        return tuple(_decode_transaction_row(dict(row)) for row in rows)

    def add(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        occurred_at: UtcTimestamp,
        *,
        intent_fingerprint: str | None = None,
    ) -> None:
        dumped_proposal = proposal.model_dump(mode="python", warnings="none")
        validated_proposal = PROPOSAL_ADAPTER.validate_python(dumped_proposal)
        if validated_proposal.proposal_type in _STRICT_JSON_PROPOSAL_TYPES:
            proposal_json = canonical_json_bytes(
                validated_proposal.model_dump(mode="json", warnings="none")
            ).decode("utf-8")
        else:
            proposal_json = _storage_json(
                validated_proposal.model_dump(mode="python", warnings="none")
            )
        decision_json = _validated_model_json(TransactionDecision, decision)
        validated_decision = TransactionDecision.model_validate_json(decision_json)
        validated_occurred_at = _validated_timestamp(occurred_at)
        try:
            validated_intent_fingerprint = (
                None
                if intent_fingerprint is None
                else SHA256_ADAPTER.validate_python(intent_fingerprint)
            )
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid intent fingerprint"
            ) from error
        _require_integrity(
            validated_decision.proposal_id == validated_proposal.proposal_id,
            "decision proposal_id does not match proposal",
        )
        self._connection.execute(
            insert(transactions).values(
                proposal_id=validated_proposal.proposal_id,
                idempotency_key=validated_proposal.idempotency_key,
                intent_fingerprint=validated_intent_fingerprint,
                proposal_hash=_proposal_hash(validated_proposal),
                proposal_json=proposal_json,
                decision_json=decision_json,
                created_at=validated_occurred_at.isoformat(),
            )
        )


class AuditRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def last(self) -> AuditEvent | None:
        events = self._read_all_events()
        return None if not events else events[-1]

    def list_all(self) -> tuple[AuditEvent, ...]:
        return self._read_all_events()

    def add(self, event: AuditEvent) -> None:
        event_json = _validated_model_json(AuditEvent, event)
        validated = AuditEvent.model_validate_json(event_json)
        events = self._read_all_events()
        expected = append_event(
            events[-1] if events else None,
            validated.event_type,
            validated.payload,
            validated.occurred_at,
        )
        _require_integrity(
            validated == expected,
            "audit event is not the exact verified next event",
        )
        self._connection.execute(
            insert(audit_events).values(
                sequence=validated.sequence,
                event_id=validated.event_id,
                previous_hash=validated.previous_hash,
                payload_hash=validated.payload_hash,
                event_hash=validated.event_hash,
                event_json=event_json,
            )
        )

    def _read_all_events(self) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            select(
                audit_events.c.sequence,
                audit_events.c.event_id,
                audit_events.c.previous_hash,
                audit_events.c.payload_hash,
                audit_events.c.event_hash,
                audit_events.c.event_json,
            ).order_by(audit_events.c.sequence)
        ).mappings()
        events = tuple(_decode_audit_row(dict(row)) for row in rows)
        verification = verify_chain(events)
        _require_integrity(
            verification.valid,
            f"audit chain is invalid at sequence {verification.first_invalid_sequence}",
        )
        return events


class PolicyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add_and_activate(self, snapshot: PolicySnapshot, created_at: UtcTimestamp) -> None:
        snapshot_json = _validated_model_json(PolicySnapshot, snapshot)
        validated = PolicySnapshot.model_validate_json(snapshot_json)
        validated_created_at = _validated_timestamp(created_at)
        _require_integrity(
            validated.policy_hash == policy_hash(validated.policy),
            "policy_hash does not match policy_json",
        )
        policy_insert = sqlite_insert(governance_policies).values(
            policy_hash=validated.policy_hash,
            policy_json=validated.policy.model_dump_json(),
            created_at=validated_created_at.isoformat(),
        )
        self._connection.execute(policy_insert.on_conflict_do_nothing())
        stored_policy = (
            self._connection.execute(
                select(
                    governance_policies.c.policy_hash,
                    governance_policies.c.policy_json,
                    governance_policies.c.created_at,
                ).where(governance_policies.c.policy_hash == validated.policy_hash)
            )
            .mappings()
            .one()
        )
        _decode_policy_row(dict(stored_policy))
        state_insert = sqlite_insert(governance_state).values(
            singleton_id=1,
            active_policy_hash=validated.policy_hash,
        )
        self._connection.execute(
            state_insert.on_conflict_do_update(
                index_elements=[governance_state.c.singleton_id],
                set_={"active_policy_hash": validated.policy_hash},
            )
        )

    def get_active(self) -> PolicySnapshot | None:
        state_rows = tuple(
            self._connection.execute(
                select(
                    governance_state.c.singleton_id,
                    governance_state.c.active_policy_hash,
                )
            ).mappings()
        )
        if not state_rows:
            return None
        _require_integrity(len(state_rows) == 1, "governance_state must contain one singleton row")
        state = dict(state_rows[0])
        _require_integrity(
            _stored_int(state, "singleton_id") == 1,
            "governance_state singleton_id must equal 1",
        )
        try:
            active_policy_hash = SHA256_ADAPTER.validate_python(
                _stored_str(state, "active_policy_hash")
            )
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid active policy hash"
            ) from error
        stored_policy = (
            self._connection.execute(
                select(
                    governance_policies.c.policy_hash,
                    governance_policies.c.policy_json,
                    governance_policies.c.created_at,
                ).where(governance_policies.c.policy_hash == active_policy_hash)
            )
            .mappings()
            .one_or_none()
        )
        if stored_policy is None:
            raise StorageIntegrityError(
                "storage integrity error: active policy does not exist in governance_policies"
            )
        return _decode_policy_row(
            dict(stored_policy),
            active_policy_hash=active_policy_hash,
        )

    def get(self, policy_hash_value: str) -> PolicySnapshot | None:
        stored_policy = (
            self._connection.execute(
                select(
                    governance_policies.c.policy_hash,
                    governance_policies.c.policy_json,
                    governance_policies.c.created_at,
                ).where(governance_policies.c.policy_hash == policy_hash_value)
            )
            .mappings()
            .one_or_none()
        )
        return None if stored_policy is None else _decode_policy_row(dict(stored_policy))

    def list_all(self) -> tuple[PolicySnapshot, ...]:
        rows = self._connection.execute(
            select(
                governance_policies.c.policy_hash,
                governance_policies.c.policy_json,
                governance_policies.c.created_at,
            ).order_by(governance_policies.c.created_at, governance_policies.c.policy_hash)
        ).mappings()
        return tuple(_decode_policy_row(dict(row)) for row in rows)


class RepositorySet:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.evidence = EvidenceRepository(connection)
        self.claims = ClaimRepository(connection)
        self.transactions = TransactionRepository(connection)
        self.audit = AuditRepository(connection)
        self.policies = PolicyRepository(connection)

    def adaptation_integrity_snapshot(self) -> AdaptationIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            ConfigurationVersionRepository,
            EvaluatorAuditRepository,
            EvaluatorCollapseRepository,
            EvaluatorHeadRepository,
            EvaluatorSuccessionRepository,
            EvaluatorVersionRepository,
            ResearchRunEventRepository,
            ResearchRunHeadRepository,
            ResearchRunRepository,
            SelfImprovementMeasurementRepository,
        )

        return AdaptationIntegritySnapshot(
            research_runs=ResearchRunRepository(self._connection).list_all(),
            research_run_events=ResearchRunEventRepository(self._connection).list_all(),
            configuration_versions=ConfigurationVersionRepository(self._connection).list_all(),
            evaluator_audits=EvaluatorAuditRepository(self._connection).list_all(),
            measurements=SelfImprovementMeasurementRepository(self._connection).list_all(),
            evaluator_versions=EvaluatorVersionRepository(self._connection).list_all(),
            evaluator_succession_decisions=EvaluatorSuccessionRepository(
                self._connection
            ).list_all(),
            evaluator_collapse_records=EvaluatorCollapseRepository(self._connection).list_all(),
            research_run_heads=ResearchRunHeadRepository(self._connection).list_all(),
            evaluator_head=EvaluatorHeadRepository(self._connection).get(),
        )

    def progress_integrity_snapshot(self) -> ProgressIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            CompletionDecisionRepository,
            ProgressEventRepository,
            ProgressHeadRepository,
            ProgressPlanRepository,
            ProgressSubtaskRepository,
            RunBudgetRepository,
            RunCheckpointRepository,
        )

        return ProgressIntegritySnapshot(
            plans=ProgressPlanRepository(self._connection).list_all(),
            subtasks=ProgressSubtaskRepository(self._connection).list_all(),
            events=ProgressEventRepository(self._connection).list_all(),
            budgets=RunBudgetRepository(self._connection).list_all(),
            checkpoints=RunCheckpointRepository(self._connection).list_all(),
            completion_decisions=CompletionDecisionRepository(self._connection).list_all(),
            heads=ProgressHeadRepository(self._connection).list_all(),
        )

    def trail_integrity_snapshot(self) -> TrailIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            EvidenceTrailAssessmentRepository,
            EvidenceTrailCheckRepository,
            EvidenceTrailHeadRepository,
            EvidenceTrailNodeRepository,
            EvidenceTrailRelationRepository,
            EvidenceTrailVersionRepository,
            ReportSentenceBindingRepository,
        )

        return TrailIntegritySnapshot(
            versions=EvidenceTrailVersionRepository(self._connection).list_all(),
            nodes=EvidenceTrailNodeRepository(self._connection).list_all(),
            relations=EvidenceTrailRelationRepository(self._connection).list_all(),
            checks=EvidenceTrailCheckRepository(self._connection).list_all(),
            assessments=EvidenceTrailAssessmentRepository(self._connection).list_all(),
            bindings=ReportSentenceBindingRepository(self._connection).list_all(),
            heads=EvidenceTrailHeadRepository(self._connection).list_all(),
        )

    def rule_integrity_snapshot(self) -> RuleIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            BehavioralRuleHeadRepository,
            BehavioralRuleVersionRepository,
            ReviewerAssessmentRepository,
            RuleConsolidationDecisionRepository,
            RuleIncidentRepository,
            RuleRegressionCaseRepository,
        )

        return RuleIntegritySnapshot(
            incidents=RuleIncidentRepository(self._connection).list_all(),
            versions=BehavioralRuleVersionRepository(self._connection).list_all(),
            assessments=ReviewerAssessmentRepository(self._connection).list_all(),
            decisions=RuleConsolidationDecisionRepository(self._connection).list_all(),
            regressions=RuleRegressionCaseRepository(self._connection).list_all(),
            heads=BehavioralRuleHeadRepository(self._connection).list_all(),
        )

    def harness_integrity_snapshot(self) -> HarnessIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            HarnessBudgetRepository,
            HarnessCampaignHeadRepository,
            HarnessCampaignRepository,
            HarnessConfoundRepository,
            HarnessDecisionRepository,
            HarnessMetricRepository,
            HarnessObservationRepository,
            HarnessPartitionManifestRepository,
        )

        return HarnessIntegritySnapshot(
            campaigns=HarnessCampaignRepository(self._connection).list_all(),
            partitions=HarnessPartitionManifestRepository(self._connection).list_all(),
            budgets=HarnessBudgetRepository(self._connection).list_all(),
            observations=HarnessObservationRepository(self._connection).list_all(),
            metrics=HarnessMetricRepository(self._connection).list_all(),
            confounds=HarnessConfoundRepository(self._connection).list_all(),
            decisions=HarnessDecisionRepository(self._connection).list_all(),
            heads=HarnessCampaignHeadRepository(self._connection).list_all(),
        )

    def handbook_integrity_snapshot(self) -> HandbookIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            HandbookVerificationRepository,
        )

        return HandbookIntegritySnapshot(
            verifications=HandbookVerificationRepository(self._connection).list_all(),
        )

    def representation_integrity_snapshot(self) -> RepresentationIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            PrimitiveEvaluationRepository,
            PrimitiveHeadRepository,
            PrimitiveVersionRepository,
            VerificationMechanismSpecRepository,
            VerificationResultRepository,
        )

        return RepresentationIntegritySnapshot(
            versions=PrimitiveVersionRepository(self._connection).list_all(),
            evaluations=PrimitiveEvaluationRepository(self._connection).list_all(),
            verification_mechanisms=VerificationMechanismSpecRepository(
                self._connection
            ).list_all(),
            verification_results=VerificationResultRepository(self._connection).list_all(),
            heads=PrimitiveHeadRepository(self._connection).list_all(),
        )

    def hypothesis_integrity_snapshot(self) -> HypothesisIntegritySnapshot:
        from super_scientist.providers.storage.domain_records import (
            CounterexampleRecordRepository,
            ExecutableModelSpecRepository,
            HypothesisAdmissionDecisionRepository,
            HypothesisHeadRepository,
            HypothesisRevisionRepository,
            HypothesisVersionRepository,
            SimulationResultRepository,
            VerificationMechanismSpecRepository,
            VerificationResultRepository,
        )

        return HypothesisIntegritySnapshot(
            versions=HypothesisVersionRepository(self._connection).list_all(),
            models=ExecutableModelSpecRepository(self._connection).list_all(),
            mechanisms=VerificationMechanismSpecRepository(self._connection).list_all(),
            simulations=SimulationResultRepository(self._connection).list_all(),
            results=VerificationResultRepository(self._connection).list_all(),
            counterexamples=CounterexampleRecordRepository(self._connection).list_all(),
            revisions=HypothesisRevisionRepository(self._connection).list_all(),
            admissions=HypothesisAdmissionDecisionRepository(self._connection).list_all(),
            heads=HypothesisHeadRepository(self._connection).list_all(),
        )

    def cognitive_integrity_snapshot(self) -> CognitiveIntegritySnapshot:
        from super_scientist.providers.storage.cognitive_records import (
            CapabilityProfileRepository,
            CohortPlanRepository,
            CollaborationSessionRepository,
            CollaborationTerminationRepository,
            CompiledProgressPlanBindingRepository,
            DiversityAssessmentRepository,
            MethodDirectionOutcomeRepository,
            PeerContributionRepository,
            PeerRequestRepository,
            ProcedureCompilationRepository,
            TopologyEventRepository,
        )

        return CognitiveIntegritySnapshot(
            capability_profiles=CapabilityProfileRepository(self._connection).list_all(),
            cohort_plans=CohortPlanRepository(self._connection).list_all(),
            diversity_assessments=DiversityAssessmentRepository(self._connection).list_all(),
            collaboration_sessions=CollaborationSessionRepository(self._connection).list_all(),
            peer_requests=PeerRequestRepository(self._connection).list_all(),
            peer_contributions=PeerContributionRepository(self._connection).list_all(),
            topology_events=TopologyEventRepository(self._connection).list_all(),
            terminations=CollaborationTerminationRepository(self._connection).list_all(),
            compilations=ProcedureCompilationRepository(self._connection).list_all(),
            method_outcomes=MethodDirectionOutcomeRepository(self._connection).list_all(),
            bindings=CompiledProgressPlanBindingRepository(self._connection).list_all(),
        )

    def evaluation_extension_integrity_snapshot(
        self,
    ) -> EvaluationExtensionIntegritySnapshot:
        from super_scientist.providers.storage.evaluation_records import (
            GuidanceCellRepository,
            GuidanceEvaluationProtocolRepository,
            HarnessExecutionTraceRepository,
            ModelHarnessAnalysisRepository,
            ModelHarnessCellRepository,
            ModelHarnessProtocolRepository,
            RewardAssessmentRepository,
        )

        return EvaluationExtensionIntegritySnapshot(
            guidance_protocols=GuidanceEvaluationProtocolRepository(self._connection).list_all(),
            guidance_cells=GuidanceCellRepository(self._connection).list_all(),
            model_harness_protocols=ModelHarnessProtocolRepository(self._connection).list_all(),
            model_harness_cells=ModelHarnessCellRepository(self._connection).list_all(),
            model_harness_analyses=ModelHarnessAnalysisRepository(self._connection).list_all(),
            harness_execution_traces=HarnessExecutionTraceRepository(self._connection).list_all(),
            reward_assessments=RewardAssessmentRepository(self._connection).list_all(),
        )

    def has_durable_state(self) -> bool:
        tables = (
            governance_policies,
            governance_state,
            evidence_records,
            claim_versions,
            claim_heads,
            transactions,
            audit_events,
            research_runs,
            research_run_events,
            configuration_versions,
            self_improvement_measurements,
            evaluator_audits,
            evaluator_versions,
            evaluator_succession_decisions,
            evaluator_collapse_records,
            research_run_heads,
            evaluator_heads,
            progress_plans,
            progress_subtasks,
            progress_events,
            run_budgets,
            run_checkpoints,
            completion_decisions,
            progress_heads,
            evidence_trail_versions,
            evidence_trail_nodes,
            evidence_trail_relations,
            evidence_trail_checks,
            evidence_trail_assessments,
            report_sentence_bindings,
            evidence_trail_heads,
            rule_incidents,
            behavioral_rule_versions,
            reviewer_assessments,
            rule_consolidation_decisions,
            rule_regression_cases,
            behavioral_rule_version_incidents,
            behavioral_rule_version_supersessions,
            reviewer_assessment_rule_versions,
            reviewer_assessment_incidents,
            rule_consolidation_assessments,
            rule_consolidation_incidents,
            rule_regression_case_incidents,
            behavioral_rule_heads,
            primitive_versions,
            primitive_evaluations,
            primitive_heads,
            hypothesis_versions,
            executable_model_specs,
            verification_mechanism_specs,
            simulation_results,
            verification_results,
            counterexample_records,
            hypothesis_revisions,
            hypothesis_admission_decisions,
            hypothesis_heads,
            behavior_rule_link_versions,
            handbook_verification_records,
            harness_campaigns,
            harness_partition_manifests,
            harness_budgets,
            harness_observations,
            harness_metrics,
            harness_confounds,
            harness_decisions,
            harness_campaign_heads,
            capability_profiles,
            cohort_plans,
            diversity_assessments,
            collaboration_sessions,
            peer_requests,
            peer_contributions,
            topology_events,
            collaboration_terminations,
            procedure_compilations,
            method_direction_outcomes,
            compiled_progress_plan_bindings,
            guidance_protocols,
            guidance_cells,
            model_harness_protocols,
            model_harness_cells,
            model_harness_analyses,
            harness_execution_traces,
            reward_assessments,
        )
        return any(
            self._connection.execute(select(table).limit(1)).first() is not None for table in tables
        )
