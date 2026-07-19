from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_progress_and_evidence_trails"
down_revision: str | None = "0002_governed_adaptation_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORITATIVE_TABLES = (
    "progress_plans",
    "progress_subtasks",
    "progress_events",
    "run_budgets",
    "run_checkpoints",
    "completion_decisions",
    "evidence_trail_versions",
    "evidence_trail_nodes",
    "evidence_trail_relations",
    "evidence_trail_checks",
    "evidence_trail_assessments",
    "report_sentence_bindings",
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
        "progress_plans",
        sa.Column("plan_version_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"]),
        sa.UniqueConstraint("run_id", "plan_version_id", name="uq_progress_plan_run"),
        _content_hash_constraint("ck_progress_plans_content_hash"),
    )
    op.create_table(
        "progress_subtasks",
        sa.Column("subtask_id", sa.String(length=160), primary_key=True),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["plan_version_id"],
            ["progress_plans.plan_version_id"],
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "subtask_id",
            name="uq_progress_subtask_plan",
        ),
        _content_hash_constraint("ck_progress_subtasks_content_hash"),
    )
    op.create_table(
        "progress_events",
        sa.Column("event_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        sa.Column("subtask_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_version_id"],
            ["progress_plans.run_id", "progress_plans.plan_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "subtask_id"],
            ["progress_subtasks.plan_version_id", "progress_subtasks.subtask_id"],
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "event_id",
            name="uq_progress_event_plan",
        ),
        _content_hash_constraint("ck_progress_events_content_hash"),
    )
    op.create_table(
        "run_budgets",
        sa.Column("budget_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_version_id"],
            ["progress_plans.run_id", "progress_plans.plan_version_id"],
        ),
        _content_hash_constraint("ck_run_budgets_content_hash"),
    )
    op.create_table(
        "run_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_version_id"],
            ["progress_plans.run_id", "progress_plans.plan_version_id"],
        ),
        _content_hash_constraint("ck_run_checkpoints_content_hash"),
    )
    op.create_table(
        "completion_decisions",
        sa.Column("completion_decision_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_version_id"],
            ["progress_plans.run_id", "progress_plans.plan_version_id"],
        ),
        _content_hash_constraint("ck_completion_decisions_content_hash"),
    )
    op.create_table(
        "evidence_trail_versions",
        sa.Column("trail_version_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_id", sa.String(length=128), nullable=False),
        sa.Column("claim_version_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["claim_version_id"],
            ["claim_versions.claim_version_id"],
        ),
        sa.UniqueConstraint("trail_id", "version", name="uq_evidence_trail_version"),
        sa.UniqueConstraint(
            "trail_id",
            "trail_version_id",
            "version",
            name="uq_evidence_trail_head_target",
        ),
        sa.UniqueConstraint(
            "trail_version_id",
            "claim_version_id",
            name="uq_evidence_trail_claim",
        ),
        sa.CheckConstraint("version >= 1", name="ck_evidence_trail_versions_version"),
        _content_hash_constraint("ck_evidence_trail_versions_content_hash"),
    )
    op.create_table(
        "evidence_trail_nodes",
        sa.Column("node_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["trail_version_id"],
            ["evidence_trail_versions.trail_version_id"],
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_records.evidence_id"]),
        sa.UniqueConstraint(
            "trail_version_id",
            "node_id",
            name="uq_evidence_trail_node",
        ),
        _content_hash_constraint("ck_evidence_trail_nodes_content_hash"),
    )
    op.create_table(
        "evidence_trail_relations",
        sa.Column("relation_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        sa.Column("source_node_id", sa.String(length=160), nullable=False),
        sa.Column("target_node_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["trail_version_id"],
            ["evidence_trail_versions.trail_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["trail_version_id", "source_node_id"],
            ["evidence_trail_nodes.trail_version_id", "evidence_trail_nodes.node_id"],
        ),
        sa.ForeignKeyConstraint(
            ["trail_version_id", "target_node_id"],
            ["evidence_trail_nodes.trail_version_id", "evidence_trail_nodes.node_id"],
        ),
        sa.UniqueConstraint(
            "trail_version_id",
            "relation_id",
            name="uq_evidence_trail_relation",
        ),
        _content_hash_constraint("ck_evidence_trail_relations_content_hash"),
    )
    op.create_table(
        "evidence_trail_checks",
        sa.Column("check_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["trail_version_id"],
            ["evidence_trail_versions.trail_version_id"],
        ),
        _content_hash_constraint("ck_evidence_trail_checks_content_hash"),
    )
    op.create_table(
        "evidence_trail_assessments",
        sa.Column("assessment_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["trail_version_id"],
            ["evidence_trail_versions.trail_version_id"],
        ),
        _content_hash_constraint("ck_evidence_trail_assessments_content_hash"),
    )
    op.create_table(
        "report_sentence_bindings",
        sa.Column("binding_id", sa.String(length=160), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        sa.Column("claim_version_id", sa.String(length=160), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["trail_version_id", "claim_version_id"],
            [
                "evidence_trail_versions.trail_version_id",
                "evidence_trail_versions.claim_version_id",
            ],
        ),
        _content_hash_constraint("ck_report_sentence_bindings_content_hash"),
    )
    op.create_table(
        "progress_heads",
        sa.Column("run_id", sa.String(length=128), primary_key=True),
        sa.Column("plan_version_id", sa.String(length=160), nullable=False),
        sa.Column("last_event_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "plan_version_id"],
            ["progress_plans.run_id", "progress_plans.plan_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "last_event_id"],
            ["progress_events.plan_version_id", "progress_events.event_id"],
        ),
    )
    op.create_table(
        "evidence_trail_heads",
        sa.Column("trail_id", sa.String(length=128), primary_key=True),
        sa.Column("trail_version_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trail_id", "trail_version_id", "version"],
            [
                "evidence_trail_versions.trail_id",
                "evidence_trail_versions.trail_version_id",
                "evidence_trail_versions.version",
            ],
        ),
        sa.CheckConstraint("version >= 1", name="ck_evidence_trail_heads_version"),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")

    op.drop_table("evidence_trail_heads")
    op.drop_table("progress_heads")
    op.drop_table("report_sentence_bindings")
    op.drop_table("evidence_trail_assessments")
    op.drop_table("evidence_trail_checks")
    op.drop_table("evidence_trail_relations")
    op.drop_table("evidence_trail_nodes")
    op.drop_table("evidence_trail_versions")
    op.drop_table("completion_decisions")
    op.drop_table("run_checkpoints")
    op.drop_table("run_budgets")
    op.drop_table("progress_events")
    op.drop_table("progress_subtasks")
    op.drop_table("progress_plans")
