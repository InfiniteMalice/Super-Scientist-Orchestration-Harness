from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from inspect import signature
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.providers.storage import domain_records
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import (
    BehaviorRuleLinkVersionRecord,
    BehaviorRuleLinkVersionRepository,
    HandbookVerificationRecord,
    HandbookVerificationRepository,
    HarnessBudgetRecord,
    HarnessBudgetRepository,
    HarnessCampaignHeadRepository,
    HarnessCampaignRecord,
    HarnessCampaignRepository,
    HarnessConfoundRecord,
    HarnessConfoundRepository,
    HarnessDecisionRecord,
    HarnessDecisionRepository,
    HarnessDecisionStatus,
    HarnessMetricRecord,
    HarnessMetricRepository,
    HarnessObservationRecord,
    HarnessObservationRepository,
    HarnessPartition,
    HarnessPartitionManifestRecord,
    HarnessPartitionManifestRepository,
    HarnessVariant,
    MetricValueRecord,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

AUTHORITATIVE_0006_TABLES = {
    "behavior_rule_link_versions",
    "handbook_verification_records",
    "harness_campaigns",
    "harness_partition_manifests",
    "harness_budgets",
    "harness_observations",
    "harness_metrics",
    "harness_confounds",
    "harness_decisions",
}
REPOSITORIES = (
    BehaviorRuleLinkVersionRepository,
    HandbookVerificationRepository,
    HarnessCampaignRepository,
    HarnessPartitionManifestRepository,
    HarnessBudgetRepository,
    HarnessObservationRepository,
    HarnessMetricRepository,
    HarnessConfoundRepository,
    HarnessDecisionRepository,
)


def test_public_0006_repositories_are_fixed_to_connection_only() -> None:
    for repository_type in REPOSITORIES:
        assert tuple(signature(repository_type).parameters) == ("connection",)
        assert repository_type.__name__ in domain_records.__all__
    assert "_AppendOnlyRecordRepository" not in domain_records.__all__


def test_0006_storage_records_are_strict_frozen_and_reject_answer_fields() -> None:
    records = _records()
    for record in records:
        assert record.model_config.get("frozen") is True
        with pytest.raises(ValidationError):
            type(record).model_validate(record.model_dump() | {"answer_reference": "forbidden"})


def test_0006_records_reject_duplicate_relationships_nonfinite_metrics_and_status_drift() -> None:
    records = _records()
    handbook = records[1]
    campaign = records[2]
    partition = records[3]
    budget = records[4]
    metric = records[6]
    decision = records[8]
    assert isinstance(handbook, HandbookVerificationRecord)
    assert isinstance(campaign, HarnessCampaignRecord)
    assert isinstance(partition, HarnessPartitionManifestRecord)
    assert isinstance(budget, HarnessBudgetRecord)
    assert isinstance(metric, HarnessMetricRecord)
    assert isinstance(decision, HarnessDecisionRecord)

    invalid_payloads = (
        (handbook, {"source_hashes": ("3" * 64, "3" * 64)}),
        (
            campaign,
            {
                "variants": (
                    HarnessVariant.EVOLVED_HARNESS,
                    HarnessVariant.EVOLVED_HARNESS,
                )
            },
        ),
        (partition, {"task_ids": ("task-1", "task-1")}),
        (budget, {"tool_ids": ("tool-1", "tool-1")}),
        (
            metric,
            {
                "metric_values": (
                    MetricValueRecord(metric_id="correctness", value=Decimal("1")),
                    MetricValueRecord(metric_id="correctness", value=Decimal("0")),
                )
            },
        ),
        (decision, {"admitted": True}),
    )
    for record, update in invalid_payloads:
        with pytest.raises(ValidationError):
            type(record).model_validate(record.model_dump(mode="python") | update)

    with pytest.raises(ValidationError):
        MetricValueRecord(metric_id="invalid", value=Decimal("NaN"))


@pytest.mark.integration
def test_ordinary_repositories_round_trip_hash_only_harness_history(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "round-trip.db")
    try:
        records = _records()
        _add_records(connection, records)
        for repository_type, record in zip(REPOSITORIES, records, strict=True):
            identifier = _identifier(record)
            assert repository_type(connection).get(identifier) == record
        serialized = b"\n".join(record.model_dump_json().encode() for record in records)
        assert b'"expected_output":' not in serialized
        assert b"answer" not in serialized
        connection.commit()
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_every_0006_authoritative_table_is_append_only(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "append-only.db")
    try:
        records = _records()
        _add_records(connection, records)
        for table_name in sorted(AUTHORITATIVE_0006_TABLES):
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"UPDATE {table_name} SET created_at = created_at"))
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"DELETE FROM {table_name}"))
        connection.commit()
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decoder_rejects_unknown_fields_and_hash_tampering(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "tamper.db")
    try:
        campaign = _records()[2]
        assert isinstance(campaign, HarnessCampaignRecord)
        HarnessCampaignRepository(connection).add(
            campaign.campaign_id,
            campaign,
            campaign.created_at,
        )
        connection.exec_driver_sql("DROP TRIGGER harness_campaigns_no_update")
        connection.execute(
            text(
                "UPDATE harness_campaigns SET record_json = "
                "json_set(record_json, '$.answer_reference', 'forbidden')"
            )
        )
        with pytest.raises(StorageIntegrityError, match="invalid record JSON"):
            HarnessCampaignRepository(connection).get(campaign.campaign_id)
        connection.execute(
            text(
                "UPDATE harness_campaigns SET record_json = :json, content_hash = :hash "
                "WHERE campaign_id = :campaign"
            ),
            {
                "json": campaign.model_dump_json(),
                "hash": "0" * 64,
                "campaign": campaign.campaign_id,
            },
        )
        with pytest.raises(StorageIntegrityError, match="content_hash"):
            HarnessCampaignRepository(connection).get(campaign.campaign_id)
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_campaign_head_is_rebuildable_and_exactly_bound_to_decisions(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "campaign-head.db")
    try:
        campaign = _records()[2]
        initial = _records()[8]
        assert isinstance(campaign, HarnessCampaignRecord)
        assert isinstance(initial, HarnessDecisionRecord)
        HarnessCampaignRepository(connection).add(
            campaign.campaign_id,
            campaign,
            campaign.created_at,
        )
        decisions = HarnessDecisionRepository(connection)
        decisions.add(initial.decision_id, initial, initial.decided_at)
        heads = HarnessCampaignHeadRepository(connection)
        heads.set(campaign.campaign_id, initial.decision_id, initial.status)
        assert heads.get(campaign.campaign_id) == (initial.decision_id, initial.status)
        assert heads.list_all() == ((campaign.campaign_id, initial.decision_id, initial.status),)

        successor = initial.model_copy(
            update={
                "decision_id": "decision-2",
                "status": HarnessDecisionStatus.REJECTED,
            }
        )
        decisions.add(successor.decision_id, successor, successor.decided_at)
        heads.set(campaign.campaign_id, successor.decision_id, successor.status)
        assert heads.get(campaign.campaign_id) == (successor.decision_id, successor.status)

        with pytest.raises(StorageIntegrityError, match="does not exist"):
            heads.set(campaign.campaign_id, "missing-decision", HarnessDecisionStatus.REJECTED)
        with pytest.raises(StorageIntegrityError, match="another campaign"):
            heads.set("other-campaign", successor.decision_id, successor.status)
        with pytest.raises(StorageIntegrityError, match="does not match"):
            heads.set(campaign.campaign_id, successor.decision_id, HarnessDecisionStatus.PROPOSED)
        connection.commit()
    finally:
        connection.close()
        engine.dispose()


def _records() -> tuple[object, ...]:
    timestamp = datetime(2026, 7, 20, tzinfo=UTC)
    policy_hash = "c" * 64
    link = BehaviorRuleLinkVersionRecord(
        link_version_id="link-v1",
        behavior_id="behavior-1",
        version=1,
        rule_version_id="rule-v1",
        manifest_hash="1" * 64,
        created_by="human-1",
        created_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    handbook = HandbookVerificationRecord(
        verification_id="handbook-verification-1",
        manifest_hash="1" * 64,
        repository_commit="2" * 64,
        source_hashes=("3" * 64,),
        generated_artifact_hash="4" * 64,
        stale_locations=(),
        missing_symbols=(),
        outcome=AssessmentOutcome.PASSED,
        verified_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    campaign = HarnessCampaignRecord(
        campaign_id="campaign-1",
        version=1,
        variants=(HarnessVariant.EVOLVED_HARNESS,),
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        created_by="human-1",
        created_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    partition = HarnessPartitionManifestRecord(
        partition_manifest_id="partition-1",
        campaign_id="campaign-1",
        partition=HarnessPartition.HARNESS_SAFETY_TASKS,
        task_ids=("task-1",),
        manifest_hash="5" * 64,
        protected_content_hash="a" * 64,
        created_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    budget = HarnessBudgetRecord(
        budget_id="budget-1",
        campaign_id="campaign-1",
        variant=HarnessVariant.EVOLVED_HARNESS,
        budget_hash="6" * 64,
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        feedback_mode="NONE",
        tool_ids=(),
        attempts=1,
        token_limit=100,
        reasoning_limit=100,
        evaluator_call_limit=1,
        wall_clock_seconds=Decimal("10"),
        cost_limit=Decimal("0"),
        human_intervention_limit=0,
        created_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    observation = HarnessObservationRecord(
        observation_id="observation-1",
        campaign_id="campaign-1",
        partition_manifest_id="partition-1",
        task_id="task-1",
        variant=HarnessVariant.EVOLVED_HARNESS,
        candidate_output_hash="b" * 64,
        attempt=1,
        negative_result=False,
        observed_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    metric = HarnessMetricRecord(
        result_id="result-1",
        campaign_id="campaign-1",
        task_id="task-1",
        expected_output_hash="a" * 64,
        candidate_output_hash="b" * 64,
        checker_id="checker-1",
        checker_version="checker-v1",
        outcome=AssessmentOutcome.PASSED,
        metric_values=(MetricValueRecord(metric_id="correctness", value=Decimal("1.0")),),
        evaluated_at=timestamp,
    )
    confound = HarnessConfoundRecord(
        confound_id="confound-1",
        campaign_id="campaign-1",
        code="EVALUATOR_CHANGED",
        description="Evaluator version differs.",
        affected_variant=HarnessVariant.EVOLVED_HARNESS,
        recorded_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    decision = HarnessDecisionRecord(
        decision_id="decision-1",
        campaign_id="campaign-1",
        status=HarnessDecisionStatus.INCONCLUSIVE,
        admitted=False,
        rationale=("Protected metrics are not yet sufficient.",),
        authority_id="human-1",
        decided_at=timestamp,
        governing_policy_hash=policy_hash,
    )
    return (link, handbook, campaign, partition, budget, observation, metric, confound, decision)


def _add_records(connection: Connection, records: tuple[object, ...]) -> None:
    _seed_rule_version(connection)
    for repository_type, record in zip(REPOSITORIES, records, strict=True):
        identifier = _identifier(record)
        timestamp_fields = {
            "created_at",
            "verified_at",
            "observed_at",
            "evaluated_at",
            "recorded_at",
            "decided_at",
        }
        created_at = next(
            value for field_name, value in vars(record).items() if field_name in timestamp_fields
        )
        repository_type(connection).add(identifier, record, created_at)


def _identifier(record: object) -> str:
    for field_name in (
        "link_version_id",
        "verification_id",
        "observation_id",
        "result_id",
        "confound_id",
        "decision_id",
        "partition_manifest_id",
        "budget_id",
        "campaign_id",
    ):
        value = getattr(record, field_name, None)
        if isinstance(value, str):
            return value
    raise AssertionError("record has no stable identifier")


def _seed_rule_version(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO rule_incidents "
            "(incident_id, record_json, content_hash, created_at) VALUES "
            "('incident-1', '{}', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'2026-07-20T00:00:00+00:00')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO behavioral_rule_versions "
            "(rule_version_id, rule_id, semantic_version, status, record_json, content_hash, "
            "created_at) VALUES ('rule-v1', 'rule-1', '1.0.0', 'PROPOSED', '{}', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'2026-07-20T00:00:00+00:00')"
        )
    )


def _connection(tmp_path: Path, name: str) -> tuple[object, Connection]:
    database_url = f"sqlite+pysqlite:///{(tmp_path / name).as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    return engine, engine.connect()
