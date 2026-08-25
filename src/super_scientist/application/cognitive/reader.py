from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Protocol

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import Connection

from super_scientist.domain.primitives import StableIdentifier
from super_scientist.providers.storage.cognitive_records import (
    CapabilityProfileRepository,
    CohortPlanRepository,
    CollaborationSessionRepository,
    CollaborationTerminationRepository,
    CompiledProgressPlanBindingRepository,
    DiversityAssessmentRepository,
    MethodDirectionOutcomeRepository,
    PeerContributionRepository,
    PeerRequestRepository,
    ProcedureCompilationRepository,
    TopologyEventRepository,
)
from super_scientist.providers.storage.evaluation_records import (
    GuidanceCellRepository,
    GuidanceEvaluationProtocolRepository,
    HarnessExecutionTraceRepository,
    ModelHarnessAnalysisRepository,
    ModelHarnessCellRepository,
    ModelHarnessProtocolRepository,
    RewardAssessmentRepository,
)

MAX_COGNITIVE_RECORD_ID_LENGTH = 200
_RECORD_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(
    Annotated[StableIdentifier, Field(max_length=MAX_COGNITIVE_RECORD_ID_LENGTH)]
)


class CognitiveRecordKind(StrEnum):
    CAPABILITY_PROFILE = "capability-profile"
    COHORT_PLAN = "cohort-plan"
    DIVERSITY_ASSESSMENT = "diversity-assessment"
    COLLABORATION_SESSION = "collaboration-session"
    PEER_REQUEST = "peer-request"
    PEER_CONTRIBUTION = "peer-contribution"
    TOPOLOGY_EVENT = "topology-event"
    COLLABORATION_TERMINATION = "collaboration-termination"
    PROCEDURE_COMPILATION = "procedure-compilation"
    METHOD_DIRECTION_OUTCOME = "method-direction-outcome"
    COMPILED_PROGRESS_PLAN_BINDING = "compiled-progress-plan-binding"
    GUIDANCE_PROTOCOL = "guidance-protocol"
    GUIDANCE_CELL = "guidance-cell"
    MODEL_HARNESS_PROTOCOL = "model-harness-protocol"
    MODEL_HARNESS_CELL = "model-harness-cell"
    MODEL_HARNESS_ANALYSIS = "model-harness-analysis"
    HARNESS_TRACE = "harness-trace"
    REWARD_ASSESSMENT = "reward-assessment"


class _PointRecordRepository(Protocol):
    def get(self, record_id: str) -> BaseModel | None: ...


type _RepositoryFactory = Callable[[Connection], _PointRecordRepository]

_FIXED_REPOSITORY_FACTORIES: Mapping[CognitiveRecordKind, _RepositoryFactory] = MappingProxyType(
    {
        CognitiveRecordKind.CAPABILITY_PROFILE: CapabilityProfileRepository,
        CognitiveRecordKind.COHORT_PLAN: CohortPlanRepository,
        CognitiveRecordKind.DIVERSITY_ASSESSMENT: DiversityAssessmentRepository,
        CognitiveRecordKind.COLLABORATION_SESSION: CollaborationSessionRepository,
        CognitiveRecordKind.PEER_REQUEST: PeerRequestRepository,
        CognitiveRecordKind.PEER_CONTRIBUTION: PeerContributionRepository,
        CognitiveRecordKind.TOPOLOGY_EVENT: TopologyEventRepository,
        CognitiveRecordKind.COLLABORATION_TERMINATION: CollaborationTerminationRepository,
        CognitiveRecordKind.PROCEDURE_COMPILATION: ProcedureCompilationRepository,
        CognitiveRecordKind.METHOD_DIRECTION_OUTCOME: MethodDirectionOutcomeRepository,
        CognitiveRecordKind.COMPILED_PROGRESS_PLAN_BINDING: (CompiledProgressPlanBindingRepository),
        CognitiveRecordKind.GUIDANCE_PROTOCOL: GuidanceEvaluationProtocolRepository,
        CognitiveRecordKind.GUIDANCE_CELL: GuidanceCellRepository,
        CognitiveRecordKind.MODEL_HARNESS_PROTOCOL: ModelHarnessProtocolRepository,
        CognitiveRecordKind.MODEL_HARNESS_CELL: ModelHarnessCellRepository,
        CognitiveRecordKind.MODEL_HARNESS_ANALYSIS: ModelHarnessAnalysisRepository,
        CognitiveRecordKind.HARNESS_TRACE: HarnessExecutionTraceRepository,
        CognitiveRecordKind.REWARD_ASSESSMENT: RewardAssessmentRepository,
    }
)

if frozenset(_FIXED_REPOSITORY_FACTORIES) != frozenset(CognitiveRecordKind):
    raise RuntimeError("cognitive record reader mapping must cover every fixed record kind")


def validate_cognitive_record_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("cognitive record identifier must be exact text")
    if not value or len(value) > MAX_COGNITIVE_RECORD_ID_LENGTH:
        raise ValueError("cognitive record identifier exceeds its fixed bound")
    identifier = _RECORD_ID_ADAPTER.validate_python(value, strict=True)
    if (
        identifier in {".", ".."}
        or "/" in identifier
        or "\\" in identifier
        or any(ord(character) < 32 for character in identifier)
    ):
        raise ValueError("cognitive record identifier must not be a path")
    return identifier


class CognitiveRecordReader:
    """One-record lookup over the fixed governed cognitive/evaluation repositories."""

    __slots__ = ("_connection",)

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("CognitiveRecordReader is final")

    def get(self, kind: CognitiveRecordKind, record_id: str) -> BaseModel | None:
        if type(kind) is not CognitiveRecordKind:
            raise TypeError("cognitive record kind must be an exact CognitiveRecordKind")
        identifier = validate_cognitive_record_id(record_id)
        repository = _FIXED_REPOSITORY_FACTORIES[kind](self._connection)
        return repository.get(identifier)
