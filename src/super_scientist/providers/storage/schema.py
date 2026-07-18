from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
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


def _content_hash_constraint(name: str) -> CheckConstraint:
    return CheckConstraint(
        "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


research_runs = Table(
    "research_runs",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_research_runs_content_hash"),
)

research_run_events = Table(
    "research_run_events",
    metadata,
    Column("run_event_id", String(160), primary_key=True),
    Column("run_id", String(128), ForeignKey("research_runs.run_id"), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_research_run_events_content_hash"),
)

configuration_versions = Table(
    "configuration_versions",
    metadata,
    Column("configuration_version_id", String(160), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_configuration_versions_content_hash"),
)

self_improvement_measurements = Table(
    "self_improvement_measurements",
    metadata,
    Column("measurement_id", String(128), primary_key=True),
    Column("run_id", String(128), ForeignKey("research_runs.run_id"), nullable=False),
    Column(
        "evaluator_audit_id",
        String(128),
        ForeignKey("evaluator_audits.evaluator_audit_id"),
        nullable=False,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_self_improvement_measurements_content_hash"),
)

evaluator_audits = Table(
    "evaluator_audits",
    metadata,
    Column("evaluator_audit_id", String(128), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_evaluator_audits_content_hash"),
)

evaluator_versions = Table(
    "evaluator_versions",
    metadata,
    Column("evaluator_version_id", String(160), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_evaluator_versions_content_hash"),
)

evaluator_succession_decisions = Table(
    "evaluator_succession_decisions",
    metadata,
    Column("evaluator_succession_decision_id", String(160), primary_key=True),
    Column(
        "predecessor_evaluator_version_id",
        String(160),
        ForeignKey("evaluator_versions.evaluator_version_id"),
        nullable=False,
    ),
    Column(
        "candidate_evaluator_version_id",
        String(160),
        ForeignKey("evaluator_versions.evaluator_version_id"),
        nullable=False,
    ),
    Column(
        "evaluator_audit_id",
        String(128),
        ForeignKey("evaluator_audits.evaluator_audit_id"),
        nullable=False,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_evaluator_succession_decisions_content_hash"),
)

evaluator_collapse_records = Table(
    "evaluator_collapse_records",
    metadata,
    Column("evaluator_collapse_record_id", String(160), primary_key=True),
    Column(
        "evaluator_version_id",
        String(160),
        ForeignKey("evaluator_versions.evaluator_version_id"),
        nullable=False,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_evaluator_collapse_records_content_hash"),
)

research_run_heads = Table(
    "research_run_heads",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column(
        "run_event_id",
        String(160),
        ForeignKey("research_run_events.run_event_id"),
        nullable=False,
    ),
)

evaluator_heads = Table(
    "evaluator_heads",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column(
        "evaluator_version_id",
        String(160),
        ForeignKey("evaluator_versions.evaluator_version_id"),
        nullable=False,
    ),
    CheckConstraint("singleton_id = 1", name="ck_evaluator_heads_singleton"),
)
