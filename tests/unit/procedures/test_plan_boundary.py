from pathlib import Path

PLAN_PATH = (
    Path(__file__).parents[3]
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-23-governed-cognitive-cohorts-procedure-compilation.md"
)


def _task(plan: str, number: int) -> str:
    start_marker = f"### Task {number}:"
    start = plan.index(start_marker)
    next_task = plan.find(f"### Task {number + 1}:", start)
    return plan[start:] if next_task == -1 else plan[start:next_task]


def test_task_8_proposal_carries_opaque_result_instead_of_typed_record() -> None:
    task_8 = _task(PLAN_PATH.read_text(encoding="utf-8"), 8)
    normalized_task_8 = " ".join(task_8.split())

    assert "compilation: OpaqueProcedureCompilationEnvelope" in task_8
    assert "compilation: ProcedureCompilationRecord" not in task_8
    assert "does not parse a nested `ProcedureCompilationResult`" in normalized_task_8


def test_task_8_routes_untrusted_proposal_bytes_through_fixed_safe_boundary() -> None:
    task_8 = _task(PLAN_PATH.read_text(encoding="utf-8"), 8)

    assert "def parse_untrusted_proposal_json(value: bytes) -> Proposal:" in task_8
    assert "raise ProposalBoundaryValidationError(" in task_8
    assert "PROPOSAL_ADAPTER.validate_json(value)" in task_8
    assert "parse_untrusted_proposal_json(canonical_json_bytes(payload))" in task_8


def test_task_12_safe_parse_precedes_recomputation_and_record_construction() -> None:
    task_12 = _task(PLAN_PATH.read_text(encoding="utf-8"), 12)

    safe_parse = task_12.index(
        "parse_untrusted_procedure_compilation_result(\n                proposal.compilation"
    )
    recompute = task_12.index("expected = compile_method(", safe_parse)
    record_factory = task_12.index(
        "ProcedureCompilationRecord.build_from_untrusted_envelope(",
        recompute,
    )

    assert safe_parse < recompute < record_factory
    assert "proposal.compilation.result" not in task_12


def test_integrity_plan_normalizes_accepted_compilation_envelopes() -> None:
    plan = PLAN_PATH.read_text(encoding="utf-8")

    assert "compilation_record_from_accepted_proposal(item)" in plan
