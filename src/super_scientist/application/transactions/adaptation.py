from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.improvement.service import (
    DecideEvaluatorSuccessionHandler,
    ProposeEvaluatorVersionHandler,
    RecordConfigurationVersionHandler,
    RecordEvaluatorAuditHandler,
    RecordSelfImprovementMeasurementHandler,
)
from super_scientist.application.research_runs.service import (
    AppendResearchRunEventHandler,
    CreateResearchRunHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.application.transactions.governance import (
    ProposeGovernancePolicyTransitionHandler,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent
from super_scientist.kernel.transactions.models import (
    AppendResearchRunEvent,
    CreateResearchRun,
    DecideEvaluatorSuccession,
    ProposeEvaluatorVersion,
    ProposeGovernancePolicyTransition,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
)
from super_scientist.providers.storage.domain_records import (
    ConfigurationVersionRepository,
    EvaluatorAuditRepository,
    EvaluatorHeadRepository,
    EvaluatorSuccessionRepository,
    EvaluatorVersionRepository,
    ResearchRunEventRepository,
    ResearchRunHeadRepository,
    ResearchRunRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.repositories import PolicyRepository

type FixedAdaptationHandler = ProposalHandler[BaseModel, BaseModel]


@dataclass(frozen=True)
class ResearchRunCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    events: ResearchRunEventRepository
    heads: ResearchRunHeadRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def list_run_events(self, run_id: str) -> tuple[ResearchRunEvent, ...]:
        return tuple(event for event in self.events.list_all() if event.run_id == run_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, ResearchRun):
            self.runs.add(record.run_id, record, record.created_at)
            return
        if isinstance(record, ResearchRunEvent):
            self.events.add(record.run_event_id, record, record.occurred_at)
            return
        raise TypeError(f"unsupported research-run authoritative record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, ResearchRunEvent):
            raise TypeError(f"unsupported research-run projection record: {type(record)!r}")
        self.heads.set(record.run_id, record.run_event_id)


@dataclass(frozen=True)
class ConfigurationCapabilities:
    active_policy: PolicySnapshot
    configurations: ConfigurationVersionRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_configuration(self, configuration_version_id: str) -> ConfigurationVersion | None:
        return self.configurations.get(configuration_version_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, ConfigurationVersion):
            raise TypeError(f"unsupported configuration record: {type(record)!r}")
        self.configurations.add(record.configuration_version_id, record, record.created_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("configuration versions have no mutable projection")


@dataclass(frozen=True)
class EvaluatorAuditCapabilities:
    active_policy: PolicySnapshot
    audits: EvaluatorAuditRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(evaluator_audit_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, EvaluatorAuditRecord):
            raise TypeError(f"unsupported evaluator audit record: {type(record)!r}")
        self.audits.add(record.evaluator_audit_id, record, record.audited_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("evaluator audits have no mutable projection")


@dataclass(frozen=True)
class MeasurementCapabilities:
    active_policy: PolicySnapshot
    measurements: SelfImprovementMeasurementRepository
    runs: ResearchRunRepository
    audits: EvaluatorAuditRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(measurement_id)

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(evaluator_audit_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, SelfImprovementMeasurementRecord):
            raise TypeError(f"unsupported measurement record: {type(record)!r}")
        self.measurements.add(record.measurement_id, record, record.decided_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("measurements have no mutable projection")


@dataclass(frozen=True)
class EvaluatorVersionCapabilities:
    active_policy: PolicySnapshot
    versions: EvaluatorVersionRepository
    measurements: SelfImprovementMeasurementRepository
    audits: EvaluatorAuditRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_evaluator_version(self, evaluator_version_id: str) -> EvaluatorVersion | None:
        return self.versions.get(evaluator_version_id)

    def measurements_for_candidate(
        self,
        candidate_version_id: str,
    ) -> tuple[SelfImprovementMeasurementRecord, ...]:
        return tuple(
            measurement
            for measurement in self.measurements.list_all()
            if measurement.candidate_version_id == candidate_version_id
        )

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(evaluator_audit_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, EvaluatorVersion):
            raise TypeError(f"unsupported evaluator version record: {type(record)!r}")
        self.versions.add(record.evaluator_version_id, record, record.created_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("evaluator proposals cannot update the evaluator head")


@dataclass(frozen=True)
class EvaluatorSuccessionCapabilities:
    active_policy: PolicySnapshot
    versions: EvaluatorVersionRepository
    audits: EvaluatorAuditRepository
    decisions: EvaluatorSuccessionRepository
    head: EvaluatorHeadRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_evaluator_version(self, evaluator_version_id: str) -> EvaluatorVersion | None:
        return self.versions.get(evaluator_version_id)

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(evaluator_audit_id)

    def get_succession_decision(
        self,
        decision_id: str,
    ) -> EvaluatorSuccessionDecision | None:
        return self.decisions.get(decision_id)

    def active_evaluator_version_id(self) -> str | None:
        return self.head.get()

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, EvaluatorSuccessionDecision):
            raise TypeError(f"unsupported evaluator succession record: {type(record)!r}")
        self.decisions.add(
            record.evaluator_succession_decision_id,
            record,
            record.decided_at,
        )

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, EvaluatorSuccessionDecision) or not record.accepted:
            raise TypeError(f"unsupported evaluator head projection: {type(record)!r}")
        self.head.set(record.candidate_evaluator_version_id)


@dataclass(frozen=True)
class GovernanceTransitionCapabilities:
    active_policy: PolicySnapshot
    runs: ResearchRunRepository
    audits: EvaluatorAuditRepository
    measurements: SelfImprovementMeasurementRepository
    policies: PolicyRepository
    projection_measurement: SelfImprovementMeasurementRecord

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_policy(self, policy_hash_value: str) -> PolicySnapshot | None:
        return self.policies.get(policy_hash_value)

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(evaluator_audit_id)

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(measurement_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, ResearchRun):
            self.runs.add(record.run_id, record, record.created_at)
            return
        if isinstance(record, EvaluatorAuditRecord):
            self.audits.add(record.evaluator_audit_id, record, record.audited_at)
            return
        if isinstance(record, SelfImprovementMeasurementRecord):
            self.measurements.add(record.measurement_id, record, record.decided_at)
            return
        raise TypeError(f"unsupported governance transition record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, PolicySnapshot):
            raise TypeError(f"unsupported governance projection: {type(record)!r}")
        if self.projection_measurement.candidate_version_id != record.policy_hash:
            raise RuntimeError("candidate policy does not match its authoritative measurement")
        self.policies.add_and_activate(record, self.projection_measurement.decided_at)


def fixed_adaptation_handlers() -> tuple[FixedAdaptationHandler, ...]:
    return (  # type: ignore[return-value]
        CreateResearchRunHandler(),
        AppendResearchRunEventHandler(),
        RecordConfigurationVersionHandler(),
        RecordEvaluatorAuditHandler(),
        RecordSelfImprovementMeasurementHandler(),
        ProposeEvaluatorVersionHandler(),
        DecideEvaluatorSuccessionHandler(),
        ProposeGovernancePolicyTransitionHandler(),
    )


def adaptation_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
) -> (
    ResearchRunCapabilities
    | ConfigurationCapabilities
    | EvaluatorAuditCapabilities
    | MeasurementCapabilities
    | EvaluatorVersionCapabilities
    | EvaluatorSuccessionCapabilities
    | GovernanceTransitionCapabilities
):
    if isinstance(proposal, (CreateResearchRun, AppendResearchRunEvent)):
        return ResearchRunCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            events=ResearchRunEventRepository(connection),
            heads=ResearchRunHeadRepository(connection),
        )
    if isinstance(proposal, RecordConfigurationVersion):
        return ConfigurationCapabilities(
            active_policy=active_policy,
            configurations=ConfigurationVersionRepository(connection),
        )
    if isinstance(proposal, RecordEvaluatorAudit):
        return EvaluatorAuditCapabilities(
            active_policy=active_policy,
            audits=EvaluatorAuditRepository(connection),
        )
    if isinstance(proposal, RecordSelfImprovementMeasurement):
        return MeasurementCapabilities(
            active_policy=active_policy,
            measurements=SelfImprovementMeasurementRepository(connection),
            runs=ResearchRunRepository(connection),
            audits=EvaluatorAuditRepository(connection),
        )
    if isinstance(proposal, ProposeEvaluatorVersion):
        return EvaluatorVersionCapabilities(
            active_policy=active_policy,
            versions=EvaluatorVersionRepository(connection),
            measurements=SelfImprovementMeasurementRepository(connection),
            audits=EvaluatorAuditRepository(connection),
        )
    if isinstance(proposal, DecideEvaluatorSuccession):
        return EvaluatorSuccessionCapabilities(
            active_policy=active_policy,
            versions=EvaluatorVersionRepository(connection),
            audits=EvaluatorAuditRepository(connection),
            decisions=EvaluatorSuccessionRepository(connection),
            head=EvaluatorHeadRepository(connection),
        )
    if isinstance(proposal, ProposeGovernancePolicyTransition):
        return GovernanceTransitionCapabilities(
            active_policy=active_policy,
            runs=ResearchRunRepository(connection),
            audits=EvaluatorAuditRepository(connection),
            measurements=SelfImprovementMeasurementRepository(connection),
            policies=PolicyRepository(connection),
            projection_measurement=proposal.measurement,
        )
    raise TypeError(f"no fixed adaptation capability for proposal: {type(proposal)!r}")
