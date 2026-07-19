from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Connection, Table, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ReviewerAssessment,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
    SemanticVersion,
)
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    TrailAssessment,
    TrailCheckResult,
)
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import (
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import (
    behavioral_rule_heads,
    behavioral_rule_version_incidents,
    behavioral_rule_version_supersessions,
    behavioral_rule_versions,
    completion_decisions,
    configuration_versions,
    evaluator_audits,
    evaluator_collapse_records,
    evaluator_heads,
    evaluator_succession_decisions,
    evaluator_versions,
    evidence_trail_assessments,
    evidence_trail_checks,
    evidence_trail_heads,
    evidence_trail_nodes,
    evidence_trail_relations,
    evidence_trail_versions,
    progress_events,
    progress_heads,
    progress_plans,
    progress_subtasks,
    report_sentence_bindings,
    research_run_events,
    research_run_heads,
    research_runs,
    reviewer_assessment_incidents,
    reviewer_assessment_rule_versions,
    reviewer_assessments,
    rule_consolidation_assessments,
    rule_consolidation_decisions,
    rule_consolidation_incidents,
    rule_incidents,
    rule_regression_case_incidents,
    rule_regression_cases,
    run_budgets,
    run_checkpoints,
    self_improvement_measurements,
)

TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)
STABLE_IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
SEMANTIC_VERSION_ADAPTER: TypeAdapter[SemanticVersion] = TypeAdapter(SemanticVersion)

type _RelationshipStorageType = type[str] | type[int]

__all__ = [
    "BehavioralRuleHeadRepository",
    "BehavioralRuleVersionRepository",
    "CompletionDecisionRepository",
    "ConfigurationVersionRepository",
    "EvaluatorAuditRepository",
    "EvaluatorCollapseRepository",
    "EvaluatorHeadRepository",
    "EvaluatorSuccessionRepository",
    "EvaluatorVersionRepository",
    "EvidenceTrailAssessmentRepository",
    "EvidenceTrailCheckRepository",
    "EvidenceTrailHeadRepository",
    "EvidenceTrailNodeRepository",
    "EvidenceTrailRelationRepository",
    "EvidenceTrailVersionRepository",
    "ProgressEventRepository",
    "ProgressHeadRepository",
    "ProgressPlanRepository",
    "ProgressSubtaskRepository",
    "ReportSentenceBindingRepository",
    "ResearchRunEventRepository",
    "ResearchRunHeadRepository",
    "ResearchRunRepository",
    "ReviewerAssessmentRepository",
    "RuleConsolidationDecisionRepository",
    "RuleIncidentRepository",
    "RuleRegressionCaseRepository",
    "RunBudgetRepository",
    "RunCheckpointRepository",
    "SelfImprovementMeasurementRepository",
]


def _require_integrity(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(f"storage integrity error: {detail}")


class _AppendOnlyRecordRepository[RecordT: BaseModel]:
    """Stores one source-controlled Pydantic record type in one fixed authoritative table."""

    def __init__(
        self,
        connection: Connection,
        *,
        table: Table,
        model_type: type[RecordT],
        identifier_field: str,
        relationship_fields: Mapping[str, str] | None = None,
        relationship_types: Mapping[str, _RelationshipStorageType] | None = None,
        nullable_relationship_fields: Collection[str] | None = None,
    ) -> None:
        _require_integrity(
            bool(model_type.model_config.get("frozen")),
            "append-only repository model must be frozen",
        )
        _require_integrity(identifier_field in model_type.model_fields, "unknown identifier field")
        self._connection = connection
        self._table = table
        self._model_type = model_type
        self._identifier_field = identifier_field
        self._relationship_fields = dict(relationship_fields or {})
        self._nullable_relationship_fields = frozenset(nullable_relationship_fields or ())
        requested_relationship_types = dict(relationship_types or {})
        _require_integrity(
            set(self._relationship_fields).issubset(table.c.keys()),
            "unknown relationship column",
        )
        _require_integrity(
            set(self._relationship_fields.values()).issubset(model_type.model_fields),
            "unknown relationship field",
        )
        _require_integrity(
            set(requested_relationship_types).issubset(self._relationship_fields),
            "unknown typed relationship column",
        )
        _require_integrity(
            self._nullable_relationship_fields.issubset(self._relationship_fields),
            "unknown nullable relationship column",
        )
        _require_integrity(
            all(
                self._table.c[column_name].nullable
                for column_name in self._nullable_relationship_fields
            ),
            "nullable relationship column must permit null",
        )
        _require_integrity(
            all(
                storage_type is str or storage_type is int
                for storage_type in requested_relationship_types.values()
            ),
            "unsupported relationship storage type",
        )
        self._relationship_types: dict[str, _RelationshipStorageType] = {
            column_name: requested_relationship_types.get(column_name, str)
            for column_name in self._relationship_fields
        }

    def get(self, record_id: str) -> RecordT | None:
        row = (
            self._connection.execute(
                select(self._table).where(self._table.c[self._identifier_field] == record_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[RecordT, ...]:
        rows = self._connection.execute(
            select(self._table).order_by(self._table.c[self._identifier_field])
        ).mappings()
        return tuple(self._decode_row(dict(row)) for row in rows)

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        _require_integrity(isinstance(record_id, str), "record identifier must be a string")
        try:
            validated = self._model_type.model_validate(record.model_dump(mode="python"))
            validated_created_at = TIMESTAMP_ADAPTER.validate_python(created_at)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid append-only record"
            ) from error
        _require_integrity(
            getattr(validated, self._identifier_field) == record_id,
            f"{self._identifier_field} does not match record",
        )
        record_json = canonical_json_bytes(validated.model_dump(mode="json")).decode("utf-8")
        values: dict[str, object] = {
            self._identifier_field: record_id,
            "record_json": record_json,
            "content_hash": sha256_hex(record_json.encode("utf-8")),
            "created_at": validated_created_at.isoformat(),
        }
        for column_name, field_name in self._relationship_fields.items():
            values[column_name] = _validated_relationship_value(
                getattr(validated, field_name),
                field_name,
                self._relationship_types[column_name],
                nullable=column_name in self._nullable_relationship_fields,
            )
        self._connection.execute(insert(self._table).values(**values))

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        try:
            record_json = _stored_string(row, "record_json")
            content_hash = _stored_string(row, "content_hash")
            record = self._model_type.model_validate_json(record_json)
            canonical_record_json = canonical_json_bytes(record.model_dump(mode="json")).decode(
                "utf-8"
            )
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError("storage integrity error: invalid record JSON") from error
        created_at = _stored_string(row, "created_at")
        try:
            TIMESTAMP_ADAPTER.validate_python(datetime.fromisoformat(created_at))
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid created_at timestamp"
            ) from error
        stored_identifier = _stored_string(row, self._identifier_field)
        _require_integrity(
            getattr(record, self._identifier_field) == stored_identifier,
            f"{self._identifier_field} does not match record_json",
        )
        _require_integrity(
            sha256_hex(record_json.encode("utf-8")) == content_hash,
            "content_hash does not match record_json",
        )
        for column_name, field_name in self._relationship_fields.items():
            storage_type = self._relationship_types[column_name]
            record_value = _validated_relationship_value(
                getattr(record, field_name),
                field_name,
                storage_type,
                nullable=column_name in self._nullable_relationship_fields,
            )
            _require_integrity(
                record_value
                == _stored_relationship_value(
                    row,
                    column_name,
                    storage_type,
                    nullable=column_name in self._nullable_relationship_fields,
                ),
                f"{column_name} does not match record_json",
            )
        _require_integrity(record_json == canonical_record_json, "record_json must be canonical")
        return record


@dataclass(frozen=True, slots=True)
class _OrderedReferenceBinding:
    table: Table
    owner_column: str
    record_field: str
    reference_column: str


class _ReferencedAppendOnlyRecordRepository[RecordT: BaseModel](
    _AppendOnlyRecordRepository[RecordT]
):
    """Materializes ordered relationship tuples and verifies exact canonical equality."""

    def __init__(
        self,
        connection: Connection,
        *,
        table: Table,
        model_type: type[RecordT],
        identifier_field: str,
        reference_bindings: tuple[_OrderedReferenceBinding, ...],
        relationship_fields: Mapping[str, str] | None = None,
        relationship_types: Mapping[str, _RelationshipStorageType] | None = None,
        nullable_relationship_fields: Collection[str] | None = None,
    ) -> None:
        super().__init__(
            connection,
            table=table,
            model_type=model_type,
            identifier_field=identifier_field,
            relationship_fields=relationship_fields,
            relationship_types=relationship_types,
            nullable_relationship_fields=nullable_relationship_fields,
        )
        _require_integrity(bool(reference_bindings), "referenced repository needs bindings")
        for binding in reference_bindings:
            _require_integrity(
                binding.owner_column in binding.table.c,
                "unknown reference owner column",
            )
            _require_integrity("position" in binding.table.c, "missing reference position")
            _require_integrity(
                binding.reference_column in binding.table.c,
                "unknown reference column",
            )
            _require_integrity(
                binding.record_field in model_type.model_fields,
                "unknown canonical reference field",
            )
        self._reference_bindings = reference_bindings

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        try:
            validated = self._model_type.model_validate(record.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid append-only record"
            ) from error
        super().add(record_id, validated, created_at)
        for binding in self._reference_bindings:
            references = _canonical_reference_tuple(validated, binding.record_field)
            for position, reference_id in enumerate(references):
                self._connection.execute(
                    insert(binding.table).values(
                        **{
                            binding.owner_column: record_id,
                            "position": position,
                            binding.reference_column: reference_id,
                        }
                    )
                )

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        record = super()._decode_row(row)
        owner_id = _validated_relationship_value(
            getattr(record, self._identifier_field),
            self._identifier_field,
            str,
        )
        for binding in self._reference_bindings:
            expected = _canonical_reference_tuple(record, binding.record_field)
            stored_rows = self._connection.execute(
                select(
                    binding.table.c.position,
                    binding.table.c[binding.reference_column],
                )
                .where(binding.table.c[binding.owner_column] == owner_id)
                .order_by(binding.table.c.position)
            ).mappings()
            actual = tuple(
                (
                    _stored_integer(dict(stored_row), "position"),
                    _stored_string(dict(stored_row), binding.reference_column),
                )
                for stored_row in stored_rows
            )
            _require_integrity(
                actual == tuple(enumerate(expected)),
                f"{binding.record_field} materialization does not match exact canonical references",
            )
        return record


def _canonical_reference_tuple(record: BaseModel, field_name: str) -> tuple[str, ...]:
    value = getattr(record, field_name)
    _require_integrity(isinstance(value, tuple), f"{field_name} must be a tuple")
    references: list[str] = []
    for item in value:
        _require_integrity(isinstance(item, str), f"{field_name} must contain strings")
        references.append(item)
    _require_integrity(
        len(set(references)) == len(references),
        f"{field_name} must contain unique identifiers",
    )
    return tuple(references)


class RuleIncidentRepository(_AppendOnlyRecordRepository[RuleIncident]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_incidents,
            model_type=RuleIncident,
            identifier_field="incident_id",
        )


class BehavioralRuleVersionRepository(_ReferencedAppendOnlyRecordRepository[BehavioralRuleVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=behavioral_rule_versions,
            model_type=BehavioralRuleVersion,
            identifier_field="rule_version_id",
            relationship_fields={
                "rule_id": "rule_id",
                "semantic_version": "semantic_version",
                "status": "status",
            },
            reference_bindings=(
                _OrderedReferenceBinding(
                    table=behavioral_rule_version_incidents,
                    owner_column="rule_version_id",
                    record_field="source_incident_ids",
                    reference_column="incident_id",
                ),
                _OrderedReferenceBinding(
                    table=behavioral_rule_version_supersessions,
                    owner_column="rule_version_id",
                    record_field="supersedes_rule_version_ids",
                    reference_column="predecessor_rule_version_id",
                ),
            ),
        )


class ReviewerAssessmentRepository(_ReferencedAppendOnlyRecordRepository[ReviewerAssessment]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=reviewer_assessments,
            model_type=ReviewerAssessment,
            identifier_field="assessment_id",
            reference_bindings=(
                _OrderedReferenceBinding(
                    table=reviewer_assessment_rule_versions,
                    owner_column="assessment_id",
                    record_field="rule_version_ids",
                    reference_column="rule_version_id",
                ),
                _OrderedReferenceBinding(
                    table=reviewer_assessment_incidents,
                    owner_column="assessment_id",
                    record_field="incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class RuleConsolidationDecisionRepository(
    _ReferencedAppendOnlyRecordRepository[RuleConsolidationDecision]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_consolidation_decisions,
            model_type=RuleConsolidationDecision,
            identifier_field="consolidation_decision_id",
            relationship_fields={
                "resulting_rule_version_id": "resulting_rule_version_id",
            },
            nullable_relationship_fields={"resulting_rule_version_id"},
            reference_bindings=(
                _OrderedReferenceBinding(
                    table=rule_consolidation_assessments,
                    owner_column="consolidation_decision_id",
                    record_field="consumed_assessment_ids",
                    reference_column="assessment_id",
                ),
                _OrderedReferenceBinding(
                    table=rule_consolidation_incidents,
                    owner_column="consolidation_decision_id",
                    record_field="consumed_incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class RuleRegressionCaseRepository(_ReferencedAppendOnlyRecordRepository[RuleRegressionCase]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_regression_cases,
            model_type=RuleRegressionCase,
            identifier_field="regression_case_id",
            relationship_fields={"rule_version_id": "rule_version_id"},
            reference_bindings=(
                _OrderedReferenceBinding(
                    table=rule_regression_case_incidents,
                    owner_column="regression_case_id",
                    record_field="incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class ResearchRunRepository(_AppendOnlyRecordRepository[ResearchRun]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=research_runs,
            model_type=ResearchRun,
            identifier_field="run_id",
        )


class ResearchRunEventRepository(_AppendOnlyRecordRepository[ResearchRunEvent]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=research_run_events,
            model_type=ResearchRunEvent,
            identifier_field="run_event_id",
            relationship_fields={"run_id": "run_id"},
        )


class ConfigurationVersionRepository(_AppendOnlyRecordRepository[ConfigurationVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=configuration_versions,
            model_type=ConfigurationVersion,
            identifier_field="configuration_version_id",
        )


class SelfImprovementMeasurementRepository(
    _AppendOnlyRecordRepository[SelfImprovementMeasurementRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=self_improvement_measurements,
            model_type=SelfImprovementMeasurementRecord,
            identifier_field="measurement_id",
            relationship_fields={
                "run_id": "run_id",
                "evaluator_audit_id": "evaluator_audit_id",
            },
        )


class EvaluatorAuditRepository(_AppendOnlyRecordRepository[EvaluatorAuditRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_audits,
            model_type=EvaluatorAuditRecord,
            identifier_field="evaluator_audit_id",
        )


class EvaluatorVersionRepository(_AppendOnlyRecordRepository[EvaluatorVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_versions,
            model_type=EvaluatorVersion,
            identifier_field="evaluator_version_id",
        )


class EvaluatorSuccessionRepository(_AppendOnlyRecordRepository[EvaluatorSuccessionDecision]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_succession_decisions,
            model_type=EvaluatorSuccessionDecision,
            identifier_field="evaluator_succession_decision_id",
            relationship_fields={
                "predecessor_evaluator_version_id": "predecessor_evaluator_version_id",
                "candidate_evaluator_version_id": "candidate_evaluator_version_id",
                "evaluator_audit_id": "evaluator_audit_id",
            },
        )


class EvaluatorCollapseRepository(_AppendOnlyRecordRepository[EvaluatorCollapseRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_collapse_records,
            model_type=EvaluatorCollapseRecord,
            identifier_field="evaluator_collapse_record_id",
            relationship_fields={"evaluator_version_id": "evaluator_version_id"},
        )


class ProgressPlanRepository(_AppendOnlyRecordRepository[ProgressPlan]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_plans,
            model_type=ProgressPlan,
            identifier_field="plan_version_id",
            relationship_fields={"run_id": "run_id"},
        )


class ProgressSubtaskRepository(_AppendOnlyRecordRepository[ProgressSubtask]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_subtasks,
            model_type=ProgressSubtask,
            identifier_field="subtask_id",
            relationship_fields={"plan_version_id": "plan_version_id"},
        )


class ProgressEventRepository(_AppendOnlyRecordRepository[ProgressValidationEvent]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_events,
            model_type=ProgressValidationEvent,
            identifier_field="event_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
                "subtask_id": "subtask_id",
            },
        )


class RunBudgetRepository(_AppendOnlyRecordRepository[BudgetAllocation]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=run_budgets,
            model_type=BudgetAllocation,
            identifier_field="budget_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )


class RunCheckpointRepository(_AppendOnlyRecordRepository[RunCheckpoint]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=run_checkpoints,
            model_type=RunCheckpoint,
            identifier_field="checkpoint_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )


class CompletionDecisionRepository(_AppendOnlyRecordRepository[CompletionDecision]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=completion_decisions,
            model_type=CompletionDecision,
            identifier_field="completion_decision_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )


class EvidenceTrailVersionRepository(_AppendOnlyRecordRepository[EvidenceTrailVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_versions,
            model_type=EvidenceTrailVersion,
            identifier_field="trail_version_id",
            relationship_fields={
                "trail_id": "trail_id",
                "claim_version_id": "claim_version_id",
                "version": "version",
            },
            relationship_types={"version": int},
        )


class EvidenceTrailNodeRepository(_AppendOnlyRecordRepository[EvidenceTrailNode]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_nodes,
            model_type=EvidenceTrailNode,
            identifier_field="node_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "evidence_id": "evidence_id",
            },
        )


class EvidenceTrailRelationRepository(_AppendOnlyRecordRepository[EvidenceTrailRelation]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_relations,
            model_type=EvidenceTrailRelation,
            identifier_field="relation_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "source_node_id": "source_node_id",
                "target_node_id": "target_node_id",
            },
        )


class EvidenceTrailCheckRepository(_AppendOnlyRecordRepository[TrailCheckResult]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_checks,
            model_type=TrailCheckResult,
            identifier_field="check_id",
            relationship_fields={"trail_version_id": "trail_version_id"},
        )


class EvidenceTrailAssessmentRepository(_AppendOnlyRecordRepository[TrailAssessment]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_assessments,
            model_type=TrailAssessment,
            identifier_field="assessment_id",
            relationship_fields={"trail_version_id": "trail_version_id"},
        )


class ReportSentenceBindingRepository(_AppendOnlyRecordRepository[ReportSentenceBinding]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=report_sentence_bindings,
            model_type=ReportSentenceBinding,
            identifier_field="binding_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "claim_version_id": "claim_version_id",
            },
        )


class BehavioralRuleHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, rule_id: str) -> tuple[str, str, RuleStatus] | None:
        row = (
            self._connection.execute(
                select(
                    behavioral_rule_heads.c.rule_id,
                    behavioral_rule_heads.c.rule_version_id,
                    behavioral_rule_heads.c.semantic_version,
                    behavioral_rule_heads.c.status,
                ).where(behavioral_rule_heads.c.rule_id == rule_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, str, RuleStatus], ...]:
        rows = self._connection.execute(
            select(
                behavioral_rule_heads.c.rule_id,
                behavioral_rule_heads.c.rule_version_id,
                behavioral_rule_heads.c.semantic_version,
                behavioral_rule_heads.c.status,
            ).order_by(behavioral_rule_heads.c.rule_id)
        ).mappings()
        heads: list[tuple[str, str, str, RuleStatus]] = []
        for row in rows:
            stored_row = dict(row)
            rule_id = _stored_string(stored_row, "rule_id")
            rule_version_id, semantic_version, status = self._decode_row(stored_row)
            heads.append((rule_id, rule_version_id, semantic_version, status))
        return tuple(heads)

    def set(
        self,
        rule_id: str,
        rule_version_id: str,
        semantic_version: str,
        status: RuleStatus,
    ) -> None:
        try:
            validated_rule_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_id)
            validated_rule_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            validated_status = RuleStatus(status)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid behavioral rule head"
            ) from error
        stored_identity = self._connection.execute(
            select(
                behavioral_rule_versions.c.rule_id,
                behavioral_rule_versions.c.semantic_version,
                behavioral_rule_versions.c.status,
            ).where(behavioral_rule_versions.c.rule_version_id == validated_rule_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity
            == (
                validated_rule_id,
                validated_semantic_version,
                validated_status.value,
            ),
            "rule version does not match rule_id, semantic_version, and status",
        )
        statement = sqlite_insert(behavioral_rule_heads).values(
            rule_id=validated_rule_id,
            rule_version_id=validated_rule_version_id,
            semantic_version=validated_semantic_version,
            status=validated_status.value,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[behavioral_rule_heads.c.rule_id],
                set_={
                    "rule_version_id": validated_rule_version_id,
                    "semantic_version": validated_semantic_version,
                    "status": validated_status.value,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, str, RuleStatus]:
        rule_id = _stored_string(row, "rule_id")
        rule_version_id = _stored_string(row, "rule_version_id")
        semantic_version = _stored_string(row, "semantic_version")
        status_text = _stored_string(row, "status")
        try:
            validated_rule_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_id)
            validated_rule_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            status = RuleStatus(status_text)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid behavioral rule head"
            ) from error
        _require_integrity(validated_rule_id == rule_id, "rule_id must be canonical")
        _require_integrity(
            validated_rule_version_id == rule_version_id,
            "rule_version_id must be canonical",
        )
        _require_integrity(
            validated_semantic_version == semantic_version,
            "semantic_version must be canonical",
        )
        stored_identity = self._connection.execute(
            select(
                behavioral_rule_versions.c.rule_id,
                behavioral_rule_versions.c.semantic_version,
                behavioral_rule_versions.c.status,
            ).where(behavioral_rule_versions.c.rule_version_id == rule_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (rule_id, semantic_version, status.value),
            "behavioral rule head references an incoherent version",
        )
        return rule_version_id, semantic_version, status


class ResearchRunHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, run_id: str) -> str | None:
        return self._connection.execute(
            select(research_run_heads.c.run_event_id).where(research_run_heads.c.run_id == run_id)
        ).scalar_one_or_none()

    def list_all(self) -> tuple[tuple[str, str], ...]:
        rows = self._connection.execute(
            select(
                research_run_heads.c.run_id,
                research_run_heads.c.run_event_id,
            ).order_by(research_run_heads.c.run_id)
        ).mappings()
        return tuple(
            (
                _stored_string(dict(row), "run_id"),
                _stored_string(dict(row), "run_event_id"),
            )
            for row in rows
        )

    def set(self, run_id: str, run_event_id: str) -> None:
        event_run_id = self._connection.execute(
            select(research_run_events.c.run_id).where(
                research_run_events.c.run_event_id == run_event_id
            )
        ).scalar_one_or_none()
        _require_integrity(
            event_run_id == run_id,
            "run_event_id does not belong to run_id",
        )
        statement = sqlite_insert(research_run_heads).values(
            run_id=run_id,
            run_event_id=run_event_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[research_run_heads.c.run_id],
                set_={"run_event_id": run_event_id},
            )
        )


class EvaluatorHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self) -> str | None:
        rows = tuple(
            self._connection.execute(
                select(
                    evaluator_heads.c.singleton_id,
                    evaluator_heads.c.evaluator_version_id,
                )
            ).mappings()
        )
        if not rows:
            return None
        _require_integrity(len(rows) == 1, "evaluator head must contain one singleton row")
        row = rows[0]
        _require_integrity(row["singleton_id"] == 1, "evaluator head singleton_id must equal 1")
        evaluator_version_id = _stored_string(dict(row), "evaluator_version_id")
        _require_integrity(
            self._connection.execute(
                select(evaluator_versions.c.evaluator_version_id).where(
                    evaluator_versions.c.evaluator_version_id == evaluator_version_id
                )
            ).scalar_one_or_none()
            == evaluator_version_id,
            "evaluator head references a missing evaluator version",
        )
        return evaluator_version_id

    def set(self, evaluator_version_id: str) -> None:
        statement = sqlite_insert(evaluator_heads).values(
            singleton_id=1,
            evaluator_version_id=evaluator_version_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[evaluator_heads.c.singleton_id],
                set_={"evaluator_version_id": evaluator_version_id},
            )
        )


class ProgressHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, run_id: str) -> tuple[str, str] | None:
        row = (
            self._connection.execute(
                select(
                    progress_heads.c.run_id,
                    progress_heads.c.plan_version_id,
                    progress_heads.c.last_event_id,
                ).where(progress_heads.c.run_id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, str], ...]:
        rows = self._connection.execute(
            select(
                progress_heads.c.run_id,
                progress_heads.c.plan_version_id,
                progress_heads.c.last_event_id,
            ).order_by(progress_heads.c.run_id)
        ).mappings()
        heads: list[tuple[str, str, str]] = []
        for row in rows:
            stored_row = dict(row)
            run_id = _stored_string(stored_row, "run_id")
            plan_version_id, last_event_id = self._decode_row(stored_row)
            heads.append((run_id, plan_version_id, last_event_id))
        return tuple(heads)

    def set(self, run_id: str, plan_version_id: str, last_event_id: str) -> None:
        plan_run_id = self._connection.execute(
            select(progress_plans.c.run_id).where(
                progress_plans.c.plan_version_id == plan_version_id
            )
        ).scalar_one_or_none()
        _require_integrity(plan_run_id == run_id, "plan_version_id does not belong to run_id")
        event_relationship = self._connection.execute(
            select(progress_events.c.run_id, progress_events.c.plan_version_id).where(
                progress_events.c.event_id == last_event_id
            )
        ).one_or_none()
        _require_integrity(event_relationship is not None, "last_event_id does not exist")
        _require_integrity(
            event_relationship == (run_id, plan_version_id),
            "last_event_id does not belong to run_id and plan_version_id",
        )
        statement = sqlite_insert(progress_heads).values(
            run_id=run_id,
            plan_version_id=plan_version_id,
            last_event_id=last_event_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[progress_heads.c.run_id],
                set_={
                    "plan_version_id": plan_version_id,
                    "last_event_id": last_event_id,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, str]:
        run_id = _stored_string(row, "run_id")
        plan_version_id = _stored_string(row, "plan_version_id")
        last_event_id = _stored_string(row, "last_event_id")
        plan_run_id = self._connection.execute(
            select(progress_plans.c.run_id).where(
                progress_plans.c.plan_version_id == plan_version_id
            )
        ).scalar_one_or_none()
        _require_integrity(plan_run_id == run_id, "progress head references an incoherent plan")
        event_relationship = self._connection.execute(
            select(progress_events.c.run_id, progress_events.c.plan_version_id).where(
                progress_events.c.event_id == last_event_id
            )
        ).one_or_none()
        _require_integrity(
            event_relationship == (run_id, plan_version_id),
            "progress head references an incoherent event",
        )
        return plan_version_id, last_event_id


class EvidenceTrailHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, trail_id: str) -> tuple[str, int] | None:
        row = (
            self._connection.execute(
                select(
                    evidence_trail_heads.c.trail_id,
                    evidence_trail_heads.c.trail_version_id,
                    evidence_trail_heads.c.version,
                ).where(evidence_trail_heads.c.trail_id == trail_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, int], ...]:
        rows = self._connection.execute(
            select(
                evidence_trail_heads.c.trail_id,
                evidence_trail_heads.c.trail_version_id,
                evidence_trail_heads.c.version,
            ).order_by(evidence_trail_heads.c.trail_id)
        ).mappings()
        heads: list[tuple[str, str, int]] = []
        for row in rows:
            stored_row = dict(row)
            trail_id = _stored_string(stored_row, "trail_id")
            trail_version_id, version = self._decode_row(stored_row)
            heads.append((trail_id, trail_version_id, version))
        return tuple(heads)

    def set(self, trail_id: str, trail_version_id: str, version: int) -> None:
        validated_version = _stored_integer({"version": version}, "version")
        stored_identity = self._connection.execute(
            select(
                evidence_trail_versions.c.trail_id,
                evidence_trail_versions.c.version,
            ).where(evidence_trail_versions.c.trail_version_id == trail_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (trail_id, validated_version),
            "trail_version_id does not match trail_id and version",
        )
        current = self.get(trail_id)
        if current == (trail_version_id, validated_version):
            return
        if current is None:
            _require_integrity(
                validated_version == 1,
                "evidence trail head must begin at version 1",
            )
        else:
            _require_integrity(
                validated_version == current[1] + 1,
                "evidence trail head requires the exact successor of the current version",
            )
        statement = sqlite_insert(evidence_trail_heads).values(
            trail_id=trail_id,
            trail_version_id=trail_version_id,
            version=validated_version,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[evidence_trail_heads.c.trail_id],
                set_={
                    "trail_version_id": trail_version_id,
                    "version": validated_version,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, int]:
        trail_id = _stored_string(row, "trail_id")
        trail_version_id = _stored_string(row, "trail_version_id")
        version = _stored_integer(row, "version")
        stored_identity = self._connection.execute(
            select(
                evidence_trail_versions.c.trail_id,
                evidence_trail_versions.c.version,
            ).where(evidence_trail_versions.c.trail_version_id == trail_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (trail_id, version),
            "evidence trail head references an incoherent version",
        )
        return trail_version_id, version


def _stored_string(row: Mapping[str, object], column_name: str) -> str:
    value = row[column_name]
    if not isinstance(value, str):
        raise StorageIntegrityError(f"storage integrity error: {column_name} must be a string")
    return value


def _stored_integer(row: Mapping[str, object], column_name: str) -> int:
    value = row[column_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageIntegrityError(f"storage integrity error: {column_name} must be an integer")
    return value


def _validated_relationship_value(
    value: object,
    field_name: str,
    storage_type: _RelationshipStorageType,
    *,
    nullable: bool = False,
) -> str | int | None:
    if value is None:
        _require_integrity(nullable, f"{field_name} must not be null")
        return None
    if storage_type is str:
        if not isinstance(value, str):
            raise StorageIntegrityError(f"storage integrity error: {field_name} must be a string")
        return value
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageIntegrityError(f"storage integrity error: {field_name} must be an integer")
    return value


def _stored_relationship_value(
    row: Mapping[str, object],
    column_name: str,
    storage_type: _RelationshipStorageType,
    *,
    nullable: bool = False,
) -> str | int | None:
    if row[column_name] is None:
        _require_integrity(nullable, f"{column_name} must not be null")
        return None
    if storage_type is str:
        return _stored_string(row, column_name)
    return _stored_integer(row, column_name)
