from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import (
    AtomicClaim,
    ClaimStatus,
    EvidenceLink,
)
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    ProposalAttempt,
    ProposeClaim,
    RejectionCode,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class ChangingArtifactStore:
    def __init__(self, delegate: FileArtifactStore) -> None:
        self._delegate = delegate

    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        return self._delegate.put(data, media_type).model_copy(
            update={"media_type": "application/x-changed"}
        )

    def read(self, ref: ArtifactRef) -> bytes:
        return self._delegate.read(ref)


class ChangingFileArtifactStore(FileArtifactStore):
    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        return (
            super().put(data, media_type).model_copy(update={"media_type": "application/x-changed"})
        )


class UnreadableArtifactStore:
    def __init__(self, delegate: FileArtifactStore) -> None:
        self._delegate = delegate

    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        return self._delegate.put(data, media_type)

    def read(self, ref: ArtifactRef) -> bytes:
        del ref
        raise OSError("source artifact became unreadable")


class CollisionInjectingUnitOfWork(DatabaseUnitOfWork):
    def __init__(
        self,
        engine: Engine,
        coordinator: TransactionCoordinator,
        collision: AddEvidence,
    ) -> None:
        super().__init__(engine)
        self._coordinator = coordinator
        self._collision = collision

    def __enter__(self) -> CollisionInjectingUnitOfWork:
        super().__enter__()
        connection = self.connection
        assert connection is not None
        decision = self._coordinator._submit_locked(
            self._collision,
            self.repositories(),
            connection,
        )
        assert decision.accepted is True
        return self


@dataclass(frozen=True)
class ExchangeRuntime:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    coordinator: TransactionCoordinator
    artifact_store: FileArtifactStore
    actor: ActorIdentity

    def add_evidence(self, content: bytes) -> None:
        artifact = self.artifact_store.put(content, "text/plain")
        decision = self.coordinator.submit(
            AddEvidence(
                proposal_id="workspace-record-1",
                idempotency_key="workspace-record-key-1",
                proposer=self.actor,
                evidence=EvidenceRecord(
                    evidence_id="workspace-evidence-1",
                    evidence_type="synthetic_observation",
                    source_locator=f"fixture://{content.decode('ascii')}",
                    retrieved_at=NOW,
                    artifact=artifact,
                    provenance={"collector": "workspace-exchange-test"},
                    ingestion_actor_id=self.actor.actor_id,
                ),
            )
        )
        assert decision.accepted is True


def _policy() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    payload = policy.model_dump(mode="json")
    payload["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(payload)),
        policy=policy,
    )


def _runtime(
    root: Path,
    name: str,
    *,
    policy_snapshot: PolicySnapshot | None = None,
) -> ExchangeRuntime:
    database_url = f"sqlite:///{(root / f'{name}.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    policy = policy_snapshot or _policy()
    artifact_store = FileArtifactStore(root / f"{name}-artifacts")
    actor = ActorIdentity(
        actor_id="workspace-scientist",
        kind=ActorKind.HUMAN,
        created_at=NOW,
    )

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    return ExchangeRuntime(
        engine=engine,
        uow_factory=uow_factory,
        coordinator=TransactionCoordinator(
            uow_factory,
            policy,
            FixedClock(),
            artifact_store,
        ),
        artifact_store=artifact_store,
        actor=actor,
    )


@pytest.fixture
def runtimes(tmp_path: Path) -> Iterator[tuple[ExchangeRuntime, ExchangeRuntime]]:
    source = _runtime(tmp_path, "source")
    target = _runtime(tmp_path, "target")
    yield source, target
    source.engine.dispose()
    target.engine.dispose()


def _exchange() -> object:
    module_path = Path("src/super_scientist/application/workspace_exchange.py")
    assert module_path.is_file()
    return importlib.import_module("super_scientist.application.workspace_exchange")


def _rehash_bundle_payload(payload: dict[str, object]) -> dict[str, object]:
    canonical = copy.deepcopy(payload)
    canonical.pop("bundle_hash")
    payload["bundle_hash"] = sha256_hex(canonical_json_bytes(canonical))
    return payload


def _durable_snapshot(runtime: ExchangeRuntime, artifact_root: Path) -> tuple[object, ...]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        database_state = (
            tuple(item.policy_hash for item in repositories.policies.list_all()),
            tuple(
                (
                    item.proposal.proposal_id,
                    item.proposal_hash,
                    item.decision.accepted,
                    tuple(reason.code for reason in item.decision.reasons),
                )
                for item in repositories.transactions.list_all()
            ),
            tuple(
                (
                    item.evidence_id,
                    item.artifact.sha256,
                    item.verification_state,
                )
                for item in repositories.evidence.list_all()
            ),
            tuple(item.event_hash for item in repositories.audit.list_all()),
        )
    artifact_state = tuple(
        (path.relative_to(artifact_root).as_posix(), path.read_bytes())
        for path in sorted(artifact_root.rglob("*"))
        if path.is_file()
    )
    return (*database_state, artifact_state)


def test_workspace_exchange_exposes_strict_canonical_import_contract() -> None:
    exchange = _exchange()

    assert exchange.WorkspaceExport.model_config["frozen"] is True
    assert exchange.WorkspaceExport.model_config["extra"] == "forbid"
    assert callable(exchange.export_workspace)
    assert callable(exchange.import_workspace)


def test_workspace_export_import_is_protected_safe_canonical_and_replayable(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, target = runtimes
    source.add_evidence(b"thermal-observation")

    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    serialized = exported.model_dump_json()

    assert tuple(record.stable_identity for record in exported.records) == tuple(
        sorted(record.stable_identity for record in exported.records)
    )
    assert str(
        source.artifact_store.resolve(source.artifact_store.put(b"x", "text/plain"))
    ) not in (serialized)
    assert "protected_expected_output" not in serialized
    assert "protected_store" not in serialized
    assert "executable_configuration" not in serialized

    imported = exchange.import_workspace(
        exported,
        uow_factory=target.uow_factory,
        artifact_store=target.artifact_store,
        source_artifact_store=source.artifact_store,
        clock=FixedClock(),
    )
    replayed = exchange.import_workspace(
        exported,
        uow_factory=target.uow_factory,
        artifact_store=target.artifact_store,
        source_artifact_store=source.artifact_store,
        clock=FixedClock(),
    )
    target_export = exchange.export_workspace(
        uow_factory=target.uow_factory,
        artifact_store=target.artifact_store,
    )

    assert imported.conflicts == ()
    assert imported.imported == 1
    assert imported.replayed == 0
    assert imported.projections_verified is True
    assert replayed.conflicts == ()
    assert replayed.imported == 0
    assert replayed.replayed == 1
    assert replayed.projections_verified is True
    assert target_export == exported


def test_authoritative_duplicate_evidence_rejection_round_trips_without_conflict(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, target = runtimes
    source.add_evidence(b"accepted-observation")
    duplicate_artifact = source.artifact_store.put(b"duplicate-observation", "text/plain")
    duplicate = source.coordinator.submit(
        AddEvidence(
            proposal_id="workspace-record-2",
            idempotency_key="workspace-record-key-2",
            proposer=source.actor,
            evidence=EvidenceRecord(
                evidence_id="workspace-evidence-1",
                evidence_type="synthetic_observation",
                source_locator="fixture://duplicate-observation",
                retrieved_at=NOW,
                artifact=duplicate_artifact,
                provenance={"collector": "workspace-exchange-test"},
                ingestion_actor_id=source.actor.actor_id,
            ),
        )
    )
    assert duplicate.accepted is False
    assert duplicate.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )

    imported = exchange.import_workspace(
        exported,
        uow_factory=target.uow_factory,
        artifact_store=target.artifact_store,
        source_artifact_store=source.artifact_store,
        clock=FixedClock(),
    )

    assert imported.imported == 2
    assert imported.replayed == 0
    assert imported.conflicts == ()
    assert imported.projections_verified is True
    assert (
        exchange.export_workspace(
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
        )
        == exported
    )


def test_workspace_export_rejects_changed_content_without_matching_hash(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, _ = runtimes
    source.add_evidence(b"thermal-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    payload = exported.model_dump(mode="json")
    payload["records"][0]["proposal"]["evidence"]["source_locator"] = "fixture://tampered"

    with pytest.raises(ValueError, match="hash"):
        exchange.WorkspaceExport.model_validate_json(json.dumps(payload))


def test_workspace_export_validates_every_record_identity_and_decision_binding(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, _ = runtimes
    source.add_evidence(b"thermal-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    base = exported.model_dump(mode="json")

    identity_mismatch = copy.deepcopy(base)
    identity_mismatch["records"][0]["stable_identity"] = "different-record"
    decision_mismatch = copy.deepcopy(base)
    decision_mismatch["records"][0]["expected_decision"]["proposal_id"] = "different-record"
    replay_marker = copy.deepcopy(base)
    replay_marker["records"][0]["expected_decision"]["replayed"] = True
    policy_mismatch = copy.deepcopy(base)
    policy_mismatch["policies"][0]["policy_hash"] = "0" * 64

    for payload, message in (
        (identity_mismatch, "stable identity"),
        (decision_mismatch, "decision identity"),
        (replay_marker, "replay marker"),
        (policy_mismatch, "policy hash"),
    ):
        with pytest.raises(ValueError, match=message):
            exchange.WorkspaceExport.model_validate_json(json.dumps(payload))


def test_workspace_export_rejects_noncanonical_and_prohibited_bundle_shapes(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, _ = runtimes
    source.add_evidence(b"thermal-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    base = exported.model_dump(mode="json")

    duplicate_policy = copy.deepcopy(base)
    duplicate_policy["policies"].append(copy.deepcopy(duplicate_policy["policies"][0]))
    missing_bootstrap = copy.deepcopy(base)
    missing_bootstrap["bootstrap_policy_hash"] = "0" * 64
    missing_active = copy.deepcopy(base)
    missing_active["active_policy_hash"] = "1" * 64
    duplicate_record = copy.deepcopy(base)
    second_record = copy.deepcopy(duplicate_record["records"][0])
    second_record["replay_order"] = 1
    duplicate_record["records"].append(second_record)
    noncontiguous_replay = copy.deepcopy(base)
    noncontiguous_replay["records"][0]["replay_order"] = 2
    duplicate_projection = copy.deepcopy(base)
    duplicate_projection["projection_expectations"].append(
        copy.deepcopy(duplicate_projection["projection_expectations"][0])
    )
    duplicate_artifact = copy.deepcopy(base)
    duplicate_artifact["artifacts"].append(copy.deepcopy(duplicate_artifact["artifacts"][0]))
    wrong_bundle_hash = copy.deepcopy(base)
    wrong_bundle_hash["bundle_hash"] = "0" * 64
    protected_field = copy.deepcopy(base)
    protected_field["artifacts"][0]["media_type"] = "protected_store"
    _rehash_bundle_payload(protected_field)
    live_path = copy.deepcopy(base)
    live_path["records"][0]["proposal"]["evidence"]["provenance"]["input_file"] = (
        "C:\\private\\thermal.txt"
    )
    live_path["records"][0]["proposal_hash"] = sha256_hex(
        canonical_json_bytes(live_path["records"][0]["proposal"])
    )
    _rehash_bundle_payload(live_path)
    non_path_text = copy.deepcopy(base)
    non_path_text["records"][0]["proposal"]["evidence"]["provenance"]["note"] = (
        "The source cited C:\\private as an example, not as a live path."
    )
    non_path_text["records"][0]["proposal_hash"] = sha256_hex(
        canonical_json_bytes(non_path_text["records"][0]["proposal"])
    )
    _rehash_bundle_payload(non_path_text)

    for payload, message in (
        (duplicate_policy, "policies"),
        (missing_bootstrap, "bootstrap policy"),
        (missing_active, "active policy"),
        (duplicate_record, "records"),
        (noncontiguous_replay, "replay order"),
        (duplicate_projection, "projections"),
        (duplicate_artifact, "artifacts"),
        (wrong_bundle_hash, "bundle hash"),
        (protected_field, "prohibited protected"),
        (live_path, "live path"),
    ):
        with pytest.raises(ValueError, match=message):
            exchange.WorkspaceExport.model_validate_json(json.dumps(payload))

    assert exchange.WorkspaceExport.model_validate_json(json.dumps(non_path_text))


def test_empty_workspace_export_uses_active_policy_as_bootstrap(tmp_path: Path) -> None:
    exchange = _exchange()
    runtime = _runtime(tmp_path, "empty")
    try:
        exported = exchange.export_workspace(
            uow_factory=runtime.uow_factory,
            artifact_store=runtime.artifact_store,
        )

        assert exported.bootstrap_policy_hash == exported.active_policy_hash
        assert exported.records == ()
        assert exported.artifacts == ()
    finally:
        runtime.engine.dispose()


def test_workspace_export_requires_an_active_policy(tmp_path: Path) -> None:
    exchange = _exchange()
    database_url = f"sqlite:///{(tmp_path / 'uninitialized.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    try:
        with pytest.raises(ValueError, match="active policy"):
            exchange.export_workspace(
                uow_factory=uow_factory,
                artifact_store=FileArtifactStore(tmp_path / "uninitialized-artifacts"),
            )
    finally:
        engine.dispose()


def test_workspace_import_rejects_a_target_with_an_unrelated_bootstrap_policy(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source")
    different_policy = GovernancePolicy(required_claim_checks=("evidence_span_exists",))
    different_payload = different_policy.model_dump(mode="json")
    different_payload["human_approval_for"] = sorted(different_policy.human_approval_for)
    target = _runtime(
        tmp_path,
        "target",
        policy_snapshot=PolicySnapshot(
            policy_hash=sha256_hex(canonical_json_bytes(different_payload)),
            policy=different_policy,
        ),
    )
    try:
        source.add_evidence(b"thermal-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )

        with pytest.raises(exchange.WorkspaceImportError, match="bootstrap policy"):
            exchange.import_workspace(
                exported,
                uow_factory=target.uow_factory,
                artifact_store=target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_import_rejects_artifact_reference_changes(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, target = runtimes
    source.add_evidence(b"thermal-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )

    with pytest.raises(exchange.WorkspaceImportError, match="artifact transfer"):
        exchange.import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=ChangingArtifactStore(target.artifact_store),
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )


def test_workspace_import_removes_artifact_written_before_reference_change(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source-changing-file")
    target = _runtime(tmp_path, "target-changing-file")
    target_artifact_root = tmp_path / "changing-file-artifacts"
    changing_store = ChangingFileArtifactStore(target_artifact_root)
    try:
        source.add_evidence(b"thermal-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        before = _durable_snapshot(target, target_artifact_root)

        with pytest.raises(exchange.WorkspaceImportError, match="canonical reference"):
            exchange.import_workspace(
                exported,
                uow_factory=target.uow_factory,
                artifact_store=changing_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )

        assert _durable_snapshot(target, target_artifact_root) == before
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_import_wraps_artifact_read_failure_without_mutating_target(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source-unreadable")
    target = _runtime(tmp_path, "target-unreadable")
    target_artifact_root = tmp_path / "target-unreadable-artifacts"
    try:
        source.add_evidence(b"thermal-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        before = _durable_snapshot(target, target_artifact_root)

        with pytest.raises(exchange.WorkspaceImportError, match="artifact transfer failed"):
            exchange.import_workspace(
                exported,
                uow_factory=target.uow_factory,
                artifact_store=target.artifact_store,
                source_artifact_store=UnreadableArtifactStore(source.artifact_store),
                clock=FixedClock(),
            )

        assert _durable_snapshot(target, target_artifact_root) == before
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_import_checks_expected_decisions_on_import_and_replay(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source")
    first_target = _runtime(tmp_path, "first-target")
    replay_target = _runtime(tmp_path, "replay-target")
    try:
        source.add_evidence(b"thermal-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        payload = exported.model_dump(mode="json")
        payload["records"][0]["expected_decision"].update(
            {
                "accepted": False,
                "reasons": [
                    {
                        "code": RejectionCode.INVALID_PROPOSAL.value,
                        "message": "tampered expected decision",
                    }
                ],
            }
        )
        changed_expectation = exchange.WorkspaceExport.model_validate_json(
            json.dumps(_rehash_bundle_payload(payload))
        )

        with pytest.raises(exchange.WorkspaceImportError, match="imported decision"):
            exchange.import_workspace(
                changed_expectation,
                uow_factory=first_target.uow_factory,
                artifact_store=first_target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )
        exchange.import_workspace(
            exported,
            uow_factory=replay_target.uow_factory,
            artifact_store=replay_target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )
        with pytest.raises(exchange.WorkspaceImportError, match="replayed decision"):
            exchange.import_workspace(
                changed_expectation,
                uow_factory=replay_target.uow_factory,
                artifact_store=replay_target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )
    finally:
        source.engine.dispose()
        first_target.engine.dispose()
        replay_target.engine.dispose()


def test_workspace_import_rejects_unprojected_artifact_metadata(tmp_path: Path) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source")
    target = _runtime(tmp_path, "target")
    try:
        source.add_evidence(b"thermal-observation")
        orphan = source.artifact_store.put(b"orphan-artifact", "text/plain")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        payload = exported.model_dump(mode="json")
        payload["artifacts"].append(
            {
                "schema_version": 1,
                "sha256": orphan.sha256,
                "size_bytes": orphan.size_bytes,
                "media_type": orphan.media_type,
            }
        )
        payload["artifacts"].sort(
            key=lambda item: (item["sha256"], item["size_bytes"], item["media_type"])
        )
        changed_artifacts = exchange.WorkspaceExport.model_validate_json(
            json.dumps(_rehash_bundle_payload(payload))
        )

        with pytest.raises(exchange.WorkspaceImportError, match="does not match"):
            exchange.import_workspace(
                changed_artifacts,
                uow_factory=target.uow_factory,
                artifact_store=target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_changed_import_under_existing_identity_is_an_audited_conflict(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source")
    changed = _runtime(tmp_path, "changed")
    target = _runtime(tmp_path, "target")
    try:
        source.add_evidence(b"thermal-observation")
        changed.add_evidence(b"changed-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        changed_export = exchange.export_workspace(
            uow_factory=changed.uow_factory,
            artifact_store=changed.artifact_store,
        )
        first = exchange.import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )
        conflict = exchange.import_workspace(
            changed_export,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=changed.artifact_store,
            clock=FixedClock(),
        )

        assert first.conflicts == ()
        assert len(conflict.conflicts) == 1
        assert conflict.conflicts[0].stable_identity == "workspace-record-1"
        assert conflict.conflicts[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
        with target.uow_factory() as unit_of_work:
            audit = unit_of_work.repositories().audit.list_all()[-1]
            assert audit.payload["decision"]["accepted"] is False
            assert audit.payload["decision"]["reasons"][0]["code"] == (
                RejectionCode.IDEMPOTENCY_CONFLICT.value
            )
    finally:
        source.engine.dispose()
        changed.engine.dispose()
        target.engine.dispose()


def test_workspace_import_rejects_unreconstructed_preflight_target(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _exchange()
    source, target = runtimes
    source.add_evidence(b"preflight-clone-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    unreconstructed = exchange.WorkspaceImportResult(
        imported=0,
        replayed=0,
        conflicts=(),
        projections_verified=False,
    )
    monkeypatch.setattr(
        exchange,
        "_commit_workspace",
        lambda *args, **kwargs: unreconstructed,
    )

    with pytest.raises(exchange.WorkspaceImportError, match="preflight clone"):
        exchange.import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )


def test_workspace_import_rejects_preflight_to_commit_result_drift(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source-drift")
    target = _runtime(tmp_path, "target-drift")
    artifact_root = tmp_path / "target-drift-artifacts"
    source.add_evidence(b"commit-drift-observation")
    collision_artifact = target.artifact_store.put(b"target-collision", "text/plain")
    collision = AddEvidence(
        proposal_id="workspace-record-1",
        idempotency_key="workspace-record-key-1",
        proposer=target.actor,
        evidence=EvidenceRecord(
            evidence_id="target-collision-evidence",
            evidence_type="synthetic_observation",
            source_locator="fixture://target-collision",
            retrieved_at=NOW,
            artifact=collision_artifact,
            provenance={"collector": "workspace-drift-test"},
            ingestion_actor_id=target.actor.actor_id,
        ),
    )
    target_uow_calls = 0

    def drifting_uow_factory() -> DatabaseUnitOfWork:
        nonlocal target_uow_calls
        target_uow_calls += 1
        if target_uow_calls == 2:
            return CollisionInjectingUnitOfWork(
                target.engine,
                target.coordinator,
                collision,
            )
        return DatabaseUnitOfWork(target.engine)

    try:
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        before = _durable_snapshot(target, artifact_root)

        with pytest.raises(
            exchange.WorkspaceImportError,
            match="changed after successful preflight",
        ):
            exchange.import_workspace(
                exported,
                uow_factory=drifting_uow_factory,
                artifact_store=target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )

        assert target_uow_calls == 2
        assert _durable_snapshot(target, artifact_root) == before
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_conflict_commit_rejects_identity_and_decision_drift(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, target = runtimes
    source.add_evidence(b"conflict-drift-observation")
    exported = exchange.export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )

    missing_identity = (
        exchange.WorkspaceImportConflict(
            stable_identity="absent-workspace-record",
            code=RejectionCode.IDEMPOTENCY_CONFLICT,
        ),
    )
    with pytest.raises(exchange.WorkspaceImportError, match="committed conflicts changed"):
        exchange._commit_conflicts(
            exported,
            expected=missing_identity,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            clock=FixedClock(),
        )

    changed_decision = (
        exchange.WorkspaceImportConflict(
            stable_identity=exported.records[0].stable_identity,
            code=RejectionCode.IDEMPOTENCY_CONFLICT,
        ),
    )
    with pytest.raises(exchange.WorkspaceImportError, match="conflict decision changed"):
        exchange._commit_conflicts(
            exported,
            expected=changed_decision,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            clock=FixedClock(),
        )


def test_workspace_replay_order_follows_audit_sequence_for_same_timestamp_dependencies(
    tmp_path: Path,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source")
    target = _runtime(tmp_path, "target")
    try:
        artifact = source.artifact_store.put(b"ordered evidence", "text/plain")
        evidence = EvidenceRecord(
            evidence_id="ordered-evidence",
            evidence_type="synthetic_observation",
            source_locator="fixture://ordered-evidence",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={"collector": "workspace-order-test"},
            extracted_span=EvidenceSpan(
                start=0,
                end=len("ordered evidence"),
                text="ordered evidence",
            ),
            ingestion_actor_id=source.actor.actor_id,
        )
        assert source.coordinator.submit(
            AddEvidence(
                proposal_id="z-evidence",
                idempotency_key="z-evidence-key",
                proposer=source.actor,
                evidence=evidence,
            )
        ).accepted
        claim = AtomicClaim(
            claim_id="ordered-claim",
            version=1,
            proposition="The ordered evidence supports this claim.",
            scope="fixture",
            population_or_system="fixture system",
            epistemic_modality="observed",
            status=ClaimStatus.PROPOSED,
            created_at=NOW,
            created_by=source.actor.actor_id,
        )
        assert source.coordinator.submit(
            ProposeClaim(
                proposal_id="m-claim",
                idempotency_key="m-claim-key",
                proposer=source.actor,
                claim=claim,
            )
        ).accepted
        assert source.coordinator.submit(
            TransitionClaim(
                proposal_id="a-transition",
                idempotency_key="a-transition-key",
                proposer=source.actor,
                next_claim=claim.model_copy(
                    update={
                        "version": 2,
                        "status": ClaimStatus.EVIDENCE_LINKED,
                        "evidence_links": (
                            EvidenceLink(
                                evidence_id=evidence.evidence_id,
                                supporting_span="ordered evidence",
                            ),
                        ),
                        "parent_version_id": "ordered-claim:1",
                    }
                ),
            )
        ).accepted

        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        replay_order = {record.stable_identity: record.replay_order for record in exported.records}
        imported = exchange.import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert tuple(record.stable_identity for record in exported.records) == (
            "a-transition",
            "m-claim",
            "z-evidence",
        )
        assert replay_order == {"z-evidence": 0, "m-claim": 1, "a-transition": 2}
        assert imported.imported == 3
        assert imported.projections_verified is True
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_audit_order_rejects_missing_or_ambiguous_transaction_identity(
    runtimes: tuple[ExchangeRuntime, ExchangeRuntime],
) -> None:
    exchange = _exchange()
    source, _ = runtimes
    source.add_evidence(b"audit-order-observation")
    with source.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        event = repositories.audit.list_all()[0]
        transaction = repositories.transactions.list_all()[0]

    ignored_payload = dict(event.payload)
    ignored_payload["transaction_persisted"] = False
    ignored = event.model_copy(update={"payload": ignored_payload})
    malformed_payload = dict(event.payload)
    malformed_payload["proposal"] = {}
    malformed = event.model_copy(update={"payload": malformed_payload})

    assert exchange._transaction_replay_orders((ignored,)) == {}
    with pytest.raises(ValueError, match="stable proposal identity"):
        exchange._transaction_replay_orders((malformed,))
    with pytest.raises(ValueError, match="more than one persisted audit event"):
        exchange._transaction_replay_orders((event, event))
    with pytest.raises(ValueError, match="governing audit event"):
        exchange._governing_hash(transaction, {})
    with pytest.raises(ValueError, match="audit replay order"):
        exchange._replay_order(transaction, {})


@pytest.mark.parametrize("failure_stage", ("expected_decision", "final_equivalence"))
def test_workspace_import_validation_failure_preserves_target_state(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, f"source-{failure_stage}")
    target = _runtime(tmp_path, f"target-{failure_stage}")
    artifact_root = tmp_path / f"target-{failure_stage}-artifacts"
    try:
        source.add_evidence(b"fail-atomic-observation")
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        payload = exported.model_dump(mode="json")
        if failure_stage == "expected_decision":
            payload["records"][0]["expected_decision"].update(
                {
                    "accepted": False,
                    "reasons": [
                        {
                            "code": RejectionCode.INVALID_PROPOSAL.value,
                            "message": "preflight must reject this decision",
                        }
                    ],
                }
            )
            message = "imported decision"
        else:
            payload["projection_expectations"][0]["content_hash"] = "0" * 64
            message = "does not match"
        invalid_bundle = exchange.WorkspaceExport.model_validate_json(
            json.dumps(_rehash_bundle_payload(payload))
        )
        before = _durable_snapshot(target, artifact_root)

        with pytest.raises(exchange.WorkspaceImportError, match=message):
            exchange.import_workspace(
                invalid_bundle,
                uow_factory=target.uow_factory,
                artifact_store=target.artifact_store,
                source_artifact_store=source.artifact_store,
                clock=FixedClock(),
            )

        assert _durable_snapshot(target, artifact_root) == before
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_workspace_import_replays_audited_invalid_proposal(tmp_path: Path) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source-invalid")
    target = _runtime(tmp_path, "target-invalid")
    try:
        attempt = ProposalAttempt(
            proposal_id="invalid-evidence",
            idempotency_key="invalid-evidence-key",
            proposer=source.actor,
            proposal_kind="add_evidence",
            intent_digest="0" * 64,
        )

        def invalid_factory() -> object:
            return AddEvidence.model_validate({})

        decision = source.coordinator.submit_intent(attempt, invalid_factory)
        assert decision.accepted is False
        assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
        exported = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )

        imported = exchange.import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert imported.imported == 1
        assert imported.conflicts == ()
        assert (
            exchange.export_workspace(
                uow_factory=target.uow_factory,
                artifact_store=target.artifact_store,
            )
            == exported
        )
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_changed_content_with_same_proposal_id_is_entity_conflict(tmp_path: Path) -> None:
    exchange = _exchange()
    source = _runtime(tmp_path, "source-original")
    changed = _runtime(tmp_path, "source-changed")
    target = _runtime(tmp_path, "target-entity-conflict")
    try:
        source.add_evidence(b"original-observation")
        changed_artifact = changed.artifact_store.put(
            b"changed-observation",
            "text/plain",
        )
        assert changed.coordinator.submit(
            AddEvidence(
                proposal_id="workspace-record-1",
                idempotency_key="different-idempotency-key",
                proposer=changed.actor,
                evidence=EvidenceRecord(
                    evidence_id="workspace-evidence-1",
                    evidence_type="synthetic_observation",
                    source_locator="fixture://changed-observation",
                    retrieved_at=NOW,
                    artifact=changed_artifact,
                    provenance={"collector": "workspace-exchange-test"},
                    ingestion_actor_id=changed.actor.actor_id,
                ),
            )
        ).accepted
        original = exchange.export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        changed_export = exchange.export_workspace(
            uow_factory=changed.uow_factory,
            artifact_store=changed.artifact_store,
        )
        exchange.import_workspace(
            original,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        conflict = exchange.import_workspace(
            changed_export,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=changed.artifact_store,
            clock=FixedClock(),
        )

        assert conflict.imported == 0
        assert conflict.conflicts[0].stable_identity == "workspace-record-1"
        assert conflict.conflicts[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
        with target.uow_factory() as unit_of_work:
            audit = unit_of_work.repositories().audit.list_all()[-1]
        assert audit.payload["decision"]["reasons"][0]["code"] == (
            RejectionCode.ENTITY_ALREADY_EXISTS.value
        )
    finally:
        source.engine.dispose()
        changed.engine.dispose()
        target.engine.dispose()
