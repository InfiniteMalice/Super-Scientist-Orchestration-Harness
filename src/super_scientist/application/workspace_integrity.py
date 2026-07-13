from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.primitives import Sha256Hex, canonical_json_bytes
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome
from super_scientist.kernel.audit.models import (
    AuditEvent,
    AuditVerification,
    json_compatible_payload,
)
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.repositories import (
    RepositorySet,
    StorageIntegrityError,
    StoredTransaction,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


def verify_workspace(
    repositories: RepositorySet,
    artifact_store: ArtifactStore,
) -> AuditVerification:
    events: tuple[AuditEvent, ...] = ()
    try:
        active_policy = repositories.policies.get_active()
        repositories.policies.list_all()
        evidence = repositories.evidence.list_all()
        heads = repositories.claims.list_heads()
        transactions = repositories.transactions.list_all()
        events = repositories.audit.list_all()
        _require(
            active_policy is not None or not repositories.has_durable_state(),
            "durable workspace state requires an active registered policy",
        )
        audit_records = _validated_audit_records(events, repositories)
        _require_transaction_audit_consistency(transactions, audit_records)
        _require_projection_consistency(repositories, transactions, evidence, heads)
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
) -> tuple[tuple[Proposal, TransactionDecision, str | None], ...]:
    records: list[tuple[Proposal, TransactionDecision, str | None]] = []
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
        records.append((proposal, decision, intent_fingerprint))
    return tuple(records)


def _require_transaction_audit_consistency(
    transactions: tuple[StoredTransaction, ...],
    audit_records: tuple[tuple[Proposal, TransactionDecision, str | None], ...],
) -> None:
    for transaction in transactions:
        matches = sum(
            proposal == transaction.proposal
            and decision == transaction.decision
            and intent_fingerprint == transaction.intent_fingerprint
            for proposal, decision, intent_fingerprint in audit_records
        )
        _require(matches == 1, "transaction does not have one exact audit decision")
    for proposal, decision, intent_fingerprint in audit_records:
        if decision.accepted:
            _require(
                any(
                    transaction.proposal == proposal
                    and transaction.decision == decision
                    and transaction.intent_fingerprint == intent_fingerprint
                    for transaction in transactions
                ),
                "accepted audit decision has no stored transaction",
            )


def _require_projection_consistency(
    repositories: RepositorySet,
    transactions: tuple[StoredTransaction, ...],
    evidence: tuple[EvidenceRecord, ...],
    heads: tuple[AtomicClaim, ...],
) -> None:
    expected_evidence: dict[str, EvidenceRecord] = {}
    expected_claims: dict[tuple[str, int], AtomicClaim] = {}
    for transaction in transactions:
        if not transaction.decision.accepted:
            continue
        proposal = transaction.proposal
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


def _add_unique[KeyT, ValueT](
    values: dict[KeyT, ValueT],
    key: KeyT,
    value: ValueT,
    label: str,
) -> None:
    _require(key not in values, f"duplicate {label}")
    values[key] = value


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(detail)
