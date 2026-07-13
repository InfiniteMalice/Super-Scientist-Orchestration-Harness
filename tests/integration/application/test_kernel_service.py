from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super_scientist.application.kernel_service import KernelService
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    ProposeClaim,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import AuditRepository

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class KernelFixture:
    service: KernelService
    uow_factory: Callable[[], DatabaseUnitOfWork]
    artifact_store: FileArtifactStore
    actor: ActorIdentity
    policy: PolicySnapshot

    def valid_add_evidence(self, proposal_id: str, key: str, content: bytes) -> AddEvidence:
        artifact = self.artifact_store.put(content, "text/plain")
        evidence = EvidenceRecord(
            evidence_id=f"evidence-{proposal_id}",
            evidence_type="observation",
            source_locator=f"fixture://{proposal_id}",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={"collector": "kernel-service-test"},
            ingestion_actor_id=self.actor.actor_id,
        )
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            evidence=evidence,
        )

    def self_approved_claim(self, proposal_id: str, key: str) -> ProposeClaim:
        claim = AtomicClaim(
            claim_id=f"claim-{proposal_id}",
            version=1,
            proposition="The fixture intervention changes the fixture outcome.",
            scope="Fixture scope",
            population_or_system="Fixture system",
            epistemic_modality="supports",
            status=ClaimStatus.PROPOSED,
            created_at=NOW,
            created_by=self.actor.actor_id,
        )
        return ProposeClaim(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            approval=Approval(approver=self.actor, approved_at=NOW),
            claim=claim,
        )


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _policy_snapshot(required_claim_checks: tuple[str, ...] = ("source_exists",)) -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=required_claim_checks)
    policy_data = policy.model_dump(mode="json")
    policy_data["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(policy_data)),
        policy=policy,
    )


def _build_kernel(tmp_path: Path) -> tuple[KernelFixture, Engine]:
    database_url = _database_url(tmp_path / "kernel.db")
    upgrade_database(database_url)
    engine: Engine = create_database_engine(database_url)
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)
    policy = _policy_snapshot()

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    return (
        KernelFixture(
            service=KernelService(uow_factory, policy, FixedClock()),
            uow_factory=uow_factory,
            artifact_store=artifact_store,
            actor=actor,
            policy=policy,
        ),
        engine,
    )


@pytest.fixture
def kernel(tmp_path: Path) -> Iterator[KernelFixture]:
    fixture, engine = _build_kernel(tmp_path)
    with fixture.uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(fixture.policy, NOW)
    yield fixture
    engine.dispose()


@pytest.fixture
def unregistered_kernel(tmp_path: Path) -> Iterator[KernelFixture]:
    fixture, engine = _build_kernel(tmp_path)
    yield fixture
    engine.dispose()


def test_accepted_evidence_is_committed_with_audit(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-1", "k-1", b"observation")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert decision.accepted
        assert repositories.evidence.get(proposal.evidence.evidence_id) == proposal.evidence
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is True


def test_rejected_claim_is_audited_but_not_projected(kernel: KernelFixture) -> None:
    proposal = kernel.self_approved_claim("p-2", "k-2")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert not decision.accepted
        assert repositories.claims.get_head(proposal.claim.claim_id) is None
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is False


def test_duplicate_submission_returns_original_decision(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-3", "k-3", b"same")

    first = kernel.service.submit(proposal)
    second = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        assert second.replayed
        assert second.model_copy(update={"replayed": False}) == first
        assert len(unit_of_work.repositories().audit.list_all()) == 1


def test_reused_idempotency_key_with_new_content_is_rejected_and_audited(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p-4", "shared-key", b"first")
    conflicting = kernel.valid_add_evidence("p-5", "shared-key", b"different")

    assert kernel.service.submit(first).accepted
    decision = kernel.service.submit(conflicting)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(first.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
        assert repositories.evidence.get(conflicting.evidence.evidence_id) is None
        assert stored is not None
        assert stored.proposal == first
        assert stored.decision.accepted
        assert len(repositories.audit.list_all()) == 2


def test_exact_retry_replays_when_constructor_policy_is_stale(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-5", "k-5", b"observation")
    first = kernel.service.submit(proposal)
    stale_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    stale_service = KernelService(kernel.uow_factory, stale_policy, FixedClock())

    replay = stale_service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert replay.replayed
        assert replay.model_copy(update={"replayed": False}) == first
        assert len(repositories.audit.list_all()) == 1


def test_idempotency_conflict_is_audited_when_constructor_policy_is_stale(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p-6", "shared-key", b"first")
    conflicting = kernel.valid_add_evidence("p-7", "shared-key", b"different")
    stale_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    stale_service = KernelService(kernel.uow_factory, stale_policy, FixedClock())

    assert kernel.service.submit(first).accepted
    decision = stale_service.submit(conflicting)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(first.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
        assert repositories.evidence.get(conflicting.evidence.evidence_id) is None
        assert stored is not None
        assert stored.proposal == first
        assert stored.decision.accepted
        assert len(repositories.audit.list_all()) == 2


def test_missing_active_policy_is_rejected_and_audited(
    unregistered_kernel: KernelFixture,
) -> None:
    proposal = unregistered_kernel.valid_add_evidence("p-5", "k-5", b"observation")

    decision = unregistered_kernel.service.submit(proposal)

    with unregistered_kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert stored is not None
        assert stored.decision == decision
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is False


def test_mismatched_active_policy_is_rejected_and_audited(kernel: KernelFixture) -> None:
    stored_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    with kernel.uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(stored_policy, NOW)
    proposal = kernel.valid_add_evidence("p-6", "k-6", b"observation")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert stored is not None
        assert stored.decision == decision
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is False


def test_reused_proposal_id_is_rejected_and_audited(kernel: KernelFixture) -> None:
    first = kernel.valid_add_evidence("p-7", "k-7", b"first")
    colliding = kernel.valid_add_evidence("p-7", "k-8", b"different")
    colliding = colliding.model_copy(
        update={
            "evidence": colliding.evidence.model_copy(
                update={"evidence_id": "evidence-p-7-conflict"}
            )
        }
    )

    assert kernel.service.submit(first).accepted
    decision = kernel.service.submit(colliding)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert decision.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
        assert repositories.evidence.get(first.evidence.evidence_id) == first.evidence
        assert repositories.evidence.get(colliding.evidence.evidence_id) is None
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 2


def test_audit_failure_rolls_back_database_rows_but_not_prepared_artifact(
    kernel: KernelFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = kernel.valid_add_evidence("p-6", "k-6", b"observation")

    def fail_add(self: AuditRepository, event: object) -> None:
        del self, event
        raise RuntimeError("disk failure")

    monkeypatch.setattr(AuditRepository, "add", fail_add)

    with pytest.raises(RuntimeError, match="disk failure"):
        kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert repositories.transactions.get_by_idempotency_key(proposal.idempotency_key) is None
        assert repositories.audit.list_all() == ()
    assert kernel.artifact_store.read(proposal.evidence.artifact) == b"observation"
