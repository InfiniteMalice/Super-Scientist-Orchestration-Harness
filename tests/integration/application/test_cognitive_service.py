from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine

from super_scientist.application.cognitive.service import (
    CognitiveOrchestrationService,
    ResearchCoordinator,
)
from super_scientist.application.harness_eval.capabilities import (
    OutputOnlyEvaluatorExecutor,
    walk_object_graph_types,
)
from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import AddEvidence
from super_scientist.providers.storage.artifacts import ArtifactStore, FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.protected_evaluation import ProtectedAnswerReader
from super_scientist.providers.storage.repositories import RepositorySet

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


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


@pytest.fixture
def cognitive_runtime(
    tmp_path: Path,
) -> Iterator[
    tuple[
        TransactionCoordinator,
        ResearchCoordinator,
        Callable[[], DatabaseUnitOfWork],
        FileArtifactStore,
        ActorIdentity,
    ]
]:
    database_url = f"sqlite:///{(tmp_path / 'cognitive-service.db').as_posix()}"
    upgrade_database(database_url)
    engine: Engine = create_database_engine(database_url)
    policy = _policy_snapshot()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    coordinator = TransactionCoordinator(uow_factory, policy, FixedClock(), artifacts)
    research = ResearchCoordinator(CognitiveOrchestrationService(coordinator))
    yield coordinator, research, uow_factory, artifacts, actor
    engine.dispose()


def _proposal(
    artifacts: FileArtifactStore,
    actor: ActorIdentity,
    proposal_id: str,
    idempotency_key: str,
) -> AddEvidence:
    artifact = artifacts.put(f"evidence:{proposal_id}:{idempotency_key}".encode(), "text/plain")
    return AddEvidence(
        proposal_id=proposal_id,
        idempotency_key=idempotency_key,
        proposer=actor,
        evidence=EvidenceRecord(
            evidence_id=f"evidence-{proposal_id}-{idempotency_key}",
            evidence_type="observation",
            source_locator=f"fixture://{proposal_id}/{idempotency_key}",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={"collector": "cognitive-service-test"},
            ingestion_actor_id=actor.actor_id,
        ),
    )


@pytest.mark.integration
def test_research_coordinator_stops_on_first_rejection(
    cognitive_runtime: tuple[
        TransactionCoordinator,
        ResearchCoordinator,
        Callable[[], DatabaseUnitOfWork],
        FileArtifactStore,
        ActorIdentity,
    ],
) -> None:
    _, research, uow_factory, artifacts, actor = cognitive_runtime
    proposals = (
        _proposal(artifacts, actor, "proposal-1", "key-1"),
        _proposal(artifacts, actor, "proposal-1", "key-2"),
        _proposal(artifacts, actor, "proposal-3", "key-3"),
    )

    decisions = research.run_declared_slice(proposals)

    assert tuple(decision.accepted for decision in decisions) == (True, False)
    with uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert repositories.transactions.get_by_proposal_id("proposal-3") is None
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 2


@pytest.mark.integration
def test_facades_require_exact_sealed_types(
    cognitive_runtime: tuple[
        TransactionCoordinator,
        ResearchCoordinator,
        Callable[[], DatabaseUnitOfWork],
        FileArtifactStore,
        ActorIdentity,
    ],
) -> None:
    _, _, _, _, _ = cognitive_runtime

    class CoordinatorSubclass(TransactionCoordinator):
        pass

    class SubmitterSubclass(CognitiveOrchestrationService):
        pass

    with pytest.raises(TypeError, match="exact transaction coordinator"):
        CognitiveOrchestrationService(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact transaction coordinator"):
        CognitiveOrchestrationService(object.__new__(CoordinatorSubclass))

    with pytest.raises(TypeError, match="sealed submit capability"):
        ResearchCoordinator(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="sealed submit capability"):
        ResearchCoordinator(object.__new__(SubmitterSubclass))


@pytest.mark.integration
def test_research_coordinator_object_graph_has_no_storage_or_execution_authority(
    cognitive_runtime: tuple[
        TransactionCoordinator,
        ResearchCoordinator,
        Callable[[], DatabaseUnitOfWork],
        FileArtifactStore,
        ActorIdentity,
    ],
) -> None:
    _, research, _, _, _ = cognitive_runtime
    forbidden = {
        RepositorySet,
        DatabaseUnitOfWork,
        Connection,
        ArtifactStore,
        ProtectedAnswerReader,
        SimulatorRegistry,
        OutputOnlyEvaluatorExecutor,
    }

    assert walk_object_graph_types(research).isdisjoint(forbidden)
    assert {name for name in dir(type(research)) if not name.startswith("_")} == {
        "run_declared_slice"
    }
