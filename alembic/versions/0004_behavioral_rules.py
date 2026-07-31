from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_behavioral_rules"
down_revision: str | None = "0003_progress_and_evidence_trails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORITATIVE_TABLES = (
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
)


def _content_hash_constraint(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _record_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )


def _ordered_reference_constraints(
    owner_column: str,
    reference_column: str,
    prefix: str,
) -> tuple[sa.CheckConstraint, sa.UniqueConstraint]:
    return (
        sa.CheckConstraint("position >= 0", name=f"ck_{prefix}_position"),
        sa.UniqueConstraint(
            owner_column,
            reference_column,
            name=f"uq_{prefix}_reference",
        ),
    )


def _create_append_only_triggers() -> None:
    for table_name in AUTHORITATIVE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )


def upgrade() -> None:
    op.create_table(
        "rule_incidents",
        sa.Column("incident_id", sa.String(length=128), primary_key=True),
        *_record_columns(),
        _content_hash_constraint("ck_rule_incidents_content_hash"),
    )
    op.create_table(
        "behavioral_rule_versions",
        sa.Column("rule_version_id", sa.String(length=192), primary_key=True),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_record_columns(),
        sa.UniqueConstraint(
            "rule_id",
            "semantic_version",
            name="uq_behavioral_rule_semantic_version",
        ),
        sa.UniqueConstraint(
            "rule_id",
            "rule_version_id",
            "semantic_version",
            "status",
            name="uq_behavioral_rule_head_target",
        ),
        sa.CheckConstraint(
            "length(semantic_version) BETWEEN 5 AND 32",
            name="ck_behavioral_rule_semantic_version_length",
        ),
        _content_hash_constraint("ck_behavioral_rule_versions_content_hash"),
    )
    op.create_table(
        "reviewer_assessments",
        sa.Column("assessment_id", sa.String(length=160), primary_key=True),
        *_record_columns(),
        _content_hash_constraint("ck_reviewer_assessments_content_hash"),
    )
    op.create_table(
        "rule_consolidation_decisions",
        sa.Column("consolidation_decision_id", sa.String(length=192), primary_key=True),
        sa.Column("resulting_rule_version_id", sa.String(length=192), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["resulting_rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        _content_hash_constraint("ck_rule_consolidation_decisions_content_hash"),
    )
    op.create_table(
        "rule_regression_cases",
        sa.Column("regression_case_id", sa.String(length=160), primary_key=True),
        sa.Column("rule_version_id", sa.String(length=192), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        _content_hash_constraint("ck_rule_regression_cases_content_hash"),
    )
    op.create_table(
        "behavioral_rule_version_incidents",
        sa.Column("rule_version_id", sa.String(length=192), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["rule_incidents.incident_id"]),
        *_ordered_reference_constraints(
            "rule_version_id",
            "incident_id",
            "behavioral_rule_version_incidents",
        ),
    )
    op.create_table(
        "behavioral_rule_version_supersessions",
        sa.Column("rule_version_id", sa.String(length=192), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("predecessor_rule_version_id", sa.String(length=192), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        *_ordered_reference_constraints(
            "rule_version_id",
            "predecessor_rule_version_id",
            "behavioral_rule_version_supersessions",
        ),
    )
    op.create_table(
        "reviewer_assessment_rule_versions",
        sa.Column("assessment_id", sa.String(length=160), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("rule_version_id", sa.String(length=192), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["reviewer_assessments.assessment_id"],
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        *_ordered_reference_constraints(
            "assessment_id",
            "rule_version_id",
            "reviewer_assessment_rule_versions",
        ),
    )
    op.create_table(
        "reviewer_assessment_incidents",
        sa.Column("assessment_id", sa.String(length=160), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["reviewer_assessments.assessment_id"],
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["rule_incidents.incident_id"]),
        *_ordered_reference_constraints(
            "assessment_id",
            "incident_id",
            "reviewer_assessment_incidents",
        ),
    )
    op.create_table(
        "rule_consolidation_assessments",
        sa.Column("consolidation_decision_id", sa.String(length=192), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["consolidation_decision_id"],
            ["rule_consolidation_decisions.consolidation_decision_id"],
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["reviewer_assessments.assessment_id"],
        ),
        *_ordered_reference_constraints(
            "consolidation_decision_id",
            "assessment_id",
            "rule_consolidation_assessments",
        ),
    )
    op.create_table(
        "rule_consolidation_incidents",
        sa.Column("consolidation_decision_id", sa.String(length=192), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["consolidation_decision_id"],
            ["rule_consolidation_decisions.consolidation_decision_id"],
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["rule_incidents.incident_id"]),
        *_ordered_reference_constraints(
            "consolidation_decision_id",
            "incident_id",
            "rule_consolidation_incidents",
        ),
    )
    op.create_table(
        "rule_regression_case_incidents",
        sa.Column("regression_case_id", sa.String(length=160), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["regression_case_id"],
            ["rule_regression_cases.regression_case_id"],
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["rule_incidents.incident_id"]),
        *_ordered_reference_constraints(
            "regression_case_id",
            "incident_id",
            "rule_regression_case_incidents",
        ),
    )
    op.create_table(
        "behavioral_rule_heads",
        sa.Column("rule_id", sa.String(length=128), primary_key=True),
        sa.Column("rule_version_id", sa.String(length=192), nullable=False),
        sa.Column("semantic_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id", "rule_version_id", "semantic_version", "status"],
            [
                "behavioral_rule_versions.rule_id",
                "behavioral_rule_versions.rule_version_id",
                "behavioral_rule_versions.semantic_version",
                "behavioral_rule_versions.status",
            ],
        ),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")

    op.drop_table("behavioral_rule_heads")
    op.drop_table("rule_regression_case_incidents")
    op.drop_table("rule_consolidation_incidents")
    op.drop_table("rule_consolidation_assessments")
    op.drop_table("reviewer_assessment_incidents")
    op.drop_table("reviewer_assessment_rule_versions")
    op.drop_table("behavioral_rule_version_supersessions")
    op.drop_table("behavioral_rule_version_incidents")
    op.drop_table("rule_regression_cases")
    op.drop_table("rule_consolidation_decisions")
    op.drop_table("reviewer_assessments")
    op.drop_table("behavioral_rule_versions")
    op.drop_table("rule_incidents")
