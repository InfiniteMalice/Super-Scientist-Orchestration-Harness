from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

governance_policies = Table(
    "governance_policies",
    metadata,
    Column("policy_hash", String(64), primary_key=True),
    Column("policy_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

governance_state = Table(
    "governance_state",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column("active_policy_hash", String(64), nullable=False),
    CheckConstraint("singleton_id = 1", name="ck_governance_state_singleton"),
)

evidence_records = Table(
    "evidence_records",
    metadata,
    Column("evidence_id", String(128), primary_key=True),
    Column("content_hash", String(64), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

claim_versions = Table(
    "claim_versions",
    metadata,
    Column("claim_version_id", String(160), primary_key=True),
    Column("claim_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("claim_id", "version", name="uq_claim_version"),
)

claim_heads = Table(
    "claim_heads",
    metadata,
    Column("claim_id", String(128), primary_key=True),
    Column("claim_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
)

transactions = Table(
    "transactions",
    metadata,
    Column("proposal_id", String(128), primary_key=True),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("intent_fingerprint", String(64), nullable=True),
    Column("proposal_hash", String(64), nullable=False),
    Column("proposal_json", Text, nullable=False),
    Column("decision_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(128), nullable=False, unique=True),
    Column("previous_hash", String(64), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("event_hash", String(64), nullable=False),
    Column("event_json", Text, nullable=False),
)
