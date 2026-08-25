from __future__ import annotations

from datetime import UTC, datetime

from super_scientist.application.workspace_exchange import (
    WorkspaceProjectionExpectation,
    export_workspace,
    import_workspace,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.transactions.models import Approval, RecordCapabilityProfile
from tests.integration.application.test_cognitive_workspace_integrity import (
    _governed_policy,
    _profile_for_policy,
)
from tests.integration.application.test_workspace_exchange import FixedClock, _runtime

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_cognitive_projection_expectations_remain_schema_one() -> None:
    expectation = WorkspaceProjectionExpectation(
        projection_kind="capability_profile_record",
        stable_identity="profile-1",
        content_hash="a" * 64,
    )

    assert expectation.schema_version == 1


def test_030_bundle_round_trip_preserves_governed_integrity_snapshot(tmp_path) -> None:
    policy = _governed_policy()
    source = _runtime(tmp_path, "cognitive-source", policy_snapshot=policy)
    target = _runtime(tmp_path, "cognitive-target", policy_snapshot=policy)
    proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=ActorIdentity(
            actor_id="source-service",
            kind=ActorKind.SERVICE,
            created_at=NOW,
        ),
        approval=Approval(
            approver=ActorIdentity(
                actor_id="reviewer",
                kind=ActorKind.HUMAN,
                created_at=NOW,
            ),
            approved_at=NOW,
        ),
        profile=_profile_for_policy(policy),
    )
    try:
        assert source.coordinator.submit(proposal).accepted is True
        exported = export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )

        result = import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert result.projections_verified is True
        assert (
            "capability_profile_record",
            proposal.profile.profile_id,
            proposal.profile.content_hash,
        ) in {
            (item.projection_kind, item.stable_identity, item.content_hash)
            for item in exported.projection_expectations
        }
        with source.uow_factory() as source_uow, target.uow_factory() as target_uow:
            assert (
                source_uow.repositories().cognitive_workspace_integrity_snapshot()
                == target_uow.repositories().cognitive_workspace_integrity_snapshot()
            )
    finally:
        source.engine.dispose()
        target.engine.dispose()
