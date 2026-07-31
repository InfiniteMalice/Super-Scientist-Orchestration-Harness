from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Literal, Protocol, cast

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
from super_scientist.providers.storage.artifacts import ArtifactStore, FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import HarnessCampaignHeadRepository
from super_scientist.providers.storage.repositories import RepositorySet, StoredTransaction

type UnitOfWorkFactory = Callable[[], DatabaseUnitOfWork]


class Clock(Protocol):
    def now(self) -> UtcTimestamp: ...


class _BorrowedUnitOfWork:
    """Expose one outer transaction to coordinator calls without committing it."""

    def __init__(self, owner: DatabaseUnitOfWork) -> None:
        self._owner = owner
        self.connection = owner.connection

    def __enter__(self) -> _BorrowedUnitOfWork:
        _active_connection(self.connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback

    def repositories(self) -> RepositorySet:
        return self._owner.repositories()


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
        return _export_workspace_snapshot(
            repositories,
            _active_connection(unit_of_work.connection),
            artifact_store,
        )


def _export_workspace_snapshot(
    repositories: RepositorySet,
    connection: Connection,
    artifact_store: ArtifactStore,
) -> WorkspaceExport:
    require_workspace_integrity(repositories, artifact_store)
    active_policy = repositories.policies.get_active()
    if active_policy is None:
        raise ValueError("workspace export requires an active policy")
    transactions = repositories.transactions.list_all()
    events = repositories.audit.list_all()
    governing_hashes = _governing_policy_hashes(events)
    replay_orders = _transaction_replay_orders(events)
    records = tuple(
        sorted(
            (
                WorkspaceRecord(
                    stable_identity=transaction.proposal.proposal_id,
                    replay_order=_replay_order(transaction, replay_orders),
                    governing_policy_hash=_governing_hash(transaction, governing_hashes),
                    proposal_hash=_proposal_hash(transaction.proposal),
                    proposal=transaction.proposal,
                    expected_decision=transaction.decision.model_copy(update={"replayed": False}),
                )
                for transaction in transactions
            ),
            key=lambda item: item.stable_identity,
        )
    )
    snapshots = repositories.policies.list_all()
    policies = tuple(
        WorkspacePolicy(policy_hash=snapshot.policy_hash, snapshot=snapshot)
        for snapshot in sorted(snapshots, key=lambda item: item.policy_hash)
    )
    first_identity = min(replay_orders, key=replay_orders.__getitem__) if replay_orders else None
    bootstrap_hash = (
        governing_hashes[first_identity]
        if first_identity is not None
        else active_policy.policy_hash
    )
    projections = _projection_expectations(repositories, connection)
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
    target_before = _target_snapshot(
        canonical,
        uow_factory=uow_factory,
        artifact_store=artifact_store,
    )
    preflight = _preflight_import(
        canonical,
        target_before=target_before,
        target_artifact_store=artifact_store,
        source_artifact_store=source_artifact_store,
        clock=clock,
    )
    if preflight.conflicts:
        return _commit_conflicts(
            canonical,
            expected=preflight.conflicts,
            uow_factory=uow_factory,
            artifact_store=artifact_store,
            clock=clock,
        )
    return _commit_workspace(
        canonical,
        uow_factory=uow_factory,
        artifact_store=artifact_store,
        source_artifact_store=source_artifact_store,
        clock=clock,
        expected=preflight,
    )


def _target_snapshot(
    workspace: WorkspaceExport,
    *,
    uow_factory: UnitOfWorkFactory,
    artifact_store: ArtifactStore,
) -> WorkspaceExport | None:
    with uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        active = repositories.policies.get_active()
        if active is None:
            if repositories.has_durable_state():
                raise WorkspaceImportError("target durable state has no active policy")
            return None
        if repositories.policies.get(workspace.bootstrap_policy_hash) is None:
            raise WorkspaceImportError("target does not contain the export bootstrap policy")
        return _export_workspace_snapshot(
            repositories,
            _active_connection(unit_of_work.connection),
            artifact_store,
        )


def _preflight_import(
    workspace: WorkspaceExport,
    *,
    target_before: WorkspaceExport | None,
    target_artifact_store: ArtifactStore,
    source_artifact_store: ArtifactStore,
    clock: Clock,
) -> WorkspaceImportResult:
    with TemporaryDirectory(prefix="ssoh-workspace-preflight-") as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'preflight.db').as_posix()}"
        upgrade_database(database_url)
        engine = create_database_engine(database_url)
        staged_artifacts = FileArtifactStore(root / "artifacts")

        def staged_uow_factory() -> DatabaseUnitOfWork:
            return DatabaseUnitOfWork(engine)

        try:
            if target_before is not None:
                cloned = _commit_workspace(
                    target_before,
                    uow_factory=staged_uow_factory,
                    artifact_store=staged_artifacts,
                    source_artifact_store=target_artifact_store,
                    clock=clock,
                )
                if not cloned.projections_verified:
                    raise WorkspaceImportError("target preflight clone was not reconstructed")
            return _commit_workspace(
                workspace,
                uow_factory=staged_uow_factory,
                artifact_store=staged_artifacts,
                source_artifact_store=source_artifact_store,
                clock=clock,
            )
        finally:
            engine.dispose()


def _commit_workspace(
    workspace: WorkspaceExport,
    *,
    uow_factory: UnitOfWorkFactory,
    artifact_store: ArtifactStore,
    source_artifact_store: ArtifactStore,
    clock: Clock,
    expected: WorkspaceImportResult | None = None,
) -> WorkspaceImportResult:
    policy_by_hash = {item.policy_hash: item.snapshot for item in workspace.policies}
    created_artifacts = _transfer_artifacts_for_commit(
        workspace.artifacts,
        source=source_artifact_store,
        target=artifact_store,
    )
    committed: WorkspaceImportResult
    try:
        with uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            _prepare_target_policy_in_repositories(
                workspace,
                policy_by_hash,
                repositories=repositories,
                clock=clock,
            )
            result = _replay_records(
                workspace,
                policy_by_hash,
                unit_of_work=unit_of_work,
                artifact_store=artifact_store,
                clock=clock,
            )
            if result.conflicts:
                committed = result
            else:
                rebuilt = _export_workspace_snapshot(
                    repositories,
                    _active_connection(unit_of_work.connection),
                    artifact_store,
                )
                if rebuilt != workspace:
                    raise WorkspaceImportError(
                        "rebuilt workspace export does not match canonical bundle"
                    )
                committed = result.model_copy(update={"projections_verified": True})
            if expected is not None and committed != expected:
                raise WorkspaceImportError("committed import changed after successful preflight")
    except BaseException:
        _remove_created_artifacts(created_artifacts)
        raise
    return committed


def _replay_records(
    workspace: WorkspaceExport,
    policies: Mapping[str, PolicySnapshot],
    *,
    unit_of_work: DatabaseUnitOfWork,
    artifact_store: ArtifactStore,
    clock: Clock,
) -> WorkspaceImportResult:
    borrowed = _BorrowedUnitOfWork(unit_of_work)

    def borrowed_uow_factory() -> DatabaseUnitOfWork:
        return cast(DatabaseUnitOfWork, borrowed)

    imported = 0
    replayed = 0
    conflicts: list[WorkspaceImportConflict] = []
    for record in sorted(workspace.records, key=lambda item: item.replay_order):
        coordinator = TransactionCoordinator(
            borrowed_uow_factory,
            policies[record.governing_policy_hash],
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
        elif decision == record.expected_decision:
            imported += 1
        elif (conflict_code := _identity_conflict_code(decision)) is not None:
            conflicts.append(
                WorkspaceImportConflict(
                    stable_identity=record.stable_identity,
                    code=conflict_code,
                )
            )
        else:
            raise WorkspaceImportError(f"imported decision changed for {record.stable_identity}")
    return WorkspaceImportResult(
        imported=imported,
        replayed=replayed,
        conflicts=tuple(conflicts),
        projections_verified=False,
    )


def _commit_conflicts(
    workspace: WorkspaceExport,
    *,
    expected: tuple[WorkspaceImportConflict, ...],
    uow_factory: UnitOfWorkFactory,
    artifact_store: ArtifactStore,
    clock: Clock,
) -> WorkspaceImportResult:
    expected_by_identity = {item.stable_identity: item.code for item in expected}
    actual: list[WorkspaceImportConflict] = []
    policies = {item.policy_hash: item.snapshot for item in workspace.policies}
    with uow_factory() as unit_of_work:
        borrowed = _BorrowedUnitOfWork(unit_of_work)

        def borrowed_uow_factory() -> DatabaseUnitOfWork:
            return cast(DatabaseUnitOfWork, borrowed)

        for record in sorted(workspace.records, key=lambda item: item.replay_order):
            expected_code = expected_by_identity.get(record.stable_identity)
            if expected_code is None:
                continue
            decision = TransactionCoordinator(
                borrowed_uow_factory,
                policies[record.governing_policy_hash],
                clock,
                artifact_store,
            ).submit_intent(
                _proposal_attempt(record),
                _constant_proposal_factory(record.proposal),
            )
            actual_code = _identity_conflict_code(decision)
            if actual_code is not expected_code:
                raise WorkspaceImportError(
                    f"conflict decision changed for {record.stable_identity}"
                )
            actual.append(
                WorkspaceImportConflict(
                    stable_identity=record.stable_identity,
                    code=actual_code,
                )
            )
        if tuple(actual) != expected:
            raise WorkspaceImportError("committed conflicts changed after successful preflight")
    return WorkspaceImportResult(
        imported=0,
        replayed=0,
        conflicts=tuple(actual),
        projections_verified=False,
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


def _transaction_replay_orders(events: tuple[AuditEvent, ...]) -> dict[str, int]:
    replay_orders: dict[str, int] = {}
    for event in events:
        payload = json_compatible_payload(event.payload)
        proposal = payload.get("proposal")
        if not isinstance(proposal, Mapping) or not payload.get("transaction_persisted"):
            continue
        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str):
            raise ValueError("audit event lacks a stable proposal identity")
        if proposal_id in replay_orders:
            raise ValueError("transaction has more than one persisted audit event")
        replay_orders[proposal_id] = len(replay_orders)
    return replay_orders


def _governing_hash(
    transaction: StoredTransaction,
    governing_hashes: Mapping[str, str],
) -> str:
    try:
        return governing_hashes[transaction.proposal.proposal_id]
    except KeyError as error:
        raise ValueError("transaction lacks a persisted governing audit event") from error


def _replay_order(
    transaction: StoredTransaction,
    replay_orders: Mapping[str, int],
) -> int:
    try:
        return replay_orders[transaction.proposal.proposal_id]
    except KeyError as error:
        raise ValueError("transaction lacks a persisted audit replay order") from error


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


def _prepare_target_policy_in_repositories(
    workspace: WorkspaceExport,
    policies: Mapping[str, PolicySnapshot],
    *,
    repositories: RepositorySet,
    clock: Clock,
) -> None:
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


def _transfer_artifacts_for_commit(
    references: tuple[WorkspaceArtifactReference, ...],
    *,
    source: ArtifactStore,
    target: ArtifactStore,
) -> tuple[Path, ...]:
    if not references:
        return ()
    if not isinstance(target, FileArtifactStore):
        raise WorkspaceImportError(
            "artifact transfer requires a rollback-capable file artifact store"
        )
    created: list[Path] = []
    try:
        for reference in references:
            artifact = reference.to_artifact()
            path = target.resolve(artifact)
            existed = path.is_file()
            try:
                stored = target.put(source.read(artifact), reference.media_type)
            finally:
                if not existed and path.is_file():
                    created.append(path)
            if stored != artifact:
                raise WorkspaceImportError("artifact transfer changed its canonical reference")
    except (OSError, ValueError) as error:
        _remove_created_artifacts(tuple(created))
        if isinstance(error, WorkspaceImportError):
            raise
        raise WorkspaceImportError(f"artifact transfer failed: {error}") from error
    return tuple(created)


def _remove_created_artifacts(paths: tuple[Path, ...]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)


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


def _identity_conflict_code(decision: TransactionDecision) -> RejectionCode | None:
    if decision.accepted or not decision.reasons:
        return None
    code = decision.reasons[0].code
    if code in {
        RejectionCode.IDEMPOTENCY_CONFLICT,
        RejectionCode.ENTITY_ALREADY_EXISTS,
    }:
        return code
    return None


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


def _contains_live_path(value: object, *, path_field: bool = False) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_live_path(item, path_field=_is_path_field(str(key)))
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list, frozenset)):
        return any(_contains_live_path(item, path_field=path_field) for item in value)
    if not path_field or not isinstance(value, str):
        return False
    candidate = value.strip()
    lowered = candidate.lower()
    return (
        lowered.startswith(("file://", "sqlite:///"))
        or PureWindowsPath(candidate).is_absolute()
        or PurePosixPath(candidate).is_absolute()
    )


def _is_path_field(field_name: str) -> bool:
    normalized = field_name.casefold()
    return (
        normalized == "path"
        or normalized.endswith(("_path", "_file", "_directory", "_root"))
        or normalized in {"repository", "manifest", "output_dir"}
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
