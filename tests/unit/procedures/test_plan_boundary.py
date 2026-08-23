import ast
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

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


def _planned_function(task: str, function_name: str) -> Any:
    for block in re.findall(r"```python\n(.*?)```", task, flags=re.DOTALL):
        tree = ast.parse(block)
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            ),
            None,
        )
        if function is not None:
            future_annotations = ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            )
            module = ast.fix_missing_locations(
                ast.Module(body=[future_annotations, function], type_ignores=[])
            )
            namespace: dict[str, Any] = {}
            # Execute only the isolated function AST from this version-controlled plan.
            exec(compile(module, str(PLAN_PATH), "exec"), namespace)
            return namespace[function_name]
    raise AssertionError(f"planned function {function_name!r} was not found")


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


def test_planned_proposal_boundary_discards_raw_validation_context() -> None:
    marker = "PRIVATE_PROPOSAL_MARKER"

    class ProposalBoundaryValidationError(ValueError):
        pass

    class MarkerProposal(BaseModel):
        model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

        proposal_type: Literal["expected"]

    rejecting_adapter = TypeAdapter(MarkerProposal)
    rejected_bytes = f'{{"proposal_type":"{marker}"}}'.encode()
    with pytest.raises(ValidationError) as raw:
        rejecting_adapter.validate_json(rejected_bytes)
    assert marker in repr(raw.value.errors())

    task_8 = _task(PLAN_PATH.read_text(encoding="utf-8"), 8)
    parser = _planned_function(task_8, "parse_untrusted_proposal_json")
    parser.__globals__.update(
        {
            "MAX_PROPOSAL_BYTES": 8 * 1_024 * 1_024,
            "PROPOSAL_ADAPTER": rejecting_adapter,
            "Proposal": MarkerProposal,
            "ProposalBoundaryValidationError": ProposalBoundaryValidationError,
            "proposal_json_is_within_depth_limit": lambda value: True,
            "suppress": suppress,
        }
    )

    try:
        parser(rejected_bytes)
    except ProposalBoundaryValidationError as error:
        assert str(error) == "transaction proposal failed validation"
        assert marker not in str(error)
        assert marker not in repr(error)
        assert error.__cause__ is None
        assert error.__context__ is None
        assert not hasattr(error, "errors")
    else:
        raise AssertionError("planned proposal parser accepted rejected bytes")


def test_task_12_safe_parse_precedes_recomputation_and_record_construction() -> None:
    task_12 = _task(PLAN_PATH.read_text(encoding="utf-8"), 12)

    safe_envelope_parse = task_12.index(
        "parse_untrusted_procedure_compilation_envelope(\n                proposal.compilation"
    )
    safe_result_parse = task_12.index(
        "parse_untrusted_procedure_compilation_result(\n                envelope",
        safe_envelope_parse,
    )
    recompute = task_12.index("expected = compile_method(", safe_result_parse)
    record_factory = task_12.index(
        "ProcedureCompilationRecord.build_from_untrusted_envelope(",
        recompute,
    )

    assert safe_envelope_parse < safe_result_parse < recompute < record_factory
    assert "proposal.compilation.result" not in task_12


def test_task_12_resolves_every_accepted_source_before_recomputation_or_acceptance() -> None:
    task_12 = _task(PLAN_PATH.read_text(encoding="utf-8"), 12)

    resolve_sources = task_12.index("resolved_sources = resolve_procedure_source_receipts(")
    reject_unresolved = task_12.index(
        "return rejected(proposal, RejectionCode.STALE_REFERENCE)",
        resolve_sources,
    )
    recompute = task_12.index("expected = compile_method(", reject_unresolved)
    accept = task_12.index("return reject_existing_or_accept(", recompute)

    assert resolve_sources < reject_unresolved < recompute < accept
    for repository_name in (
        "accepted_source_receipts",
        "capability_profiles",
        "artifact_catalog_snapshots",
        "tool_catalog_snapshots",
        "validator_catalog_snapshots",
        "source_snapshots",
    ):
        assert repository_name in task_12
    assert "resolve every `AcceptedSourceReceiptRef`" in task_12
    binding_handler = task_12.index("class BindCompiledProgressPlanHandler:")
    binding_resolution = task_12.index(
        "resolved_sources = resolve_procedure_source_receipts(",
        binding_handler,
    )
    binding_rejection = task_12.index(
        "return rejected(proposal, RejectionCode.STALE_REFERENCE)",
        binding_resolution,
    )
    progress_mapping = task_12.index(
        "expected_plan = procedure_to_progress_plan(",
        binding_rejection,
    )

    assert binding_resolution < binding_rejection < progress_mapping


def test_integrity_plan_normalizes_accepted_compilation_envelopes() -> None:
    plan = PLAN_PATH.read_text(encoding="utf-8")

    assert "compilation_record_from_accepted_proposal(item)" in plan
