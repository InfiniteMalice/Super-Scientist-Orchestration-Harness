from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, update

from super_scientist.application.kernel_service import KernelService
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import (
    EvidenceRecord,
    EvidenceSpan,
    VerificationState,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    ProposeClaim,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import audit_events, claim_heads

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class IntegrityFixture:
    engine: Engine
    service: KernelService
    artifacts: FileArtifactStore
    actor: ActorIdentity

    def uow(self) -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(self.engine)

    def evidence_proposal(self, proposal_id: str = "proposal-evidence") -> AddEvidence:
        artifact = self.artifacts.put(b"authoritative evidence", "application/octet-stream")
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=f"key-{proposal_id}",
            proposer=self.actor,
            evidence=EvidenceRecord(
                evidence_id=f"evidence-{proposal_id}",
                evidence_type="observation",
                source_locator=f"fixture://{proposal_id}",
                retrieved_at=NOW,
                artifact=artifact,
                provenance={"collector": "integrity-test"},
                ingestion_actor_id=self.actor.actor_id,
                verification_state=VerificationState.UNVERIFIED,
            ),
        )

    def claim_proposal(self) -> ProposeClaim:
        return ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="key-claim",
            proposer=self.actor,
            claim=AtomicClaim(
                claim_id="claim-1",
                version=1,
                proposition="The fixture is intact.",
                scope="fixture",
                population_or_system="fixture system",
                epistemic_modality="observed",
                status=ClaimStatus.PROPOSED,
                created_at=NOW,
                created_by=self.actor.actor_id,
            ),
        )


@pytest.fixture
def integrity(tmp_path: Path) -> Iterator[IntegrityFixture]:
    database_url = f"sqlite:///{(tmp_path / 'kernel.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="actor-1", kind=ActorKind.HUMAN, created_at=NOW)
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    with DatabaseUnitOfWork(engine) as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(snapshot, NOW)
    service = KernelService(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        FixedClock(),
        artifacts,
    )
    yield IntegrityFixture(engine=engine, service=service, artifacts=artifacts, actor=actor)
    engine.dispose()


def _verify(integrity: IntegrityFixture) -> object:
    with integrity.uow() as unit_of_work:
        return verify_workspace(unit_of_work.repositories(), integrity.artifacts)


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_workspace_verifier_rehashes_every_projected_artifact(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.evidence_proposal()
    assert integrity.service.submit(proposal).accepted
    path = integrity.artifacts.resolve(proposal.evidence.artifact)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    result = _verify(integrity)

    assert not result.valid
    assert "artifact" in result.reason


def test_workspace_verifier_rechecks_stored_text_span_binding(
    integrity: IntegrityFixture,
) -> None:
    artifact = integrity.artifacts.put(b"authoritative evidence", "text/plain")
    proposal = AddEvidence(
        proposal_id="proposal-invalid-span",
        idempotency_key="key-invalid-span",
        proposer=integrity.actor,
        evidence=EvidenceRecord(
            evidence_id="evidence-invalid-span",
            evidence_type="observation",
            source_locator="fixture://invalid-span",
            retrieved_at=NOW,
            artifact=artifact,
            extracted_span=EvidenceSpan(start=0, end=5, text="wrong"),
            provenance={"collector": "integrity-test"},
            ingestion_actor_id=integrity.actor.actor_id,
        ),
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    event = append_event(
        None,
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": "a" * 64,
        },
        NOW,
    )
    with integrity.uow() as unit_of_work:
        repositories = unit_of_work.repositories()
        repositories.evidence.add(
            proposal.evidence.model_copy(
                update={"verification_state": VerificationState.HASH_VERIFIED}
            )
        )
        repositories.transactions.add(proposal, decision, NOW)
        repositories.audit.add(event)

    result = _verify(integrity)

    assert not result.valid
    assert "span" in result.reason


def test_workspace_verifier_detects_corrupt_claim_head(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            update(claim_heads)
            .where(claim_heads.c.claim_id == proposal.claim.claim_id)
            .values(version=99)
        )

    result = _verify(integrity)

    assert not result.valid
    assert "claim head" in result.reason


def test_workspace_verifier_detects_transaction_audit_mismatch(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    mismatch = TransactionDecision(
        proposal_id=proposal.proposal_id,
        accepted=False,
        reasons=(
            {
                "code": RejectionCode.PERMISSION_DENIED,
                "message": "mismatched fixture decision",
            },
        ),
    )
    replacement = append_event(
        None,
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": mismatch.model_dump(mode="json"),
            "policy_hash": "a" * 64,
        },
        NOW,
    )
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        unit_of_work.connection.execute(
            update(audit_events)
            .where(audit_events.c.sequence == 1)
            .values(
                event_id=replacement.event_id,
                previous_hash=replacement.previous_hash,
                payload_hash=replacement.payload_hash,
                event_hash=replacement.event_hash,
                event_json=replacement.model_dump_json(),
            )
        )

    result = _verify(integrity)

    assert not result.valid
    assert "transaction" in result.reason


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_exact_replay_fails_closed_on_artifact_corruption_without_new_audit(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.evidence_proposal()
    assert integrity.service.submit(proposal).accepted
    path = integrity.artifacts.resolve(proposal.evidence.artifact)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    with pytest.raises(StorageIntegrityError, match="workspace integrity"):
        integrity.service.submit(proposal)

    with integrity.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(audit_events)).scalar_one() == 1
