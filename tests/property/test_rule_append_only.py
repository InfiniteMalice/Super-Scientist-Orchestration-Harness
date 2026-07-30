from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ConflictClassification,
    OverlapClassification,
    ReviewerAssessment,
    ReviewerRole,
    RuleAction,
    RuleAuthority,
    RuleConsolidationDecision,
    RuleIncident,
    RuleIncidentKind,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.providers.storage import domain_records, schema
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    BehavioralRuleHeadRepository,
    BehavioralRuleVersionRepository,
    ReviewerAssessmentRepository,
    RuleConsolidationDecisionRepository,
    RuleIncidentRepository,
    RuleRegressionCaseRepository,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64

AUTHORITATIVE_0004_TABLES = {
    "rule_incidents",
    "behavioral_rule_versions",
    "reviewer_assessments",
    "rule_consolidation_decisions",
    "rule_regression_cases",
    "behavioral_rule_version_incidents",
    "behavioral_rule_version_supersessions",
    "reviewer_assessment_rule_versions",
    "reviewer_assessment_incidents",
    "rule_consolidation_assessments",
    "rule_consolidation_incidents",
    "rule_regression_case_incidents",
}

RULE_REPOSITORIES = (
    RuleIncidentRepository,
    BehavioralRuleVersionRepository,
    ReviewerAssessmentRepository,
    RuleConsolidationDecisionRepository,
    RuleRegressionCaseRepository,
)


def test_public_rule_repositories_are_fixed_to_connection_only() -> None:
    for repository_type in RULE_REPOSITORIES:
        assert tuple(signature(repository_type).parameters) == ("connection",)
        assert repository_type.__name__ in domain_records.__all__
    assert tuple(signature(BehavioralRuleHeadRepository).parameters) == ("connection",)
    assert "BehavioralRuleHeadRepository" in domain_records.__all__
    assert "_AppendOnlyRecordRepository" not in domain_records.__all__


@pytest.mark.integration
def test_rule_repositories_round_trip_history_and_exact_reference_order(
    tmp_path: Path,
) -> None:
    engine, connection = _connection(tmp_path, "round-trip.db")
    records = _records()
    rejection = records.decision.model_copy(
        update={
            "consolidation_decision_id": "decision-2",
            "resulting_rule_version_id": None,
            "action": RuleAction.REJECT,
        }
    )
    try:
        _add_records(connection, records)
        RuleConsolidationDecisionRepository(connection).add(
            rejection.consolidation_decision_id,
            rejection,
            rejection.decided_at,
        )

        assert RuleIncidentRepository(connection).list_all() == records.incidents
        assert BehavioralRuleVersionRepository(connection).get("rule-1-v1") == records.rule
        assert ReviewerAssessmentRepository(connection).get("assessment-1") == records.assessment
        assert RuleConsolidationDecisionRepository(connection).get("decision-1") == records.decision
        assert RuleConsolidationDecisionRepository(connection).get("decision-2") == rejection
        assert RuleRegressionCaseRepository(connection).get("regression-1") == records.regression

        assert _ordered_references(
            connection,
            schema.behavioral_rule_version_incidents,
            "rule_version_id",
            "rule-1-v1",
            "incident_id",
        ) == ("incident-1", "incident-2")
        assert _ordered_references(
            connection,
            schema.rule_consolidation_assessments,
            "consolidation_decision_id",
            "decision-1",
            "assessment_id",
        ) == ("assessment-1",)
        assert _ordered_references(
            connection,
            schema.rule_consolidation_incidents,
            "consolidation_decision_id",
            "decision-1",
            "incident_id",
        ) == ("incident-2", "incident-1")
        assert (
            connection.execute(
                select(schema.rule_consolidation_decisions.c.resulting_rule_version_id).where(
                    schema.rule_consolidation_decisions.c.consolidation_decision_id == "decision-1"
                )
            ).scalar_one()
            == "rule-1-v1"
        )
        assert (
            connection.execute(
                select(schema.rule_consolidation_decisions.c.resulting_rule_version_id).where(
                    schema.rule_consolidation_decisions.c.consolidation_decision_id == "decision-2"
                )
            ).scalar_one()
            is None
        )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_rule_version_supersessions_round_trip_in_canonical_order(
    tmp_path: Path,
) -> None:
    engine, connection = _connection(tmp_path, "supersession-order.db")
    records = _records()
    predecessor_one, predecessor_two, successor, _ = _lineage_rules(records.rule)
    try:
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id,
                incident,
                incident.recorded_at,
            )
        repository = BehavioralRuleVersionRepository(connection)
        for rule in (predecessor_one, predecessor_two, successor):
            repository.add(rule.rule_version_id, rule, rule.created_at)

        assert repository.get(successor.rule_version_id) == successor
        assert (
            _ordered_references(
                connection,
                schema.behavioral_rule_version_supersessions,
                "rule_version_id",
                successor.rule_version_id,
                "predecessor_rule_version_id",
            )
            == successor.supersedes_rule_version_ids
        )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_every_rule_history_and_reference_table_is_append_only(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "append-only.db")
    try:
        records = _records()
        _add_records(connection, records)
        predecessor_one, predecessor_two, successor, _ = _lineage_rules(records.rule)
        successor = successor.model_copy(
            update={
                "rule_version_id": "rule-1-v2",
                "semantic_version": "1.1.0",
            }
        )
        repository = BehavioralRuleVersionRepository(connection)
        for rule in (predecessor_one, predecessor_two, successor):
            repository.add(rule.rule_version_id, rule, rule.created_at)
        for table_name in sorted(AUTHORITATIVE_0004_TABLES):
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"UPDATE {table_name} SET rowid = rowid"))
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"DELETE FROM {table_name}"))
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_repositories_reject_missing_incident_and_assessment_references(
    tmp_path: Path,
) -> None:
    engine, connection = _connection(tmp_path, "missing-references.db")
    records = _records()
    try:
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id, incident, incident.recorded_at
            )
        BehavioralRuleVersionRepository(connection).add(
            records.rule.rule_version_id,
            records.rule,
            records.rule.created_at,
        )

        missing_assessment = records.decision.model_copy(
            update={"consumed_assessment_ids": ("missing-assessment",)}
        )
        with pytest.raises(IntegrityError):
            RuleConsolidationDecisionRepository(connection).add(
                missing_assessment.consolidation_decision_id,
                missing_assessment,
                missing_assessment.decided_at,
            )
        connection.rollback()

        connection.close()
        engine.dispose()
        engine, connection = _connection(tmp_path, "missing-incident.db")
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id, incident, incident.recorded_at
            )
        BehavioralRuleVersionRepository(connection).add(
            records.rule.rule_version_id,
            records.rule,
            records.rule.created_at,
        )
        missing_incident = records.regression.model_copy(
            update={"incident_ids": ("missing-incident",)}
        )
        with pytest.raises(IntegrityError):
            RuleRegressionCaseRepository(connection).add(
                missing_incident.regression_case_id,
                missing_incident,
                missing_incident.created_at,
            )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_rule_repository_rejects_missing_superseded_rule_version(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "missing-supersession.db")
    records = _records()
    successor = records.rule.model_copy(
        update={"supersedes_rule_version_ids": ("missing-rule-version",)}
    )
    try:
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id,
                incident,
                incident.recorded_at,
            )
        with pytest.raises(IntegrityError):
            BehavioralRuleVersionRepository(connection).add(
                successor.rule_version_id,
                successor,
                successor.created_at,
            )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decision_repository_rejects_missing_resulting_rule_version(
    tmp_path: Path,
) -> None:
    engine, connection = _connection(tmp_path, "missing-result.db")
    records = _records()
    decision = records.decision.model_copy(
        update={"resulting_rule_version_id": "missing-rule-version"}
    )
    try:
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id,
                incident,
                incident.recorded_at,
            )
        BehavioralRuleVersionRepository(connection).add(
            records.rule.rule_version_id,
            records.rule,
            records.rule.created_at,
        )
        ReviewerAssessmentRepository(connection).add(
            records.assessment.assessment_id,
            records.assessment,
            records.assessment.provenance.assessed_at,
        )
        with pytest.raises(IntegrityError):
            RuleConsolidationDecisionRepository(connection).add(
                decision.consolidation_decision_id,
                decision,
                decision.decided_at,
            )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decoder_rejects_materialized_reference_drift(tmp_path: Path) -> None:
    database_path = tmp_path / "reference-drift.db"
    engine, connection = _connection_for_path(database_path)
    records = _records()
    third_incident = records.incidents[0].model_copy(
        update={"incident_id": "incident-3", "summary": "Third retained incident"}
    )
    try:
        _add_records(connection, records)
        RuleIncidentRepository(connection).add(
            third_incident.incident_id,
            third_incident,
            third_incident.recorded_at,
        )
        connection.commit()
    finally:
        connection.close()
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute("DROP TRIGGER rule_consolidation_incidents_no_update")
        raw_connection.execute(
            "UPDATE rule_consolidation_incidents SET incident_id = 'incident-3' "
            "WHERE consolidation_decision_id = 'decision-1' AND position = 0"
        )

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        with pytest.raises(StorageIntegrityError, match="exact canonical references"):
            RuleConsolidationDecisionRepository(connection).get("decision-1")
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decoder_rejects_resulting_rule_version_drift(tmp_path: Path) -> None:
    database_path = tmp_path / "resulting-rule-drift.db"
    engine, connection = _connection_for_path(database_path)
    records = _records()
    _, _, _, spare = _lineage_rules(records.rule)
    try:
        _add_records(connection, records)
        BehavioralRuleVersionRepository(connection).add(
            spare.rule_version_id,
            spare,
            spare.created_at,
        )
        connection.commit()
    finally:
        connection.close()
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute("DROP TRIGGER rule_consolidation_decisions_no_update")
        raw_connection.execute(
            "UPDATE rule_consolidation_decisions "
            "SET resulting_rule_version_id = 'rule-spare-v1' "
            "WHERE consolidation_decision_id = 'decision-1'"
        )

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        with pytest.raises(StorageIntegrityError, match="does not match record_json"):
            RuleConsolidationDecisionRepository(connection).get("decision-1")
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "tamper_sql",
    (
        "UPDATE behavioral_rule_version_supersessions "
        "SET predecessor_rule_version_id = 'rule-spare-v1' "
        "WHERE rule_version_id = 'rule-1-v1' AND position = 0",
        "UPDATE behavioral_rule_version_supersessions SET position = 2 "
        "WHERE rule_version_id = 'rule-1-v1' AND position = 1",
    ),
    ids=("reference", "position-gap"),
)
def test_decoder_rejects_supersession_reference_drift(
    tmp_path: Path,
    tamper_sql: str,
) -> None:
    database_path = tmp_path / "supersession-drift.db"
    engine, connection = _connection_for_path(database_path)
    records = _records()
    predecessor_one, predecessor_two, successor, spare = _lineage_rules(records.rule)
    try:
        for incident in records.incidents:
            RuleIncidentRepository(connection).add(
                incident.incident_id,
                incident,
                incident.recorded_at,
            )
        repository = BehavioralRuleVersionRepository(connection)
        for rule in (predecessor_one, predecessor_two, spare, successor):
            repository.add(rule.rule_version_id, rule, rule.created_at)
        connection.commit()
    finally:
        connection.close()
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute("DROP TRIGGER behavioral_rule_version_supersessions_no_update")
        raw_connection.execute(tamper_sql)

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        with pytest.raises(StorageIntegrityError, match="exact canonical references"):
            BehavioralRuleVersionRepository(connection).get(successor.rule_version_id)
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decoder_rejects_unknown_fields_and_content_hash_tampering(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "corrupt-record.db")
    records = _records()
    try:
        _add_records(connection, records)
        connection.exec_driver_sql("DROP TRIGGER rule_incidents_no_update")
        connection.execute(
            text(
                "UPDATE rule_incidents SET record_json = :record_json "
                "WHERE incident_id = 'incident-1'"
            ),
            {
                "record_json": records.incidents[0].model_dump_json(exclude_none=False)[:-1]
                + ',"x":1}'
            },
        )
        with pytest.raises(StorageIntegrityError, match="invalid record JSON"):
            RuleIncidentRepository(connection).get("incident-1")

        connection.rollback()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _add_records(connection, records)
        connection.exec_driver_sql("DROP TRIGGER behavioral_rule_versions_no_update")
        connection.execute(
            text(
                "UPDATE behavioral_rule_versions SET content_hash = :digest "
                "WHERE rule_version_id = 'rule-1-v1'"
            ),
            {"digest": "f" * 64},
        )
        with pytest.raises(StorageIntegrityError, match="content_hash"):
            BehavioralRuleVersionRepository(connection).get("rule-1-v1")
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


def test_rule_models_reject_duplicate_or_non_semantic_references() -> None:
    records = _records()
    with pytest.raises(ValidationError, match="source_incident_ids"):
        BehavioralRuleVersion.model_validate(
            records.rule.model_dump(mode="python")
            | {"source_incident_ids": ("incident-1", "incident-1")}
        )
    with pytest.raises(ValidationError, match="semantic_version"):
        BehavioralRuleVersion.model_validate(
            records.rule.model_dump(mode="python") | {"semantic_version": "latest"}
        )
    with pytest.raises(ValidationError, match="consumed_assessment_ids"):
        RuleConsolidationDecision.model_validate(
            records.decision.model_dump(mode="python")
            | {"consumed_assessment_ids": ("assessment-1", "assessment-1")}
        )


def test_decision_model_requires_result_only_for_rule_producing_actions() -> None:
    decision = _records().decision
    with pytest.raises(ValidationError, match="resulting_rule_version_id"):
        RuleConsolidationDecision.model_validate(
            decision.model_dump(mode="python") | {"resulting_rule_version_id": None}
        )
    with pytest.raises(ValidationError, match="resulting_rule_version_id"):
        RuleConsolidationDecision.model_validate(
            decision.model_dump(mode="python") | {"action": RuleAction.REJECT}
        )


@pytest.mark.integration
def test_behavioral_rule_head_is_mutable_but_must_match_exact_version(
    tmp_path: Path,
) -> None:
    engine, connection = _connection(tmp_path, "heads.db")
    records = _records()
    second = records.rule.model_copy(
        update={
            "rule_version_id": "rule-1-v2",
            "semantic_version": "1.1.0",
            "status": RuleStatus.ACTIVE,
        }
    )
    heads = BehavioralRuleHeadRepository(connection)
    try:
        _add_records(connection, records)
        BehavioralRuleVersionRepository(connection).add(
            second.rule_version_id,
            second,
            second.created_at,
        )

        assert heads.get("rule-1") is None
        heads.set("rule-1", "rule-1-v1", "1.0.0", RuleStatus.UNDER_REVIEW)
        assert heads.get("rule-1") == ("rule-1-v1", "1.0.0", RuleStatus.UNDER_REVIEW)
        heads.set("rule-1", "rule-1-v2", "1.1.0", RuleStatus.ACTIVE)
        assert heads.list_all() == (("rule-1", "rule-1-v2", "1.1.0", RuleStatus.ACTIVE),)

        with pytest.raises(StorageIntegrityError, match="does not match"):
            heads.set("rule-1", "rule-1-v2", "1.0.0", RuleStatus.ACTIVE)
        with pytest.raises(StorageIntegrityError, match="does not match"):
            heads.set("rule-1", "rule-1-v2", "1.1.0", RuleStatus.PROPOSED)
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


class _RuleRecords:
    def __init__(
        self,
        incidents: tuple[RuleIncident, ...],
        rule: BehavioralRuleVersion,
        assessment: ReviewerAssessment,
        decision: RuleConsolidationDecision,
        regression: RuleRegressionCase,
    ) -> None:
        self.incidents = incidents
        self.rule = rule
        self.assessment = assessment
        self.decision = decision
        self.regression = regression


def _records() -> _RuleRecords:
    creator = _actor("creator", ActorKind.HUMAN)
    reviewer = _actor("reviewer", ActorKind.TOOL)
    incidents = (
        RuleIncident(
            incident_id="incident-1",
            incident_kind=RuleIncidentKind.VERIFIED_FAILURE,
            summary="A verified workflow failure",
            evidence_ids=("evidence-1",),
            observed_at=NOW,
            reported_by=creator,
            recorded_at=NOW,
            governing_policy_hash=POLICY_HASH,
        ),
        RuleIncident(
            incident_id="incident-2",
            incident_kind=RuleIncidentKind.VALIDATED_COUNTEREXAMPLE,
            summary="A retained counterexample",
            evidence_ids=("evidence-2",),
            observed_at=NOW,
            reported_by=creator,
            recorded_at=NOW,
            governing_policy_hash=POLICY_HASH,
        ),
    )
    rule = BehavioralRuleVersion(
        rule_version_id="rule-1-v1",
        rule_id="rule-1",
        semantic_version="1.0.0",
        title="Preserve contradictory incidents",
        canonical_statement="Retain both incidents until an explicit boundary is tested.",
        rationale="Deleting either incident would erase the competing failure mode.",
        authority=RuleAuthority.PROJECT,
        scope=("behavioral-rule consolidation",),
        triggers=("contradictory incidents",),
        required_behavior=("retain both incidents",),
        prohibited_behavior=("delete dissenting history",),
        exceptions=(),
        decision_boundary="Apply while the separating variable remains unresolved.",
        precedence_rule_ids=(),
        source_incident_ids=("incident-1", "incident-2"),
        evidence_ids=("evidence-1", "evidence-2"),
        counterexamples=("A demonstrated obsolete incident may be superseded, not deleted.",),
        regression_test_ids=("test-both-incidents",),
        retrieval_terms=("contradiction", "incident retention"),
        aliases=("retain-dissent",),
        related_rule_ids=(),
        conflict_rule_ids=(),
        supersedes_rule_version_ids=(),
        status=RuleStatus.UNDER_REVIEW,
        creator=creator,
        approver=None,
        created_at=NOW,
        approved_at=None,
        governing_policy_hash=POLICY_HASH,
    )
    assessment = ReviewerAssessment(
        assessment_id="assessment-1",
        role=ReviewerRole.CONFLICT,
        provenance=_provenance(reviewer),
        proposal_id="proposal-1",
        rule_version_ids=("rule-1-v1",),
        incident_ids=("incident-1", "incident-2"),
        overlap=OverlapClassification.PARTIAL_OVERLAP,
        conflict=ConflictClassification.COMPETING_FAILURE_MODES,
        findings=("The incidents fail under different conditions.",),
        candidate_statement="Retain both incidents and test the separating condition.",
        scope=("behavioral-rule consolidation",),
        triggers=("contradictory incidents",),
        exceptions=(),
        counterexamples=("Deleting either incident loses a regression.",),
        regression_test_ids=("test-both-incidents",),
        recommended_action=RuleAction.ACCEPT_WITH_REVISION,
        uncertainty=("The separating variable still needs measurement.",),
    )
    decision = RuleConsolidationDecision(
        consolidation_decision_id="decision-1",
        proposal_id="proposal-1",
        consumed_assessment_ids=("assessment-1",),
        consumed_incident_ids=("incident-2", "incident-1"),
        resulting_rule_version_id="rule-1-v1",
        action=RuleAction.ACCEPT_WITH_REVISION,
        rationale="The canonical version retains both motivating failures.",
        separating_variable="measured operating condition",
        decision_boundary="Select behavior using the measured operating condition.",
        accepted_recommendations=("retain both incidents",),
        rejected_recommendations=("newest rule wins",),
        preserved_dissent=("boundary measurement remains uncertain",),
        decided_by=creator,
        decided_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    regression = RuleRegressionCase(
        regression_case_id="regression-1",
        rule_version_id="rule-1-v1",
        incident_ids=("incident-1", "incident-2"),
        test_id="test-both-incidents",
        scenario="Exercise both competing failure modes.",
        expected_behavior="Each incident remains represented by an explicit branch.",
        created_by=creator,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    return _RuleRecords(incidents, rule, assessment, decision, regression)


def _lineage_rules(
    successor_template: BehavioralRuleVersion,
) -> tuple[
    BehavioralRuleVersion,
    BehavioralRuleVersion,
    BehavioralRuleVersion,
    BehavioralRuleVersion,
]:
    predecessor_one = successor_template.model_copy(
        update={
            "rule_version_id": "rule-1-v0",
            "semantic_version": "0.9.0",
            "status": RuleStatus.SUPERSEDED,
        }
    )
    predecessor_two = successor_template.model_copy(
        update={
            "rule_version_id": "rule-legacy-v1",
            "rule_id": "rule-legacy",
            "status": RuleStatus.SUPERSEDED,
        }
    )
    successor = successor_template.model_copy(
        update={
            "supersedes_rule_version_ids": (
                predecessor_two.rule_version_id,
                predecessor_one.rule_version_id,
            )
        }
    )
    spare = successor_template.model_copy(
        update={
            "rule_version_id": "rule-spare-v1",
            "rule_id": "rule-spare",
        }
    )
    return predecessor_one, predecessor_two, successor, spare


def _add_records(connection: Connection, records: _RuleRecords) -> None:
    for incident in records.incidents:
        RuleIncidentRepository(connection).add(incident.incident_id, incident, incident.recorded_at)
    BehavioralRuleVersionRepository(connection).add(
        records.rule.rule_version_id,
        records.rule,
        records.rule.created_at,
    )
    ReviewerAssessmentRepository(connection).add(
        records.assessment.assessment_id,
        records.assessment,
        records.assessment.provenance.assessed_at,
    )
    RuleConsolidationDecisionRepository(connection).add(
        records.decision.consolidation_decision_id,
        records.decision,
        records.decision.decided_at,
    )
    RuleRegressionCaseRepository(connection).add(
        records.regression.regression_case_id,
        records.regression,
        records.regression.created_at,
    )


def _connection(tmp_path: Path, name: str) -> tuple[object, Connection]:
    return _connection_for_path(tmp_path / name)


def _connection_for_path(database_path: Path) -> tuple[object, Connection]:
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    return engine, connection


def _ordered_references(
    connection: Connection,
    table: object,
    owner_column: str,
    owner_id: str,
    reference_column: str,
) -> tuple[str, ...]:
    table_columns = table.c
    return tuple(
        connection.execute(
            select(table_columns[reference_column])
            .where(table_columns[owner_column] == owner_id)
            .order_by(table_columns.position)
        ).scalars()
    )


def _actor(actor_id: str, kind: ActorKind) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)


def _provenance(actor: ActorIdentity) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=actor,
        actor_version="reviewer-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("The supplied incidents are immutable.",),
        evidence_ids=("evidence-1", "evidence-2"),
        checks_run=("conflict-classification",),
        limitations=("No claim of formal completeness.",),
        result=AssessmentOutcome.PASSED,
        meaningful_confidence=None,
        assessed_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
