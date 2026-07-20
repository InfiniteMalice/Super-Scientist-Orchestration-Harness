from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_handbook_and_harness_evaluation"
down_revision: str | None = "0005_hypotheses_and_representations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORITATIVE_TABLES = (
    "behavior_rule_link_versions",
    "handbook_verification_records",
    "harness_campaigns",
    "harness_partition_manifests",
    "harness_budgets",
    "harness_observations",
    "harness_metrics",
    "harness_confounds",
    "harness_decisions",
)

HARNESS_VARIANTS = (
    "UNCHANGED_HARNESS_SINGLE_ATTEMPT",
    "UNCHANGED_HARNESS_BEST_OF_N",
    "UNCHANGED_HARNESS_RETRY_WITH_FEEDBACK",
    "UNCHANGED_HARNESS_TASK_LEVEL_SEARCH",
    "RANDOM_HARNESS_SEARCH",
    "SIMPLE_PARAMETER_SEARCH",
    "EVOLVED_HARNESS",
)

HARNESS_PARTITIONS = (
    "HARNESS_DISCOVERY_TASKS",
    "HARNESS_VALIDATION_TASKS",
    "HARNESS_TRANSFER_TASKS",
    "HARNESS_REGRESSION_TASKS",
    "HARNESS_SAFETY_TASKS",
)

HARNESS_DECISION_STATUSES = (
    "PROPOSED",
    "DISCOVERY_GAIN",
    "VALIDATION_GAIN",
    "TRANSFER_VALIDATED",
    "REGRESSION_DETECTED",
    "BENCHMARK_SPECIFIC",
    "INCONCLUSIVE",
    "REJECTED",
    "ADMITTED",
    "ROLLED_BACK",
)

ASSESSMENT_OUTCOMES = ("PASSED", "FAILED", "INCONCLUSIVE", "ABSTAINED")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _content_hash_constraint(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _hash_constraint(column_name: str, name: str, *, nullable: bool = False) -> sa.CheckConstraint:
    prefix = f"{column_name} IS NULL OR " if nullable else ""
    return sa.CheckConstraint(
        f"{prefix}(length({column_name}) = 64 AND {column_name} NOT GLOB '*[^0-9a-f]*')",
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
        "behavior_rule_link_versions",
        sa.Column("link_version_id", sa.String(length=160), primary_key=True),
        sa.Column("behavior_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rule_version_id", sa.String(length=192), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["behavioral_rule_versions.rule_version_id"],
        ),
        sa.UniqueConstraint(
            "behavior_id",
            "version",
            name="uq_behavior_rule_link_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_behavior_rule_link_versions_version"),
        _content_hash_constraint("ck_behavior_rule_link_versions_content_hash"),
    )
    op.create_table(
        "handbook_verification_records",
        sa.Column("verification_id", sa.String(length=160), primary_key=True),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        *_record_columns(),
        _hash_constraint("manifest_hash", "ck_handbook_verification_manifest_hash"),
        sa.CheckConstraint(
            f"outcome IN ({_quoted(ASSESSMENT_OUTCOMES)})",
            name="ck_handbook_verification_outcome",
        ),
        _content_hash_constraint("ck_handbook_verification_records_content_hash"),
    )
    op.create_table(
        "harness_campaigns",
        sa.Column("campaign_id", sa.String(length=160), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_record_columns(),
        sa.CheckConstraint("version >= 1", name="ck_harness_campaigns_version"),
        _content_hash_constraint("ck_harness_campaigns_content_hash"),
    )
    op.create_table(
        "harness_partition_manifests",
        sa.Column("partition_manifest_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("partition", sa.String(length=48), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("protected_content_hash", sa.String(length=64), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(["campaign_id"], ["harness_campaigns.campaign_id"]),
        sa.UniqueConstraint(
            "partition_manifest_id",
            "campaign_id",
            name="uq_harness_partition_campaign_scope",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "partition",
            name="uq_harness_campaign_partition",
        ),
        sa.CheckConstraint(
            f"partition IN ({_quoted(HARNESS_PARTITIONS)})",
            name="ck_harness_partition_manifests_partition",
        ),
        _hash_constraint("manifest_hash", "ck_harness_partition_manifests_manifest_hash"),
        _hash_constraint(
            "protected_content_hash",
            "ck_harness_partition_manifests_protected_hash",
            nullable=True,
        ),
        _content_hash_constraint("ck_harness_partition_manifests_content_hash"),
    )
    op.create_table(
        "harness_budgets",
        sa.Column("budget_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(["campaign_id"], ["harness_campaigns.campaign_id"]),
        sa.UniqueConstraint(
            "campaign_id",
            "variant",
            name="uq_harness_campaign_budget_variant",
        ),
        sa.CheckConstraint(
            f"variant IN ({_quoted(HARNESS_VARIANTS)})",
            name="ck_harness_budgets_variant",
        ),
        _content_hash_constraint("ck_harness_budgets_content_hash"),
    )
    op.create_table(
        "harness_observations",
        sa.Column("observation_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("partition_manifest_id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=160), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False),
        sa.Column("candidate_output_hash", sa.String(length=64), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["partition_manifest_id", "campaign_id"],
            [
                "harness_partition_manifests.partition_manifest_id",
                "harness_partition_manifests.campaign_id",
            ],
        ),
        sa.CheckConstraint(
            f"variant IN ({_quoted(HARNESS_VARIANTS)})",
            name="ck_harness_observations_variant",
        ),
        _hash_constraint(
            "candidate_output_hash",
            "ck_harness_observations_candidate_output_hash",
        ),
        _content_hash_constraint("ck_harness_observations_content_hash"),
    )
    op.create_table(
        "harness_metrics",
        sa.Column("result_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=160), nullable=False),
        sa.Column("expected_output_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_output_hash", sa.String(length=64), nullable=False),
        sa.Column("checker_id", sa.String(length=160), nullable=False),
        sa.Column("checker_version", sa.String(length=160), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(["campaign_id"], ["harness_campaigns.campaign_id"]),
        _hash_constraint("expected_output_hash", "ck_harness_metrics_expected_output_hash"),
        _hash_constraint("candidate_output_hash", "ck_harness_metrics_candidate_output_hash"),
        sa.CheckConstraint(
            f"outcome IN ({_quoted(ASSESSMENT_OUTCOMES)})",
            name="ck_harness_metrics_outcome",
        ),
        _content_hash_constraint("ck_harness_metrics_content_hash"),
    )
    op.create_table(
        "harness_confounds",
        sa.Column("confound_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(["campaign_id"], ["harness_campaigns.campaign_id"]),
        _content_hash_constraint("ck_harness_confounds_content_hash"),
    )
    op.create_table(
        "harness_decisions",
        sa.Column("decision_id", sa.String(length=160), primary_key=True),
        sa.Column("campaign_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(["campaign_id"], ["harness_campaigns.campaign_id"]),
        sa.UniqueConstraint(
            "decision_id",
            "campaign_id",
            "status",
            name="uq_harness_decision_head_target",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(HARNESS_DECISION_STATUSES)})",
            name="ck_harness_decisions_status",
        ),
        _content_hash_constraint("ck_harness_decisions_content_hash"),
    )
    op.create_table(
        "harness_campaign_heads",
        sa.Column("campaign_id", sa.String(length=160), primary_key=True),
        sa.Column("decision_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id", "campaign_id", "status"],
            [
                "harness_decisions.decision_id",
                "harness_decisions.campaign_id",
                "harness_decisions.status",
            ],
        ),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")
    op.drop_table("harness_campaign_heads")
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.drop_table(table_name)
