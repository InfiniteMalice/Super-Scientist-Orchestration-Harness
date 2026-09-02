from __future__ import annotations

from collections.abc import Callable, Iterator, MutableMapping
from copy import copy, deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine

from super_scientist.application.cognitive import service as service_module
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
    research = ResearchCoordinator()
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
    coordinator, research, uow_factory, artifacts, actor = cognitive_runtime
    proposals = (
        _proposal(artifacts, actor, "proposal-1", "key-1"),
        _proposal(artifacts, actor, "proposal-1", "key-2"),
        _proposal(artifacts, actor, "proposal-3", "key-3"),
    )

    decisions = research.run_declared_slice(
        CognitiveOrchestrationService(),
        coordinator,
        proposals,
    )

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

    with pytest.raises(TypeError, match="cannot be subclassed"):

        class CoordinatorSubclass(CognitiveOrchestrationService):
            pass

    with pytest.raises(TypeError, match="cannot be subclassed"):

        class ResearchSubclass(ResearchCoordinator):
            pass

    service = CognitiveOrchestrationService()
    research = ResearchCoordinator()
    with pytest.raises(TypeError, match="exact transaction coordinator"):
        service.submit(object(), object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="sealed cognitive service"):
        research.run_declared_slice(object(), object(), ())  # type: ignore[arg-type]


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
    del cognitive_runtime
    submitter = CognitiveOrchestrationService()
    research = ResearchCoordinator()
    forbidden = {
        TransactionCoordinator,
        RepositorySet,
        DatabaseUnitOfWork,
        Connection,
        ArtifactStore,
        FileArtifactStore,
        ProtectedAnswerReader,
        SimulatorRegistry,
        OutputOnlyEvaluatorExecutor,
    }

    assert walk_object_graph_types(research).isdisjoint(forbidden)
    assert walk_object_graph_types(submitter).isdisjoint(forbidden)
    with pytest.raises(AttributeError):
        object.__getattribute__(submitter, "_coordinator")
    with pytest.raises(AttributeError):
        object.__getattribute__(submitter, "_token")
    with pytest.raises(AttributeError):
        object.__getattribute__(research, "_submitter")
    with pytest.raises(TypeError, match="cannot be copied"):
        copy(submitter)
    with pytest.raises(TypeError, match="cannot be copied"):
        deepcopy(submitter)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy(research)
    with pytest.raises(TypeError, match="cannot be copied"):
        deepcopy(research)
    assert {name for name in dir(type(research)) if not name.startswith("_")} == {
        "run_declared_slice"
    }


@pytest.mark.integration
def test_facade_authority_registry_is_not_module_mutable_forgeable_or_leaked(
    cognitive_runtime: tuple[
        TransactionCoordinator,
        ResearchCoordinator,
        Callable[[], DatabaseUnitOfWork],
        FileArtifactStore,
        ActorIdentity,
    ],
) -> None:
    coordinator, _, _, artifacts, actor = cognitive_runtime
    assert all(
        not isinstance(value, MutableMapping)
        for name, value in vars(service_module).items()
        if not name.startswith("__")
    )
    assert all(type(value) is not TransactionCoordinator for value in vars(service_module).values())

    forged_submitter = object.__new__(CognitiveOrchestrationService)
    proposal = _proposal(artifacts, actor, "forged-proposal", "forged-key")
    assert forged_submitter.submit(coordinator, proposal).accepted is True
    forged_research = object.__new__(ResearchCoordinator)
    assert forged_research.run_declared_slice(forged_submitter, coordinator, ()) == ()
    with pytest.raises(AttributeError):
        object.__setattr__(forged_submitter, "_token", object())

    assert CognitiveOrchestrationService.submit.__closure__ is None
    assert ResearchCoordinator.run_declared_slice.__closure__ is None

    class HostileReceiver:
        def __getattribute__(self, name: str) -> object:
            del name
            raise AssertionError("hostile receiver hook must not run")

    with pytest.raises(TypeError, match="exact cognitive service"):
        CognitiveOrchestrationService.submit(  # type: ignore[arg-type]
            HostileReceiver(),
            coordinator,
            proposal,
        )
    with pytest.raises(TypeError, match="exact research coordinator"):
        ResearchCoordinator.run_declared_slice(  # type: ignore[arg-type]
            HostileReceiver(),
            forged_submitter,
            coordinator,
            (),
        )
