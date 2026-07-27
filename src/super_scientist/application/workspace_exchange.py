from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import require_workspace_integrity
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    InvalidProposal,
    Proposal,
    ProposalAttempt,
    ProposalKind,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.domain_records import HarnessCampaignHeadRepository
from super_scientist.providers.storage.repositories import RepositorySet, StoredTransaction

type UnitOfWorkFactory = Callable[[], DatabaseUnitOfWork]


class Clock(Protocol):
    def now(self) -> UtcTimestamp: ...


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


class WorkspacePolicy(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    policy_hash: Sha256Hex
    snapshot: PolicySnapshot

    @model_validator(mode="after")
    def require_exact_hash(self) -> WorkspacePolicy:
        if self.policy_hash != self.snapshot.policy_hash:
            raise ValueError("workspace policy hash does not match its snapshot")
        return self


class WorkspaceArtifactReference(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    sha256: Sha256Hex
    size_bytes: int = Field(strict=True, ge=0)
    media_type: StableIdentifier

    @classmethod
    def from_artifact(cls, artifact: ArtifactRef) -> WorkspaceArtifactReference:
        return cls(
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type=artifact.media_type,
        )

    def to_artifact(self) -> ArtifactRef:
        return ArtifactRef(
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            relative_path=f"sha256/{self.sha256[:2]}/{self.sha256}",
        )


class WorkspaceRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    stable_identity: StableIdentifier
    replay_order: int = Field(strict=True, ge=0)
    governing_policy_hash: Sha256Hex
    proposal_hash: Sha256Hex
    proposal: Proposal
    expected_decision: TransactionDecision

    @model_validator(mode="after")
    def require_exact_identity_and_hash(self) -> WorkspaceRecord:
        if self.stable_identity != self.proposal.proposal_id:
            raise ValueError("workspace record stable identity does not match proposal")
        if self.proposal_hash != _proposal_hash(self.proposal):
            raise ValueError("workspace record proposal hash does not match proposal content")
        if self.expected_decision.proposal_id != self.stable_identity:
            raise ValueError("workspace record decision identity does not match proposal")
        if self.expected_decision.replayed:
            raise ValueError("workspace record cannot export a replay marker")
        return self


class WorkspaceProjectionExpectation(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    projection_kind: StableIdentifier
    stable_identity: StableIdentifier
    content_hash: Sha256Hex


class WorkspaceExport(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    bootstrap_policy_hash: Sha256Hex
    active_policy_hash: Sha256Hex
    policies: tuple[WorkspacePolicy, ...] = Field(min_length=1)
    records: tuple[WorkspaceRecord, ...]
    projection_expectations: tuple[WorkspaceProjectionExpectation, ...]
    artifacts: tuple[WorkspaceArtifactReference, ...]
    bundle_hash: Sha256Hex

    @model_validator(mode="after")
    def require_canonical_bundle(self) -> WorkspaceExport:
        policy_hashes = tuple(item.policy_hash for item in self.policies)
        if policy_hashes != tuple(sorted(policy_hashes)) or len(set(policy_hashes)) != len(
            policy_hashes
        ):
            raise ValueError("workspace policies must be unique and sorted by stable identity")
        if self.bootstrap_policy_hash not in policy_hashes:
            raise ValueError("workspace bootstrap policy is absent")
        if self.active_policy_hash not in policy_hashes:
            raise ValueError("workspace active policy is absent")
        identities = tuple(item.stable_identity for item in self.records)
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(identities):
            raise ValueError("workspace records must be unique and sorted by stable identity")
        replay_orders = tuple(item.replay_order for item in self.records)
        if tuple(sorted(replay_orders)) != tuple(range(len(replay_orders))):
            raise ValueError("workspace replay order must be contiguous")
        projection_keys = tuple(
            (item.projection_kind, item.stable_identity) for item in self.projection_expectations
        )
        if projection_keys != tuple(sorted(projection_keys)) or len(set(projection_keys)) != len(
            projection_keys
        ):
            raise ValueError("workspace projections must be unique and canonically sorted")
        artifact_keys = tuple(
            (item.sha256, item.size_bytes, item.media_type) for item in self.artifacts
        )
        if artifact_keys != tuple(sorted(artifact_keys)) or len(set(artifact_keys)) != len(
            artifact_keys
        ):
            raise ValueError("workspace artifacts must be unique and canonically sorted")
        if self.bundle_hash != _workspace_export_hash(self):
            raise ValueError("workspace bundle hash does not match canonical content")
        _require_protected_safe(self.model_dump(mode="json"))
        return self


class WorkspaceImportConflict(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    stable_identity: StableIdentifier
    code: RejectionCode


class WorkspaceImportResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    imported: int = Field(strict=True, ge=0)
    replayed: int = Field(strict=True, ge=0)
    conflicts: tuple[WorkspaceImportConflict, ...]
    projections_verified: bool


class WorkspaceImportError(ValueError):
    """Raised when a canonical bundle cannot be imported exactly."""


def export_workspace(
    *,
    uow_factory: UnitOfWorkFactory,
    artifact_store: ArtifactStore,
) -> WorkspaceExport:
    with uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        require_workspace_integrity(repositories, artifact_store)
        active_policy = repositories.policies.get_active()
        if active_policy is None:
            raise ValueError("workspace export requires an active policy")
        transactions = repositories.transactions.list_all()
        governing_hashes = _governing_policy_hashes(repositories.audit.list_all())
        records = tuple(
            sorted(
                (
                    WorkspaceRecord(
                        stable_identity=transaction.proposal.proposal_id,
                        replay_order=index,
                        governing_policy_hash=_governing_hash(transaction, governing_hashes),
                        proposal_hash=_proposal_hash(transaction.proposal),
                        proposal=transaction.proposal,
                        expected_decision=transaction.decision.model_copy(
                            update={"replayed": False}
                        ),
                    )
                    for index, transaction in enumerate(transactions)
                ),
                key=lambda item: item.stable_identity,
            )
        )
        snapshots = repositories.policies.list_all()
        policies = tuple(
            WorkspacePolicy(policy_hash=snapshot.policy_hash, snapshot=snapshot)
            for snapshot in sorted(snapshots, key=lambda item: item.policy_hash)
        )
        bootstrap_hash = (
            governing_hashes[transactions[0].proposal.proposal_id]
            if transactions
            else active_policy.policy_hash
        )
        projections = _projection_expectations(
            repositories,
            _active_connection(unit_of_work.connection),
        )
        artifacts = _artifact_references(records)
    payload: dict[str, object] = {
        "schema_version": 1,
        "bootstrap_policy_hash": bootstrap_hash,
        "active_policy_hash": active_policy.policy_hash,
        "policies": policies,
        "records": records,
        "projection_expectations": projections,
        "artifacts": artifacts,
    }
    return WorkspaceExport(
        schema_version=1,
        bootstrap_policy_hash=bootstrap_hash,
        active_policy_hash=active_policy.policy_hash,
        policies=policies,
        records=records,
        projection_expectations=projections,
        artifacts=artifacts,
        bundle_hash=sha256_hex(canonical_json_bytes(_json_value(payload))),
    )


def import_workspace(
    workspace: WorkspaceExport,
    *,
    uow_factory: UnitOfWorkFactory,
    artifact_store: ArtifactStore,
    source_artifact_store: ArtifactStore,
    clock: Clock,
) -> WorkspaceImportResult:
    canonical = WorkspaceExport.model_validate_json(workspace.model_dump_json())
    policy_by_hash = {item.policy_hash: item.snapshot for item in canonical.policies}
    _prepare_target_policy(
        canonical,
        policy_by_hash,
        uow_factory=uow_factory,
        clock=clock,
    )
    _transfer_artifacts(
        canonical.artifacts,
        source=source_artifact_store,
        target=artifact_store,
    )
    imported = 0
    replayed = 0
    conflicts: list[WorkspaceImportConflict] = []
    for record in sorted(canonical.records, key=lambda item: item.replay_order):
        coordinator = TransactionCoordinator(
            uow_factory,
            policy_by_hash[record.governing_policy_hash],
            clock,
            artifact_store,
        )
        attempt = _proposal_attempt(record)
        decision = coordinator.submit_intent(
            attempt,
            _constant_proposal_factory(record.proposal),
        )
        if decision.replayed:
            replayed += 1
            if decision.model_copy(update={"replayed": False}) != record.expected_decision:
                raise WorkspaceImportError(
                    f"replayed decision changed for {record.stable_identity}"
                )
        elif _is_identity_conflict(decision):
            conflicts.append(
                WorkspaceImportConflict(
                    stable_identity=record.stable_identity,
                    code=RejectionCode.IDEMPOTENCY_CONFLICT,
                )
            )
        elif decision != record.expected_decision:
            raise WorkspaceImportError(f"imported decision changed for {record.stable_identity}")
        else:
            imported += 1
    if conflicts:
        return WorkspaceImportResult(
            imported=imported,
            replayed=replayed,
            conflicts=tuple(conflicts),
            projections_verified=False,
        )
    target = export_workspace(uow_factory=uow_factory, artifact_store=artifact_store)
    if target != canonical:
        raise WorkspaceImportError("rebuilt workspace export does not match canonical bundle")
    return WorkspaceImportResult(
        imported=imported,
        replayed=replayed,
        conflicts=(),
        projections_verified=True,
    )


def _proposal_hash(proposal: Proposal) -> str:
    return sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json", warnings="none")))


def _constant_proposal_factory(proposal: Proposal) -> Callable[[], Proposal]:
    def factory() -> Proposal:
        return proposal

    return factory


def _workspace_export_hash(workspace: WorkspaceExport) -> str:
    payload = workspace.model_dump(mode="json")
    del payload["bundle_hash"]
    return sha256_hex(canonical_json_bytes(payload))


def _governing_policy_hashes(events: tuple[AuditEvent, ...]) -> dict[str, str]:
    governing: dict[str, str] = {}
    for event in events:
        payload = json_compatible_payload(event.payload)
        proposal = payload.get("proposal")
        if not isinstance(proposal, Mapping) or not payload.get("transaction_persisted"):
            continue
        proposal_id = proposal.get("proposal_id")
        policy_hash = payload.get("policy_hash")
        if not isinstance(proposal_id, str) or not isinstance(policy_hash, str):
            raise ValueError("audit event lacks a stable proposal or policy identity")
        if proposal_id in governing:
            raise ValueError("transaction has more than one persisted audit event")
        governing[proposal_id] = policy_hash
    return governing


def _governing_hash(
    transaction: StoredTransaction,
    governing_hashes: Mapping[str, str],
) -> str:
    try:
        return governing_hashes[transaction.proposal.proposal_id]
    except KeyError as error:
        raise ValueError("transaction lacks a persisted governing audit event") from error


def _artifact_references(
    records: tuple[WorkspaceRecord, ...],
) -> tuple[WorkspaceArtifactReference, ...]:
    references: dict[tuple[str, int, str], WorkspaceArtifactReference] = {}
    for record in records:
        for artifact in _walk_artifacts(record.proposal):
            reference = WorkspaceArtifactReference.from_artifact(artifact)
            references[(reference.sha256, reference.size_bytes, reference.media_type)] = reference
    return tuple(references[key] for key in sorted(references))


def _walk_artifacts(value: object) -> tuple[ArtifactRef, ...]:
    if isinstance(value, ArtifactRef):
        return (value,)
    if isinstance(value, BaseModel):
        return tuple(
            artifact for item in value.__dict__.values() for artifact in _walk_artifacts(item)
        )
    if isinstance(value, Mapping):
        return tuple(artifact for item in value.values() for artifact in _walk_artifacts(item))
    if isinstance(value, (tuple, list, frozenset)):
        return tuple(artifact for item in value for artifact in _walk_artifacts(item))
    return ()


def _projection_expectations(
    repositories: RepositorySet,
    connection: Connection,
) -> tuple[WorkspaceProjectionExpectation, ...]:
    values: list[tuple[str, str, object]] = []
    active = repositories.policies.get_active()
    if active is not None:
        values.append(("active_policy", "active", active.policy_hash))
    values.extend(
        ("claim_head", claim.claim_id, claim.model_dump(mode="json"))
        for claim in repositories.claims.list_heads()
    )
    adaptation = repositories.adaptation_integrity_snapshot()
    values.extend(("research_run_head", item[0], item[1]) for item in adaptation.research_run_heads)
    if adaptation.evaluator_head is not None:
        values.append(("evaluator_head", "active", adaptation.evaluator_head))
    values.extend(
        ("progress_head", item[0], item[1:])
        for item in repositories.progress_integrity_snapshot().heads
    )
    values.extend(
        ("trail_head", item[0], item[1:]) for item in repositories.trail_integrity_snapshot().heads
    )
    values.extend(
        ("rule_head", item[0], item[1:]) for item in repositories.rule_integrity_snapshot().heads
    )
    values.extend(
        ("primitive_head", item[0], item[1:])
        for item in repositories.representation_integrity_snapshot().heads
    )
    values.extend(
        ("hypothesis_head", item[0], item[1:])
        for item in repositories.hypothesis_integrity_snapshot().heads
    )
    values.extend(
        ("harness_campaign_head", item[0], item[1:])
        for item in HarnessCampaignHeadRepository(connection).list_all()
    )
    return tuple(
        sorted(
            (
                WorkspaceProjectionExpectation(
                    projection_kind=kind,
                    stable_identity=identity,
                    content_hash=sha256_hex(
                        canonical_json_bytes(
                            _json_value({"kind": kind, "identity": identity, "value": value})
                        )
                    ),
                )
                for kind, identity, value in values
            ),
            key=lambda item: (item.projection_kind, item.stable_identity),
        )
    )


def _prepare_target_policy(
    workspace: WorkspaceExport,
    policies: Mapping[str, PolicySnapshot],
    *,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
) -> None:
    with uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        active = repositories.policies.get_active()
        if active is None:
            if repositories.has_durable_state():
                raise WorkspaceImportError("target durable state has no active policy")
            repositories.policies.add_and_activate(
                policies[workspace.bootstrap_policy_hash],
                clock.now(),
            )
        elif repositories.policies.get(workspace.bootstrap_policy_hash) is None:
            raise WorkspaceImportError("target does not contain the export bootstrap policy")


def _transfer_artifacts(
    references: tuple[WorkspaceArtifactReference, ...],
    *,
    source: ArtifactStore,
    target: ArtifactStore,
) -> None:
    for reference in references:
        artifact = reference.to_artifact()
        stored = target.put(source.read(artifact), reference.media_type)
        if stored != artifact:
            raise WorkspaceImportError("artifact transfer changed its canonical reference")


def _proposal_attempt(record: WorkspaceRecord) -> ProposalAttempt:
    proposal = record.proposal
    if isinstance(proposal, InvalidProposal):
        if proposal.proposer is None or proposal.attempted_proposal_kind is None:
            raise WorkspaceImportError("invalid proposal lacks a stable import intent")
        proposer = proposal.proposer
        proposal_kind: ProposalKind = proposal.attempted_proposal_kind
    else:
        proposer = proposal.proposer
        proposal_kind = proposal.proposal_type
    return ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key=proposal.idempotency_key,
        proposer=proposer,
        proposal_kind=proposal_kind,
        intent_digest=record.proposal_hash,
    )


def _is_identity_conflict(decision: TransactionDecision) -> bool:
    return (
        not decision.accepted
        and bool(decision.reasons)
        and decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    )


def _require_protected_safe(value: object) -> None:
    serialized = canonical_json_bytes(value).decode("utf-8").lower()
    prohibited = (
        "protected_expected_output",
        "protected_store",
        "protected_database",
        "executable_configuration",
    )
    if any(term in serialized for term in prohibited):
        raise ValueError("workspace export contains a prohibited protected or executable field")
    if _contains_live_path(value):
        raise ValueError("workspace export contains a prohibited live path")


def _contains_live_path(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_live_path(item) for item in value.values())
    if isinstance(value, (tuple, list, frozenset)):
        return any(_contains_live_path(item) for item in value)
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    lowered = candidate.lower()
    return (
        lowered.startswith(("file://", "sqlite:///"))
        or PureWindowsPath(candidate).is_absolute()
        or PurePosixPath(candidate).is_absolute()
    )


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _active_connection(connection: Connection | None) -> Connection:
    if connection is None or connection.closed:
        raise RuntimeError("unit of work is not active")
    return connection


__all__ = [
    "WorkspaceArtifactReference",
    "WorkspaceExport",
    "WorkspaceImportConflict",
    "WorkspaceImportError",
    "WorkspaceImportResult",
    "WorkspacePolicy",
    "WorkspaceProjectionExpectation",
    "WorkspaceRecord",
    "export_workspace",
    "import_workspace",
]
