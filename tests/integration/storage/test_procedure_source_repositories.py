from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.procedures.models import (
    AcceptedSourceReceiptRef,
    ProcedureEvidenceSourceKind,
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
)
from super_scientist.providers.storage.repositories import RepositorySet
from tests.unit.collaboration.conftest import actor, profile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64


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


def _persist_accepted(repositories: RepositorySet, proposal, occurred_at: datetime):
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repositories.transactions.add(proposal, decision, occurred_at)
    stored = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
    assert stored is not None
    event = append_event(
        repositories.audit.last(),
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": POLICY_HASH,
            "stored_policy_hash": POLICY_HASH,
            "transaction_persisted": True,
        },
        occurred_at,
    )
    repositories.audit.add(event)
    return stored, event


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
