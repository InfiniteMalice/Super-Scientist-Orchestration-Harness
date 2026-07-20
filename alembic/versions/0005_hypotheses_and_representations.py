from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_hypotheses_and_representations"
down_revision: str | None = "0004_behavioral_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORITATIVE_TABLES = (
    "primitive_versions",
    "hypothesis_versions",
    "executable_model_specs",
    "verification_mechanism_specs",
    "simulation_results",
    "verification_results",
    "primitive_evaluations",
    "counterexample_records",
    "hypothesis_revisions",
    "hypothesis_admission_decisions",
)

REFERENCE_TABLES = (
    "primitive_version_predecessors",
    "primitive_version_dependencies",
    "primitive_version_measurements",
    "primitive_evaluation_verification_results",
    "primitive_evaluation_evidence",
    "hypothesis_version_primitives",
    "hypothesis_version_evidence",
    "verification_result_simulations",
    "counterexample_simulations",
    "counterexample_verification_results",
    "counterexample_evidence",
    "hypothesis_revision_verification_results",
    "hypothesis_revision_counterexamples",
    "hypothesis_admission_models",
    "hypothesis_admission_verification_results",
    "hypothesis_admission_counterexamples",
    "hypothesis_admission_revisions",
)

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

RESULT_CATEGORIES = (
    "FORMAL_VERIFICATION_RESULT",
    "DETERMINISTIC_CHECK_RESULT",
    "LEARNED_JUDGE_RESULT",
)

EXECUTION_MODES = ("METADATA_ONLY", "BUILTIN_DETERMINISTIC_SIMULATOR")
BUILTIN_SIMULATORS = ("thermal-chamber-v1", "exponential-decay-v1")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _content_hash_constraint(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _optional_hash_constraint(column_name: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column_name} IS NULL OR "
        f"(length({column_name}) = 64 AND {column_name} NOT GLOB '*[^0-9a-f]*')",
        name=name,
    )


def _record_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )


def _create_reference_table(
    table_name: str,
    owner_column: str,
    owner_length: int,
    owner_table: str,
    reference_column: str,
    reference_length: int,
    reference_table: str,
    reference_target_column: str | None = None,
) -> None:
    op.create_table(
        table_name,
        sa.Column(owner_column, sa.String(length=owner_length), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column(reference_column, sa.String(length=reference_length), nullable=False),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.{owner_column}"]),
        sa.ForeignKeyConstraint(
            [reference_column],
            [f"{reference_table}.{reference_target_column or reference_column}"],
        ),
        sa.CheckConstraint("position >= 0", name=f"ck_{table_name}_position"),
        sa.UniqueConstraint(
            owner_column,
            reference_column,
            name=f"uq_{table_name}_reference",
        ),
    )


def _create_scoped_reference_table(
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
) -> None:
    scope_names = tuple(column_name for column_name, _ in scope_columns)
    if len(scope_names) != len(owner_scope_targets) or len(scope_names) != len(
        reference_scope_targets
    ):
        raise ValueError("scoped reference targets must exactly match scope columns")
    op.create_table(
        table_name,
        sa.Column(owner_column, sa.String(length=owner_length), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column(reference_column, sa.String(length=reference_length), nullable=False),
        *(
            sa.Column(name, sa.String(length=length), nullable=False)
            for name, length in scope_columns
        ),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.{owner_column}"]),
        sa.ForeignKeyConstraint(
            [reference_column],
            [f"{reference_table}.{reference_column}"],
        ),
        sa.ForeignKeyConstraint(
            [owner_column, *scope_names],
            [
                f"{owner_table}.{owner_column}",
                *(f"{owner_table}.{column_name}" for column_name in owner_scope_targets),
            ],
        ),
        sa.ForeignKeyConstraint(
            [reference_column, *scope_names],
            [
                f"{reference_table}.{reference_column}",
                *(f"{reference_table}.{column_name}" for column_name in reference_scope_targets),
            ],
        ),
        sa.CheckConstraint("position >= 0", name=f"ck_{table_name}_position"),
        sa.UniqueConstraint(
            owner_column,
            reference_column,
            name=f"uq_{table_name}_reference",
        ),
        sa.UniqueConstraint(
            owner_column,
            "position",
            reference_column,
            *scope_names,
            name=f"uq_{table_name}_scoped_row",
        ),
    )


def _create_append_only_triggers() -> None:
    for table_name in (*AUTHORITATIVE_TABLES, *REFERENCE_TABLES):
        op.execute(
            f"CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )


def _create_hypothesis_lineage_triggers() -> None:
    op.execute(
        "CREATE TRIGGER hypothesis_admission_requires_revision_lineage "
        "BEFORE INSERT ON hypothesis_admission_decisions "
        "WHEN NEW.terminal_revision_id IS NULL AND EXISTS ("
        "SELECT 1 FROM hypothesis_revisions AS revision "
        "WHERE revision.hypothesis_id = NEW.hypothesis_id "
        "AND revision.resulting_hypothesis_version_id = NEW.hypothesis_version_id "
        "AND revision.resulting_version = NEW.version) "
        "BEGIN SELECT RAISE(ABORT, 'admission omits retained revision lineage'); END"
    )
    op.execute(
        "CREATE TRIGGER hypothesis_admission_revision_bounds "
        "BEFORE INSERT ON hypothesis_admission_revisions "
        "WHEN NOT EXISTS ("
        "SELECT 1 FROM hypothesis_admission_decisions AS decision "
        "WHERE decision.admission_decision_id = NEW.admission_decision_id "
        "AND decision.hypothesis_id = NEW.hypothesis_id "
        "AND decision.terminal_revision_id IS NOT NULL "
        "AND NEW.position <= decision.terminal_revision_position "
        "AND ((NEW.position = decision.terminal_revision_position "
        "AND NEW.revision_id = decision.terminal_revision_id) "
        "OR (NEW.position < decision.terminal_revision_position "
        "AND NEW.revision_id <> decision.terminal_revision_id))) "
        "BEGIN SELECT RAISE(ABORT, 'admission revision is outside the declared chain'); END"
    )
    op.execute(
        "CREATE TRIGGER hypothesis_admission_revision_chain "
        "BEFORE INSERT ON hypothesis_admission_revisions "
        "WHEN NEW.position > 0 AND NOT EXISTS ("
        "SELECT 1 FROM hypothesis_admission_revisions AS previous_reference "
        "JOIN hypothesis_revisions AS previous_revision "
        "ON previous_revision.revision_id = previous_reference.revision_id "
        "JOIN hypothesis_revisions AS current_revision "
        "ON current_revision.revision_id = NEW.revision_id "
        "WHERE previous_reference.admission_decision_id = NEW.admission_decision_id "
        "AND previous_reference.position = NEW.position - 1 "
        "AND previous_reference.hypothesis_id = NEW.hypothesis_id "
        "AND previous_revision.hypothesis_id = NEW.hypothesis_id "
        "AND current_revision.hypothesis_id = NEW.hypothesis_id "
        "AND previous_revision.resulting_hypothesis_version_id = "
        "current_revision.prior_hypothesis_version_id "
        "AND previous_revision.resulting_version = current_revision.prior_version) "
        "BEGIN SELECT RAISE(ABORT, 'admission revisions must form a contiguous chain'); END"
    )


def upgrade() -> None:
    op.create_table(
        "primitive_versions",
        sa.Column("primitive_version_id", sa.String(length=192), primary_key=True),
        sa.Column("primitive_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_record_columns(),
        sa.UniqueConstraint(
            "primitive_id",
            "semantic_version",
            name="uq_primitive_semantic_version",
        ),
        sa.UniqueConstraint(
            "primitive_id",
            "primitive_version_id",
            "semantic_version",
            "status",
            name="uq_primitive_head_target",
        ),
        sa.CheckConstraint(
            "length(semantic_version) BETWEEN 5 AND 32",
            name="ck_primitive_semantic_version_length",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(PRIMITIVE_STATUSES)})",
            name="ck_primitive_versions_status",
        ),
        _content_hash_constraint("ck_primitive_versions_content_hash"),
    )
    op.create_table(
        "hypothesis_versions",
        sa.Column("hypothesis_version_id", sa.String(length=192), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("admission_status", sa.String(length=40), nullable=False),
        *_record_columns(),
        sa.UniqueConstraint("hypothesis_id", "version", name="uq_hypothesis_version"),
        sa.UniqueConstraint(
            "hypothesis_id",
            "hypothesis_version_id",
            name="uq_hypothesis_version_scope",
        ),
        sa.UniqueConstraint(
            "hypothesis_id",
            "hypothesis_version_id",
            "version",
            name="uq_hypothesis_revision_target",
        ),
        sa.UniqueConstraint(
            "hypothesis_id",
            "hypothesis_version_id",
            "version",
            "admission_status",
            name="uq_hypothesis_head_target",
        ),
        sa.CheckConstraint("version >= 1", name="ck_hypothesis_versions_version"),
        sa.CheckConstraint(
            f"admission_status IN ({_quoted(HYPOTHESIS_STATUSES)})",
            name="ck_hypothesis_versions_admission_status",
        ),
        _content_hash_constraint("ck_hypothesis_versions_content_hash"),
    )
    op.create_table(
        "executable_model_specs",
        sa.Column("model_spec_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("execution_mode", sa.String(length=48), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("artifact_media_type", sa.String(length=128), nullable=True),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
        sa.Column("builtin_simulator_id", sa.String(length=64), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "hypothesis_version_id"],
            ["hypothesis_versions.hypothesis_id", "hypothesis_versions.hypothesis_version_id"],
        ),
        sa.UniqueConstraint(
            "model_spec_id",
            "hypothesis_id",
            name="uq_model_spec_hypothesis",
        ),
        sa.UniqueConstraint(
            "model_spec_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "execution_mode",
            name="uq_model_spec_hypothesis_execution",
        ),
        sa.CheckConstraint(
            f"execution_mode IN ({_quoted(EXECUTION_MODES)})",
            name="ck_model_specs_execution_mode",
        ),
        sa.CheckConstraint(
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
    op.create_table(
        "verification_mechanism_specs",
        sa.Column("mechanism_spec_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("mechanism_category", sa.String(length=48), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "hypothesis_version_id"],
            ["hypothesis_versions.hypothesis_id", "hypothesis_versions.hypothesis_version_id"],
        ),
        sa.UniqueConstraint(
            "mechanism_spec_id",
            "hypothesis_id",
            name="uq_mechanism_spec_hypothesis",
        ),
        sa.UniqueConstraint(
            "mechanism_spec_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "mechanism_category",
            name="uq_mechanism_spec_hypothesis_category",
        ),
        sa.CheckConstraint(
            f"mechanism_category IN ({_quoted(MECHANISM_CATEGORIES)})",
            name="ck_mechanism_specs_category",
        ),
        _content_hash_constraint("ck_verification_mechanism_specs_content_hash"),
    )
    op.create_table(
        "simulation_results",
        sa.Column("simulation_result_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("model_spec_id", sa.String(length=160), nullable=False),
        sa.Column("execution_mode", sa.String(length=48), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["model_spec_id", "hypothesis_id", "hypothesis_version_id", "execution_mode"],
            [
                "executable_model_specs.model_spec_id",
                "executable_model_specs.hypothesis_id",
                "executable_model_specs.hypothesis_version_id",
                "executable_model_specs.execution_mode",
            ],
        ),
        sa.UniqueConstraint(
            "simulation_result_id",
            "hypothesis_id",
            name="uq_simulation_result_hypothesis",
        ),
        sa.UniqueConstraint(
            "simulation_result_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "model_spec_id",
            "execution_mode",
            name="uq_simulation_result_scope",
        ),
        sa.CheckConstraint(
            "execution_mode = 'BUILTIN_DETERMINISTIC_SIMULATOR'",
            name="ck_simulation_results_execution_mode",
        ),
        _content_hash_constraint("ck_simulation_results_content_hash"),
    )
    op.create_table(
        "verification_results",
        sa.Column("verification_result_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("mechanism_spec_id", sa.String(length=160), nullable=False),
        sa.Column("mechanism_category", sa.String(length=48), nullable=False),
        sa.Column("result_category", sa.String(length=48), nullable=False),
        sa.Column("model_spec_id", sa.String(length=160), nullable=True),
        sa.Column("model_execution_mode", sa.String(length=48), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.UniqueConstraint(
            "verification_result_id",
            "hypothesis_id",
            name="uq_verification_result_hypothesis",
        ),
        sa.UniqueConstraint(
            "verification_result_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "model_spec_id",
            "model_execution_mode",
            name="uq_verification_result_model_scope",
        ),
        sa.CheckConstraint(
            "((mechanism_category = 'FORMAL_VERIFIER' "
            "AND result_category = 'FORMAL_VERIFICATION_RESULT') OR "
            "(mechanism_category = 'INDEPENDENT_DETERMINISTIC_CHECKER' "
            "AND result_category = 'DETERMINISTIC_CHECK_RESULT') OR "
            "(mechanism_category = 'LEARNED_JUDGE' "
            "AND result_category = 'LEARNED_JUDGE_RESULT'))",
            name="ck_verification_results_category_pair",
        ),
        sa.CheckConstraint(
            "((model_spec_id IS NULL AND model_execution_mode IS NULL) OR "
            "(model_spec_id IS NOT NULL AND model_execution_mode IS NOT NULL))",
            name="ck_verification_results_model_pair",
        ),
        _content_hash_constraint("ck_verification_results_content_hash"),
    )
    op.create_table(
        "primitive_evaluations",
        sa.Column("primitive_evaluation_id", sa.String(length=160), primary_key=True),
        sa.Column("primitive_version_id", sa.String(length=192), nullable=False),
        sa.Column("frame", sa.String(length=24), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["primitive_version_id"],
            ["primitive_versions.primitive_version_id"],
        ),
        sa.CheckConstraint(
            "frame IN ('OLD_FRAME', 'NEW_FRAME')",
            name="ck_primitive_evaluations_frame",
        ),
        _content_hash_constraint("ck_primitive_evaluations_content_hash"),
    )
    op.create_table(
        "counterexample_records",
        sa.Column("counterexample_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("model_spec_id", sa.String(length=160), nullable=True),
        sa.Column("model_execution_mode", sa.String(length=48), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(
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
        sa.UniqueConstraint(
            "counterexample_id",
            "hypothesis_id",
            name="uq_counterexample_hypothesis",
        ),
        sa.UniqueConstraint(
            "counterexample_id",
            "hypothesis_id",
            "hypothesis_version_id",
            "model_spec_id",
            "model_execution_mode",
            name="uq_counterexample_model_scope",
        ),
        sa.CheckConstraint(
            "((model_spec_id IS NULL AND model_execution_mode IS NULL) OR "
            "(model_spec_id IS NOT NULL AND model_execution_mode IS NOT NULL))",
            name="ck_counterexamples_model_pair",
        ),
        _content_hash_constraint("ck_counterexample_records_content_hash"),
    )
    op.create_table(
        "hypothesis_revisions",
        sa.Column("revision_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("prior_hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("prior_version", sa.Integer(), nullable=False),
        sa.Column("resulting_hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "prior_hypothesis_version_id", "prior_version"],
            [
                "hypothesis_versions.hypothesis_id",
                "hypothesis_versions.hypothesis_version_id",
                "hypothesis_versions.version",
            ],
        ),
        sa.UniqueConstraint(
            "revision_id",
            "hypothesis_id",
            name="uq_hypothesis_revision_scope",
        ),
        sa.UniqueConstraint(
            "revision_id",
            "hypothesis_id",
            "resulting_hypothesis_version_id",
            "resulting_version",
            name="uq_hypothesis_revision_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "resulting_hypothesis_version_id", "resulting_version"],
            [
                "hypothesis_versions.hypothesis_id",
                "hypothesis_versions.hypothesis_version_id",
                "hypothesis_versions.version",
            ],
        ),
        sa.CheckConstraint(
            "prior_version >= 1 AND resulting_version = prior_version + 1",
            name="ck_hypothesis_revisions_contiguous",
        ),
        sa.CheckConstraint(
            "prior_hypothesis_version_id <> resulting_hypothesis_version_id",
            name="ck_hypothesis_revisions_distinct_versions",
        ),
        _content_hash_constraint("ck_hypothesis_revisions_content_hash"),
    )
    op.create_table(
        "hypothesis_admission_decisions",
        sa.Column("admission_decision_id", sa.String(length=160), primary_key=True),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("admission_status", sa.String(length=40), nullable=False),
        sa.Column("terminal_revision_id", sa.String(length=160), nullable=True),
        sa.Column("terminal_revision_position", sa.Integer(), nullable=True),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "hypothesis_version_id", "version", "admission_status"],
            [
                "hypothesis_versions.hypothesis_id",
                "hypothesis_versions.hypothesis_version_id",
                "hypothesis_versions.version",
                "hypothesis_versions.admission_status",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["terminal_revision_id", "hypothesis_id", "hypothesis_version_id", "version"],
            [
                "hypothesis_revisions.revision_id",
                "hypothesis_revisions.hypothesis_id",
                "hypothesis_revisions.resulting_hypothesis_version_id",
                "hypothesis_revisions.resulting_version",
            ],
        ),
        sa.UniqueConstraint(
            "admission_decision_id",
            "hypothesis_id",
            name="uq_hypothesis_admission_scope",
        ),
        sa.CheckConstraint("version >= 1", name="ck_admission_decisions_version"),
        sa.CheckConstraint(
            f"admission_status IN ({_quoted(HYPOTHESIS_STATUSES)})",
            name="ck_admission_decisions_status",
        ),
        sa.CheckConstraint(
            "((terminal_revision_id IS NULL AND terminal_revision_position IS NULL) OR "
            "(terminal_revision_id IS NOT NULL AND terminal_revision_position >= 0))",
            name="ck_admission_terminal_revision_pair",
        ),
        _content_hash_constraint("ck_hypothesis_admission_decisions_content_hash"),
    )

    _create_reference_table(
        "primitive_version_predecessors",
        "primitive_version_id",
        192,
        "primitive_versions",
        "predecessor_primitive_version_id",
        192,
        "primitive_versions",
        "primitive_version_id",
    )
    _create_reference_table(
        "primitive_version_dependencies",
        "primitive_version_id",
        192,
        "primitive_versions",
        "dependency_primitive_version_id",
        192,
        "primitive_versions",
        "primitive_version_id",
    )
    _create_reference_table(
        "primitive_version_measurements",
        "primitive_version_id",
        192,
        "primitive_versions",
        "measurement_id",
        128,
        "self_improvement_measurements",
    )
    _create_reference_table(
        "primitive_evaluation_verification_results",
        "primitive_evaluation_id",
        160,
        "primitive_evaluations",
        "verification_result_id",
        160,
        "verification_results",
    )
    _create_reference_table(
        "primitive_evaluation_evidence",
        "primitive_evaluation_id",
        160,
        "primitive_evaluations",
        "evidence_id",
        128,
        "evidence_records",
    )
    _create_reference_table(
        "hypothesis_version_primitives",
        "hypothesis_version_id",
        192,
        "hypothesis_versions",
        "primitive_version_id",
        192,
        "primitive_versions",
    )
    _create_reference_table(
        "hypothesis_version_evidence",
        "hypothesis_version_id",
        192,
        "hypothesis_versions",
        "evidence_id",
        128,
        "evidence_records",
    )
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_reference_table(
        "counterexample_evidence",
        "counterexample_id",
        160,
        "counterexample_records",
        "evidence_id",
        128,
        "evidence_records",
    )
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    _create_scoped_reference_table(
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
    )

    op.create_table(
        "primitive_heads",
        sa.Column("primitive_id", sa.String(length=128), primary_key=True),
        sa.Column("primitive_version_id", sa.String(length=192), nullable=False),
        sa.Column("semantic_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["primitive_id", "primitive_version_id", "semantic_version", "status"],
            [
                "primitive_versions.primitive_id",
                "primitive_versions.primitive_version_id",
                "primitive_versions.semantic_version",
                "primitive_versions.status",
            ],
        ),
    )
    op.create_table(
        "hypothesis_heads",
        sa.Column("hypothesis_id", sa.String(length=128), primary_key=True),
        sa.Column("hypothesis_version_id", sa.String(length=192), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("admission_status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "hypothesis_version_id", "version", "admission_status"],
            [
                "hypothesis_versions.hypothesis_id",
                "hypothesis_versions.hypothesis_version_id",
                "hypothesis_versions.version",
                "hypothesis_versions.admission_status",
            ],
        ),
        sa.CheckConstraint("version >= 1", name="ck_hypothesis_heads_version"),
    )
    _create_hypothesis_lineage_triggers()
    _create_append_only_triggers()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS hypothesis_admission_revision_chain")
    op.execute("DROP TRIGGER IF EXISTS hypothesis_admission_revision_bounds")
    op.execute("DROP TRIGGER IF EXISTS hypothesis_admission_requires_revision_lineage")
    for table_name in reversed((*AUTHORITATIVE_TABLES, *REFERENCE_TABLES)):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update")

    op.drop_table("hypothesis_heads")
    op.drop_table("primitive_heads")
    for table_name in reversed(REFERENCE_TABLES):
        op.drop_table(table_name)
    for table_name in reversed(AUTHORITATIVE_TABLES):
        op.drop_table(table_name)
