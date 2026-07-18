from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super_scientist.application.kernel_service import KernelService
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import AddEvidence
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class Runtime:
    service: KernelService
    coordinator: TransactionCoordinator
    uow_factory: Callable[[], DatabaseUnitOfWork]
    artifact_store: FileArtifactStore
    actor: ActorIdentity

    def add_evidence_proposal(self, proposal_id: str, key: str) -> AddEvidence:
        artifact = self.artifact_store.put(b"coordinator characterization", "text/plain")
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            evidence=EvidenceRecord(
                evidence_id=f"evidence-{proposal_id}",
                evidence_type="observation",
                source_locator=f"fixture://{proposal_id}",
                retrieved_at=NOW,
                artifact=artifact,
                provenance={"collector": "transaction-coordinator-test"},
                ingestion_actor_id=self.actor.actor_id,
            ),
        )

    def transaction_and_audit_counts(self) -> tuple[int, int]:
        with self.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            return (
                len(repositories.transactions.list_all()),
                len(repositories.audit.list_all()),
            )


def _policy_snapshot() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    policy_data = policy.model_dump(mode="json")
    policy_data["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(policy_data)),
        policy=policy,
    )


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    database_url = f"sqlite:///{(tmp_path / 'kernel.db').as_posix()}"
    upgrade_database(database_url)
    engine: Engine = create_database_engine(database_url)
    policy = _policy_snapshot()
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    coordinator = TransactionCoordinator(uow_factory, policy, FixedClock(), artifact_store)
    yield Runtime(
        service=KernelService(uow_factory, policy, FixedClock(), artifact_store),
        coordinator=coordinator,
        uow_factory=uow_factory,
        artifact_store=artifact_store,
        actor=actor,
    )
    engine.dispose()


@pytest.mark.integration
def test_coordinator_preserves_one_decision_and_audit_event_per_new_attempt(
    runtime: Runtime,
) -> None:
    assert not hasattr(runtime.service, "coordinator")

    decision = runtime.coordinator.submit(runtime.add_evidence_proposal("proposal-1", "key-1"))

    assert decision.accepted is True
    assert runtime.transaction_and_audit_counts() == (1, 1)


@pytest.mark.integration
def test_compatibility_router_declares_the_resolved_proposal_type(runtime: Runtime) -> None:
    proposal_types = ("add_evidence", "propose_claim", "transition_claim")

    assert tuple(
        runtime.coordinator.router.resolve(proposal_type).proposal_type
        for proposal_type in proposal_types
    ) == proposal_types


@pytest.mark.integration
def test_exact_replay_does_not_readmit_or_append(runtime: Runtime) -> None:
    proposal = runtime.add_evidence_proposal("proposal-1", "key-1")

    first = runtime.service.submit(proposal)
    second = runtime.service.submit(proposal)

    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})
    assert runtime.transaction_and_audit_counts() == (1, 1)
