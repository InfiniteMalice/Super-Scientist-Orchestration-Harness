from sqlalchemy import (
    CheckConstraint,
    Column,
    Constraint,
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


def _optional_hash_constraint(column_name: str, name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column_name} IS NULL OR "
        f"(length({column_name}) = 64 AND {column_name} NOT GLOB '*[^0-9a-f]*')",
        name=name,
    )


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


PRIMITIVE_STATUSES = (
    "PROPOSED",
    "DUPLICATE_SUSPECTED",
    "UNDER_DEFINITION",
    "EXPERIMENTAL",
    "LOCALLY_USEFUL",
    "REPLICATED",
    "STABILIZED",
    "REJECTED",
    "SUPERSEDED",
    "RETIRED",
)

HYPOTHESIS_STATUSES = (
    "SOURCE_PATTERN_REVIEW_PENDING",
    "GENERIC_PATTERN_EXTRACTED",
    "TRANSFER_TESTING",
    "TRANSFER_VALIDATED",
    "DOMAIN_SPECIFIC",
    "BENCHMARK_SPECIFIC",
    "REJECTED",
    "ADMITTED_TO_SSOH",
)

MECHANISM_CATEGORIES = (
    "FORMAL_VERIFIER",
    "INDEPENDENT_DETERMINISTIC_CHECKER",
    "LEARNED_JUDGE",
)

EXECUTION_MODES = ("METADATA_ONLY", "BUILTIN_DETERMINISTIC_SIMULATOR")
BUILTIN_SIMULATORS = ("thermal-chamber-v1", "exponential-decay-v1")

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

primitive_versions = Table(
    "primitive_versions",
    metadata,
    Column("primitive_version_id", String(192), primary_key=True),
    Column("primitive_id", String(128), nullable=False),
    Column("semantic_version", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint(
        "primitive_id",
        "semantic_version",
        name="uq_primitive_semantic_version",
    ),
    UniqueConstraint(
        "primitive_id",
        "primitive_version_id",
        "semantic_version",
        "status",
        name="uq_primitive_head_target",
    ),
    CheckConstraint(
        "length(semantic_version) BETWEEN 5 AND 32",
        name="ck_primitive_semantic_version_length",
    ),
    CheckConstraint(
        f"status IN ({_quoted(PRIMITIVE_STATUSES)})",
        name="ck_primitive_versions_status",
    ),
    _content_hash_constraint("ck_primitive_versions_content_hash"),
)

hypothesis_versions = Table(
    "hypothesis_versions",
    metadata,
    Column("hypothesis_version_id", String(192), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("admission_status", String(40), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("hypothesis_id", "version", name="uq_hypothesis_version"),
    UniqueConstraint(
        "hypothesis_id",
        "hypothesis_version_id",
        name="uq_hypothesis_version_scope",
    ),
    UniqueConstraint(
        "hypothesis_id",
        "hypothesis_version_id",
        "version",
        name="uq_hypothesis_revision_target",
    ),
    UniqueConstraint(
        "hypothesis_id",
        "hypothesis_version_id",
        "version",
        "admission_status",
        name="uq_hypothesis_head_target",
    ),
    CheckConstraint("version >= 1", name="ck_hypothesis_versions_version"),
    CheckConstraint(
        f"admission_status IN ({_quoted(HYPOTHESIS_STATUSES)})",
        name="ck_hypothesis_versions_admission_status",
    ),
    _content_hash_constraint("ck_hypothesis_versions_content_hash"),
)

executable_model_specs = Table(
    "executable_model_specs",
    metadata,
    Column("model_spec_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("execution_mode", String(48), nullable=False),
    Column("artifact_hash", String(64), nullable=True),
    Column("artifact_media_type", String(128), nullable=True),
    Column("artifact_size_bytes", Integer, nullable=True),
    Column("builtin_simulator_id", String(64), nullable=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["hypothesis_id", "hypothesis_version_id"],
        ["hypothesis_versions.hypothesis_id", "hypothesis_versions.hypothesis_version_id"],
    ),
    UniqueConstraint(
        "model_spec_id",
        "hypothesis_id",
        name="uq_model_spec_hypothesis",
    ),
    UniqueConstraint(
        "model_spec_id",
        "hypothesis_id",
        "hypothesis_version_id",
        "execution_mode",
        name="uq_model_spec_hypothesis_execution",
    ),
    CheckConstraint(
        f"execution_mode IN ({_quoted(EXECUTION_MODES)})",
        name="ck_model_specs_execution_mode",
    ),
    CheckConstraint(
        "((execution_mode = 'METADATA_ONLY' AND artifact_hash IS NOT NULL "
        "AND artifact_media_type IS NOT NULL AND artifact_size_bytes IS NOT NULL "
        "AND artifact_size_bytes >= 0 AND builtin_simulator_id IS NULL) OR "
        "(execution_mode = 'BUILTIN_DETERMINISTIC_SIMULATOR' "
        "AND artifact_hash IS NULL AND artifact_media_type IS NULL "
        "AND artifact_size_bytes IS NULL "
        f"AND builtin_simulator_id IN ({_quoted(BUILTIN_SIMULATORS)})))",
        name="ck_model_specs_safe_execution_shape",
    ),
    _optional_hash_constraint("artifact_hash", "ck_model_specs_artifact_hash"),
    _content_hash_constraint("ck_executable_model_specs_content_hash"),
)

verification_mechanism_specs = Table(
    "verification_mechanism_specs",
    metadata,
    Column("mechanism_spec_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("mechanism_category", String(48), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["hypothesis_id", "hypothesis_version_id"],
        ["hypothesis_versions.hypothesis_id", "hypothesis_versions.hypothesis_version_id"],
    ),
    UniqueConstraint(
        "mechanism_spec_id",
        "hypothesis_id",
        name="uq_mechanism_spec_hypothesis",
    ),
    UniqueConstraint(
        "mechanism_spec_id",
        "hypothesis_id",
        "hypothesis_version_id",
        "mechanism_category",
        name="uq_mechanism_spec_hypothesis_category",
    ),
    CheckConstraint(
        f"mechanism_category IN ({_quoted(MECHANISM_CATEGORIES)})",
        name="ck_mechanism_specs_category",
    ),
    _content_hash_constraint("ck_verification_mechanism_specs_content_hash"),
)

simulation_results = Table(
    "simulation_results",
    metadata,
    Column("simulation_result_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("model_spec_id", String(160), nullable=False),
    Column("execution_mode", String(48), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["model_spec_id", "hypothesis_id", "hypothesis_version_id", "execution_mode"],
        [
            "executable_model_specs.model_spec_id",
            "executable_model_specs.hypothesis_id",
            "executable_model_specs.hypothesis_version_id",
            "executable_model_specs.execution_mode",
        ],
    ),
    UniqueConstraint(
        "simulation_result_id",
        "hypothesis_id",
        name="uq_simulation_result_hypothesis",
    ),
    UniqueConstraint(
        "simulation_result_id",
        "hypothesis_id",
        "hypothesis_version_id",
        "model_spec_id",
        "execution_mode",
        name="uq_simulation_result_scope",
    ),
    CheckConstraint(
        "execution_mode = 'BUILTIN_DETERMINISTIC_SIMULATOR'",
        name="ck_simulation_results_execution_mode",
    ),
    _content_hash_constraint("ck_simulation_results_content_hash"),
)

verification_results = Table(
    "verification_results",
    metadata,
    Column("verification_result_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("mechanism_spec_id", String(160), nullable=False),
    Column("mechanism_category", String(48), nullable=False),
    Column("result_category", String(48), nullable=False),
    Column("model_spec_id", String(160), nullable=True),
    Column("model_execution_mode", String(48), nullable=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        [
            "mechanism_spec_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "mechanism_category",
        ],
        [
            "verification_mechanism_specs.mechanism_spec_id",
            "verification_mechanism_specs.hypothesis_id",
            "verification_mechanism_specs.hypothesis_version_id",
            "verification_mechanism_specs.mechanism_category",
        ],
    ),
    ForeignKeyConstraint(
        [
            "model_spec_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "model_execution_mode",
        ],
        [
            "executable_model_specs.model_spec_id",
            "executable_model_specs.hypothesis_id",
            "executable_model_specs.hypothesis_version_id",
            "executable_model_specs.execution_mode",
        ],
    ),
    UniqueConstraint(
        "verification_result_id",
        "hypothesis_id",
        name="uq_verification_result_hypothesis",
    ),
    UniqueConstraint(
        "verification_result_id",
        "hypothesis_id",
        "hypothesis_version_id",
        "model_spec_id",
        "model_execution_mode",
        name="uq_verification_result_model_scope",
    ),
    CheckConstraint(
        "((mechanism_category = 'FORMAL_VERIFIER' "
        "AND result_category = 'FORMAL_VERIFICATION_RESULT') OR "
        "(mechanism_category = 'INDEPENDENT_DETERMINISTIC_CHECKER' "
        "AND result_category = 'DETERMINISTIC_CHECK_RESULT') OR "
        "(mechanism_category = 'LEARNED_JUDGE' "
        "AND result_category = 'LEARNED_JUDGE_RESULT'))",
        name="ck_verification_results_category_pair",
    ),
    CheckConstraint(
        "((model_spec_id IS NULL AND model_execution_mode IS NULL) OR "
        "(model_spec_id IS NOT NULL AND model_execution_mode IS NOT NULL))",
        name="ck_verification_results_model_pair",
    ),
    _content_hash_constraint("ck_verification_results_content_hash"),
)

primitive_evaluations = Table(
    "primitive_evaluations",
    metadata,
    Column("primitive_evaluation_id", String(160), primary_key=True),
    Column(
        "primitive_version_id",
        String(192),
        ForeignKey("primitive_versions.primitive_version_id"),
        nullable=False,
    ),
    Column("frame", String(24), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint(
        "frame IN ('OLD_FRAME', 'NEW_FRAME')",
        name="ck_primitive_evaluations_frame",
    ),
    _content_hash_constraint("ck_primitive_evaluations_content_hash"),
)

counterexample_records = Table(
    "counterexample_records",
    metadata,
    Column("counterexample_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("model_spec_id", String(160), nullable=True),
    Column("model_execution_mode", String(48), nullable=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        [
            "model_spec_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "model_execution_mode",
        ],
        [
            "executable_model_specs.model_spec_id",
            "executable_model_specs.hypothesis_id",
            "executable_model_specs.hypothesis_version_id",
            "executable_model_specs.execution_mode",
        ],
    ),
    UniqueConstraint(
        "counterexample_id",
        "hypothesis_id",
        name="uq_counterexample_hypothesis",
    ),
    UniqueConstraint(
        "counterexample_id",
        "hypothesis_id",
        "hypothesis_version_id",
        "model_spec_id",
        "model_execution_mode",
        name="uq_counterexample_model_scope",
    ),
    CheckConstraint(
        "((model_spec_id IS NULL AND model_execution_mode IS NULL) OR "
        "(model_spec_id IS NOT NULL AND model_execution_mode IS NOT NULL))",
        name="ck_counterexamples_model_pair",
    ),
    _content_hash_constraint("ck_counterexample_records_content_hash"),
)

hypothesis_revisions = Table(
    "hypothesis_revisions",
    metadata,
    Column("revision_id", String(160), primary_key=True),
    Column("hypothesis_id", String(128), nullable=False),
    Column("prior_hypothesis_version_id", String(192), nullable=False),
    Column("prior_version", Integer, nullable=False),
    Column("resulting_hypothesis_version_id", String(192), nullable=False),
    Column("resulting_version", Integer, nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["hypothesis_id", "prior_hypothesis_version_id", "prior_version"],
        [
            "hypothesis_versions.hypothesis_id",
            "hypothesis_versions.hypothesis_version_id",
            "hypothesis_versions.version",
        ],
    ),
    UniqueConstraint(
        "revision_id",
        "hypothesis_id",
        name="uq_hypothesis_revision_scope",
    ),
    UniqueConstraint(
        "revision_id",
        "hypothesis_id",
        "resulting_hypothesis_version_id",
        "resulting_version",
        name="uq_hypothesis_revision_terminal",
    ),
    ForeignKeyConstraint(
        ["hypothesis_id", "resulting_hypothesis_version_id", "resulting_version"],
        [
            "hypothesis_versions.hypothesis_id",
            "hypothesis_versions.hypothesis_version_id",
            "hypothesis_versions.version",
        ],
    ),
    CheckConstraint(
        "prior_version >= 1 AND resulting_version = prior_version + 1",
        name="ck_hypothesis_revisions_contiguous",
    ),
    CheckConstraint(
        "prior_hypothesis_version_id <> resulting_hypothesis_version_id",
        name="ck_hypothesis_revisions_distinct_versions",
    ),
    _content_hash_constraint("ck_hypothesis_revisions_content_hash"),
)

hypothesis_admission_decisions = Table(
    "hypothesis_admission_decisions",
    metadata,
    Column("admission_decision_id", String(160), primary_key=True),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("hypothesis_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("admission_status", String(40), nullable=False),
    Column("terminal_revision_id", String(160), nullable=True),
    Column("terminal_revision_position", Integer, nullable=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["hypothesis_id", "hypothesis_version_id", "version", "admission_status"],
        [
            "hypothesis_versions.hypothesis_id",
            "hypothesis_versions.hypothesis_version_id",
            "hypothesis_versions.version",
            "hypothesis_versions.admission_status",
        ],
    ),
    ForeignKeyConstraint(
        ["terminal_revision_id", "hypothesis_id", "hypothesis_version_id", "version"],
        [
            "hypothesis_revisions.revision_id",
            "hypothesis_revisions.hypothesis_id",
            "hypothesis_revisions.resulting_hypothesis_version_id",
            "hypothesis_revisions.resulting_version",
        ],
    ),
    UniqueConstraint(
        "admission_decision_id",
        "hypothesis_id",
        name="uq_hypothesis_admission_scope",
    ),
    CheckConstraint("version >= 1", name="ck_admission_decisions_version"),
    CheckConstraint(
        f"admission_status IN ({_quoted(HYPOTHESIS_STATUSES)})",
        name="ck_admission_decisions_status",
    ),
    CheckConstraint(
        "((terminal_revision_id IS NULL AND terminal_revision_position IS NULL) OR "
        "(terminal_revision_id IS NOT NULL AND terminal_revision_position >= 0))",
        name="ck_admission_terminal_revision_pair",
    ),
    _content_hash_constraint("ck_hypothesis_admission_decisions_content_hash"),
)


def _ordered_reference_table(
    table_name: str,
    owner_column: str,
    owner_length: int,
    owner_table: str,
    reference_column: str,
    reference_length: int,
    reference_table: str,
    reference_target_column: str | None = None,
) -> Table:
    return Table(
        table_name,
        metadata,
        Column(
            owner_column,
            String(owner_length),
            ForeignKey(f"{owner_table}.{owner_column}"),
            primary_key=True,
        ),
        Column("position", Integer, primary_key=True),
        Column(
            reference_column,
            String(reference_length),
            ForeignKey(f"{reference_table}.{reference_target_column or reference_column}"),
            nullable=False,
        ),
        CheckConstraint("position >= 0", name=f"ck_{table_name}_position"),
        UniqueConstraint(
            owner_column,
            reference_column,
            name=f"uq_{table_name}_reference",
        ),
    )


def _scoped_ordered_reference_table(
    table_name: str,
    owner_column: str,
    owner_length: int,
    owner_table: str,
    reference_column: str,
    reference_length: int,
    reference_table: str,
    scope_columns: tuple[tuple[str, int], ...],
    owner_scope_targets: tuple[str, ...],
    reference_scope_targets: tuple[str, ...],
    *,
    defer_owner_foreign_keys: bool = False,
) -> Table:
    scope_names = tuple(column_name for column_name, _ in scope_columns)
    if len(scope_names) != len(owner_scope_targets) or len(scope_names) != len(
        reference_scope_targets
    ):
        raise ValueError("scoped reference targets must exactly match scope columns")
    owner_foreign_key_deferrable = True if defer_owner_foreign_keys else None
    owner_foreign_key_initially = "DEFERRED" if defer_owner_foreign_keys else None
    return Table(
        table_name,
        metadata,
        Column(owner_column, String(owner_length), primary_key=True),
        Column("position", Integer, primary_key=True),
        Column(reference_column, String(reference_length), nullable=False),
        *(Column(name, String(length), nullable=False) for name, length in scope_columns),
        ForeignKeyConstraint(
            [owner_column],
            [f"{owner_table}.{owner_column}"],
            deferrable=owner_foreign_key_deferrable,
            initially=owner_foreign_key_initially,
        ),
        ForeignKeyConstraint(
            [reference_column],
            [f"{reference_table}.{reference_column}"],
        ),
        ForeignKeyConstraint(
            [owner_column, *scope_names],
            [
                f"{owner_table}.{owner_column}",
                *(f"{owner_table}.{column_name}" for column_name in owner_scope_targets),
            ],
            deferrable=owner_foreign_key_deferrable,
            initially=owner_foreign_key_initially,
        ),
        ForeignKeyConstraint(
            [reference_column, *scope_names],
            [
                f"{reference_table}.{reference_column}",
                *(f"{reference_table}.{column_name}" for column_name in reference_scope_targets),
            ],
        ),
        CheckConstraint("position >= 0", name=f"ck_{table_name}_position"),
        UniqueConstraint(
            owner_column,
            reference_column,
            name=f"uq_{table_name}_reference",
        ),
        UniqueConstraint(
            owner_column,
            "position",
            reference_column,
            *scope_names,
            name=f"uq_{table_name}_scoped_row",
        ),
    )


primitive_version_predecessors = _ordered_reference_table(
    "primitive_version_predecessors",
    "primitive_version_id",
    192,
    "primitive_versions",
    "predecessor_primitive_version_id",
    192,
    "primitive_versions",
    "primitive_version_id",
)
primitive_version_dependencies = _ordered_reference_table(
    "primitive_version_dependencies",
    "primitive_version_id",
    192,
    "primitive_versions",
    "dependency_primitive_version_id",
    192,
    "primitive_versions",
    "primitive_version_id",
)
primitive_version_measurements = _ordered_reference_table(
    "primitive_version_measurements",
    "primitive_version_id",
    192,
    "primitive_versions",
    "measurement_id",
    128,
    "self_improvement_measurements",
)
primitive_evaluation_verification_results = _ordered_reference_table(
    "primitive_evaluation_verification_results",
    "primitive_evaluation_id",
    160,
    "primitive_evaluations",
    "verification_result_id",
    160,
    "verification_results",
)
primitive_evaluation_evidence = _ordered_reference_table(
    "primitive_evaluation_evidence",
    "primitive_evaluation_id",
    160,
    "primitive_evaluations",
    "evidence_id",
    128,
    "evidence_records",
)
hypothesis_version_primitives = _ordered_reference_table(
    "hypothesis_version_primitives",
    "hypothesis_version_id",
    192,
    "hypothesis_versions",
    "primitive_version_id",
    192,
    "primitive_versions",
)
hypothesis_version_evidence = _ordered_reference_table(
    "hypothesis_version_evidence",
    "hypothesis_version_id",
    192,
    "hypothesis_versions",
    "evidence_id",
    128,
    "evidence_records",
)
verification_result_simulations = _scoped_ordered_reference_table(
    "verification_result_simulations",
    "verification_result_id",
    160,
    "verification_results",
    "simulation_result_id",
    160,
    "simulation_results",
    (
        ("hypothesis_id", 128),
        ("hypothesis_version_id", 192),
        ("model_spec_id", 160),
        ("model_execution_mode", 48),
    ),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "model_execution_mode"),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "execution_mode"),
)
counterexample_simulations = _scoped_ordered_reference_table(
    "counterexample_simulations",
    "counterexample_id",
    160,
    "counterexample_records",
    "simulation_result_id",
    160,
    "simulation_results",
    (
        ("hypothesis_id", 128),
        ("hypothesis_version_id", 192),
        ("model_spec_id", 160),
        ("model_execution_mode", 48),
    ),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "model_execution_mode"),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "execution_mode"),
)
counterexample_verification_results = _scoped_ordered_reference_table(
    "counterexample_verification_results",
    "counterexample_id",
    160,
    "counterexample_records",
    "verification_result_id",
    160,
    "verification_results",
    (
        ("hypothesis_id", 128),
        ("hypothesis_version_id", 192),
        ("model_spec_id", 160),
        ("model_execution_mode", 48),
    ),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "model_execution_mode"),
    ("hypothesis_id", "hypothesis_version_id", "model_spec_id", "model_execution_mode"),
)
counterexample_evidence = _ordered_reference_table(
    "counterexample_evidence",
    "counterexample_id",
    160,
    "counterexample_records",
    "evidence_id",
    128,
    "evidence_records",
)
hypothesis_revision_verification_results = _scoped_ordered_reference_table(
    "hypothesis_revision_verification_results",
    "revision_id",
    160,
    "hypothesis_revisions",
    "verification_result_id",
    160,
    "verification_results",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
)
hypothesis_revision_counterexamples = _scoped_ordered_reference_table(
    "hypothesis_revision_counterexamples",
    "revision_id",
    160,
    "hypothesis_revisions",
    "counterexample_id",
    160,
    "counterexample_records",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
)
hypothesis_admission_models = _scoped_ordered_reference_table(
    "hypothesis_admission_models",
    "admission_decision_id",
    160,
    "hypothesis_admission_decisions",
    "model_spec_id",
    160,
    "executable_model_specs",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
)
hypothesis_admission_verification_results = _scoped_ordered_reference_table(
    "hypothesis_admission_verification_results",
    "admission_decision_id",
    160,
    "hypothesis_admission_decisions",
    "verification_result_id",
    160,
    "verification_results",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
)
hypothesis_admission_counterexamples = _scoped_ordered_reference_table(
    "hypothesis_admission_counterexamples",
    "admission_decision_id",
    160,
    "hypothesis_admission_decisions",
    "counterexample_id",
    160,
    "counterexample_records",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
)
hypothesis_admission_revisions = _scoped_ordered_reference_table(
    "hypothesis_admission_revisions",
    "admission_decision_id",
    160,
    "hypothesis_admission_decisions",
    "revision_id",
    160,
    "hypothesis_revisions",
    (("hypothesis_id", 128),),
    ("hypothesis_id",),
    ("hypothesis_id",),
    defer_owner_foreign_keys=True,
)

primitive_heads = Table(
    "primitive_heads",
    metadata,
    Column("primitive_id", String(128), primary_key=True),
    Column("primitive_version_id", String(192), nullable=False),
    Column("semantic_version", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    ForeignKeyConstraint(
        ["primitive_id", "primitive_version_id", "semantic_version", "status"],
        [
            "primitive_versions.primitive_id",
            "primitive_versions.primitive_version_id",
            "primitive_versions.semantic_version",
            "primitive_versions.status",
        ],
    ),
)

hypothesis_heads = Table(
    "hypothesis_heads",
    metadata,
    Column("hypothesis_id", String(128), primary_key=True),
    Column("hypothesis_version_id", String(192), nullable=False),
    Column("version", Integer, nullable=False),
    Column("admission_status", String(40), nullable=False),
    ForeignKeyConstraint(
        ["hypothesis_id", "hypothesis_version_id", "version", "admission_status"],
        [
            "hypothesis_versions.hypothesis_id",
            "hypothesis_versions.hypothesis_version_id",
            "hypothesis_versions.version",
            "hypothesis_versions.admission_status",
        ],
    ),
    CheckConstraint("version >= 1", name="ck_hypothesis_heads_version"),
)

behavior_rule_link_versions = Table(
    "behavior_rule_link_versions",
    metadata,
    Column("link_version_id", String(160), primary_key=True),
    Column("behavior_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column(
        "rule_version_id",
        String(192),
        ForeignKey("behavioral_rule_versions.rule_version_id"),
        nullable=False,
    ),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("behavior_id", "version", name="uq_behavior_rule_link_version"),
    CheckConstraint("version >= 1", name="ck_behavior_rule_link_versions_version"),
    _content_hash_constraint("ck_behavior_rule_link_versions_content_hash"),
)

handbook_verification_records = Table(
    "handbook_verification_records",
    metadata,
    Column("verification_id", String(160), primary_key=True),
    Column("manifest_hash", String(64), nullable=False),
    Column("outcome", String(24), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _optional_hash_constraint("manifest_hash", "ck_handbook_verification_manifest_hash"),
    CheckConstraint(
        f"outcome IN ({_quoted(ASSESSMENT_OUTCOMES)})",
        name="ck_handbook_verification_outcome",
    ),
    _content_hash_constraint("ck_handbook_verification_records_content_hash"),
)

harness_campaigns = Table(
    "harness_campaigns",
    metadata,
    Column("campaign_id", String(160), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("version >= 1", name="ck_harness_campaigns_version"),
    _content_hash_constraint("ck_harness_campaigns_content_hash"),
)

harness_partition_manifests = Table(
    "harness_partition_manifests",
    metadata,
    Column("partition_manifest_id", String(160), primary_key=True),
    Column(
        "campaign_id",
        String(160),
        ForeignKey("harness_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("partition", String(48), nullable=False),
    Column("manifest_hash", String(64), nullable=False),
    Column("protected_content_hash", String(64), nullable=True),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint(
        "partition_manifest_id",
        "campaign_id",
        name="uq_harness_partition_campaign_scope",
    ),
    UniqueConstraint("campaign_id", "partition", name="uq_harness_campaign_partition"),
    CheckConstraint(
        f"partition IN ({_quoted(HARNESS_PARTITIONS)})",
        name="ck_harness_partition_manifests_partition",
    ),
    _optional_hash_constraint(
        "manifest_hash",
        "ck_harness_partition_manifests_manifest_hash",
    ),
    _optional_hash_constraint(
        "protected_content_hash",
        "ck_harness_partition_manifests_protected_hash",
    ),
    _content_hash_constraint("ck_harness_partition_manifests_content_hash"),
)

harness_budgets = Table(
    "harness_budgets",
    metadata,
    Column("budget_id", String(160), primary_key=True),
    Column(
        "campaign_id",
        String(160),
        ForeignKey("harness_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("variant", String(64), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("campaign_id", "variant", name="uq_harness_campaign_budget_variant"),
    CheckConstraint(
        f"variant IN ({_quoted(HARNESS_VARIANTS)})",
        name="ck_harness_budgets_variant",
    ),
    _content_hash_constraint("ck_harness_budgets_content_hash"),
)

harness_observations = Table(
    "harness_observations",
    metadata,
    Column("observation_id", String(160), primary_key=True),
    Column("campaign_id", String(160), nullable=False),
    Column("partition_manifest_id", String(160), nullable=False),
    Column("task_id", String(160), nullable=False),
    Column("variant", String(64), nullable=False),
    Column("candidate_output_hash", String(64), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    ForeignKeyConstraint(
        ["partition_manifest_id", "campaign_id"],
        [
            "harness_partition_manifests.partition_manifest_id",
            "harness_partition_manifests.campaign_id",
        ],
    ),
    CheckConstraint(
        f"variant IN ({_quoted(HARNESS_VARIANTS)})",
        name="ck_harness_observations_variant",
    ),
    _optional_hash_constraint(
        "candidate_output_hash",
        "ck_harness_observations_candidate_output_hash",
    ),
    _content_hash_constraint("ck_harness_observations_content_hash"),
)

harness_metrics = Table(
    "harness_metrics",
    metadata,
    Column("result_id", String(160), primary_key=True),
    Column(
        "campaign_id",
        String(160),
        ForeignKey("harness_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("task_id", String(160), nullable=False),
    Column("expected_output_hash", String(64), nullable=False),
    Column("candidate_output_hash", String(64), nullable=False),
    Column("checker_id", String(160), nullable=False),
    Column("checker_version", String(160), nullable=False),
    Column("outcome", String(24), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _optional_hash_constraint(
        "expected_output_hash",
        "ck_harness_metrics_expected_output_hash",
    ),
    _optional_hash_constraint(
        "candidate_output_hash",
        "ck_harness_metrics_candidate_output_hash",
    ),
    CheckConstraint(
        f"outcome IN ({_quoted(ASSESSMENT_OUTCOMES)})",
        name="ck_harness_metrics_outcome",
    ),
    _content_hash_constraint("ck_harness_metrics_content_hash"),
)

harness_confounds = Table(
    "harness_confounds",
    metadata,
    Column("confound_id", String(160), primary_key=True),
    Column(
        "campaign_id",
        String(160),
        ForeignKey("harness_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("code", String(128), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    _content_hash_constraint("ck_harness_confounds_content_hash"),
)

harness_decisions = Table(
    "harness_decisions",
    metadata,
    Column("decision_id", String(160), primary_key=True),
    Column(
        "campaign_id",
        String(160),
        ForeignKey("harness_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("status", String(40), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint(
        "decision_id",
        "campaign_id",
        "status",
        name="uq_harness_decision_head_target",
    ),
    CheckConstraint(
        f"status IN ({_quoted(HARNESS_DECISION_STATUSES)})",
        name="ck_harness_decisions_status",
    ),
    _content_hash_constraint("ck_harness_decisions_content_hash"),
)

harness_campaign_heads = Table(
    "harness_campaign_heads",
    metadata,
    Column("campaign_id", String(160), primary_key=True),
    Column("decision_id", String(160), nullable=False),
    Column("status", String(40), nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "campaign_id", "status"],
        [
            "harness_decisions.decision_id",
            "harness_decisions.campaign_id",
            "harness_decisions.status",
        ],
    ),
)

MAX_PHASE_A_CANONICAL_RECORD_BYTES = 64 * 1024 * 1024
MAX_GOVERNED_STORAGE_ENVELOPE_OVERHEAD_BYTES = 64 * 1024
MAX_GOVERNED_RECORD_JSON_BYTES = (
    MAX_PHASE_A_CANONICAL_RECORD_BYTES + MAX_GOVERNED_STORAGE_ENVELOPE_OVERHEAD_BYTES
)
GOVERNED_APPEND_ONLY_TRIGGER_SUFFIXES = ("no_update", "no_delete", "no_replace")


def _governed_record_table(
    name: str,
    id_column: Column[str],
    relationship_columns: tuple[Column[str], ...] = (),
    table_constraints: tuple[Constraint, ...] = (),
) -> Table:
    identifier_columns = (id_column, *relationship_columns)
    return Table(
        name,
        metadata,
        id_column,
        *relationship_columns,
        Column("schema_version", Integer, nullable=False),
        Column("record_json", Text, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column(
            "transaction_id",
            String(200),
            ForeignKey(
                "transactions.proposal_id",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
            index=True,
        ),
        Column(
            "governing_policy_hash",
            String(64),
            ForeignKey(
                "governance_policies.policy_hash",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
            index=True,
        ),
        Column("created_at", String(40), nullable=False),
        *table_constraints,
        *(
            CheckConstraint(
                f"typeof({column.name}) = 'text' "
                f"AND length({column.name}) BETWEEN 1 AND 200 "
                f"AND length(CAST({column.name} AS BLOB)) BETWEEN 1 AND 800 "
                f"AND instr({column.name}, char(0)) = 0",
                name=f"ck_{name}_{column.name}",
            )
            for column in identifier_columns
        ),
        CheckConstraint(
            "typeof(transaction_id) = 'text' "
            "AND length(transaction_id) BETWEEN 1 AND 200 "
            "AND length(CAST(transaction_id AS BLOB)) BETWEEN 1 AND 800 "
            "AND instr(transaction_id, char(0)) = 0",
            name=f"ck_{name}_transaction_id",
        ),
        CheckConstraint(
            "typeof(schema_version) = 'integer' AND schema_version = 1",
            name=f"ck_{name}_schema_version",
        ),
        CheckConstraint(
            "typeof(record_json) = 'text' "
            f"AND length(record_json) BETWEEN 1 AND {MAX_GOVERNED_RECORD_JSON_BYTES} "
            "AND length(CAST(record_json AS BLOB)) BETWEEN 1 "
            f"AND {MAX_GOVERNED_RECORD_JSON_BYTES} "
            "AND instr(record_json, char(0)) = 0",
            name=f"ck_{name}_record_json",
        ),
        CheckConstraint(
            "typeof(content_hash) = 'text' "
            "AND length(content_hash) = 64 "
            "AND length(CAST(content_hash AS BLOB)) = 64 "
            "AND instr(content_hash, char(0)) = 0 "
            "AND content_hash NOT GLOB '*[^0-9a-f]*'",
            name=f"ck_{name}_content_hash",
        ),
        CheckConstraint(
            "typeof(governing_policy_hash) = 'text' "
            "AND length(governing_policy_hash) = 64 "
            "AND length(CAST(governing_policy_hash AS BLOB)) = 64 "
            "AND instr(governing_policy_hash, char(0)) = 0 "
            "AND governing_policy_hash NOT GLOB '*[^0-9a-f]*'",
            name=f"ck_{name}_governing_policy_hash",
        ),
        CheckConstraint(
            "typeof(created_at) = 'text' "
            "AND length(created_at) BETWEEN 1 AND 40 "
            "AND length(CAST(created_at AS BLOB)) BETWEEN 1 AND 40 "
            "AND instr(created_at, char(0)) = 0",
            name=f"ck_{name}_created_at",
        ),
    )


capability_profiles = _governed_record_table(
    "capability_profiles",
    Column("profile_id", String(200), primary_key=True),
)
cohort_plans = _governed_record_table(
    "cohort_plans",
    Column("cohort_plan_id", String(200), primary_key=True),
    (Column("request_id", String(200), nullable=False, index=True),),
)
diversity_assessments = _governed_record_table(
    "diversity_assessments",
    Column("diversity_assessment_id", String(200), primary_key=True),
    (
        Column(
            "cohort_plan_id",
            String(200),
            ForeignKey("cohort_plans.cohort_plan_id"),
            nullable=False,
            index=True,
        ),
    ),
)
collaboration_sessions = _governed_record_table(
    "collaboration_sessions",
    Column("session_id", String(200), primary_key=True),
    (
        Column(
            "cohort_plan_id",
            String(200),
            ForeignKey("cohort_plans.cohort_plan_id"),
            nullable=False,
            index=True,
        ),
    ),
)
peer_requests = _governed_record_table(
    "peer_requests",
    Column("request_id", String(200), primary_key=True),
    (
        Column(
            "session_id",
            String(200),
            ForeignKey("collaboration_sessions.session_id"),
            nullable=False,
            index=True,
        ),
    ),
    (
        UniqueConstraint(
            "request_id",
            "session_id",
            name="uq_peer_requests_session_scope",
        ),
    ),
)
peer_contributions = _governed_record_table(
    "peer_contributions",
    Column("contribution_id", String(200), primary_key=True),
    (
        Column("session_id", String(200), nullable=False, index=True),
        Column("request_id", String(200), nullable=False, index=True),
    ),
    (
        ForeignKeyConstraint(
            ["request_id", "session_id"],
            ["peer_requests.request_id", "peer_requests.session_id"],
        ),
    ),
)
topology_events = _governed_record_table(
    "topology_events",
    Column("event_id", String(200), primary_key=True),
    (
        Column(
            "session_id",
            String(200),
            ForeignKey("collaboration_sessions.session_id"),
            nullable=False,
            index=True,
        ),
    ),
)
collaboration_terminations = _governed_record_table(
    "collaboration_terminations",
    Column(
        "session_id",
        String(200),
        ForeignKey("collaboration_sessions.session_id"),
        primary_key=True,
    ),
)
procedure_compilations = _governed_record_table(
    "procedure_compilations",
    Column("compilation_id", String(200), primary_key=True),
)
method_direction_outcomes = _governed_record_table(
    "method_direction_outcomes",
    Column("outcome_id", String(200), primary_key=True),
    (
        Column(
            "compilation_id",
            String(200),
            ForeignKey("procedure_compilations.compilation_id"),
            nullable=False,
            index=True,
        ),
    ),
)
compiled_progress_plan_bindings = _governed_record_table(
    "compiled_progress_plan_bindings",
    Column("binding_id", String(200), primary_key=True),
    (
        Column(
            "compilation_id",
            String(200),
            ForeignKey("procedure_compilations.compilation_id"),
            nullable=False,
            index=True,
        ),
    ),
)
guidance_protocols = _governed_record_table(
    "guidance_protocols",
    Column("protocol_id", String(200), primary_key=True),
)
guidance_cells = _governed_record_table(
    "guidance_cells",
    Column("cell_id", String(200), primary_key=True),
    (
        Column(
            "protocol_id",
            String(200),
            ForeignKey("guidance_protocols.protocol_id"),
            nullable=False,
            index=True,
        ),
    ),
)
model_harness_protocols = _governed_record_table(
    "model_harness_protocols",
    Column("protocol_id", String(200), primary_key=True),
)
model_harness_cells = _governed_record_table(
    "model_harness_cells",
    Column("cell_id", String(200), primary_key=True),
    (
        Column(
            "protocol_id",
            String(200),
            ForeignKey("model_harness_protocols.protocol_id"),
            nullable=False,
            index=True,
        ),
    ),
)
model_harness_analyses = _governed_record_table(
    "model_harness_analyses",
    Column(
        "protocol_id",
        String(200),
        ForeignKey("model_harness_protocols.protocol_id"),
        primary_key=True,
    ),
)
harness_execution_traces = _governed_record_table(
    "harness_execution_traces",
    Column("trace_id", String(200), primary_key=True),
    (Column("protocol_id", String(200), nullable=False, index=True),),
)
reward_assessments = _governed_record_table(
    "reward_assessments",
    Column("assessment_id", String(200), primary_key=True),
    (
        Column(
            "trace_id",
            String(200),
            ForeignKey("harness_execution_traces.trace_id"),
            nullable=False,
            index=True,
        ),
        Column("observation_id", String(200), nullable=False, index=True),
    ),
)
