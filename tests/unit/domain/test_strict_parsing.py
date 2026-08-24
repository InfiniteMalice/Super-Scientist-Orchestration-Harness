import ast
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from enum import Enum, StrEnum
from functools import reduce
from operator import or_
from pathlib import Path
from types import SimpleNamespace, UnionType
from typing import Annotated, Any, Self, Union, get_args, get_origin

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.cognition import (
    CapabilityAssessment,
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CohortPlan,
    CohortPlanReceiptRef,
    CohortRequest,
    DiversityAssessment,
    DiversityFingerprint,
    ErrorCorrelationRecord,
)
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationTermination,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
)
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.harness_eval import (
    EvidenceReceipt,
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
    HarnessExecutionTrace,
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardValidityAssessment,
    TraceFreshness,
    assess_reward_validity,
    parse_untrusted_harness_execution_trace,
    reward_validity_receipt,
    trace_freshness,
)
from super_scientist.domain.harness_eval.traces import RewardObservation
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import (
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    OpaqueProcedureCompilationEnvelope,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    parse_untrusted_procedure_compilation_result,
)
from super_scientist.domain.progress.models import ProgressPlan
from super_scientist.kernel.transactions import models as transaction_models
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    Proposal,
    ProposalBase,
    ProposeClaim,
    TransitionClaim,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
NOW = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)


def _plan_python_block(plan: str, class_name: str) -> str:
    blocks = re.findall(r"```python\n(.*?)```", plan, flags=re.DOTALL)
    return next(block for block in blocks if f"class {class_name}" in block)


def _phase_a_plan() -> str:
    return (
        Path(__file__).parents[3]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-23-governed-cognitive-cohorts-procedure-compilation.md"
    ).read_text(encoding="utf-8")


class _ProposalBoundaryValidationError(ValueError):
    pass


class _RejectionCode(StrEnum):
    MISSING_ENTITY = "MISSING_ENTITY"
    DERIVATION_MISMATCH = "DERIVATION_MISMATCH"
    STALE_REFERENCE = "STALE_REFERENCE"
    INVALID_REWARD = "INVALID_REWARD"


class _RejectionReason(BaseModel):
    code: _RejectionCode
    message: str


class _TransactionDecision(BaseModel):
    proposal_id: str
    accepted: bool
    reasons: tuple[_RejectionReason, ...]


def _assert_fixed_invalid_reward_decision(decision: object) -> None:
    assert type(decision) is _TransactionDecision
    assert decision.accepted is False
    assert decision.reasons == (
        _RejectionReason(
            code=_RejectionCode.INVALID_REWARD,
            message="reward assessment proposal is invalid",
        ),
    )


def _task_8_and_13_namespace() -> dict[str, object]:
    plan = _phase_a_plan()
    task_8_source = _plan_python_block(plan, "RecordCohortPlan")
    adapter_source = _plan_python_block(plan, "HarnessTraceProposalAdapter")

    namespace: dict[str, object] = {
        "ActorIdentity": ActorIdentity,
        "ActorKind": ActorKind,
        "Annotated": Annotated,
        "Approval": Approval,
        "BaseModel": BaseModel,
        "Any": Any,
        "Decimal": Decimal,
        "DecimalException": DecimalException,
        "Enum": Enum,
        "Mapping": Mapping,
        "Union": Union,
        "UnionType": UnionType,
        "CapabilityProfile": CapabilityProfile,
        "CapabilityProfileReceiptRef": CapabilityProfileReceiptRef,
        "CohortPlan": CohortPlan,
        "CohortPlanReceiptRef": CohortPlanReceiptRef,
        "CohortRequest": CohortRequest,
        "CollaborationSession": CollaborationSession,
        "CollaborationTermination": CollaborationTermination,
        "CompiledProgressPlanBinding": CompiledProgressPlanBinding,
        "ConfigDict": ConfigDict,
        "DiversityAssessment": DiversityAssessment,
        "ErrorCorrelationRecord": ErrorCorrelationRecord,
        "Field": Field,
        "field_validator": field_validator,
        "get_args": get_args,
        "get_origin": get_origin,
        "GuidanceEvaluationCell": GuidanceEvaluationCell,
        "GuidanceEvaluationProtocol": GuidanceEvaluationProtocol,
        "HarnessExecutionTrace": HarnessExecutionTrace,
        "json": json,
        "Literal": __import__("typing").Literal,
        "MethodDirectionOutcome": MethodDirectionOutcome,
        "ModelHarnessAnalysis": ModelHarnessAnalysis,
        "ModelHarnessCell": ModelHarnessCell,
        "ModelHarnessProtocol": ModelHarnessProtocol,
        "OpaqueProcedureCompilationEnvelope": OpaqueProcedureCompilationEnvelope,
        "PeerContribution": PeerContribution,
        "PeerRequest": PeerRequest,
        "ProcedureCompilationReceiptRef": ProcedureCompilationReceiptRef,
        "ProgressPlan": ProgressPlan,
        "Proposal": Proposal,
        "ProposalBase": ProposalBase,
        "PROPOSAL_ADAPTER": PROPOSAL_ADAPTER,
        "ProposalBoundaryValidationError": _ProposalBoundaryValidationError,
        "RejectionCode": _RejectionCode,
        "RejectionReason": _RejectionReason,
        "RewardHackingFamily": RewardHackingFamily,
        "Self": Self,
        "StableIdentifier": StableIdentifier,
        "TopologyEvent": TopologyEvent,
        "UtcTimestamp": UtcTimestamp,
        "UTC": UTC,
        "datetime": datetime,
        "RewardObservation": RewardObservation,
        "RewardHackingFinding": RewardHackingFinding,
        "RewardValidityAssessment": RewardValidityAssessment,
        "TransactionDecision": _TransactionDecision,
        "TypeAdapter": TypeAdapter,
        "ValidationInfo": ValidationInfo,
        "model_validator": model_validator,
        "parse_untrusted_harness_execution_trace": parse_untrusted_harness_execution_trace,
        "suppress": __import__("contextlib").suppress,
    }
    exec(compile(task_8_source, "task-8-contract", "exec"), namespace)
    governed_union = reduce(or_, namespace["GOVERNED_PROPOSAL_CLASSES"])
    namespace["Proposal"] = Annotated[
        governed_union,
        Field(discriminator="proposal_type"),
    ]
    namespace["PROPOSAL_ADAPTER"] = TypeAdapter(namespace["Proposal"])
    exec(compile(adapter_source, "task-13-adapter-contract", "exec"), namespace)
    return namespace


def _runtime_task_8_namespace() -> dict[str, object]:
    return dict(vars(transaction_models))


@pytest.fixture(params=("plan", "runtime"))
def task_8_namespace(request: pytest.FixtureRequest) -> dict[str, object]:
    if request.param == "plan":
        return _task_8_and_13_namespace()
    return _runtime_task_8_namespace()


def _boundary_error_type(namespace: dict[str, object]) -> type[ValueError]:
    boundary_error = namespace["ProposalBoundaryValidationError"]
    assert isinstance(boundary_error, type)
    assert issubclass(boundary_error, ValueError)
    return boundary_error


def _assert_detached_proposal_boundary_error(
    error: BaseException,
    expected_type: type[ValueError] = _ProposalBoundaryValidationError,
) -> None:
    assert type(error) is expected_type
    assert str(error) == "transaction proposal failed validation"
    assert error.__cause__ is None
    assert error.__context__ is None


GOVERNED_PROPOSAL_CLASS_NAMES = (
    "RecordCapabilityProfile",
    "RecordCohortPlan",
    "RecordDiversityAssessment",
    "RecordCollaborationSession",
    "AppendPeerRequest",
    "AppendPeerContribution",
    "AppendTopologyEvent",
    "RecordCollaborationTermination",
    "RecordProcedureCompilation",
    "RecordMethodDirectionOutcome",
    "BindCompiledProgressPlan",
    "RecordGuidanceEvaluationProtocol",
    "AppendGuidanceEvaluationCell",
    "RecordModelHarnessProtocol",
    "AppendModelHarnessCell",
    "RecordModelHarnessAnalysis",
    "RecordHarnessExecutionTrace",
    "RecordRewardAssessment",
)


def _governed_proposal_examples(
    namespace: dict[str, object],
) -> tuple[BaseModel, ...]:
    from super_scientist.domain.cognition import assess_diversity
    from super_scientist.domain.collaboration import (
        TopologyOperation,
        TopologySnapshot,
        evaluate_termination,
        initial_collaboration_state,
    )
    from super_scientist.domain.procedures import (
        MethodDirectionStatus,
        ProcedureCompilationRecord,
        canonical_model_hash,
        compile_method,
        procedure_to_progress_plan,
    )
    from tests.unit.cognition.test_diversity import (
        _cohort,
        _correlation,
        _profile,
    )
    from tests.unit.collaboration.conftest import session_factory
    from tests.unit.collaboration.test_engine import _contribution, _request
    from tests.unit.collaboration.test_topology import _event
    from tests.unit.harness_eval.test_guidance import (
        _cell as guidance_cell,
    )
    from tests.unit.harness_eval.test_guidance import (
        _protocol as guidance_protocol,
    )
    from tests.unit.harness_eval.test_model_harness_matrix import (
        _cells as matrix_cells,
    )
    from tests.unit.harness_eval.test_model_harness_matrix import (
        _protocol as matrix_protocol,
    )
    from tests.unit.harness_eval.test_model_harness_matrix import analyze_model_harness
    from tests.unit.harness_eval.test_rewards import (
        assess_reward_validity as valid_assessment,
    )
    from tests.unit.harness_eval.test_traces import valid_trace
    from tests.unit.procedures.test_compiler import (
        NOW as PROCEDURE_NOW,
    )
    from tests.unit.procedures.test_compiler import (
        POLICY_HASH,
        valid_request,
    )

    profiles = (
        _profile("peer-a", prompt_strategy="direct"),
        _profile("peer-b", prompt_strategy="critique-first"),
    )
    cohort = _cohort(*profiles)
    correlation = _correlation()
    diversity = assess_diversity(cohort, profiles, (correlation,))
    profile_receipts = tuple(
        CapabilityProfileReceiptRef(
            proposal_id=f"profile-proposal-{index}",
            proposal_hash=profile.content_hash,
            audit_event_id=f"profile-audit-{index}",
            audit_event_hash=str(index + 1) * 64,
        )
        for index, profile in enumerate(profiles)
    )
    cohort_receipt = CohortPlanReceiptRef(
        proposal_id="cohort-proposal",
        proposal_hash=cohort.content_hash,
        audit_event_id="cohort-audit",
        audit_event_hash="3" * 64,
    )

    make_session = session_factory.__wrapped__()
    session = make_session("peer-a", "peer-b")
    collaboration_state = initial_collaboration_state(session)
    after_topology = TopologySnapshot.build(
        active_peer_ids=collaboration_state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    topology_event = _event(
        session,
        collaboration_state.topology,
        after_topology,
        TopologyOperation.DISABLE_EDGE,
        edge=("peer-a", "peer-b"),
    )

    compilation_result = compile_method(valid_request())
    compilation = OpaqueProcedureCompilationEnvelope.build(
        compilation_id="round-trip-compilation",
        result=compilation_result,
        created_at=PROCEDURE_NOW,
        governing_policy_hash=POLICY_HASH,
    )
    compilation_record = ProcedureCompilationRecord.build(
        compilation_id=compilation.compilation_id,
        result=compilation_result,
        created_at=PROCEDURE_NOW,
        governing_policy_hash=POLICY_HASH,
    )
    compilation_receipt = ProcedureCompilationReceiptRef(
        proposal_id="compilation-proposal",
        proposal_hash="4" * 64,
        audit_event_id="compilation-audit",
        audit_event_hash="5" * 64,
    )
    progress_plan = procedure_to_progress_plan(
        compilation_result,
        run_id="round-trip-run",
        plan_version_id="round-trip-plan",
        version=1,
        created_at=PROCEDURE_NOW,
        governing_policy_hash=POLICY_HASH,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="round-trip-binding",
        compilation_receipt=compilation_receipt,
        compilation_id=compilation_record.compilation_id,
        compilation_hash=compilation_record.content_hash,
        procedure_id=compilation_result.procedure.procedure_id,
        procedure_hash=compilation_result.procedure.content_hash,
        plan=progress_plan,
        plan_hash=canonical_model_hash(progress_plan),
        created_at=PROCEDURE_NOW,
        governing_policy_hash=POLICY_HASH,
    )
    direction_outcome = MethodDirectionOutcome.build(
        outcome_id="round-trip-direction",
        status=MethodDirectionStatus.UNSUPPORTED,
        evidence_refs=(),
        failed_method_ids=("method-a",),
        rejected_procedure_ids=(compilation_result.procedure.procedure_id,),
        budget_reference_ids=("budget-a",),
        terminal_rule="Independent validation rejected the method",
        created_at=PROCEDURE_NOW,
        governing_policy_hash=POLICY_HASH,
    )

    guidance = guidance_protocol()
    guidance_result = guidance_cell(protocol=guidance)
    matrix = matrix_protocol()
    matrix_results = matrix_cells(matrix)
    matrix_analysis = analyze_model_harness(matrix, matrix_results)
    trace = valid_trace()
    assessment = valid_assessment(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )
    metadata = namespace["HarnessTraceRecordMetadata"](
        received_at=NOW,
        source_id="all-proposals-round-trip",
    )

    def proposal(class_name: str, **values: object) -> BaseModel:
        return namespace[class_name](
            proposal_id=f"round-trip-{class_name}",
            idempotency_key=f"round-trip-key-{class_name}",
            proposer=_actor(),
            **values,
        )

    return (
        proposal("RecordCapabilityProfile", profile=profiles[0]),
        proposal(
            "RecordCohortPlan",
            request=cohort.request_snapshot,
            profile_receipts=profile_receipts,
            plan=cohort,
        ),
        proposal(
            "RecordDiversityAssessment",
            cohort_plan_receipt=cohort_receipt,
            profile_receipts=profile_receipts,
            error_correlations=(correlation,),
            assessment=diversity,
        ),
        proposal("RecordCollaborationSession", session=session),
        proposal("AppendPeerRequest", request=_request(session, "peer-a")),
        proposal(
            "AppendPeerContribution",
            contribution=_contribution(session, "peer-a"),
        ),
        proposal("AppendTopologyEvent", event=topology_event),
        proposal(
            "RecordCollaborationTermination",
            session_id=session.session_id,
            termination=evaluate_termination(collaboration_state),
        ),
        proposal("RecordProcedureCompilation", compilation=compilation),
        proposal(
            "RecordMethodDirectionOutcome",
            compilation_id=compilation_record.compilation_id,
            outcome=direction_outcome,
        ),
        proposal(
            "BindCompiledProgressPlan",
            compilation_receipt=compilation_receipt,
            binding=binding,
            plan=progress_plan,
        ),
        proposal("RecordGuidanceEvaluationProtocol", protocol=guidance),
        proposal("AppendGuidanceEvaluationCell", cell=guidance_result),
        proposal("RecordModelHarnessProtocol", protocol=matrix),
        proposal("AppendModelHarnessCell", cell=matrix_results[0]),
        proposal("RecordModelHarnessAnalysis", analysis=matrix_analysis),
        proposal(
            "RecordHarnessExecutionTrace",
            envelope=namespace["HarnessExecutionTraceEnvelope"](
                metadata=metadata,
                trace=trace,
            ),
        ),
        proposal(
            "RecordRewardAssessment",
            observation=assessment.observation,
            findings=assessment.findings,
            assessment=assessment,
        ),
    )


def test_task_8_declares_every_governed_proposal_and_bounds_each_collection() -> None:
    plan = _phase_a_plan()
    source = plan.split("<!-- task-8-13-trace-contract:start -->", 1)[1].split(
        "<!-- task-8-13-trace-contract:end -->", 1
    )[0]
    tree = ast.parse(source)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    assert (
        tuple(
            node.id
            for node in next(
                node
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "GOVERNED_PROPOSAL_CLASSES"
                    for target in node.targets
                )
            ).value.elts
            if isinstance(node, ast.Name)
        )
        == GOVERNED_PROPOSAL_CLASS_NAMES
    )

    collection_maxima: dict[tuple[str, str], str] = {}
    for class_name in GOVERNED_PROPOSAL_CLASS_NAMES:
        class_node = classes[class_name]
        assert [base.id for base in class_node.bases if isinstance(base, ast.Name)] == [
            "GovernedProposalBase"
        ]
        for field_node in class_node.body:
            if not isinstance(field_node, ast.AnnAssign) or not isinstance(
                field_node.target, ast.Name
            ):
                continue
            annotation = field_node.annotation
            if not (
                isinstance(annotation, ast.Subscript)
                and isinstance(annotation.value, ast.Name)
                and annotation.value.id == "tuple"
            ):
                continue
            collection_key = (class_name, field_node.target.id)
            assert isinstance(field_node.value, ast.Call)
            assert isinstance(field_node.value.func, ast.Name)
            assert field_node.value.func.id == "Field"
            max_length = next(
                keyword.value
                for keyword in field_node.value.keywords
                if keyword.arg == "max_length"
            )
            collection_maxima[collection_key] = ast.unparse(max_length)

    assert collection_maxima == {
        ("RecordCohortPlan", "profile_receipts"): "MAX_PROPOSAL_COLLECTION_ITEMS",
        (
            "RecordDiversityAssessment",
            "profile_receipts",
        ): "MAX_PROPOSAL_COLLECTION_ITEMS",
        (
            "RecordDiversityAssessment",
            "error_correlations",
        ): "MAX_PROPOSAL_COLLECTION_ITEMS",
        ("RecordRewardAssessment", "findings"): "len(RewardHackingFamily)",
    }

    namespace = _task_8_and_13_namespace()
    assert (
        tuple(class_type.__name__ for class_type in namespace["GOVERNED_PROPOSAL_CLASSES"])
        == GOVERNED_PROPOSAL_CLASS_NAMES
    )
    for (class_name, field_name), maximum in {
        ("RecordCohortPlan", "profile_receipts"): 256,
        ("RecordDiversityAssessment", "profile_receipts"): 256,
        ("RecordDiversityAssessment", "error_correlations"): 256,
        ("RecordRewardAssessment", "findings"): len(RewardHackingFamily),
    }.items():
        field = namespace[class_name].model_fields[field_name]
        assert maximum in {
            constraint.max_length
            for constraint in field.metadata
            if hasattr(constraint, "max_length")
        }


def test_task_8_all_governed_proposals_round_trip_through_fixed_parser(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    proposals = _governed_proposal_examples(namespace)

    assert tuple(type(proposal).__name__ for proposal in proposals) == (
        GOVERNED_PROPOSAL_CLASS_NAMES
    )
    for proposal in proposals:
        parsed = namespace["parse_untrusted_proposal_json"](proposal.model_dump_json())
        assert parsed == proposal


def test_task_8_relationship_proposals_bind_exact_bounded_parent_identifiers(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    by_name = {
        type(proposal).__name__: proposal for proposal in _governed_proposal_examples(namespace)
    }
    relationships = {
        "RecordCollaborationTermination": "session_id",
        "RecordMethodDirectionOutcome": "compilation_id",
    }

    for class_name, field_name in relationships.items():
        proposal = by_name[class_name]
        for edge_id in ("会話/session id:識別", "界" * 200):
            if class_name == "RecordCollaborationTermination":
                session = by_name["RecordCollaborationSession"].session
                parent_payload = session.model_dump(mode="python", exclude={"content_hash"})
                parent_payload["session_id"] = edge_id
                parent = CollaborationSession.build(**parent_payload)
                expected_value = parent.session_id
            else:
                envelope = by_name["RecordProcedureCompilation"].compilation
                parent = ProcedureCompilationRecord.build(
                    compilation_id=edge_id,
                    result=parse_untrusted_procedure_compilation_result(envelope),
                    created_at=envelope.created_at,
                    governing_policy_hash=envelope.governing_policy_hash,
                )
                expected_value = parent.compilation_id
            payload = proposal.model_dump(mode="python")
            payload[field_name] = expected_value
            parsed = namespace[class_name].model_validate(payload, strict=True)
            assert getattr(parsed, field_name) == edge_id

        for invalid in ("", " outer-space ", "x" * 201, "contains\x00nul"):
            payload = proposal.model_dump(mode="python")
            payload[field_name] = invalid
            with pytest.raises(ValidationError):
                namespace[class_name].model_validate(payload, strict=True)


MATERIALIZED_0007_IDENTIFIER_PATHS = {
    "RecordCapabilityProfile": (("profile", "profile_id"),),
    "RecordCohortPlan": (("plan", "cohort_plan_id"), ("plan", "request_id")),
    "RecordDiversityAssessment": (
        ("assessment", "diversity_assessment_id"),
        ("assessment", "cohort_plan_id"),
    ),
    "RecordCollaborationSession": (
        ("session", "session_id"),
        ("session", "cohort_plan", "cohort_plan_id"),
    ),
    "AppendPeerRequest": (("request", "request_id"), ("request", "session_id")),
    "AppendPeerContribution": (
        ("contribution", "contribution_id"),
        ("contribution", "session_id"),
        ("contribution", "request_id"),
    ),
    "AppendTopologyEvent": (("event", "event_id"), ("event", "session_id")),
    "RecordCollaborationTermination": (("session_id",),),
    "RecordProcedureCompilation": (("compilation", "compilation_id"),),
    "RecordMethodDirectionOutcome": (
        ("outcome", "outcome_id"),
        ("compilation_id",),
    ),
    "BindCompiledProgressPlan": (
        ("binding", "binding_id"),
        ("binding", "compilation_id"),
    ),
    "RecordGuidanceEvaluationProtocol": (("protocol", "protocol_id"),),
    "AppendGuidanceEvaluationCell": (("cell", "cell_id"), ("cell", "protocol_id")),
    "RecordModelHarnessProtocol": (("protocol", "protocol_id"),),
    "AppendModelHarnessCell": (("cell", "cell_id"), ("cell", "protocol_id")),
    "RecordModelHarnessAnalysis": (("analysis", "protocol_id"),),
    "RecordHarnessExecutionTrace": (
        ("envelope", "trace", "trace_id"),
        ("envelope", "trace", "observed_binding", "protocol_id"),
    ),
    "RecordRewardAssessment": (
        ("assessment", "assessment_id"),
        ("assessment", "trace_id"),
        ("assessment", "observation", "observation_id"),
    ),
}


def _replace_nested_value(payload: dict[str, object], path: tuple[str, ...]) -> None:
    parent = payload
    for field_name in path[:-1]:
        value = parent[field_name]
        assert isinstance(value, dict)
        parent = value
    parent[path[-1]] = "materialized\x00identifier"


def test_task_8_all_0007_materialized_identifiers_reject_nul_before_projection(
    task_8_namespace: dict[str, object],
) -> None:
    proposals = _governed_proposal_examples(task_8_namespace)
    assert {type(proposal).__name__ for proposal in proposals} == set(
        MATERIALIZED_0007_IDENTIFIER_PATHS
    )

    for proposal in proposals:
        class_name = type(proposal).__name__
        for path in MATERIALIZED_0007_IDENTIFIER_PATHS[class_name]:
            payload = proposal.model_dump(mode="python")
            _replace_nested_value(payload, path)
            with pytest.raises(ValidationError, match="NUL"):
                task_8_namespace[class_name].model_validate(payload, strict=True)

        transaction_payload = proposal.model_dump(mode="python")
        transaction_payload["proposal_id"] = "transaction\x00identifier"
        with pytest.raises(ValidationError):
            task_8_namespace[class_name].model_validate(transaction_payload, strict=True)


def test_phase_a_materialized_identifier_aliases_preserve_canonical_unicode_contract() -> None:
    from super_scientist.domain.cognition.models import BoundedIdentifier as CognitionIdentifier
    from super_scientist.domain.collaboration.models import (
        BoundedIdentifier as CollaborationIdentifier,
    )
    from super_scientist.domain.harness_eval.guidance import (
        BoundedIdentifier as EvaluationIdentifier,
    )
    from super_scientist.domain.harness_eval.rewards import BoundedAssessmentIdentifier
    from super_scientist.domain.harness_eval.traces import BoundedTraceIdentifier
    from super_scientist.domain.procedures.models import (
        BoundedIdentifier as ProcedureIdentifier,
    )

    aliases = (
        CognitionIdentifier,
        CollaborationIdentifier,
        ProcedureIdentifier,
        EvaluationIdentifier,
        BoundedTraceIdentifier,
        BoundedAssessmentIdentifier,
    )
    for identifier_type in aliases:
        adapter = TypeAdapter(identifier_type)
        assert adapter.validate_python("会話/id space:識別", strict=True) == "会話/id space:識別"
        assert adapter.validate_python("界" * 200, strict=True) == "界" * 200
        with pytest.raises(ValidationError, match="NUL"):
            adapter.validate_python("id\x00value", strict=True)


def test_task_8_runtime_registers_every_governed_proposal_in_closed_parser() -> None:
    planned_namespace = _task_8_and_13_namespace()
    planned_proposals = _governed_proposal_examples(planned_namespace)

    assert (
        tuple(
            proposal_type.__name__ for proposal_type in transaction_models.GOVERNED_PROPOSAL_CLASSES
        )
        == GOVERNED_PROPOSAL_CLASS_NAMES
    )
    assert {
        code.value
        for code in transaction_models.RejectionCode
        if code.value
        in {
            "DERIVATION_MISMATCH",
            "STALE_REFERENCE",
            "COLLABORATION_BOUND_EXCEEDED",
            "INVALID_PROCEDURE",
            "UNMATCHED_EVALUATION",
            "INVALID_REWARD",
        }
    } == {
        "DERIVATION_MISMATCH",
        "STALE_REFERENCE",
        "COLLABORATION_BOUND_EXCEEDED",
        "INVALID_PROCEDURE",
        "UNMATCHED_EVALUATION",
        "INVALID_REWARD",
    }

    for planned_proposal in planned_proposals:
        parsed = transaction_models.parse_untrusted_proposal_json(
            planned_proposal.model_dump_json()
        )
        assert type(parsed).__name__ == type(planned_proposal).__name__
        assert parsed.model_dump(mode="json") == planned_proposal.model_dump(mode="json")


def test_task_8_runtime_parser_rejects_unknown_governed_field_with_detached_error() -> None:
    planned_namespace = _task_8_and_13_namespace()
    planned_proposal = _governed_proposal_examples(planned_namespace)[0]
    payload = planned_proposal.model_dump(mode="json") | {"authority": "peer"}

    with pytest.raises(transaction_models.ProposalBoundaryValidationError) as error:
        transaction_models.parse_untrusted_proposal_json(canonical_json_bytes(payload))

    assert str(error.value) == "transaction proposal failed validation"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_task_8_parser_rejects_nested_governed_extras(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    boundary_error = _boundary_error_type(namespace)
    proposal = _governed_proposal_examples(namespace)[0]
    payload = proposal.model_dump(mode="json")
    payload["profile"]["unexpected"] = True

    with pytest.raises(boundary_error) as error:
        namespace["parse_untrusted_proposal_json"](canonical_json_bytes(payload))

    _assert_detached_proposal_boundary_error(error.value, boundary_error)


def test_task_8_runtime_parser_exact_gate_and_resource_bounds_avoid_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_adapter = transaction_models.PROPOSAL_ADAPTER
    validated_values: list[str | bytes] = []

    class RecordingAdapter:
        def validate_json(self, value: str | bytes) -> Proposal:
            validated_values.append(value)
            return real_adapter.validate_json(value)

        def validate_python(self, value: object, *, strict: bool) -> Proposal:
            return real_adapter.validate_python(value, strict=strict)

    monkeypatch.setattr(transaction_models, "PROPOSAL_ADAPTER", RecordingAdapter())
    proposal = _evidence_proposal()
    text_payload = proposal.model_dump_json()
    bytes_payload = text_payload.encode("utf-8")

    assert transaction_models.parse_untrusted_proposal_json(text_payload) == proposal
    assert transaction_models.parse_untrusted_proposal_json(bytes_payload) == proposal
    assert validated_values == [text_payload, bytes_payload]

    hooks: list[str] = []

    class HostileStr(str):
        def __len__(self) -> int:
            hooks.append("str-len")
            return super().__len__()

    class HostileBytes(bytes):
        def __len__(self) -> int:
            hooks.append("bytes-len")
            return super().__len__()

    class HostileByteArray(bytearray):
        def __len__(self) -> int:
            hooks.append("bytearray-len")
            return super().__len__()

    class HookedMeta(type):
        def __getattribute__(cls, name: str) -> object:
            hooks.append(f"metaclass-{name}")
            return super().__getattribute__(name)

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    oversized_multibyte = "\N{EURO SIGN}" * (transaction_models.MAX_PROPOSAL_BYTES // 3 + 1)
    over_depth = (
        "[" * (transaction_models.MAX_PROPOSAL_JSON_DEPTH + 1)
        + "0"
        + "]" * (transaction_models.MAX_PROPOSAL_JSON_DEPTH + 1)
    )
    over_nodes = json.dumps([0] * transaction_models.MAX_PROPOSAL_JSON_NODES)
    over_container = json.dumps([0] * (transaction_models.MAX_PROPOSAL_JSON_CONTAINER_ITEMS + 1))
    rejected_values = (
        HostileStr(text_payload),
        HostileBytes(bytes_payload),
        HostileByteArray(bytes_payload),
        MetaclassBacked,
        oversized_multibyte,
        over_depth,
        over_nodes,
        over_container,
    )

    for rejected in rejected_values:
        with pytest.raises(transaction_models.ProposalBoundaryValidationError) as error:
            transaction_models.parse_untrusted_proposal_json(rejected)
        _assert_detached_proposal_boundary_error(
            error.value,
            transaction_models.ProposalBoundaryValidationError,
        )

    assert hooks == []
    assert validated_values == [text_payload, bytes_payload]


def test_task_8_reward_proposal_json_round_trip_preserves_tagged_value_kind(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    from tests.unit.harness_eval.test_rewards import (
        assess_reward_validity as valid_assessment,
    )
    from tests.unit.harness_eval.test_traces import reward_observation, valid_trace

    for reward_value, expected_kind in (
        (Decimal("0.9"), "numeric"),
        ("PASS", "categorical"),
    ):
        observation = reward_observation(value=reward_value)
        trace = valid_trace(observation=observation)
        assessment = valid_assessment(
            observation,
            trace,
            findings=(),
            verifier_succeeded=True,
        )
        proposal = namespace["RecordRewardAssessment"](
            proposal_id=f"tagged-{expected_kind}",
            idempotency_key=f"tagged-{expected_kind}-key",
            proposer=_actor(),
            observation=assessment.observation,
            findings=assessment.findings,
            assessment=assessment,
        )
        payload = json.loads(proposal.model_dump_json())
        assert payload["observation"]["value"]["kind"] == expected_kind

        parsed = namespace["parse_untrusted_proposal_json"](proposal.model_dump_json())

        assert parsed == proposal
        assert type(parsed.observation.value) is type(reward_value)


def test_task_8_governed_json_normalizer_bounds_collections(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    boundary_error = _boundary_error_type(namespace)
    normalize = namespace["_normalize_json_proposal_value"]
    maximum = namespace["MAX_PROPOSAL_RECONSTRUCTION_ITEMS"]
    exact_array = ["item"] * maximum

    assert normalize(exact_array, tuple[str, ...]) == tuple(exact_array)
    assert normalize(exact_array, list[str]) == exact_array
    with pytest.raises(ValueError, match="tuple must be a bounded array"):
        normalize([*exact_array, "excess"], tuple[str, ...])
    with pytest.raises(ValueError, match="list must be a bounded array"):
        normalize([*exact_array, "excess"], list[str])

    proposals = _governed_proposal_examples(namespace)
    cohort_proposal = next(
        proposal for proposal in proposals if type(proposal).__name__ == "RecordCohortPlan"
    )
    values = BaseModel.model_dump(cohort_proposal, mode="python", warnings=False)
    receipt = cohort_proposal.profile_receipts[0]
    values["profile_receipts"] = (receipt,) * 256
    maximum_proposal = namespace["RecordCohortPlan"](**values)
    assert (
        namespace["parse_untrusted_proposal_json"](maximum_proposal.model_dump_json())
        == maximum_proposal
    )

    excessive_payload = maximum_proposal.model_dump(mode="json")
    excessive_payload["profile_receipts"].append(receipt.model_dump(mode="json"))
    with pytest.raises(boundary_error) as error:
        namespace["parse_untrusted_proposal_json"](json.dumps(excessive_payload))
    _assert_detached_proposal_boundary_error(error.value, boundary_error)


def test_task_8_governed_parser_detaches_coercive_and_oversized_json(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    boundary_error = _boundary_error_type(namespace)
    proposals = {
        type(proposal).__name__: proposal for proposal in _governed_proposal_examples(namespace)
    }
    guidance = proposals["RecordGuidanceEvaluationProtocol"]
    reward = proposals["RecordRewardAssessment"]
    guidance_payload = guidance.model_dump(mode="json")
    reward_payload = reward.model_dump(mode="json")

    mutations: list[dict[str, object]] = []
    coercive_identifier = json.loads(json.dumps(guidance_payload))
    coercive_identifier["proposal_id"] = 1
    mutations.append(coercive_identifier)

    coercive_decimal = json.loads(json.dumps(guidance_payload))
    coercive_decimal["protocol"]["evaluation_budget"]["wall_clock_seconds"] = 5
    mutations.append(coercive_decimal)

    untagged_numeric = json.loads(json.dumps(reward_payload))
    untagged_numeric["observation"]["value"]["value"] = 0.9
    mutations.append(untagged_numeric)

    wrong_collection = json.loads(json.dumps(reward_payload))
    wrong_collection["findings"] = {}
    mutations.append(wrong_collection)

    oversized_collection = json.loads(json.dumps(guidance_payload))
    oversized_collection["protocol"]["artifact_ids"] = ["artifact"] * (
        namespace["MAX_PROPOSAL_RECONSTRUCTION_ITEMS"] + 1
    )
    mutations.append(oversized_collection)

    for payload in mutations:
        with pytest.raises(boundary_error) as error:
            namespace["parse_untrusted_proposal_json"](json.dumps(payload))
        _assert_detached_proposal_boundary_error(error.value, boundary_error)

    with pytest.raises(ValidationError):
        namespace["RecordGuidanceEvaluationProtocol"].model_validate(
            guidance_payload,
            strict=True,
        )


def test_task_8_reward_json_requires_exact_tag_before_union_fallback(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    boundary_error = _boundary_error_type(namespace)
    reward = next(
        proposal
        for proposal in _governed_proposal_examples(namespace)
        if type(proposal).__name__ == "RecordRewardAssessment"
    )

    for bare_value in ("0.9", "PASS", 0.9, ["numeric", "0.9"]):
        payload = reward.model_dump(mode="json")
        payload["observation"]["value"] = bare_value
        with pytest.raises(boundary_error) as error:
            namespace["parse_untrusted_proposal_json"](json.dumps(payload))
        _assert_detached_proposal_boundary_error(error.value, boundary_error)

    with pytest.raises(ValueError, match="exact tagged object"):
        namespace["_normalize_json_proposal_value"](None, Decimal | str)


def test_task_8_null_reward_observation_round_trips_in_trace_proposal(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    from tests.unit.harness_eval.test_traces import reward_observation, valid_trace

    observation = reward_observation(value=None)
    trace = valid_trace(observation=observation)
    proposal = namespace["RecordHarnessExecutionTrace"](
        proposal_id="null-reward-trace",
        idempotency_key="null-reward-trace-key",
        proposer=_actor(),
        envelope=namespace["HarnessExecutionTraceEnvelope"](
            metadata=namespace["HarnessTraceRecordMetadata"](
                received_at=NOW,
                source_id="null-reward-round-trip",
            ),
            trace=trace,
        ),
    )

    parsed = namespace["parse_untrusted_proposal_json"](proposal.model_dump_json())

    assert parsed == proposal
    assert parsed.envelope.trace.reward_observation.value is None


def test_task_8_decimal_json_failures_are_safely_detached(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    boundary_error = _boundary_error_type(namespace)
    normalize = namespace["_normalize_json_proposal_value"]

    with pytest.raises(ValueError) as direct_error:
        normalize("not-a-decimal", Decimal)
    assert str(direct_error.value) == "proposal JSON decimal text is invalid"
    assert direct_error.value.__cause__ is None
    assert direct_error.value.__context__ is None

    proposals = {
        type(proposal).__name__: proposal for proposal in _governed_proposal_examples(namespace)
    }
    guidance_payload = proposals["RecordGuidanceEvaluationProtocol"].model_dump(mode="json")
    guidance_payload["protocol"]["evaluation_budget"]["cost_limit"] = "not-a-decimal"
    matrix_payload = proposals["RecordModelHarnessProtocol"].model_dump(mode="json")
    matrix_payload["protocol"]["model_budgets"][0]["budget"]["wall_clock_seconds"] = "not-a-decimal"
    reward_payload = proposals["RecordRewardAssessment"].model_dump(mode="json")
    reward_payload["observation"]["value"] = {
        "kind": "numeric",
        "value": "not-a-decimal",
    }

    for payload in (guidance_payload, matrix_payload, reward_payload):
        with pytest.raises(boundary_error) as error:
            namespace["parse_untrusted_proposal_json"](json.dumps(payload))
        _assert_detached_proposal_boundary_error(error.value, boundary_error)


def test_task_8_proposal_json_depth_checker_is_iterative_bounded_and_exact(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    checker = namespace["proposal_json_is_within_depth_limit"]
    maximum_depth = namespace["MAX_PROPOSAL_JSON_DEPTH"]
    maximum_nodes = namespace["MAX_PROPOSAL_JSON_NODES"]

    assert checker('{"proposal_type":"record_capability_profile"}') is True
    assert checker("[" * maximum_depth + "0" + "]" * maximum_depth) is True
    assert checker("[" * (maximum_depth + 1) + "0" + "]" * (maximum_depth + 1)) is False
    assert checker(json.dumps([0] * (maximum_nodes - 1))) is True
    assert checker(json.dumps([0] * maximum_nodes)) is False

    hooks: list[str] = []

    class HostileText(str):
        def __len__(self) -> int:
            hooks.append("text-len")
            return super().__len__()

        def encode(self, *args: object, **kwargs: object) -> bytes:
            hooks.append("text-encode")
            return super().encode(*args, **kwargs)

    class HostileBytes(bytes):
        def __len__(self) -> int:
            hooks.append("bytes-len")
            return super().__len__()

    class HookedMeta(type):
        def __getattribute__(cls, name: str) -> object:
            hooks.append(f"metaclass-{name}")
            return super().__getattribute__(name)

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    assert checker(HostileText("{}")) is False
    assert checker(HostileBytes(b"{}")) is False
    assert checker(bytearray(b"{}")) is False
    assert checker(MetaclassBacked) is False
    assert hooks == []


def test_task_8_governed_identifier_boundary_rejects_raw_string_subclasses(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    proposal_base = namespace["GovernedProposalBase"]
    assert (
        proposal_base(
            proposal_id="p" * 200,
            idempotency_key="i" * 200,
            proposer=_actor(),
        ).proposal_id
        == "p" * 200
    )

    for field_name in ("proposal_id", "idempotency_key"):
        with pytest.raises(ValidationError):
            proposal_base(
                **(
                    {
                        "proposal_id": "proposal",
                        "idempotency_key": "idempotency",
                        "proposer": _actor(),
                    }
                    | {field_name: "x" * 201}
                )
            )

    hooks: list[str] = []

    class HostileIdentifier(str):
        def __len__(self) -> int:
            hooks.append("identifier-len")
            return super().__len__()

        def __iter__(self):
            hooks.append("identifier-iter")
            return super().__iter__()

    with pytest.raises(ValidationError):
        proposal_base(
            proposal_id=HostileIdentifier("proposal"),
            idempotency_key="idempotency",
            proposer=_actor(),
        )
    assert hooks == []


def test_task_8_and_13_trace_boundary_contract_revalidates_untrusted_inputs() -> None:
    namespace = _task_8_and_13_namespace()

    from tests.unit.harness_eval.test_traces import valid_trace

    trace = valid_trace()
    metadata_type = namespace["HarnessTraceRecordMetadata"]
    envelope_type = namespace["HarnessExecutionTraceEnvelope"]
    assert isinstance(metadata_type, type)
    assert isinstance(envelope_type, type)
    metadata = metadata_type(received_at=NOW, source_id="harness-adapter")
    assert metadata_type(received_at=NOW, source_id="s" * 200).source_id == "s" * 200
    with pytest.raises(ValidationError):
        metadata_type(received_at=NOW, source_id="s" * 201)
    proposal = namespace["HarnessTraceProposalAdapter"]().from_untrusted_payload(
        trace.model_dump_json(),
        metadata,
        "trace-proposal",
        "trace-idempotency",
        _actor(),
    )

    assert type(proposal) is namespace["RecordHarnessExecutionTrace"]
    assert type(proposal.envelope.metadata) is metadata_type
    assert type(proposal.envelope.trace) is HarnessExecutionTrace
    assert proposal.envelope.schema_version == 1
    assert proposal.envelope.trace == trace
    assert proposal.envelope.metadata is not metadata
    assert "approval" in namespace["RecordRewardAssessment"].model_fields
    assert metadata_type.model_config == {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
    }
    assert envelope_type.model_config == metadata_type.model_config

    with pytest.raises(ValidationError, match="extra_forbidden"):
        metadata_type.model_validate(
            {
                "schema_version": 1,
                "received_at": NOW,
                "source_id": "harness-adapter",
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        envelope_type.model_validate(
            {
                "schema_version": "1",
                "metadata": metadata,
                "trace": trace,
            }
        )

    copied_metadata = metadata.model_copy(update={"source_id": "x" * 201})
    with pytest.raises(_ProposalBoundaryValidationError) as copied_error:
        namespace["HarnessTraceProposalAdapter"]().from_untrusted_payload(
            trace.model_dump_json(),
            copied_metadata,
            "trace-proposal-copy",
            "trace-idempotency-copy",
            _actor(),
        )
    _assert_detached_proposal_boundary_error(copied_error.value)

    forged_trace = trace.model_dump(mode="json") | {"content_hash": "f" * 64}
    with pytest.raises(_ProposalBoundaryValidationError) as trace_error:
        namespace["HarnessTraceProposalAdapter"]().from_untrusted_payload(
            json.dumps(forged_trace),
            metadata,
            "trace-proposal-forged",
            "trace-idempotency-forged",
            _actor(),
        )
    _assert_detached_proposal_boundary_error(trace_error.value)


def test_task_8_trace_proposal_strict_json_round_trip() -> None:
    namespace = _task_8_and_13_namespace()

    from tests.unit.harness_eval.test_traces import valid_trace

    proposal = namespace["HarnessTraceProposalAdapter"]().from_untrusted_payload(
        valid_trace().model_dump_json(),
        namespace["HarnessTraceRecordMetadata"](
            received_at=NOW,
            source_id="json-round-trip",
        ),
        "json-round-trip-proposal",
        "json-round-trip-idempotency",
        _actor(),
    )

    decoded = namespace["RecordHarnessExecutionTrace"].model_validate_json(
        proposal.model_dump_json(),
        strict=True,
    )

    assert decoded == proposal


def test_task_13_trace_adapter_rejects_nonexact_metadata_without_hooks() -> None:
    namespace = _task_8_and_13_namespace()

    from tests.unit.harness_eval.test_traces import valid_trace

    trace_json = valid_trace().model_dump_json()
    metadata_type = namespace["HarnessTraceRecordMetadata"]
    adapter = namespace["HarnessTraceProposalAdapter"]()
    hooks: list[str] = []

    class MetadataSubclass(metadata_type):
        def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
            hooks.append("subclass-model-dump")
            return super().model_dump(*args, **kwargs)

    class HookedMeta(type):
        def __getattribute__(cls, name: str) -> object:
            hooks.append(f"metaclass-{name}")
            return super().__getattribute__(name)

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    class HostileMetadataText(str):
        def __hash__(self) -> int:
            hooks.append("metadata-hash")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            hooks.append("metadata-eq")
            return super().__eq__(other)

        def __len__(self) -> int:
            hooks.append("metadata-len")
            return super().__len__()

    class HostileDateTime(datetime):
        def utcoffset(self):
            hooks.append("datetime-utcoffset")
            return super().utcoffset()

        def isoformat(self, *args: object, **kwargs: object) -> str:
            hooks.append("datetime-isoformat")
            return super().isoformat(*args, **kwargs)

    exact_metadata = metadata_type(received_at=NOW, source_id="exact-metadata")
    injected_metadata = exact_metadata.model_copy()

    def injected_model_dump(*args: object, **kwargs: object) -> dict[str, object]:
        hooks.append("injected-model-dump")
        return {}

    object.__setattr__(injected_metadata, "model_dump", injected_model_dump)
    hostile_source_metadata = exact_metadata.model_copy(
        update={"source_id": HostileMetadataText("hostile-source")}
    )
    hostile_datetime_metadata = exact_metadata.model_copy(
        update={"received_at": HostileDateTime(2026, 7, 13, 15, 0, tzinfo=UTC)}
    )
    hostile_key_metadata = exact_metadata.model_copy()
    hostile_key_state = object.__getattribute__(hostile_key_metadata, "__dict__")
    hostile_key_state[HostileMetadataText("hostile-key")] = "value"
    hostile_values = (
        MetadataSubclass(received_at=NOW, source_id="subclass-metadata"),
        MetaclassBacked,
        injected_metadata,
        hostile_source_metadata,
        hostile_datetime_metadata,
        hostile_key_metadata,
    )

    hooks.clear()
    for index, hostile in enumerate(hostile_values):
        with pytest.raises(_ProposalBoundaryValidationError) as error:
            adapter.from_untrusted_payload(
                trace_json,
                hostile,
                f"hostile-proposal-{index}",
                f"hostile-idempotency-{index}",
                _actor(),
            )
        _assert_detached_proposal_boundary_error(error.value)

    assert hooks == []


def test_task_13_trace_adapter_freshly_reconstructs_proposer_identity() -> None:
    namespace = _task_8_and_13_namespace()

    from tests.unit.harness_eval.test_traces import valid_trace

    adapter = namespace["HarnessTraceProposalAdapter"]()
    metadata = namespace["HarnessTraceRecordMetadata"](
        received_at=NOW,
        source_id="actor-boundary",
    )
    actor = _actor()
    proposal = adapter.from_untrusted_payload(
        valid_trace().model_dump_json(),
        metadata,
        "actor-proposal",
        "actor-idempotency",
        actor,
    )
    assert proposal.proposer == actor
    assert proposal.proposer is not actor

    invalid_actors = (
        actor.model_copy(update={"actor_id": ""}),
        actor.model_copy(update={"provider_id": None}),
        actor.model_copy(update={"model_id": None}),
    )
    for index, invalid_actor in enumerate(invalid_actors):
        with pytest.raises(_ProposalBoundaryValidationError) as error:
            adapter.from_untrusted_payload(
                valid_trace().model_dump_json(),
                metadata,
                f"invalid-actor-proposal-{index}",
                f"invalid-actor-idempotency-{index}",
                invalid_actor,
            )
        _assert_detached_proposal_boundary_error(error.value)

    hooks: list[str] = []

    class HostileIdentifier(str):
        def __len__(self) -> int:
            hooks.append("adapter-identifier-len")
            return super().__len__()

        def __iter__(self):
            hooks.append("adapter-identifier-iter")
            return super().__iter__()

    hostile_inputs = (
        (HostileIdentifier("proposal-id"), "idempotency-id", actor),
        ("proposal-id", HostileIdentifier("idempotency-id"), actor),
        (
            "proposal-id",
            "idempotency-id",
            actor.model_copy(update={"actor_id": HostileIdentifier("actor-id")}),
        ),
    )
    hooks.clear()
    for proposal_id, idempotency_key, proposer in hostile_inputs:
        with pytest.raises(_ProposalBoundaryValidationError) as error:
            adapter.from_untrusted_payload(
                valid_trace().model_dump_json(),
                metadata,
                proposal_id,
                idempotency_key,
                proposer,
            )
        _assert_detached_proposal_boundary_error(error.value)
    assert hooks == []


def test_task_8_runtime_fresh_helpers_reject_copied_and_injected_state_without_hooks() -> None:
    from tests.unit.harness_eval.test_rewards import assess_reward_validity as valid_assessment
    from tests.unit.harness_eval.test_traces import valid_trace

    trace = valid_trace()
    assessment = valid_assessment(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )
    actor = _actor()
    approval = Approval(approver=_actor(), approved_at=NOW)
    proposal = transaction_models.RecordRewardAssessment(
        proposal_id="runtime-fresh-reward",
        idempotency_key="runtime-fresh-reward-key",
        proposer=actor,
        approval=approval,
        observation=assessment.observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    fresh = transaction_models._fresh_reward_assessment_proposal(proposal)

    assert fresh == proposal
    assert fresh is not proposal
    assert fresh.proposer is not actor
    assert fresh.approval is not approval
    assert fresh.approval is not None
    assert fresh.approval.approver is not approval.approver
    assert fresh.observation is not proposal.observation
    assert fresh.assessment is not proposal.assessment
    assert all(
        fresh_finding is not original_finding
        for fresh_finding, original_finding in zip(
            fresh.findings,
            proposal.findings,
            strict=True,
        )
    )

    metadata = transaction_models.HarnessTraceRecordMetadata(
        received_at=NOW,
        source_id="runtime-fresh-metadata",
    )
    fresh_metadata = transaction_models._fresh_harness_trace_metadata(metadata)
    assert fresh_metadata == metadata
    assert fresh_metadata is not metadata

    hooks: list[str] = []

    class HostileIdentifier(str):
        def __len__(self) -> int:
            hooks.append("identifier-len")
            return super().__len__()

        def __iter__(self):
            hooks.append("identifier-iter")
            return super().__iter__()

    class HostileDateTime(datetime):
        def utcoffset(self):
            hooks.append("datetime-utcoffset")
            return super().utcoffset()

    class HostileSerializer:
        def to_python(self, *args: object, **kwargs: object) -> object:
            hooks.append("serializer")
            raise AssertionError("injected serializer must not run")

    copied_metadata = metadata.model_copy(update={"source_id": HostileIdentifier("hostile-source")})
    injected_metadata = metadata.model_copy()
    object.__setattr__(
        injected_metadata,
        "__pydantic_serializer__",
        HostileSerializer(),
    )
    injected_proposer = actor.model_copy()
    object.__setattr__(
        injected_proposer,
        "__pydantic_serializer__",
        HostileSerializer(),
    )
    injected_approval = approval.model_copy()
    object.__setattr__(
        injected_approval,
        "__pydantic_serializer__",
        HostileSerializer(),
    )
    copied_proposer = actor.model_copy(update={"actor_id": HostileIdentifier("hostile-actor")})
    copied_approval = approval.model_copy(
        update={"approved_at": HostileDateTime(2026, 7, 13, 15, 0, tzinfo=UTC)}
    )
    copied_observation = assessment.observation.model_copy(
        update={"observation_id": HostileIdentifier("hostile-observation")}
    )
    copied_finding = assessment.findings[0].model_copy(
        update={"finding_id": HostileIdentifier("hostile-finding")}
    )
    copied_assessment = assessment.model_copy(
        update={"findings": (copied_finding, *assessment.findings[1:])}
    )
    injected_assessment = assessment.model_copy()
    object.__setattr__(
        injected_assessment,
        "__pydantic_serializer__",
        HostileSerializer(),
    )
    injected_proposal = proposal.model_copy()
    object.__setattr__(
        injected_proposal,
        "__pydantic_serializer__",
        HostileSerializer(),
    )

    hostile_metadata = (copied_metadata, injected_metadata)
    hostile_proposals = (
        proposal.model_copy(update={"proposer": copied_proposer}),
        proposal.model_copy(update={"proposer": injected_proposer}),
        proposal.model_copy(update={"approval": copied_approval}),
        proposal.model_copy(update={"approval": injected_approval}),
        proposal.model_copy(update={"observation": copied_observation}),
        proposal.model_copy(
            update={
                "findings": (copied_finding, *assessment.findings[1:]),
                "assessment": copied_assessment,
            }
        ),
        proposal.model_copy(update={"assessment": injected_assessment}),
        injected_proposal,
    )

    hooks.clear()
    for hostile in hostile_metadata:
        with pytest.raises(ValueError):
            transaction_models._fresh_harness_trace_metadata(hostile)
    for hostile in hostile_proposals:
        with pytest.raises(ValueError):
            transaction_models._fresh_reward_assessment_proposal(hostile)
    assert hooks == []


def test_task_8_runtime_invalid_reward_decision_is_fixed_and_total_without_hooks() -> None:
    hooks: list[str] = []

    class HookedMeta(type):
        def __getattribute__(cls, name: str) -> object:
            hooks.append(f"metaclass-{name}")
            return super().__getattribute__(name)

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    for value in (object(), 1, None, MetaclassBacked):
        decision = transaction_models._invalid_reward_decision(value)
        assert type(decision) is transaction_models.TransactionDecision
        assert decision.proposal_id == "invalid-reward-proposal"
        assert decision.accepted is False
        assert decision.reasons == (
            transaction_models.RejectionReason(
                code=transaction_models.RejectionCode.INVALID_REWARD,
                message="reward assessment proposal is invalid",
            ),
        )

    assert hooks == []


def test_task_13_reward_handler_executes_with_focused_capabilities() -> None:
    plan = _phase_a_plan()
    namespace = _task_8_and_13_namespace()
    handler_source = _plan_python_block(plan, "RecordRewardAssessmentHandler")

    namespace.update(
        {
            "EvidenceReceipt": EvidenceReceipt,
            "RejectionCode": _RejectionCode,
            "RejectionReason": _RejectionReason,
            "TransactionDecision": _TransactionDecision,
            "TraceFreshness": TraceFreshness,
            "assess_reward_validity": assess_reward_validity,
            "reject_existing_or_accept": lambda proposal, existing: (
                "accepted",
                proposal,
            ),
            "rejected": lambda proposal, code: ("rejected", code),
            "reward_validity_receipt": reward_validity_receipt,
            "trace_freshness": trace_freshness,
        }
    )
    exec(compile(handler_source, "task-13-handler-contract", "exec"), namespace)

    from tests.unit.harness_eval.test_rewards import assess_reward_validity as valid_assessment
    from tests.unit.harness_eval.test_traces import valid_trace

    trace = valid_trace()
    assessment = valid_assessment(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )
    proposal = namespace["RecordRewardAssessment"](
        proposal_id="reward-proposal",
        idempotency_key="reward-idempotency",
        proposer=_actor(),
        approval=Approval(approver=_actor(), approved_at=NOW),
        observation=assessment.observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    assert proposal.approval is not None
    with pytest.raises(ValidationError):
        namespace["RecordRewardAssessment"](
            proposal_id=1,
            idempotency_key="reward-idempotency-coercive",
            proposer=_actor(),
            observation=assessment.observation,
            findings=assessment.findings,
            assessment=assessment,
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        namespace["RecordRewardAssessment"](
            proposal_id="reward-proposal-extra",
            idempotency_key="reward-idempotency-extra",
            proposer=_actor(),
            observation=assessment.observation,
            findings=assessment.findings,
            assessment=assessment,
            unexpected=True,
        )
    capabilities = SimpleNamespace(
        expectation=assessment.expectation,
        verification=assessment.verification,
        diagnostic_coverage=assessment.diagnostic_coverage,
        inventory=assessment.evidence_inventory,
    )

    class FocusedContext:
        def __init__(self, resolved: object | None) -> None:
            self.trace = trace
            self.existing_assessment = None
            self.resolved = resolved
            self.requested_receipts: tuple[EvidenceReceipt, EvidenceReceipt] | None = None

        def resolve_reward_assessment_capabilities(
            self,
            *,
            trace_receipt: EvidenceReceipt,
            assessment_receipt: EvidenceReceipt,
        ) -> object | None:
            self.requested_receipts = (trace_receipt, assessment_receipt)
            return self.resolved

    current = FocusedContext(capabilities)
    accepted_result = namespace["RecordRewardAssessmentHandler"]().decide(
        proposal,
        current,
    )
    assert accepted_result[0] == "accepted"
    assert accepted_result[1] == proposal
    assert accepted_result[1] is not proposal
    assert current.requested_receipts == (
        EvidenceReceipt(
            record_id=trace.trace_id,
            schema_version=trace.schema_version,
            content_hash=trace.content_hash,
        ),
        reward_validity_receipt(assessment),
    )

    stale = FocusedContext(None)
    assert namespace["RecordRewardAssessmentHandler"]().decide(proposal, stale) == (
        "rejected",
        _RejectionCode.STALE_REFERENCE,
    )

    other_trace = valid_trace(observed_binding_updates={"harness_hash": "d" * 64})
    other_assessment = valid_assessment(
        other_trace.reward_observation,
        other_trace,
        findings=(),
        verifier_succeeded=True,
    )
    cross_trace_findings = (other_assessment.findings[0], *assessment.findings[1:])
    invalid_finding_sets = (
        cross_trace_findings,
        (),
        (*assessment.findings, assessment.findings[0]),
    )
    for findings in invalid_finding_sets:
        copied = proposal.model_copy(update={"findings": findings})
        result = namespace["RecordRewardAssessmentHandler"]().decide(
            copied,
            FocusedContext(capabilities),
        )
        _assert_fixed_invalid_reward_decision(result)

    hooks: list[str] = []
    proposal_type = namespace["RecordRewardAssessment"]

    class ProposalSubclass(proposal_type):
        def __getattribute__(self, name: str) -> object:
            hooks.append(f"proposal-subclass-{name}")
            return super().__getattribute__(name)

    subclass_proposal = ProposalSubclass(
        **BaseModel.model_dump(proposal, mode="python", warnings=False)
    )

    class HostileSerializer:
        def to_python(self, *args: object, **kwargs: object) -> object:
            hooks.append("hostile-serializer")
            raise AssertionError("hostile serializer must not run")

    serializer_proposal = proposal.model_copy()
    object.__setattr__(
        serializer_proposal,
        "__pydantic_serializer__",
        HostileSerializer(),
    )

    class HostileIdentifier(str):
        def __len__(self) -> int:
            hooks.append("proposal-identifier-len")
            return super().__len__()

        def __iter__(self):
            hooks.append("proposal-identifier-iter")
            return super().__iter__()

    hostile_identifier_proposals = (
        proposal.model_copy(update={"proposal_id": HostileIdentifier("hostile-proposal")}),
        proposal.model_copy(update={"idempotency_key": HostileIdentifier("hostile-idempotency")}),
    )

    hostile_observation = assessment.observation.model_copy(
        update={"observation_id": HostileIdentifier("hostile-observation")}
    )
    hostile_finding = assessment.findings[0].model_copy(
        update={"finding_id": HostileIdentifier("hostile-finding")}
    )
    hostile_finding_assessment = assessment.model_copy(
        update={"findings": (hostile_finding, *assessment.findings[1:])}
    )
    hostile_binding = assessment.trace.observed_binding.model_copy(
        update={"task_id": HostileIdentifier("hostile-task")}
    )
    hostile_trace = assessment.trace.model_copy(update={"observed_binding": hostile_binding})
    hostile_deep_assessment = assessment.model_copy(update={"trace": hostile_trace})
    serializer_assessment = assessment.model_copy()
    object.__setattr__(
        serializer_assessment,
        "__pydantic_serializer__",
        HostileSerializer(),
    )
    hostile_nested_proposals = (
        proposal.model_copy(update={"observation": hostile_observation}),
        proposal.model_copy(
            update={
                "findings": (hostile_finding, *assessment.findings[1:]),
                "assessment": hostile_finding_assessment,
            }
        ),
        proposal.model_copy(update={"assessment": hostile_deep_assessment}),
        proposal.model_copy(update={"assessment": serializer_assessment}),
    )

    hooks.clear()
    for hostile_proposal in (
        subclass_proposal,
        serializer_proposal,
        *hostile_identifier_proposals,
        *hostile_nested_proposals,
    ):
        decision = namespace["RecordRewardAssessmentHandler"]().decide(
            hostile_proposal,
            FocusedContext(capabilities),
        )
        _assert_fixed_invalid_reward_decision(decision)
    assert hooks == []


def test_task_13_invalid_reward_decision_is_total_without_hooks() -> None:
    namespace = _task_8_and_13_namespace()
    hooks: list[str] = []

    class HookedMeta(type):
        def __getattribute__(cls, name: str) -> object:
            hooks.append(f"metaclass-{name}")
            return super().__getattribute__(name)

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    for value in (object(), 1, None, MetaclassBacked):
        decision = namespace["_invalid_reward_decision"](value)
        _assert_fixed_invalid_reward_decision(decision)
        assert decision.proposal_id == "invalid-reward-proposal"

    assert hooks == []


def test_task_8_recursive_proposal_reconstruction_bounds_containers(
    task_8_namespace: dict[str, object],
) -> None:
    namespace = task_8_namespace
    fresh_exact_value = namespace["_fresh_exact_value"]
    maximum = namespace["MAX_PROPOSAL_RECONSTRUCTION_ITEMS"]
    exact_tuple = ("item",) * maximum
    exact_list = ["item"] * maximum

    assert fresh_exact_value(exact_tuple, tuple[str, ...]) == exact_tuple
    assert fresh_exact_value(exact_list, list[str]) == exact_list
    with pytest.raises(ValueError, match="tuple must be exact and bounded"):
        fresh_exact_value((*exact_tuple, "excess"), tuple[str, ...])
    with pytest.raises(ValueError, match="list must be exact and bounded"):
        fresh_exact_value([*exact_list, "excess"], list[str])


def test_task_8_reward_proposal_bounds_identifiers_and_findings() -> None:
    namespace = _task_8_and_13_namespace()

    from tests.unit.harness_eval.test_rewards import assess_reward_validity as valid_assessment
    from tests.unit.harness_eval.test_traces import valid_trace

    trace = valid_trace()
    assessment = valid_assessment(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )
    proposal_type = namespace["RecordRewardAssessment"]

    def values() -> dict[str, object]:
        return {
            "proposal_id": "p" * 200,
            "idempotency_key": "i" * 200,
            "proposer": _actor(),
            "observation": assessment.observation,
            "findings": assessment.findings,
            "assessment": assessment,
        }

    proposal = proposal_type(**values())
    assert len(proposal.findings) == len(RewardHackingFamily)
    for class_name in (
        "RecordCohortPlan",
        "RecordProcedureCompilation",
        "BindCompiledProgressPlan",
        "RecordHarnessExecutionTrace",
        "RecordRewardAssessment",
    ):
        assert issubclass(namespace[class_name], namespace["GovernedProposalBase"])

    for field_name in ("proposal_id", "idempotency_key"):
        with pytest.raises(ValidationError):
            proposal_type(**(values() | {field_name: "x" * 201}))

    with pytest.raises(ValidationError):
        proposal_type(**(values() | {"findings": ()}))
    with pytest.raises(ValidationError):
        proposal_type(**(values() | {"findings": (*assessment.findings, assessment.findings[0])}))

    metadata = namespace["HarnessTraceRecordMetadata"](
        received_at=NOW,
        source_id="bounded-adapter",
    )
    adapter = namespace["HarnessTraceProposalAdapter"]()
    trace_json = trace.model_dump_json()
    assert (
        adapter.from_untrusted_payload(
            trace_json,
            metadata,
            "p" * 200,
            "i" * 200,
            _actor(),
        ).proposal_id
        == "p" * 200
    )
    for proposal_id, idempotency_key in (
        ("p" * 201, "i" * 200),
        ("p" * 200, "i" * 201),
    ):
        with pytest.raises(_ProposalBoundaryValidationError) as error:
            adapter.from_untrusted_payload(
                trace_json,
                metadata,
                proposal_id,
                idempotency_key,
                _actor(),
            )
        _assert_detached_proposal_boundary_error(error.value)

    other_trace = valid_trace(observed_binding_updates={"harness_hash": "d" * 64})
    other_assessment = valid_assessment(
        other_trace.reward_observation,
        other_trace,
        findings=(),
        verifier_succeeded=True,
    )
    with pytest.raises(ValidationError):
        proposal_type(
            **(
                values()
                | {
                    "findings": (
                        other_assessment.findings[0],
                        *assessment.findings[1:],
                    )
                }
            )
        )


def test_task_8_untrusted_proposal_parser_rejects_non_exact_text_without_hooks() -> None:
    block = _plan_python_block(_phase_a_plan(), "RecordCohortPlan")
    tree = ast.parse(block)
    parser_tree = ast.Module(
        body=[
            node
            for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id
                    in {
                        "MAX_PROPOSAL_BYTES",
                        "MAX_PROPOSAL_JSON_DEPTH",
                        "MAX_PROPOSAL_JSON_NODES",
                        "MAX_PROPOSAL_JSON_CONTAINER_ITEMS",
                    }
                    for target in node.targets
                )
            )
            or (
                isinstance(node, ast.FunctionDef)
                and node.name
                in {
                    "proposal_json_is_within_depth_limit",
                    "parse_untrusted_proposal_json",
                }
            )
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(parser_tree)

    validated_values: list[str | bytes] = []

    class Adapter:
        def validate_json(self, value: str | bytes) -> str:
            validated_values.append(value)
            return "parsed"

    namespace: dict[str, object] = {
        "PROPOSAL_ADAPTER": Adapter(),
        "Proposal": object,
        "ProposalBoundaryValidationError": _ProposalBoundaryValidationError,
        "DecimalException": DecimalException,
        "suppress": __import__("contextlib").suppress,
    }
    exec(compile(parser_tree, "task-8-parser-contract", "exec"), namespace)
    parser = namespace["parse_untrusted_proposal_json"]

    assert parser("{}") == "parsed"
    assert parser(b"{}") == "parsed"
    assert validated_values == ["{}", b"{}"]

    oversized_multibyte_text = "\N{EURO SIGN}" * ((8 * 1_024 * 1_024) // 3 + 1)
    with pytest.raises(_ProposalBoundaryValidationError) as oversized_error:
        parser(oversized_multibyte_text)
    _assert_detached_proposal_boundary_error(oversized_error.value)
    assert validated_values == ["{}", b"{}"]

    hooks: list[str] = []

    class HostileStr(str):
        def __len__(self) -> int:
            hooks.append("str-len")
            return super().__len__()

    class HostileBytes(bytes):
        def __len__(self) -> int:
            hooks.append("bytes-len")
            return super().__len__()

    class HostileByteArray(bytearray):
        def __len__(self) -> int:
            hooks.append("bytearray-len")
            return super().__len__()

    class HookedMeta(type):
        def __len__(cls) -> int:
            hooks.append("metaclass-len")
            return 2

    class MetaclassBacked(metaclass=HookedMeta):
        pass

    for value in (HostileStr("{}"), HostileBytes(b"{}"), HostileByteArray(b"{}"), MetaclassBacked):
        with pytest.raises(_ProposalBoundaryValidationError) as error:
            parser(value)
        _assert_detached_proposal_boundary_error(error.value)

    assert hooks == []


def _actor() -> ActorIdentity:
    return ActorIdentity(
        actor_id="model-actor",
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="a" * 64,
    )


def _evidence_proposal() -> AddEvidence:
    actor = _actor()
    text = "support"
    return AddEvidence(
        proposal_id="proposal-evidence",
        idempotency_key="key-evidence",
        proposer=actor,
        evidence=EvidenceRecord(
            evidence_id="evidence-1",
            evidence_type="document",
            source_locator="fixture://strict",
            retrieved_at=NOW,
            artifact=ArtifactRef(
                sha256="b" * 64,
                size_bytes=len(text),
                media_type="text/plain",
                relative_path=f"sha256/bb/{'b' * 64}",
            ),
            extracted_span=EvidenceSpan(start=0, end=len(text), text=text),
            structured_observation={"score": 1, "labels": ["strict", "json"]},
            provenance={"collector": "test"},
            ingestion_actor_id=actor.actor_id,
        ),
    )


def _claim(status: ClaimStatus, version: int) -> AtomicClaim:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="support")
    return AtomicClaim(
        claim_id="claim-1",
        version=version,
        proposition="Strict parsing is required.",
        scope="fixture",
        population_or_system="fixture system",
        epistemic_modality="observed",
        status=status,
        evidence_links=(() if status is ClaimStatus.PROPOSED else (link,)),
        assumptions=("serialized tuples are accepted",),
        parent_version_id=(None if version == 1 else f"claim-1:{version - 1}"),
        created_at=NOW,
        created_by=_actor().actor_id,
    )


def _proposal_payload(kind: str) -> dict[str, object]:
    if kind == "evidence":
        proposal: Proposal = _evidence_proposal()
    elif kind == "claim":
        proposal = ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="key-claim",
            proposer=_actor(),
            claim=_claim(ClaimStatus.PROPOSED, 1),
        )
    else:
        proposal = TransitionClaim(
            proposal_id="proposal-transition",
            idempotency_key="key-transition",
            proposer=_actor(),
            next_claim=_claim(ClaimStatus.EVIDENCE_LINKED, 2),
        )
    return proposal.model_dump(mode="json")


def _add_extra(payload: dict[str, object], path: tuple[str | int, ...]) -> None:
    target: object = payload
    for segment in path:
        if isinstance(segment, int):
            assert isinstance(target, list)
            target = target[segment]
        else:
            assert isinstance(target, dict)
            target = target[segment]
    assert isinstance(target, dict)
    target["unexpected_field"] = "must be rejected"


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("evidence", ("proposer",)),
        ("evidence", ("evidence",)),
        ("evidence", ("evidence", "artifact")),
        ("evidence", ("evidence", "extracted_span")),
        ("claim", ("claim",)),
        ("transition", ("next_claim", "evidence_links", 0)),
        ("transition", ("next_claim",)),
    ],
    ids=[
        "model-identity",
        "evidence-record",
        "artifact",
        "span",
        "claim",
        "evidence-link",
        "transition-next-claim",
    ],
)
def test_external_proposal_json_forbids_nested_extras(
    kind: str,
    path: tuple[str | int, ...],
) -> None:
    payload = _proposal_payload(kind)
    _add_extra(payload, path)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        PROPOSAL_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("kind", ["evidence", "claim", "transition"])
def test_strict_external_parser_accepts_legitimate_json_collections(kind: str) -> None:
    payload = _proposal_payload(kind)

    parsed = PROPOSAL_ADAPTER.validate_json(json.dumps(payload))

    assert parsed.proposal_id == payload["proposal_id"]


def test_cognition_records_forbid_extras_and_coercive_schema_versions() -> None:
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )
    profile = CapabilityProfile.build(
        profile_id="profile-strict",
        actor=_actor(),
        diversity_fingerprint=fingerprint,
        governing_policy_hash="f" * 64,
    )
    request = CohortRequest.build(
        request_id="request-strict",
        task_id="task-strict",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=0,
        max_members=1,
        candidate_actor_ids=("model-actor",),
        prohibited_combinations=(),
        governing_policy_hash="f" * 64,
    )

    profile_payload = profile.model_dump(mode="json") | {"unexpected": True}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CapabilityProfile.model_validate_json(json.dumps(profile_payload), strict=True)

    request_payload = request.model_dump(mode="json") | {"schema_version": "1"}
    with pytest.raises(ValidationError):
        CohortRequest.model_validate_json(json.dumps(request_payload), strict=True)

    assert (
        CapabilityProfile.model_validate_json(
            json.dumps(profile.model_dump(mode="json")),
            strict=True,
        )
        == profile
    )


def test_cognition_json_parser_accepts_bounded_tuple_collections() -> None:
    request = CohortRequest.build(
        request_id="request-json",
        task_id="task-json",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=0,
        max_members=2,
        candidate_actor_ids=("peer-a", "peer-b"),
        prohibited_combinations=(("peer-a", "peer-b"),),
        governing_policy_hash="f" * 64,
    )

    parsed = CohortRequest.model_validate_json(
        json.dumps(request.model_dump(mode="json")),
        strict=True,
    )

    assert parsed == request


def test_cognition_rejects_unbounded_nested_actor_identity() -> None:
    actor = _actor().model_copy(update={"provider_id": "p" * 10_000})
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-bounded-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-bounded-actor",
            actor=actor,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_rejects_coercive_nested_actor_timestamp() -> None:
    actor_payload = _actor().model_dump(mode="python")
    actor_payload["created_at"] = NOW.isoformat()
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-strict-actor",
            actor=actor_payload,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_rejects_coercive_nested_actor_kind() -> None:
    actor_payload = _actor().model_dump(mode="python")
    actor_payload["kind"] = "model"
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict-actor-kind",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-strict-actor-kind",
            actor=actor_payload,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_revalidates_preconstructed_actor_identity() -> None:
    malformed_actor = _actor().model_copy(update={"kind": "model"})
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-copied-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    valid = CapabilityProfile.build(
        profile_id="profile-copied-actor",
        actor=_actor(),
        diversity_fingerprint=fingerprint,
        governing_policy_hash="f" * 64,
    )
    payload = valid.model_dump(mode="python")
    payload["actor"] = malformed_actor
    rehashed = valid.model_copy(update={"actor": malformed_actor}).model_dump(
        mode="json", warnings=False
    )
    payload["content_hash"] = sha256_hex(
        canonical_json_bytes(
            {key: value for key, value in rehashed.items() if key != "content_hash"}
        )
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile(**payload)


def test_strict_capability_assessment_parser_rejects_self_report_as_satisfied() -> None:
    payload = {
        "schema_version": 1,
        "profile_id": "profile-self-report",
        "actor_id": "model-actor",
        "requirement": {
            "schema_version": 1,
            "requirement_id": "requirement-self-report",
            "capability_id": "analysis",
            "task_family_id": "research",
            "evidence_snapshot_hash": "a" * 64,
            "required_tools": [],
            "required_modalities": [],
            "required_schema_ids": [],
            "required_execution_constraints": [],
            "disqualifying_failure_categories": [],
        },
        "matched_assertion_ids": ["assertion-self-report"],
        "verified_assertion_ids": [],
        "disposition": "SATISFIED",
        "evidence_status": "SELF_REPORTED",
        "missing_dimensions": [],
        "failed_dimensions": [],
    }

    with pytest.raises(ValidationError, match="capability assessment"):
        CapabilityAssessment.model_validate_json(json.dumps(payload), strict=True)
