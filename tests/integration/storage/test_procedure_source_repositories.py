from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.procedures.models import (
    AcceptedSourceReceiptRef,
    ArtifactCatalogEntry,
    CatalogFactStatus,
    ProcedureEvidenceSourceKind,
    RegisteredTool,
    RegisteredValidator,
    catalog_snapshot_content_hash,
)
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    RecordCapabilityProfile,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.cognitive_records import CapabilityProfileRepository
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.procedure_sources import (
    AcceptedProcedureSourceReceiptReader,
    ArtifactCatalogSnapshotRepository,
    ProcedureSourceBinding,
    ProcedureSourceSnapshot,
    ProcedureSourceSnapshotRepository,
    ToolCatalogSnapshotRepository,
    ValidatorCatalogSnapshotRepository,
)
from super_scientist.providers.storage.repositories import RepositorySet, StorageIntegrityError
from tests.unit.collaboration.conftest import actor, profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64
MAX_SOURCE_BYTES = 64 * 1024 * 1024


@pytest.mark.integration
def test_procedure_receipt_reader_returns_none_for_an_unknown_receipt(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'sources.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            reader = AcceptedProcedureSourceReceiptReader(connection)
            assert reader.get("missing-receipt") is None
    finally:
        engine.dispose()


def _persist_accepted(
    repositories: RepositorySet,
    proposal,
    occurred_at: datetime,
    *,
    audit_policy_fields: dict[str, object] | None = None,
):
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repositories.transactions.add(proposal, decision, occurred_at)
    stored = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
    assert stored is not None
    policy_fields = (
        {"policy_hash": POLICY_HASH, "stored_policy_hash": POLICY_HASH}
        if audit_policy_fields is None
        else audit_policy_fields
    )
    event = append_event(
        repositories.audit.last(),
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "transaction_persisted": True,
            **policy_fields,
        },
        occurred_at,
    )
    repositories.audit.add(event)
    return stored, event


@pytest.mark.integration
@pytest.mark.parametrize(
    "audit_policy_fields",
    (
        {"policy_hash": POLICY_HASH},
        {"policy_hash": POLICY_HASH, "stored_policy_hash": ""},
        {"policy_hash": POLICY_HASH, "stored_policy_hash": "e" * 64},
    ),
    ids=("missing-stored-policy", "empty-stored-policy", "divergent-stored-policy"),
)
def test_source_snapshot_requires_exact_stored_policy_audit_provenance(
    tmp_path,
    audit_policy_fields: dict[str, object],
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'snapshot-policy.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "snapshot-policy-artifacts")
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            snapshot = ProcedureSourceSnapshot(
                snapshot_family_id="workspace-sources",
                snapshot_id="snapshot-policy",
                source_bindings=(),
            )
            artifact = artifacts.put(
                canonical_json_bytes(snapshot.model_dump(mode="json")),
                "application/json",
            )
            evidence = _evidence(
                evidence_id=snapshot.snapshot_id,
                artifact=artifact,
                retrieved_at=NOW,
            )
            proposal = AddEvidence(
                proposal_id="proposal-snapshot-policy",
                idempotency_key="proposal-snapshot-policy",
                proposer=actor("coordinator"),
                evidence=evidence,
            )
            repositories.evidence.add(evidence)
            _persist_accepted(
                repositories,
                proposal,
                NOW,
                audit_policy_fields=audit_policy_fields,
            )
            snapshots = ProcedureSourceSnapshotRepository(connection, artifacts)

            assert snapshots.resolve_exact(snapshot.snapshot_id, artifact.sha256) is None
            assert snapshots.is_current(snapshot.snapshot_id, artifact.sha256) is False
    finally:
        engine.dispose()


@pytest.mark.integration
def test_capability_source_requires_exact_transaction_audit_and_record(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'capability-source.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            repositories = RepositorySet(connection)
            record = profile("peer-a")
            proposal = RecordCapabilityProfile(
                proposal_id="proposal-profile-a",
                idempotency_key="proposal-profile-a",
                proposer=actor("coordinator"),
                profile=record,
            )
            stored, event = _persist_accepted(repositories, proposal, NOW)
            repository = CapabilityProfileRepository(connection)
            repository.add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=POLICY_HASH,
            )
            reference = AcceptedSourceReceiptRef.build(
                receipt_id="receipt-profile-a",
                source_kind=ProcedureEvidenceSourceKind.CAPABILITY_PROFILE,
                source_record_id=record.profile_id,
                source_schema_version=record.schema_version,
                source_content_hash=record.content_hash,
                source_snapshot_id="snapshot-a",
                source_snapshot_hash="a" * 64,
                proposal_id=proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=event.event_id,
                audit_event_hash=event.event_hash,
            )

            assert AcceptedProcedureSourceReceiptReader(connection).resolve(reference) is not None
            assert repository.resolve(reference) == record
            forged = reference.model_copy(update={"audit_event_hash": "b" * 64})
            assert AcceptedProcedureSourceReceiptReader(connection).resolve(forged) is None
            assert repository.resolve(forged) is None
            transaction.rollback()
    finally:
        engine.dispose()


def _evidence(
    *,
    evidence_id: str,
    artifact,
    retrieved_at: datetime,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="procedure-source",
        source_locator=f"fixture:{evidence_id}",
        retrieved_at=retrieved_at,
        artifact=artifact,
        provenance={"fixture": "task-10"},
        ingestion_actor_id="coordinator",
        verification_state=VerificationState.HASH_VERIFIED,
    )


@pytest.mark.integration
def test_catalog_and_source_snapshot_readers_require_canonical_artifacts_and_freshness(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'catalog-source.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            catalog_bytes = canonical_json_bytes(
                {"catalog_kind": "ARTIFACT_CATALOG", "entries": (), "complete": True}
            )
            catalog_artifact = artifacts.put(catalog_bytes, "application/json")
            catalog_evidence = _evidence(
                evidence_id="artifact-catalog-a",
                artifact=catalog_artifact,
                retrieved_at=NOW,
            )
            catalog_proposal = AddEvidence(
                proposal_id="proposal-artifact-catalog",
                idempotency_key="proposal-artifact-catalog",
                proposer=actor("coordinator"),
                evidence=catalog_evidence,
            )
            repositories.evidence.add(catalog_evidence)
            stored, event = _persist_accepted(repositories, catalog_proposal, NOW)
            catalog_hash = catalog_snapshot_content_hash("ARTIFACT_CATALOG", (), True)
            assert catalog_hash == catalog_artifact.sha256
            catalog_reference = AcceptedSourceReceiptRef.build(
                receipt_id="receipt-artifact-catalog",
                source_kind=ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
                source_record_id=catalog_evidence.evidence_id,
                source_schema_version=1,
                source_content_hash=catalog_hash,
                source_snapshot_id="snapshot-a",
                source_snapshot_hash="a" * 64,
                proposal_id=catalog_proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=event.event_id,
                audit_event_hash=event.event_hash,
            )
            resolved_catalog = ArtifactCatalogSnapshotRepository(
                connection,
                artifacts,
            ).resolve(catalog_reference)
            assert resolved_catalog is not None
            assert resolved_catalog.entries == ()
            assert resolved_catalog.complete is True

            first_snapshot = ProcedureSourceSnapshot(
                snapshot_family_id="workspace-sources",
                snapshot_id="snapshot-a",
                source_bindings=(
                    ProcedureSourceBinding(
                        source_record_id=catalog_evidence.evidence_id,
                        source_content_hash=catalog_hash,
                    ),
                ),
            )
            first_bytes = canonical_json_bytes(first_snapshot.model_dump(mode="json"))
            first_artifact = artifacts.put(first_bytes, "application/json")
            first_evidence = _evidence(
                evidence_id=first_snapshot.snapshot_id,
                artifact=first_artifact,
                retrieved_at=NOW + timedelta(seconds=1),
            )
            first_proposal = AddEvidence(
                proposal_id="proposal-snapshot-a",
                idempotency_key="proposal-snapshot-a",
                proposer=actor("coordinator"),
                evidence=first_evidence,
            )
            repositories.evidence.add(first_evidence)
            _persist_accepted(repositories, first_proposal, NOW + timedelta(seconds=1))

            snapshots = ProcedureSourceSnapshotRepository(connection, artifacts)
            assert snapshots.resolve_exact("snapshot-a", first_artifact.sha256) == first_snapshot
            assert snapshots.is_current("snapshot-a", first_artifact.sha256)

            second_snapshot = ProcedureSourceSnapshot(
                snapshot_family_id="workspace-sources",
                snapshot_id="snapshot-b",
                source_bindings=first_snapshot.source_bindings,
            )
            second_bytes = canonical_json_bytes(second_snapshot.model_dump(mode="json"))
            second_artifact = artifacts.put(second_bytes, "application/json")
            second_evidence = _evidence(
                evidence_id=second_snapshot.snapshot_id,
                artifact=second_artifact,
                retrieved_at=NOW + timedelta(seconds=2),
            )
            second_proposal = AddEvidence(
                proposal_id="proposal-snapshot-b",
                idempotency_key="proposal-snapshot-b",
                proposer=actor("coordinator"),
                evidence=second_evidence,
            )
            repositories.evidence.add(second_evidence)
            _persist_accepted(repositories, second_proposal, NOW + timedelta(seconds=2))

            assert not snapshots.is_current("snapshot-a", first_artifact.sha256)
            assert snapshots.is_current("snapshot-b", second_artifact.sha256)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source_kind", "entry", "repository_type"),
    (
        (
            ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
            ArtifactCatalogEntry(
                artifact_id="artifact-a",
                artifact=ArtifactRef(
                    sha256="c" * 64,
                    size_bytes=17,
                    media_type="application/octet-stream",
                    relative_path="sha256/cc/" + "c" * 64,
                ),
                availability=CatalogFactStatus.PRESENT,
            ),
            ArtifactCatalogSnapshotRepository,
        ),
        (
            ProcedureEvidenceSourceKind.TOOL_CATALOG,
            RegisteredTool(
                tool=actor("tool-a", ActorKind.TOOL),
                availability=CatalogFactStatus.PRESENT,
                authorization=CatalogFactStatus.PRESENT,
            ),
            ToolCatalogSnapshotRepository,
        ),
        (
            ProcedureEvidenceSourceKind.VALIDATOR_CATALOG,
            RegisteredValidator(
                validator=actor("validator-a"),
                validator_version="validator-v1",
                registration=CatalogFactStatus.PRESENT,
            ),
            ValidatorCatalogSnapshotRepository,
        ),
    ),
)
def test_nonempty_catalog_readers_decode_strict_canonical_json_mode(
    tmp_path,
    source_kind: ProcedureEvidenceSourceKind,
    entry: Any,
    repository_type: type[Any],
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / source_kind.value).as_posix()}.db"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / f"artifacts-{source_kind.value}")
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            entries = (entry,)
            artifact_bytes = canonical_json_bytes(
                {
                    "catalog_kind": source_kind.value,
                    "entries": tuple(item.model_dump(mode="json") for item in entries),
                    "complete": True,
                }
            )
            artifact = artifacts.put(artifact_bytes, "application/json")
            evidence = _evidence(
                evidence_id=f"source-{source_kind.value.lower()}",
                artifact=artifact,
                retrieved_at=NOW,
            )
            proposal = AddEvidence(
                proposal_id=f"proposal-{source_kind.value.lower()}",
                idempotency_key=f"proposal-{source_kind.value.lower()}",
                proposer=actor("coordinator"),
                evidence=evidence,
            )
            repositories.evidence.add(evidence)
            stored, event = _persist_accepted(repositories, proposal, NOW)
            reference = AcceptedSourceReceiptRef.build(
                receipt_id=f"receipt-{source_kind.value.lower()}",
                source_kind=source_kind,
                source_record_id=evidence.evidence_id,
                source_schema_version=1,
                source_content_hash=artifact.sha256,
                source_snapshot_id="snapshot-a",
                source_snapshot_hash="a" * 64,
                proposal_id=proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=event.event_id,
                audit_event_hash=event.event_hash,
            )

            resolved = repository_type(connection, artifacts).resolve(reference)
            assert resolved is not None
            assert resolved.entries == entries
    finally:
        engine.dispose()


@pytest.mark.integration
def test_source_reader_detaches_corrupt_transaction_sentinel(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'source-corrupt.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "source-corrupt-artifacts")
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            artifact_bytes = canonical_json_bytes(
                {
                    "catalog_kind": ProcedureEvidenceSourceKind.ARTIFACT_CATALOG.value,
                    "entries": (),
                    "complete": True,
                }
            )
            artifact = artifacts.put(artifact_bytes, "application/json")
            evidence = _evidence(
                evidence_id="source-corrupt",
                artifact=artifact,
                retrieved_at=NOW,
            )
            proposal = AddEvidence(
                proposal_id="proposal-source-corrupt",
                idempotency_key="proposal-source-corrupt",
                proposer=actor("coordinator"),
                evidence=evidence,
            )
            repositories.evidence.add(evidence)
            stored, event = _persist_accepted(repositories, proposal, NOW)
            reference = AcceptedSourceReceiptRef.build(
                receipt_id="receipt-source-corrupt",
                source_kind=ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
                source_record_id=evidence.evidence_id,
                source_schema_version=1,
                source_content_hash=artifact.sha256,
                source_snapshot_id="snapshot-a",
                source_snapshot_hash="a" * 64,
                proposal_id=proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=event.event_id,
                audit_event_hash=event.event_hash,
            )
            proposal_json = connection.execute(
                text("SELECT proposal_json FROM transactions WHERE proposal_id = :proposal_id"),
                {"proposal_id": proposal.proposal_id},
            ).scalar_one()
            connection.exec_driver_sql("DROP TRIGGER transactions_no_update")
            connection.execute(
                text(
                    "UPDATE transactions SET proposal_json = :proposal_json "
                    "WHERE proposal_id = :proposal_id"
                ),
                {
                    "proposal_json": " " + proposal_json,
                    "proposal_id": proposal.proposal_id,
                },
            )
            legacy = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
            assert legacy is not None
            assert legacy.proposal == proposal
            corrupt = json.loads(proposal_json)
            sentinel = "SECRET-SOURCE-SENTINEL"
            corrupt["unknown"] = sentinel
            connection.execute(
                text(
                    "UPDATE transactions SET proposal_json = :proposal_json "
                    "WHERE proposal_id = :proposal_id"
                ),
                {
                    "proposal_json": canonical_json_bytes(corrupt).decode(),
                    "proposal_id": proposal.proposal_id,
                },
            )

            with pytest.raises(StorageIntegrityError, match="invalid transaction record") as caught:
                ArtifactCatalogSnapshotRepository(connection, artifacts).resolve(reference)
            assert caught.value.__cause__ is None
            assert caught.value.__context__ is None
            assert sentinel not in str(caught.value)
    finally:
        engine.dispose()


class _FailOnReadArtifactStore:
    def __init__(self) -> None:
        self.read_called = False

    def read(self, ref: ArtifactRef) -> bytes:
        self.read_called = True
        raise AssertionError("oversized artifact must be rejected before read")


@pytest.mark.integration
@pytest.mark.parametrize("source_family", ("catalog", "snapshot"))
def test_oversized_trusted_artifact_ref_is_rejected_before_store_read(
    tmp_path,
    source_family: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / source_family).as_posix()}.db"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    store = _FailOnReadArtifactStore()
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            evidence_id = "oversized-catalog" if source_family == "catalog" else "snapshot-a"
            artifact = ArtifactRef(
                sha256="a" * 64,
                size_bytes=MAX_SOURCE_BYTES + 1,
                media_type="application/json",
                relative_path="sha256/aa/" + "a" * 64,
            )
            evidence = _evidence(evidence_id=evidence_id, artifact=artifact, retrieved_at=NOW)
            proposal = AddEvidence(
                proposal_id=f"proposal-{source_family}",
                idempotency_key=f"proposal-{source_family}",
                proposer=actor("coordinator"),
                evidence=evidence,
            )
            repositories.evidence.add(evidence)
            stored, event = _persist_accepted(repositories, proposal, NOW)
            if source_family == "catalog":
                reference = AcceptedSourceReceiptRef.build(
                    receipt_id="receipt-oversized-catalog",
                    source_kind=ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
                    source_record_id=evidence.evidence_id,
                    source_schema_version=1,
                    source_content_hash=artifact.sha256,
                    source_snapshot_id="snapshot-a",
                    source_snapshot_hash="b" * 64,
                    proposal_id=proposal.proposal_id,
                    proposal_hash=stored.proposal_hash,
                    audit_event_id=event.event_id,
                    audit_event_hash=event.event_hash,
                )
                assert (
                    ArtifactCatalogSnapshotRepository(connection, store).resolve(reference) is None
                )
            else:
                assert (
                    ProcedureSourceSnapshotRepository(connection, store).resolve_exact(
                        evidence_id,
                        artifact.sha256,
                    )
                    is None
                )
            assert store.read_called is False
    finally:
        engine.dispose()


class _TamperingArtifactStore:
    def __init__(self, delegate: FileArtifactStore, target_hash: str, replacement: bytes) -> None:
        self._delegate = delegate
        self._target_hash = target_hash
        self._replacement = replacement

    def read(self, ref: ArtifactRef) -> bytes:
        if ref.sha256 == self._target_hash:
            return self._replacement
        return self._delegate.read(ref)


@pytest.mark.integration
def test_source_snapshot_rejects_valid_canonical_bytes_with_wrong_actual_hash(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'snapshot-hash.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "snapshot-hash-artifacts")
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            snapshot = ProcedureSourceSnapshot(
                snapshot_family_id="workspace-sources",
                snapshot_id="snapshot-a",
                source_bindings=(),
            )
            artifact_bytes = canonical_json_bytes(snapshot.model_dump(mode="json"))
            artifact = artifacts.put(artifact_bytes, "application/json")
            evidence = _evidence(
                evidence_id=snapshot.snapshot_id, artifact=artifact, retrieved_at=NOW
            )
            proposal = AddEvidence(
                proposal_id="proposal-snapshot-a",
                idempotency_key="proposal-snapshot-a",
                proposer=actor("coordinator"),
                evidence=evidence,
            )
            repositories.evidence.add(evidence)
            _persist_accepted(repositories, proposal, NOW)
            replacement = ProcedureSourceSnapshot(
                snapshot_family_id=snapshot.snapshot_family_id,
                snapshot_id=snapshot.snapshot_id,
                source_bindings=(
                    ProcedureSourceBinding(
                        source_record_id="forged-source",
                        source_content_hash="b" * 64,
                    ),
                ),
            )
            tampering_store = _TamperingArtifactStore(
                artifacts,
                artifact.sha256,
                canonical_json_bytes(replacement.model_dump(mode="json")),
            )

            assert (
                ProcedureSourceSnapshotRepository(
                    connection,
                    tampering_store,
                ).resolve_exact(snapshot.snapshot_id, artifact.sha256)
                is None
            )
    finally:
        engine.dispose()
