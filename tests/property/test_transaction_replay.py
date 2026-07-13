from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st

from super_scientist.application.kernel_service import KernelService
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


def _policy_snapshot() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    policy_data = policy.model_dump(mode="json")
    policy_data["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(policy_data)),
        policy=policy,
    )


@given(st.binary(min_size=1, max_size=64))
@settings(deadline=None)
def test_replaying_a_submission_is_stable(content: bytes) -> None:
    with TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database_url = f"sqlite:///{(root / 'kernel.db').as_posix()}"
        upgrade_database(database_url)
        engine = create_database_engine(database_url)
        policy = _policy_snapshot()
        with DatabaseUnitOfWork(engine) as unit_of_work:
            unit_of_work.repositories().policies.add_and_activate(policy, NOW)
        artifact_store = FileArtifactStore(root / "artifacts")
        actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)
        artifact = artifact_store.put(content, "application/octet-stream")
        proposal = AddEvidence(
            proposal_id="proposal-1",
            idempotency_key="key-1",
            proposer=actor,
            evidence=EvidenceRecord(
                evidence_id="evidence-1",
                evidence_type="observation",
                source_locator="fixture://property",
                retrieved_at=NOW,
                artifact=artifact,
                provenance={"collector": "property-test"},
                ingestion_actor_id=actor.actor_id,
            ),
        )
        def uow_factory() -> DatabaseUnitOfWork:
            return DatabaseUnitOfWork(engine)

        service = KernelService(uow_factory, policy, FixedClock())

        first = service.submit(proposal)
        replay = service.submit(proposal)

        engine.dispose()

    assert first.accepted
    assert replay.replayed
    assert replay.model_copy(update={"replayed": False}) == first
