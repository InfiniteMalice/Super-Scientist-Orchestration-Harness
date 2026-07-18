from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_governed_adaptation_foundation"
down_revision: str | None = "0001_epistemic_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORITATIVE_TABLES = (
    "research_runs",
    "research_run_events",
    "configuration_versions",
    "self_improvement_measurements",
    "evaluator_audits",
    "evaluator_versions",
    "evaluator_succession_decisions",
    "evaluator_collapse_records",
)


def _content_hash_constraint(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
        name=name,
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
        "research_runs",
        sa.Column("run_id", sa.String(length=128), primary_key=True),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        _content_hash_constraint("ck_research_runs_content_hash"),
    )
    op.create_table(
        "research_run_events",
        sa.Column("run_event_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"]),
        _content_hash_constraint("ck_research_run_events_content_hash"),
    )
    op.create_table(
        "configuration_versions",
        sa.Column("configuration_version_id", sa.String(length=160), primary_key=True),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        _content_hash_constraint("ck_configuration_versions_content_hash"),
    )
    op.create_table(
        "evaluator_audits",
        sa.Column("evaluator_audit_id", sa.String(length=128), primary_key=True),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        _content_hash_constraint("ck_evaluator_audits_content_hash"),
    )
    op.create_table(
        "evaluator_versions",
        sa.Column("evaluator_version_id", sa.String(length=160), primary_key=True),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        _content_hash_constraint("ck_evaluator_versions_content_hash"),
    )
    op.create_table(
        "self_improvement_measurements",
        sa.Column("measurement_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("evaluator_audit_id", sa.String(length=128), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"]),
        sa.ForeignKeyConstraint(["evaluator_audit_id"], ["evaluator_audits.evaluator_audit_id"]),
        _content_hash_constraint("ck_self_improvement_measurements_content_hash"),
    )
    op.create_table(
        "evaluator_succession_decisions",
        sa.Column("evaluator_succession_decision_id", sa.String(length=160), primary_key=True),
        sa.Column("predecessor_evaluator_version_id", sa.String(length=160), nullable=False),
        sa.Column("candidate_evaluator_version_id", sa.String(length=160), nullable=False),
        sa.Column("evaluator_audit_id", sa.String(length=128), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["predecessor_evaluator_version_id"],
            ["evaluator_versions.evaluator_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["candidate_evaluator_version_id"],
            ["evaluator_versions.evaluator_version_id"],
        ),
        sa.ForeignKeyConstraint(["evaluator_audit_id"], ["evaluator_audits.evaluator_audit_id"]),
        _content_hash_constraint("ck_evaluator_succession_decisions_content_hash"),
    )
    op.create_table(
        "evaluator_collapse_records",
        sa.Column("evaluator_collapse_record_id", sa.String(length=160), primary_key=True),
        sa.Column("evaluator_version_id", sa.String(length=160), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluator_version_id"],
            ["evaluator_versions.evaluator_version_id"],
        ),
        _content_hash_constraint("ck_evaluator_collapse_records_content_hash"),
    )
    op.create_table(
        "research_run_heads",
        sa.Column("run_id", sa.String(length=128), primary_key=True),
        sa.Column("run_event_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(["run_event_id"], ["research_run_events.run_event_id"]),
    )
    op.create_table(
        "evaluator_heads",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("evaluator_version_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluator_version_id"],
            ["evaluator_versions.evaluator_version_id"],
        ),
        sa.CheckConstraint("singleton_id = 1", name="ck_evaluator_heads_singleton"),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")

    op.drop_table("evaluator_heads")
    op.drop_table("research_run_heads")
    op.drop_table("evaluator_collapse_records")
    op.drop_table("evaluator_succession_decisions")
    op.drop_table("self_improvement_measurements")
    op.drop_table("evaluator_versions")
    op.drop_table("evaluator_audits")
    op.drop_table("configuration_versions")
    op.drop_table("research_run_events")
    op.drop_table("research_runs")
