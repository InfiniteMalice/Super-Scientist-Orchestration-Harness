from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import (
    Column,
    Connection,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    Proposal,
    ProposeClaim,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.append_only import (
    OrderedReferenceBinding,
    ReferencedAppendOnlyRecordRepository,
    StrictFrozenStorageRecord,
)
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import RepositorySet, StorageIntegrityError
from super_scientist.providers.storage.schema import (
    audit_events,
    claim_heads,
    claim_versions,
    evidence_records,
    governance_policies,
    governance_state,
    transactions,
)

PROPOSAL_ADAPTER = TypeAdapter(Proposal)
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

_fixture_metadata = MetaData()
fixture_records = Table(
    "fixture_records",
    _fixture_metadata,
    Column("record_id", String(128), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
)
fixture_reference_targets = Table(
    "fixture_reference_targets",
    _fixture_metadata,
    Column("reference_id", String(128), primary_key=True),
)
fixture_record_references = Table(
    "fixture_record_references",
    _fixture_metadata,
    Column("record_id", ForeignKey("fixture_records.record_id"), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column(
        "reference_id",
        ForeignKey("fixture_reference_targets.reference_id"),
        nullable=False,
    ),
)


class FixtureRecord(StrictFrozenStorageRecord):
    record_id: str
    reference_ids: tuple[str, ...]


class FixtureRepository(ReferencedAppendOnlyRecordRepository[FixtureRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=fixture_records,
            model_type=FixtureRecord,
            identifier_field="record_id",
            reference_bindings=(
                OrderedReferenceBinding(
                    table=fixture_record_references,
                    owner_column="record_id",
                    record_field="reference_ids",
                    reference_column="reference_id",
                ),
            ),
        )

    def insert_raw_payload(self, payload: str) -> None:
        self._connection.execute(
            insert(fixture_records).values(
                record_id="record-1",
                record_json=payload,
                content_hash=sha256_hex(payload.encode("utf-8")),
                created_at=NOW.isoformat(),
            )
        )


@dataclass(frozen=True)
class StorageRuntime:
    engine: Engine

    def uow(self) -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(self.engine)

    def referenced_repository(self, connection: Connection) -> FixtureRepository:
        return FixtureRepository(connection)

    def invalid_reference_record(self) -> FixtureRecord:
        return FixtureRecord(record_id="record-1", reference_ids=("missing-reference",))

    def count(self, table_name: str) -> int:
        table = {
            "fixture_records": fixture_records,
            "fixture_record_references": fixture_record_references,
        }[table_name]
        with self.engine.begin() as connection:
            return connection.execute(select(func.count()).select_from(table)).scalar_one()


@pytest.fixture
def storage_runtime(tmp_path: Path) -> Iterator[StorageRuntime]:
    url = _database_url(tmp_path / "append-only.db")
    upgrade_database(url)
    engine = create_database_engine(url)
    _fixture_metadata.create_all(engine)
    yield StorageRuntime(engine)
    engine.dispose()


@pytest.fixture(name="runtime")
def runtime_fixture(storage_runtime: StorageRuntime) -> StorageRuntime:
    return storage_runtime


def test_append_only_repository_rejects_unknown_payload_field(
    storage_runtime: StorageRuntime,
) -> None:
    with storage_runtime.uow() as uow:
        assert uow.connection is not None
        repository = storage_runtime.referenced_repository(uow.connection)
        repository.insert_raw_payload('{"record_id":"record-1","unknown":true}')
        with pytest.raises(StorageIntegrityError, match="invalid record JSON"):
            repository.list_all()


def test_append_only_add_rolls_back_record_and_references(runtime: StorageRuntime) -> None:
    with pytest.raises(IntegrityError), runtime.uow() as uow:
        assert uow.connection is not None
        runtime.referenced_repository(uow.connection).add(
            "record-1",
            runtime.invalid_reference_record(),
            NOW,
        )
    assert runtime.count("fixture_records") == 0
    assert runtime.count("fixture_record_references") == 0


def _evidence_record(evidence_id: str, now: datetime = NOW) -> EvidenceRecord:
    span_text = "supporting fixture span"
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="document",
        source_locator=f"fixture://{evidence_id}",
        retrieved_at=now,
        artifact=ArtifactRef(
            sha256="b" * 64,
            size_bytes=len(span_text),
            media_type="text/plain",
            relative_path=f"sha256/bb/{'b' * 64}",
        ),
        extracted_span=EvidenceSpan(start=0, end=len(span_text), text=span_text),
        structured_observation={
            "measurements": [1, 2],
            "labels": {"alpha", "beta"},
            "tag_collision": {
                "__super_scientist_storage_type__": "user-value",
                "items": ["not", "an", "envelope"],
            },
        },
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )


@dataclass(frozen=True)
class RepositoryFixture:
    repositories: RepositorySet
    connection: Connection
    now: datetime = NOW

    def actor(self, actor_id: str = "actor-1") -> ActorIdentity:
        return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=self.now)

    def evidence_record(self, evidence_id: str) -> EvidenceRecord:
        return _evidence_record(evidence_id, self.now)

    def claim(
        self,
        claim_id: str,
        *,
        version: int,
        status: str,
    ) -> AtomicClaim:
        claim_status = ClaimStatus(status)
        evidence_links = (
            ()
            if claim_status in {ClaimStatus.PROPOSED, ClaimStatus.WITHDRAWN}
            else (EvidenceLink(evidence_id="ev-1", supporting_span="fixture span"),)
        )
        return AtomicClaim(
            claim_id=claim_id,
            version=version,
            proposition="The intervention changes the outcome.",
            scope="Controlled fixture setting",
            population_or_system="Fixture system",
            epistemic_modality="supports",
            status=claim_status,
            evidence_links=evidence_links,
            parent_version_id=None if version == 1 else f"{claim_id}:{version - 1}",
            created_at=self.now,
            created_by="actor-1",
        )

    def add_evidence_proposal(self, proposal_id: str, key: str) -> AddEvidence:
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor(),
            evidence=self.evidence_record("ev-1"),
        )

    def policy_snapshot(self) -> PolicySnapshot:
        policy = GovernancePolicy(required_claim_checks=("source_exists", "evidence_span_exists"))
        canonical_policy = policy.model_dump(mode="json")
        canonical_policy["human_approval_for"] = sorted(policy.human_approval_for)
        return PolicySnapshot(
            policy_hash=sha256_hex(canonical_json_bytes(canonical_policy)),
            policy=policy,
        )


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture
def repository_fixture(tmp_path: Path) -> Iterator[RepositoryFixture]:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)
    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.connection is not None
        yield RepositoryFixture(
            repositories=unit_of_work.repositories(),
            connection=unit_of_work.connection,
        )
    engine.dispose()


def test_evidence_repository_add_get_and_list_round_trip(
    repository_fixture: RepositoryFixture,
) -> None:
    record = repository_fixture.evidence_record("ev-1")

    repository_fixture.repositories.evidence.add(record)

    assert repository_fixture.repositories.evidence.get("ev-1") == record
    assert repository_fixture.repositories.evidence.list_all() == (record,)


def test_claim_repository_preserves_versions_and_moves_head(
    repository_fixture: RepositoryFixture,
) -> None:
    first = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    second = repository_fixture.claim("claim-1", version=2, status="EVIDENCE_LINKED")

    repository_fixture.repositories.claims.add_version(first)
    repository_fixture.repositories.claims.add_version(second)

    assert repository_fixture.repositories.claims.get_head("claim-1") == second
    assert repository_fixture.repositories.claims.get_head_required("claim-1") == second
    assert repository_fixture.repositories.claims.list_heads() == (second,)
    assert repository_fixture.repositories.claims.history("claim-1") == (first, second)
    version_ids = tuple(
        repository_fixture.connection.execute(
            select(claim_versions.c.claim_version_id).order_by(claim_versions.c.version)
        ).scalars()
    )
    head_id = repository_fixture.connection.execute(
        select(claim_heads.c.claim_version_id).where(claim_heads.c.claim_id == "claim-1")
    ).scalar_one()
    assert version_ids == ("claim-1:1", "claim-1:2")
    assert head_id == "claim-1:2"


def test_claim_repository_required_lookup_rejects_missing_claim(
    repository_fixture: RepositoryFixture,
) -> None:
    with pytest.raises(KeyError, match="claim does not exist: missing"):
        repository_fixture.repositories.claims.get_head_required("missing")


def test_claim_repository_rejects_orphan_regression_and_gap_versions(
    repository_fixture: RepositoryFixture,
) -> None:
    first = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    orphan = repository_fixture.claim("claim-1", version=2, status="EVIDENCE_LINKED")

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.add_version(orphan)

    repository_fixture.repositories.claims.add_version(first)

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.add_version(first)
    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.add_version(
            repository_fixture.claim("claim-1", version=3, status="EVIDENCE_LINKED")
        )

    assert repository_fixture.repositories.claims.history("claim-1") == (first,)


def test_transaction_repository_round_trips_by_idempotency_key(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id="proposal-1", accepted=True)

    repository_fixture.repositories.transactions.add(proposal, decision, repository_fixture.now)

    stored = repository_fixture.repositories.transactions.get_by_idempotency_key("key-1")
    proposal_json = repository_fixture.connection.execute(
        select(transactions.c.proposal_json).where(transactions.c.idempotency_key == "key-1")
    ).scalar_one()
    assert stored is not None
    assert stored.proposal == proposal
    assert stored.proposal_hash == sha256_hex(
        canonical_json_bytes(proposal.model_dump(mode="json"))
    )
    assert stored.proposal_hash != sha256_hex(proposal_json.encode("utf-8"))
    assert stored.decision == decision
    assert stored.created_at == repository_fixture.now
    assert repository_fixture.repositories.transactions.list_all() == (stored,)


def test_transaction_repository_looks_up_by_proposal_id(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    repository_fixture.repositories.transactions.add(proposal, decision, repository_fixture.now)

    stored = repository_fixture.repositories.transactions.get_by_proposal_id(proposal.proposal_id)
    assert stored is not None
    assert stored.proposal == proposal
    assert repository_fixture.repositories.transactions.get_by_proposal_id("missing") is None


@pytest.mark.parametrize(
    ("row_proposal_id", "row_idempotency_key", "decision_proposal_id"),
    [
        ("different-proposal", "key-1", "proposal-1"),
        ("proposal-1", "different-key", "proposal-1"),
        ("proposal-1", "key-1", "different-proposal"),
    ],
)
def test_transaction_repository_rejects_redundant_identity_corruption(
    repository_fixture: RepositoryFixture,
    row_proposal_id: str,
    row_idempotency_key: str,
    decision_proposal_id: str,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id=decision_proposal_id, accepted=True)
    proposal_json = PROPOSAL_ADAPTER.dump_json(proposal).decode("utf-8")
    repository_fixture.connection.execute(
        insert(transactions).values(
            proposal_id=row_proposal_id,
            idempotency_key=row_idempotency_key,
            proposal_hash=sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json"))),
            proposal_json=proposal_json,
            decision_json=decision.model_dump_json(),
            created_at=repository_fixture.now.isoformat(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.transactions.get_by_idempotency_key(row_idempotency_key)


def test_transaction_repository_rejects_stored_proposal_hash_corruption(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repository_fixture.connection.execute(
        insert(transactions).values(
            proposal_id=proposal.proposal_id,
            idempotency_key=proposal.idempotency_key,
            proposal_hash="f" * 64,
            proposal_json=PROPOSAL_ADAPTER.dump_json(proposal).decode("utf-8"),
            decision_json=decision.model_dump_json(),
            created_at=repository_fixture.now.isoformat(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.transactions.get_by_idempotency_key(
            proposal.idempotency_key
        )


def test_transaction_repository_rejects_malformed_created_at(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-time", "key-time")
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repository_fixture.connection.execute(
        insert(transactions).values(
            proposal_id=proposal.proposal_id,
            idempotency_key=proposal.idempotency_key,
            proposal_hash=sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json"))),
            proposal_json=PROPOSAL_ADAPTER.dump_json(proposal).decode("utf-8"),
            decision_json=decision.model_dump_json(),
            created_at="not-a-timestamp",
        )
    )

    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.transactions.get_by_idempotency_key(
            proposal.idempotency_key
        )


def test_transaction_repository_rejects_decision_for_another_proposal(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id="different-proposal", accepted=True)

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.transactions.add(proposal, decision, repository_fixture.now)


def test_transaction_repository_round_trips_other_strict_proposal_variants(
    repository_fixture: RepositoryFixture,
) -> None:
    proposals: tuple[Proposal, ...] = (
        ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="key-claim",
            proposer=repository_fixture.actor("proposer"),
            approval=Approval(
                approver=repository_fixture.actor("approver"),
                approved_at=repository_fixture.now,
            ),
            claim=repository_fixture.claim("claim-1", version=1, status="PROPOSED"),
        ),
        TransitionClaim(
            proposal_id="proposal-transition",
            idempotency_key="key-transition",
            proposer=repository_fixture.actor("proposer"),
            next_claim=repository_fixture.claim(
                "claim-1",
                version=2,
                status="EVIDENCE_LINKED",
            ),
        ),
    )

    for proposal in proposals:
        repository_fixture.repositories.transactions.add(
            proposal,
            TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
            repository_fixture.now,
        )

    assert (
        tuple(
            repository_fixture.repositories.transactions.get_by_idempotency_key(
                proposal.idempotency_key
            ).proposal
            for proposal in proposals
        )
        == proposals
    )


def test_policy_and_audit_repositories_round_trip(
    repository_fixture: RepositoryFixture,
) -> None:
    snapshot = repository_fixture.policy_snapshot()
    event_record = append_event(
        None,
        "test",
        {"accepted": True},
        repository_fixture.now,
    )

    repository_fixture.repositories.policies.add_and_activate(snapshot, repository_fixture.now)
    repository_fixture.repositories.audit.add(event_record)

    assert repository_fixture.repositories.policies.get_active() == snapshot
    assert repository_fixture.repositories.audit.last() == event_record
    assert repository_fixture.repositories.audit.list_all() == (event_record,)


def test_policy_repository_rejects_mismatched_snapshot_hash_before_activation(
    repository_fixture: RepositoryFixture,
) -> None:
    snapshot = repository_fixture.policy_snapshot().model_copy(update={"policy_hash": "f" * 64})

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.policies.add_and_activate(snapshot, repository_fixture.now)

    policy_rows = repository_fixture.connection.execute(
        select(governance_policies.c.policy_hash)
    ).all()
    state_rows = repository_fixture.connection.execute(
        select(governance_state.c.singleton_id)
    ).all()
    assert policy_rows == []
    assert state_rows == []


def test_policy_repository_rejects_stored_policy_hash_corruption(
    repository_fixture: RepositoryFixture,
) -> None:
    snapshot = repository_fixture.policy_snapshot()
    repository_fixture.connection.execute(
        insert(governance_policies).values(
            policy_hash="f" * 64,
            policy_json=snapshot.policy.model_dump_json(),
            created_at=repository_fixture.now.isoformat(),
        )
    )
    repository_fixture.connection.execute(
        insert(governance_state).values(singleton_id=1, active_policy_hash="f" * 64)
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.policies.get_active()


def test_audit_repository_requires_the_exact_verified_next_event(
    repository_fixture: RepositoryFixture,
) -> None:
    first = append_event(None, "test", {"accepted": True}, repository_fixture.now)

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.audit.add(first.model_copy(update={"sequence": 2}))

    repository_fixture.repositories.audit.add(first)

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.audit.add(
            append_event(None, "test", {"accepted": True}, repository_fixture.now)
        )

    second = append_event(first, "test", {"accepted": True}, repository_fixture.now)
    repository_fixture.repositories.audit.add(second)
    assert repository_fixture.repositories.audit.list_all() == (first, second)


def test_audit_repository_rejects_chain_corruption_on_reads(
    repository_fixture: RepositoryFixture,
) -> None:
    first = append_event(None, "test", {"accepted": True}, repository_fixture.now)
    repository_fixture.repositories.audit.add(first)
    corrupted = append_event(first, "test", {"accepted": True}, repository_fixture.now)
    corrupted = corrupted.model_copy(update={"event_hash": "f" * 64})
    repository_fixture.connection.execute(
        insert(audit_events).values(
            sequence=corrupted.sequence,
            event_id=corrupted.event_id,
            previous_hash=corrupted.previous_hash,
            payload_hash=corrupted.payload_hash,
            event_hash=corrupted.event_hash,
            event_json=corrupted.model_dump_json(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.audit.last()


def test_audit_repository_rejects_redundant_column_corruption(
    repository_fixture: RepositoryFixture,
) -> None:
    event_record = append_event(
        None,
        "test",
        {"accepted": True},
        repository_fixture.now,
    )
    repository_fixture.connection.execute(
        insert(audit_events).values(
            sequence=event_record.sequence,
            event_id="audit-in-column",
            previous_hash=event_record.previous_hash,
            payload_hash=event_record.payload_hash,
            event_hash=event_record.event_hash,
            event_json=event_record.model_dump_json(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.audit.list_all()


def test_evidence_repository_rejects_redundant_column_corruption(
    repository_fixture: RepositoryFixture,
) -> None:
    record = repository_fixture.evidence_record("evidence-in-json")
    repository_fixture.repositories.evidence.add(record)
    stored_json = repository_fixture.connection.execute(
        select(evidence_records.c.record_json).where(
            evidence_records.c.evidence_id == record.evidence_id
        )
    ).scalar_one()
    repository_fixture.connection.execute(
        insert(evidence_records).values(
            evidence_id="evidence-in-column",
            content_hash=record.content_hash,
            record_json=stored_json,
            created_at=record.retrieved_at.isoformat(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.evidence.get("evidence-in-column")


def test_claim_repository_rejects_redundant_version_and_head_identity_corruption(
    repository_fixture: RepositoryFixture,
) -> None:
    first = repository_fixture.claim("claim-a", version=1, status="PROPOSED")
    other = repository_fixture.claim("claim-b", version=1, status="PROPOSED")
    repository_fixture.repositories.claims.add_version(first)
    repository_fixture.repositories.claims.add_version(other)
    repository_fixture.connection.execute(
        update(claim_heads)
        .where(claim_heads.c.claim_id == first.claim_id)
        .values(claim_version_id="claim-b:1", version=1, status=ClaimStatus.PROPOSED.value)
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.get_head(first.claim_id)


def test_claim_repository_rejects_stale_head_projection(
    repository_fixture: RepositoryFixture,
) -> None:
    first = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    second = repository_fixture.claim("claim-1", version=2, status="EVIDENCE_LINKED")
    repository_fixture.repositories.claims.add_version(first)
    repository_fixture.repositories.claims.add_version(second)
    repository_fixture.connection.execute(
        update(claim_heads)
        .where(claim_heads.c.claim_id == first.claim_id)
        .values(
            claim_version_id="claim-1:1",
            version=1,
            status=ClaimStatus.PROPOSED.value,
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.get_head(first.claim_id)
    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.list_heads()


def test_claim_repository_list_heads_rejects_deleted_projection(
    repository_fixture: RepositoryFixture,
) -> None:
    claim = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    repository_fixture.repositories.claims.add_version(claim)
    repository_fixture.connection.execute(
        delete(claim_heads).where(claim_heads.c.claim_id == claim.claim_id)
    )

    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.claims.list_heads()


def test_claim_repository_rejects_gapped_history(
    repository_fixture: RepositoryFixture,
) -> None:
    first = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    third = repository_fixture.claim("claim-1", version=3, status="EVIDENCE_LINKED")
    repository_fixture.repositories.claims.add_version(first)
    record_json = third.model_dump_json()
    repository_fixture.connection.execute(
        insert(claim_versions).values(
            claim_version_id="claim-1:3",
            claim_id=third.claim_id,
            version=third.version,
            status=third.status.value,
            record_json=record_json,
            content_hash=sha256_hex(record_json.encode("utf-8")),
            created_at=third.created_at.isoformat(),
        )
    )

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.history(first.claim_id)


@pytest.mark.parametrize("column", ("version", "status", "content_hash", "created_at"))
def test_claim_repository_rejects_redundant_version_column_corruption(
    repository_fixture: RepositoryFixture,
    column: str,
) -> None:
    claim = repository_fixture.claim("claim-1", version=2, status="EVIDENCE_LINKED")
    record_json = claim.model_dump_json()
    values: dict[str, object] = {
        "claim_version_id": "claim-1:2",
        "claim_id": claim.claim_id,
        "version": claim.version,
        "status": claim.status.value,
        "record_json": record_json,
        "content_hash": sha256_hex(record_json.encode("utf-8")),
        "created_at": claim.created_at.isoformat(),
    }
    values[column] = {
        "version": 1,
        "status": ClaimStatus.PROPOSED.value,
        "content_hash": "f" * 64,
        "created_at": "2026-07-12T12:00:01+00:00",
    }[column]
    repository_fixture.connection.execute(insert(claim_versions).values(**values))

    with pytest.raises(ValueError, match="storage integrity"):
        repository_fixture.repositories.claims.history(claim.claim_id)


def test_repositories_validate_persisted_json_contracts(
    repository_fixture: RepositoryFixture,
) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    connection = repository_fixture.connection
    connection.execute(
        insert(evidence_records).values(
            evidence_id="invalid-evidence",
            content_hash="a" * 64,
            record_json='{"evidence_id": 1}',
            created_at=repository_fixture.now.isoformat(),
        )
    )
    connection.execute(
        insert(claim_versions).values(
            claim_version_id="invalid-claim:1",
            claim_id="invalid-claim",
            version=1,
            status="PROPOSED",
            record_json="{}",
            content_hash="a" * 64,
            created_at=repository_fixture.now.isoformat(),
        )
    )
    connection.execute(
        insert(claim_heads).values(
            claim_id="invalid-claim",
            claim_version_id="invalid-claim:1",
            version=1,
            status="PROPOSED",
        )
    )
    connection.execute(
        insert(transactions).values(
            proposal_id="proposal-1",
            idempotency_key="key-1",
            proposal_hash="a" * 64,
            proposal_json=PROPOSAL_ADAPTER.dump_json(proposal).decode("utf-8"),
            decision_json='{"proposal_id":"proposal-1","accepted":false,"reasons":[]}',
            created_at=repository_fixture.now.isoformat(),
        )
    )
    connection.execute(
        insert(audit_events).values(
            sequence=1,
            event_id="invalid-audit",
            previous_hash="0" * 64,
            payload_hash="a" * 64,
            event_hash="a" * 64,
            event_json="{}",
        )
    )
    connection.execute(
        insert(governance_policies).values(
            policy_hash="a" * 64,
            policy_json='{"required_claim_checks":[]}',
            created_at=repository_fixture.now.isoformat(),
        )
    )
    connection.execute(insert(governance_state).values(singleton_id=1, active_policy_hash="a" * 64))

    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.evidence.get("invalid-evidence")
    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.claims.get_head("invalid-claim")
    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.transactions.get_by_idempotency_key("key-1")
    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.audit.last()
    with pytest.raises(StorageIntegrityError, match="storage integrity"):
        repository_fixture.repositories.policies.get_active()


def test_repository_writes_revalidate_bypassed_models(
    repository_fixture: RepositoryFixture,
) -> None:
    evidence = repository_fixture.evidence_record("ev-1")
    claim = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id="proposal-1", accepted=True)
    audit_event = append_event(None, "test", {}, repository_fixture.now)
    snapshot = repository_fixture.policy_snapshot()

    with pytest.raises(ValidationError):
        repository_fixture.repositories.evidence.add(
            cast(EvidenceRecord, evidence.model_copy(update={"evidence_id": 1}))
        )
    with pytest.raises(ValidationError):
        repository_fixture.repositories.claims.add_version(
            cast(AtomicClaim, claim.model_copy(update={"version": True}))
        )
    with pytest.raises(ValidationError):
        repository_fixture.repositories.transactions.add(
            cast(Proposal, proposal.model_copy(update={"proposal_id": 1})),
            decision,
            repository_fixture.now,
        )
    with pytest.raises(ValidationError):
        repository_fixture.repositories.transactions.add(
            proposal,
            decision.model_copy(update={"accepted": False}),
            repository_fixture.now,
        )
    with pytest.raises(ValidationError):
        repository_fixture.repositories.audit.add(audit_event.model_copy(update={"sequence": True}))
    with pytest.raises(ValidationError):
        repository_fixture.repositories.policies.add_and_activate(
            cast(
                PolicySnapshot,
                snapshot.model_copy(update={"policy": {"required_claim_checks": []}}),
            ),
            repository_fixture.now,
        )


def test_unit_of_work_uses_begin_immediate_and_commits(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)
    statements: list[str] = []

    def capture_statement(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    record = _evidence_record("committed")
    with DatabaseUnitOfWork(engine) as unit_of_work:
        unit_of_work.repositories().evidence.add(record)

    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.repositories().evidence.get("committed") == record
    engine.dispose()

    assert "BEGIN IMMEDIATE" in statements


def test_unit_of_work_rolls_back_closes_and_deactivates(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)
    record = _evidence_record("rolled-back")
    unit_of_work = DatabaseUnitOfWork(engine)
    active_connection: Connection | None = None

    with pytest.raises(RuntimeError, match="force rollback"), unit_of_work:
        active_connection = unit_of_work.connection
        unit_of_work.repositories().evidence.add(record)
        raise RuntimeError("force rollback")

    assert active_connection is not None
    assert active_connection.closed
    assert unit_of_work.connection is None
    with pytest.raises(RuntimeError, match="unit of work is not active"):
        unit_of_work.repositories()
    with DatabaseUnitOfWork(engine) as checking_uow:
        assert checking_uow.repositories().evidence.get("rolled-back") is None
    engine.dispose()


def test_unit_of_work_closes_connection_when_begin_immediate_fails(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "kernel.db")
    upgrade_database(url)
    engine = create_database_engine(url)
    attempted_connections: list[Connection] = []

    def fail_begin(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement == "BEGIN IMMEDIATE":
            attempted_connections.append(connection)
            raise RuntimeError("begin failed")

    event.listen(engine, "before_cursor_execute", fail_begin)
    unit_of_work = DatabaseUnitOfWork(engine)

    with pytest.raises(RuntimeError, match="begin failed"):
        unit_of_work.__enter__()

    assert attempted_connections[0].closed
    assert unit_of_work.connection is None
    engine.dispose()
