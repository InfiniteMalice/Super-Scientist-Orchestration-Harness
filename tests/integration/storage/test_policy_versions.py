from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import governance_policies

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
pytestmark = pytest.mark.integration


def test_policy_repository_decodes_mixed_history(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    v1_snapshot = _snapshot(GovernancePolicy(required_claim_checks=("source_exists",)))
    v2_snapshot = _snapshot(_v2_policy())

    with DatabaseUnitOfWork(engine) as unit_of_work:
        policies = unit_of_work.repositories().policies
        policies.add_and_activate(v1_snapshot, NOW)
        policies.add_and_activate(v2_snapshot, NOW.replace(second=1))

        assert [item.policy.schema_version for item in policies.list_all()] == [1, 2]
        assert policies.get_active() == v2_snapshot
    engine.dispose()


@pytest.mark.parametrize(
    "policy_json",
    [
        "{not json",
        '{"schema_version":3,"required_claim_checks":["source_exists"]}',
        '{"schema_version":1,"required_claim_checks":["source_exists"],"unexpected":true}',
    ],
    ids=["corrupt-json", "unknown-version", "unknown-field"],
)
def test_policy_repository_fails_closed_on_invalid_stored_policy(
    tmp_path: Path,
    policy_json: str,
) -> None:
    engine = _engine(tmp_path)
    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            insert(governance_policies).values(
                policy_hash="f" * 64,
                policy_json=policy_json,
                created_at=NOW.isoformat(),
            )
        )
        with pytest.raises(StorageIntegrityError, match="governance policy"):
            unit_of_work.repositories().policies.list_all()
    engine.dispose()


def test_policy_repository_rejects_stored_hash_mismatch_for_v2(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    policy = _v2_policy()
    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            insert(governance_policies).values(
                policy_hash="f" * 64,
                policy_json=policy.model_dump_json(),
                created_at=NOW.isoformat(),
            )
        )
        with pytest.raises(StorageIntegrityError, match="policy_hash"):
            unit_of_work.repositories().policies.list_all()
    engine.dispose()


@pytest.mark.parametrize("reverse", (False, True))
def test_policy_repository_rejects_duplicate_v2_requirement_keys(
    tmp_path: Path,
    reverse: bool,
) -> None:
    engine = _engine(tmp_path)
    payload = _v2_policy().model_dump(mode="json")
    duplicate = dict(payload["adaptation_requirements"][0])
    duplicate["minimum_verification"] = VerificationLevel.FORMAL_VERIFIER.value
    duplicate["permitted_grounding"] = [ExternalGrounding.FORMAL_SYSTEM.value]
    requirements = [payload["adaptation_requirements"][0], duplicate]
    if reverse:
        requirements.reverse()
    payload["adaptation_requirements"] = requirements
    policy_json = canonical_json_bytes(payload).decode("utf-8")
    with DatabaseUnitOfWork(engine) as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            insert(governance_policies).values(
                policy_hash=sha256_hex(policy_json.encode("utf-8")),
                policy_json=policy_json,
                created_at=NOW.isoformat(),
            )
        )
        with pytest.raises(StorageIntegrityError, match="governance policy"):
            unit_of_work.repositories().policies.list_all()
    engine.dispose()


def _engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'policies.db').as_posix()}"
    upgrade_database(url)
    return create_database_engine(url)


def _snapshot(policy: GovernancePolicy | GovernancePolicyV2) -> PolicySnapshot:
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _v2_policy() -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"governance_change"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.GOVERNANCE_POLICY,
                persistence=PersistenceScope.GOVERNANCE_POLICY,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )
