from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_epistemic_kernel"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = (
    "governance_policies",
    "evidence_records",
    "claim_versions",
    "transactions",
    "audit_events",
)


def _create_append_only_triggers() -> None:
    for table_name in APPEND_ONLY_TABLES:
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
        "governance_policies",
        sa.Column("policy_hash", sa.String(length=64), primary_key=True),
        sa.Column("policy_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "governance_state",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("active_policy_hash", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "evidence_records",
        sa.Column("evidence_id", sa.String(length=128), primary_key=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "claim_versions",
        sa.Column("claim_version_id", sa.String(length=160), primary_key=True),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("claim_id", "version", name="uq_claim_version"),
    )
    op.create_table(
        "claim_heads",
        sa.Column("claim_id", sa.String(length=128), primary_key=True),
        sa.Column("claim_version_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "transactions",
        sa.Column("proposal_id", sa.String(length=128), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("proposal_hash", sa.String(length=64), nullable=False),
        sa.Column("proposal_json", sa.Text(), nullable=False),
        sa.Column("decision_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("event_json", sa.Text(), nullable=False),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    for table_name in reversed(APPEND_ONLY_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")

    op.drop_table("audit_events")
    op.drop_table("transactions")
    op.drop_table("claim_heads")
    op.drop_table("claim_versions")
    op.drop_table("evidence_records")
    op.drop_table("governance_state")
    op.drop_table("governance_policies")
