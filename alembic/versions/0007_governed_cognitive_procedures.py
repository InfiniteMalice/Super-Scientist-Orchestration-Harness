from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_governed_cognitive_procedures"
down_revision: str | None = "0006_handbook_and_harness_evaluation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_IDENTIFIER_LENGTH = 200
MAX_IDENTIFIER_UTF8_BYTES = 800
MAX_PHASE_A_CANONICAL_RECORD_BYTES = 64 * 1024 * 1024
MAX_STORAGE_ENVELOPE_OVERHEAD_BYTES = 64 * 1024
MAX_RECORD_JSON_BYTES = MAX_PHASE_A_CANONICAL_RECORD_BYTES + MAX_STORAGE_ENVELOPE_OVERHEAD_BYTES
MAX_CREATED_AT_LENGTH = 40

AUTHORITATIVE_TABLES = (
    "capability_profiles",
    "cohort_plans",
    "diversity_assessments",
    "collaboration_sessions",
    "peer_requests",
    "peer_contributions",
    "topology_events",
    "collaboration_terminations",
    "procedure_compilations",
    "method_direction_outcomes",
    "compiled_progress_plan_bindings",
    "guidance_protocols",
    "guidance_cells",
    "model_harness_protocols",
    "model_harness_cells",
    "model_harness_analyses",
    "harness_execution_traces",
    "reward_assessments",
)

RELATIONSHIP_INDEXES = {
    "cohort_plans": ("request_id",),
    "diversity_assessments": ("cohort_plan_id",),
    "collaboration_sessions": ("cohort_plan_id",),
    "peer_requests": ("session_id",),
    "peer_contributions": ("session_id", "request_id"),
    "topology_events": ("session_id",),
    "method_direction_outcomes": ("compilation_id",),
    "compiled_progress_plan_bindings": ("compilation_id",),
    "guidance_cells": ("protocol_id",),
    "model_harness_cells": ("protocol_id",),
    "harness_execution_traces": ("protocol_id",),
    "reward_assessments": ("trace_id", "observation_id"),
}


def _hash_constraint(column_name: str, table_name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"typeof({column_name}) = 'text' "
        f"AND length({column_name}) = 64 "
        f"AND length(CAST({column_name} AS BLOB)) = 64 "
        f"AND instr({column_name}, char(0)) = 0 "
        f"AND {column_name} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_{table_name}_{column_name}",
    )


def _identifier_constraint(column_name: str, table_name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"typeof({column_name}) = 'text' "
        f"AND length({column_name}) BETWEEN 1 AND {MAX_IDENTIFIER_LENGTH} "
        f"AND length(CAST({column_name} AS BLOB)) BETWEEN 1 "
        f"AND {MAX_IDENTIFIER_UTF8_BYTES} "
        f"AND instr({column_name}, char(0)) = 0",
        name=f"ck_{table_name}_{column_name}",
    )


def _bounded_text_constraint(
    column_name: str,
    table_name: str,
    *,
    maximum: int,
) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"typeof({column_name}) = 'text' "
        f"AND length({column_name}) BETWEEN 1 AND {maximum} "
        f"AND length(CAST({column_name} AS BLOB)) BETWEEN 1 AND {maximum} "
        f"AND instr({column_name}, char(0)) = 0",
        name=f"ck_{table_name}_{column_name}",
    )


def _create_record_table(
    name: str,
    id_column: sa.Column[object],
    relationship_columns: Sequence[sa.Column[object]] = (),
    table_constraints: Sequence[sa.Constraint] = (),
) -> None:
    op.create_table(
        name,
        id_column,
        *relationship_columns,
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "transaction_id",
            sa.String(length=MAX_IDENTIFIER_LENGTH),
            sa.ForeignKey(
                "transactions.proposal_id",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
        ),
        sa.Column(
            "governing_policy_hash",
            sa.String(length=64),
            sa.ForeignKey(
                "governance_policies.policy_hash",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        *table_constraints,
        *(
            _identifier_constraint(str(column.name), name)
            for column in (id_column, *relationship_columns)
        ),
        _identifier_constraint("transaction_id", name),
        sa.CheckConstraint(
            "typeof(schema_version) = 'integer' AND schema_version = 1",
            name=f"ck_{name}_schema_version",
        ),
        _bounded_text_constraint("record_json", name, maximum=MAX_RECORD_JSON_BYTES),
        _hash_constraint("content_hash", name),
        _hash_constraint("governing_policy_hash", name),
        _bounded_text_constraint("created_at", name, maximum=MAX_CREATED_AT_LENGTH),
    )
    for column_name in (
        *RELATIONSHIP_INDEXES.get(name, ()),
        "transaction_id",
        "governing_policy_hash",
    ):
        op.create_index(f"ix_{name}_{column_name}", name, [column_name])
    _create_append_only_triggers(name)


def _create_append_only_triggers(table_name: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name} "
        "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
    )
    op.execute(
        f"CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name} "
        "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
    )


def upgrade() -> None:
    _create_record_table(
        "capability_profiles",
        sa.Column("profile_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
    )
    _create_record_table(
        "cohort_plans",
        sa.Column("cohort_plan_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (sa.Column("request_id", sa.String(length=MAX_IDENTIFIER_LENGTH), nullable=False),),
    )
    _create_record_table(
        "diversity_assessments",
        sa.Column(
            "diversity_assessment_id",
            sa.String(length=MAX_IDENTIFIER_LENGTH),
            primary_key=True,
        ),
        (
            sa.Column(
                "cohort_plan_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("cohort_plans.cohort_plan_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "collaboration_sessions",
        sa.Column("session_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "cohort_plan_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("cohort_plans.cohort_plan_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "peer_requests",
        sa.Column("request_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "session_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("collaboration_sessions.session_id"),
                nullable=False,
            ),
        ),
        (
            sa.UniqueConstraint(
                "request_id",
                "session_id",
                name="uq_peer_requests_session_scope",
            ),
        ),
    )
    _create_record_table(
        "peer_contributions",
        sa.Column("contribution_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column("session_id", sa.String(length=MAX_IDENTIFIER_LENGTH), nullable=False),
            sa.Column("request_id", sa.String(length=MAX_IDENTIFIER_LENGTH), nullable=False),
        ),
        (
            sa.ForeignKeyConstraint(
                ["request_id", "session_id"],
                ["peer_requests.request_id", "peer_requests.session_id"],
            ),
        ),
    )
    _create_record_table(
        "topology_events",
        sa.Column("event_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "session_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("collaboration_sessions.session_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "collaboration_terminations",
        sa.Column(
            "session_id",
            sa.String(length=MAX_IDENTIFIER_LENGTH),
            sa.ForeignKey("collaboration_sessions.session_id"),
            primary_key=True,
        ),
    )
    _create_record_table(
        "procedure_compilations",
        sa.Column("compilation_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
    )
    _create_record_table(
        "method_direction_outcomes",
        sa.Column("outcome_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "compilation_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("procedure_compilations.compilation_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "compiled_progress_plan_bindings",
        sa.Column("binding_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "compilation_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("procedure_compilations.compilation_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "guidance_protocols",
        sa.Column("protocol_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
    )
    _create_record_table(
        "guidance_cells",
        sa.Column("cell_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "protocol_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("guidance_protocols.protocol_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "model_harness_protocols",
        sa.Column("protocol_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
    )
    _create_record_table(
        "model_harness_cells",
        sa.Column("cell_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "protocol_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("model_harness_protocols.protocol_id"),
                nullable=False,
            ),
        ),
    )
    _create_record_table(
        "model_harness_analyses",
        sa.Column(
            "protocol_id",
            sa.String(length=MAX_IDENTIFIER_LENGTH),
            sa.ForeignKey("model_harness_protocols.protocol_id"),
            primary_key=True,
        ),
    )
    _create_record_table(
        "harness_execution_traces",
        sa.Column("trace_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (sa.Column("protocol_id", sa.String(length=MAX_IDENTIFIER_LENGTH), nullable=False),),
    )
    _create_record_table(
        "reward_assessments",
        sa.Column("assessment_id", sa.String(length=MAX_IDENTIFIER_LENGTH), primary_key=True),
        (
            sa.Column(
                "trace_id",
                sa.String(length=MAX_IDENTIFIER_LENGTH),
                sa.ForeignKey("harness_execution_traces.trace_id"),
                nullable=False,
            ),
            sa.Column("observation_id", sa.String(length=MAX_IDENTIFIER_LENGTH), nullable=False),
        ),
    )


def downgrade() -> None:
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")
        indexed_columns = (
            *RELATIONSHIP_INDEXES.get(table_name, ()),
            "transaction_id",
            "governing_policy_hash",
        )
        for column_name in reversed(indexed_columns):
            op.drop_index(f"ix_{table_name}_{column_name}", table_name=table_name)
        op.drop_table(table_name)
