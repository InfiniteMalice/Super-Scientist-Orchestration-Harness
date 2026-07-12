from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, event, insert, select

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
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import RepositorySet
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
        policy_hash = sha256_hex(canonical_json_bytes(policy.model_dump(mode="json")))
        return PolicySnapshot(policy_hash=policy_hash, policy=policy)


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
    assert stored.proposal_hash == sha256_hex(proposal_json.encode("utf-8"))
    assert stored.decision == decision
    assert repository_fixture.repositories.transactions.list_all() == (stored,)


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
            claim_id="claim-1",
            expected_version=1,
            target_status=ClaimStatus.EVIDENCE_LINKED,
        ),
    )

    for proposal in proposals:
        repository_fixture.repositories.transactions.add(
            proposal,
            TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
            repository_fixture.now,
        )

    assert tuple(
        repository_fixture.repositories.transactions.get_by_idempotency_key(
            proposal.idempotency_key
        ).proposal
        for proposal in proposals
    ) == proposals


def test_policy_and_audit_repositories_round_trip(
    repository_fixture: RepositoryFixture,
) -> None:
    snapshot = repository_fixture.policy_snapshot()
    event_record = append_event(
        None,
        "audit-1",
        "test",
        {"accepted": True},
        repository_fixture.now,
    )

    repository_fixture.repositories.policies.add_and_activate(snapshot, repository_fixture.now)
    repository_fixture.repositories.audit.add(event_record)

    assert repository_fixture.repositories.policies.get_active() == snapshot
    assert repository_fixture.repositories.audit.last() == event_record
    assert repository_fixture.repositories.audit.list_all() == (event_record,)


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

    with pytest.raises(ValidationError):
        repository_fixture.repositories.evidence.get("invalid-evidence")
    with pytest.raises(ValidationError):
        repository_fixture.repositories.claims.get_head("invalid-claim")
    with pytest.raises(ValidationError):
        repository_fixture.repositories.transactions.get_by_idempotency_key("key-1")
    with pytest.raises(ValidationError):
        repository_fixture.repositories.audit.last()
    with pytest.raises(ValidationError):
        repository_fixture.repositories.policies.get_active()


def test_repository_writes_revalidate_bypassed_models(
    repository_fixture: RepositoryFixture,
) -> None:
    evidence = repository_fixture.evidence_record("ev-1")
    claim = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id="proposal-1", accepted=True)
    audit_event = append_event(None, "audit-1", "test", {}, repository_fixture.now)
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
