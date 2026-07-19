from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
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
    UniqueConstraint("run_id", "run_event_id", name="uq_research_run_event_run"),
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
    Column("run_event_id", String(160), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "run_event_id"],
        ["research_run_events.run_id", "research_run_events.run_event_id"],
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

progress_plans = Table(
    "progress_plans",
    metadata,
    Column("plan_version_id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(["run_id"], ["research_runs.run_id"]),
    UniqueConstraint("run_id", "plan_version_id", name="uq_progress_plan_run"),
    _content_hash_constraint("ck_progress_plans_content_hash"),
)

progress_subtasks = Table(
    "progress_subtasks",
    metadata,
    Column("subtask_id", String(160), primary_key=True),
    Column("plan_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(["plan_version_id"], ["progress_plans.plan_version_id"]),
    UniqueConstraint("plan_version_id", "subtask_id", name="uq_progress_subtask_plan"),
    _content_hash_constraint("ck_progress_subtasks_content_hash"),
)

progress_events = Table(
    "progress_events",
    metadata,
    Column("event_id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("plan_version_id", String(160), nullable=False),
    Column("subtask_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "plan_version_id"],
        ["progress_plans.run_id", "progress_plans.plan_version_id"],
    ),
    ForeignKeyConstraint(
        ["plan_version_id", "subtask_id"],
        ["progress_subtasks.plan_version_id", "progress_subtasks.subtask_id"],
    ),
    UniqueConstraint("plan_version_id", "event_id", name="uq_progress_event_plan"),
    _content_hash_constraint("ck_progress_events_content_hash"),
)

run_budgets = Table(
    "run_budgets",
    metadata,
    Column("budget_id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("plan_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "plan_version_id"],
        ["progress_plans.run_id", "progress_plans.plan_version_id"],
    ),
    _content_hash_constraint("ck_run_budgets_content_hash"),
)

run_checkpoints = Table(
    "run_checkpoints",
    metadata,
    Column("checkpoint_id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("plan_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "plan_version_id"],
        ["progress_plans.run_id", "progress_plans.plan_version_id"],
    ),
    _content_hash_constraint("ck_run_checkpoints_content_hash"),
)

completion_decisions = Table(
    "completion_decisions",
    metadata,
    Column("completion_decision_id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("plan_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "plan_version_id"],
        ["progress_plans.run_id", "progress_plans.plan_version_id"],
    ),
    _content_hash_constraint("ck_completion_decisions_content_hash"),
)

evidence_trail_versions = Table(
    "evidence_trail_versions",
    metadata,
    Column("trail_version_id", String(160), primary_key=True),
    Column("trail_id", String(128), nullable=False),
    Column("claim_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(["claim_version_id"], ["claim_versions.claim_version_id"]),
    UniqueConstraint("trail_id", "version", name="uq_evidence_trail_version"),
    UniqueConstraint(
        "trail_id",
        "trail_version_id",
        "version",
        name="uq_evidence_trail_head_target",
    ),
    UniqueConstraint(
        "trail_version_id",
        "claim_version_id",
        name="uq_evidence_trail_claim",
    ),
    CheckConstraint("version >= 1", name="ck_evidence_trail_versions_version"),
    _content_hash_constraint("ck_evidence_trail_versions_content_hash"),
)

evidence_trail_nodes = Table(
    "evidence_trail_nodes",
    metadata,
    Column("node_id", String(160), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("evidence_id", String(128), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["trail_version_id"],
        ["evidence_trail_versions.trail_version_id"],
    ),
    ForeignKeyConstraint(["evidence_id"], ["evidence_records.evidence_id"]),
    UniqueConstraint("trail_version_id", "node_id", name="uq_evidence_trail_node"),
    _content_hash_constraint("ck_evidence_trail_nodes_content_hash"),
)

evidence_trail_relations = Table(
    "evidence_trail_relations",
    metadata,
    Column("relation_id", String(160), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("source_node_id", String(160), nullable=False),
    Column("target_node_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["trail_version_id"],
        ["evidence_trail_versions.trail_version_id"],
    ),
    ForeignKeyConstraint(
        ["trail_version_id", "source_node_id"],
        ["evidence_trail_nodes.trail_version_id", "evidence_trail_nodes.node_id"],
    ),
    ForeignKeyConstraint(
        ["trail_version_id", "target_node_id"],
        ["evidence_trail_nodes.trail_version_id", "evidence_trail_nodes.node_id"],
    ),
    UniqueConstraint(
        "trail_version_id",
        "relation_id",
        name="uq_evidence_trail_relation",
    ),
    _content_hash_constraint("ck_evidence_trail_relations_content_hash"),
)

evidence_trail_checks = Table(
    "evidence_trail_checks",
    metadata,
    Column("check_id", String(160), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["trail_version_id"],
        ["evidence_trail_versions.trail_version_id"],
    ),
    _content_hash_constraint("ck_evidence_trail_checks_content_hash"),
)

evidence_trail_assessments = Table(
    "evidence_trail_assessments",
    metadata,
    Column("assessment_id", String(160), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["trail_version_id"],
        ["evidence_trail_versions.trail_version_id"],
    ),
    _content_hash_constraint("ck_evidence_trail_assessments_content_hash"),
)

report_sentence_bindings = Table(
    "report_sentence_bindings",
    metadata,
    Column("binding_id", String(160), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("claim_version_id", String(160), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["trail_version_id", "claim_version_id"],
        [
            "evidence_trail_versions.trail_version_id",
            "evidence_trail_versions.claim_version_id",
        ],
    ),
    _content_hash_constraint("ck_report_sentence_bindings_content_hash"),
)

progress_heads = Table(
    "progress_heads",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("plan_version_id", String(160), nullable=False),
    Column("last_event_id", String(160), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "plan_version_id"],
        ["progress_plans.run_id", "progress_plans.plan_version_id"],
    ),
    ForeignKeyConstraint(
        ["plan_version_id", "last_event_id"],
        ["progress_events.plan_version_id", "progress_events.event_id"],
    ),
)

evidence_trail_heads = Table(
    "evidence_trail_heads",
    metadata,
    Column("trail_id", String(128), primary_key=True),
    Column("trail_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["trail_id", "trail_version_id", "version"],
        [
            "evidence_trail_versions.trail_id",
            "evidence_trail_versions.trail_version_id",
            "evidence_trail_versions.version",
        ],
    ),
    CheckConstraint("version >= 1", name="ck_evidence_trail_heads_version"),
)

rule_incidents = Table(
    "rule_incidents",
    metadata,
    Column("incident_id", String(128), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_rule_incidents_content_hash"),
)

behavioral_rule_versions = Table(
    "behavioral_rule_versions",
    metadata,
    Column("rule_version_id", String(192), primary_key=True),
    Column("rule_id", String(128), nullable=False),
    Column("semantic_version", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint(
        "rule_id",
        "semantic_version",
        name="uq_behavioral_rule_semantic_version",
    ),
    UniqueConstraint(
        "rule_id",
        "rule_version_id",
        "semantic_version",
        "status",
        name="uq_behavioral_rule_head_target",
    ),
    CheckConstraint(
        "length(semantic_version) BETWEEN 5 AND 32",
        name="ck_behavioral_rule_semantic_version_length",
    ),
    _content_hash_constraint("ck_behavioral_rule_versions_content_hash"),
)

reviewer_assessments = Table(
    "reviewer_assessments",
    metadata,
    Column("assessment_id", String(160), primary_key=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_reviewer_assessments_content_hash"),
)

rule_consolidation_decisions = Table(
    "rule_consolidation_decisions",
    metadata,
    Column("consolidation_decision_id", String(192), primary_key=True),
    Column(
        "resulting_rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        nullable=True,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_rule_consolidation_decisions_content_hash"),
)

rule_regression_cases = Table(
    "rule_regression_cases",
    metadata,
    Column("regression_case_id", String(160), primary_key=True),
    Column(
        "rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        nullable=False,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_rule_regression_cases_content_hash"),
)

behavioral_rule_version_incidents = Table(
    "behavioral_rule_version_incidents",
    metadata,
    Column(
        "rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "incident_id",
        String(128),
        ForeignKey("rule_incidents.incident_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_behavioral_rule_version_incidents_position"),
    UniqueConstraint(
        "rule_version_id",
        "incident_id",
        name="uq_behavioral_rule_version_incidents_reference",
    ),
)

behavioral_rule_version_supersessions = Table(
    "behavioral_rule_version_supersessions",
    metadata,
    Column(
        "rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "predecessor_rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        nullable=False,
    ),
    CheckConstraint(
        "position >= 0",
        name="ck_behavioral_rule_version_supersessions_position",
    ),
    UniqueConstraint(
        "rule_version_id",
        "predecessor_rule_version_id",
        name="uq_behavioral_rule_version_supersessions_reference",
    ),
)

reviewer_assessment_rule_versions = Table(
    "reviewer_assessment_rule_versions",
    metadata,
    Column(
        "assessment_id",
        String(160),
        ForeignKey("reviewer_assessments.assessment_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_reviewer_assessment_rule_versions_position"),
    UniqueConstraint(
        "assessment_id",
        "rule_version_id",
        name="uq_reviewer_assessment_rule_versions_reference",
    ),
)

reviewer_assessment_incidents = Table(
    "reviewer_assessment_incidents",
    metadata,
    Column(
        "assessment_id",
        String(160),
        ForeignKey("reviewer_assessments.assessment_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "incident_id",
        String(128),
        ForeignKey("rule_incidents.incident_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_reviewer_assessment_incidents_position"),
    UniqueConstraint(
        "assessment_id",
        "incident_id",
        name="uq_reviewer_assessment_incidents_reference",
    ),
)

rule_consolidation_assessments = Table(
    "rule_consolidation_assessments",
    metadata,
    Column(
        "consolidation_decision_id",
        String(192),
        ForeignKey("rule_consolidation_decisions.consolidation_decision_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "assessment_id",
        String(160),
        ForeignKey("reviewer_assessments.assessment_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_rule_consolidation_assessments_position"),
    UniqueConstraint(
        "consolidation_decision_id",
        "assessment_id",
        name="uq_rule_consolidation_assessments_reference",
    ),
)

rule_consolidation_incidents = Table(
    "rule_consolidation_incidents",
    metadata,
    Column(
        "consolidation_decision_id",
        String(192),
        ForeignKey("rule_consolidation_decisions.consolidation_decision_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "incident_id",
        String(128),
        ForeignKey("rule_incidents.incident_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_rule_consolidation_incidents_position"),
    UniqueConstraint(
        "consolidation_decision_id",
        "incident_id",
        name="uq_rule_consolidation_incidents_reference",
    ),
)

rule_regression_case_incidents = Table(
    "rule_regression_case_incidents",
    metadata,
    Column(
        "regression_case_id",
        String(160),
        ForeignKey("rule_regression_cases.regression_case_id"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "incident_id",
        String(128),
        ForeignKey("rule_incidents.incident_id"),
        nullable=False,
    ),
    CheckConstraint("position >= 0", name="ck_rule_regression_case_incidents_position"),
    UniqueConstraint(
        "regression_case_id",
        "incident_id",
        name="uq_rule_regression_case_incidents_reference",
    ),
)

behavioral_rule_heads = Table(
    "behavioral_rule_heads",
    metadata,
    Column("rule_id", String(128), primary_key=True),
    Column("rule_version_id", String(192), nullable=False),
    Column("semantic_version", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    ForeignKeyConstraint(
        ["rule_id", "rule_version_id", "semantic_version", "status"],
        [
            "behavioral_rule_versions.rule_id",
            "behavioral_rule_versions.rule_version_id",
            "behavioral_rule_versions.semantic_version",
            "behavioral_rule_versions.status",
        ],
    ),
)
