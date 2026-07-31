from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.harness_eval.models import (
    CampaignIteration,
    HarnessCampaign,
    HarnessCampaignReport,
    HarnessConfound,
    HarnessDecision,
    ProtectedCheckerResult,
    harness_campaign_hash,
)
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import (
    CreateHarnessCampaign,
    DecideHarnessCampaign,
    RecordHarnessConfound,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
)
from super_scientist.providers.storage.domain_records import (
    EvaluatorAuditRepository,
    HarnessBudgetRecord,
    HarnessBudgetRepository,
    HarnessCampaignHeadRepository,
    HarnessCampaignRecord,
    HarnessCampaignRepository,
    HarnessConfoundRecord,
    HarnessConfoundRepository,
    HarnessDecisionRecord,
    HarnessDecisionRepository,
    HarnessMetricRecord,
    HarnessMetricRepository,
    HarnessObservationRecord,
    HarnessObservationRepository,
    HarnessPartitionManifestRecord,
    HarnessPartitionManifestRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.protected_evaluation import (
    create_protected_result_gateway,
)
from super_scientist.providers.storage.repositories import TransactionRepository


@dataclass(frozen=True)
class HarnessCampaignCapabilities:
    active_policy: PolicySnapshot
    campaigns: HarnessCampaignRepository
    partitions: HarnessPartitionManifestRepository
    budgets: HarnessBudgetRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None:
        return self.campaigns.get(campaign_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, HarnessCampaign):
            raise TypeError(f"unsupported harness campaign record: {type(record)!r}")
        campaign_record = _campaign_record(record)
        self.campaigns.add(
            campaign_record.campaign_id,
            campaign_record,
            campaign_record.created_at,
        )
        for manifest in record.partitions:
            stored = _partition_record(manifest)
            self.partitions.add(stored.partition_manifest_id, stored, stored.created_at)
        for item in record.budgets:
            stored_budget = HarnessBudgetRecord(
                budget_id=item.budget_id,
                campaign_id=record.campaign_id,
                variant=item.variant,
                budget_hash=sha256_hex(canonical_json_bytes(item.budget.model_dump(mode="json"))),
                **item.budget.model_dump(mode="python"),
                created_at=record.created_at,
                governing_policy_hash=record.governing_policy_hash,
            )
            self.budgets.add(stored_budget.budget_id, stored_budget, stored_budget.created_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("campaign creation has no independent mutable projection")


@dataclass(frozen=True)
class HarnessIterationCapabilities:
    active_policy: PolicySnapshot
    campaigns: HarnessCampaignRepository
    partitions: HarnessPartitionManifestRepository
    budgets: HarnessBudgetRepository
    observations: HarnessObservationRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None:
        return self.campaigns.get(campaign_id)

    def get_partition_manifest(
        self,
        partition_manifest_id: str,
    ) -> HarnessPartitionManifestRecord | None:
        return self.partitions.get(partition_manifest_id)

    def get_budget(self, budget_id: str) -> HarnessBudgetRecord | None:
        return self.budgets.get(budget_id)

    def get_observation(self, observation_id: str) -> HarnessObservationRecord | None:
        return self.observations.get(observation_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, CampaignIteration):
            raise TypeError(f"unsupported harness iteration record: {type(record)!r}")
        campaign_id = _campaign_id_for_manifest(self.partitions, record.partition_manifest_id)
        stored = HarnessObservationRecord(
            observation_id=record.observation_id,
            campaign_id=campaign_id,
            partition_manifest_id=record.partition_manifest_id,
            task_id=record.task_id,
            variant=record.variant,
            iteration_index=record.iteration_index,
            budget_id=record.budget_id,
            candidate_output_hash=record.candidate_output_hash,
            attempt=record.attempt,
            negative_result=record.negative_result,
            result_id=record.result_id,
            outcome=record.outcome,
            evaluator_version_id=record.evaluator_version_id,
            observed_at=record.observed_at,
            governing_policy_hash=self.active_policy.policy_hash,
        )
        self.observations.add(stored.observation_id, stored, stored.observed_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("campaign iterations have no mutable projection")


@dataclass(frozen=True)
class HarnessProtectedResultCapabilities:
    active_policy: PolicySnapshot
    connection: Connection
    campaigns: HarnessCampaignRepository
    partitions: HarnessPartitionManifestRepository
    observations: HarnessObservationRepository
    metrics: HarnessMetricRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None:
        return self.campaigns.get(campaign_id)

    def get_partition_manifest(
        self,
        partition_manifest_id: str,
    ) -> HarnessPartitionManifestRecord | None:
        return self.partitions.get(partition_manifest_id)

    def get_observation(self, observation_id: str) -> HarnessObservationRecord | None:
        return self.observations.get(observation_id)

    def get_result(self, result_id: str) -> HarnessMetricRecord | None:
        return self.metrics.get(result_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if type(record) is not ProtectedCheckerResult:
            raise TypeError(f"unsupported protected result record: {type(record)!r}")
        gateway = create_protected_result_gateway(self.connection)
        try:
            gateway.append_result(record)
        finally:
            gateway.close()

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("protected results have no mutable projection")


@dataclass(frozen=True)
class HarnessConfoundCapabilities:
    active_policy: PolicySnapshot
    campaigns: HarnessCampaignRepository
    confounds: HarnessConfoundRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None:
        return self.campaigns.get(campaign_id)

    def get_confound(self, confound_id: str) -> HarnessConfoundRecord | None:
        return self.confounds.get(confound_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, HarnessConfound):
            raise TypeError(f"unsupported harness confound record: {type(record)!r}")
        stored = HarnessConfoundRecord(
            confound_id=record.confound_id,
            campaign_id=record.campaign_id,
            code=record.code.value,
            description=record.description,
            affected_variant=record.affected_variant,
            resolved=record.resolved,
            independent_analysis_id=record.independent_analysis_id,
            recorded_at=record.recorded_at,
            governing_policy_hash=record.governing_policy_hash,
        )
        self.confounds.add(stored.confound_id, stored, stored.recorded_at)

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("campaign confounds have no mutable projection")


@dataclass(frozen=True)
class HarnessDecisionCapabilities:
    active_policy: PolicySnapshot
    campaigns: HarnessCampaignRepository
    decisions: HarnessDecisionRepository
    heads: HarnessCampaignHeadRepository
    partitions: HarnessPartitionManifestRepository
    budgets: HarnessBudgetRepository
    observations: HarnessObservationRepository
    metrics: HarnessMetricRepository
    confounds: HarnessConfoundRepository
    audits: EvaluatorAuditRepository
    measurements: SelfImprovementMeasurementRepository
    transactions: TransactionRepository

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_campaign(self, campaign_id: str) -> HarnessCampaignRecord | None:
        return self.campaigns.get(campaign_id)

    def get_decision(self, decision_id: str) -> HarnessDecisionRecord | None:
        return self.decisions.get(decision_id)

    def get_evaluator_audit(self, audit_id: str) -> EvaluatorAuditRecord | None:
        return self.audits.get(audit_id)

    def get_measurement(self, measurement_id: str) -> SelfImprovementMeasurementRecord | None:
        return self.measurements.get(measurement_id)

    def list_iterations(self, campaign_id: str) -> tuple[CampaignIteration, ...]:
        manifest_ids = {
            item.partition_manifest_id
            for item in self.partitions.list_all()
            if item.campaign_id == campaign_id
        }
        return tuple(
            stored.proposal.iteration
            for stored in self.transactions.list_all()
            if stored.decision.accepted
            and isinstance(stored.proposal, RecordHarnessIteration)
            and stored.proposal.iteration.partition_manifest_id in manifest_ids
        )

    def list_partition_manifests(
        self,
        campaign_id: str,
    ) -> tuple[HarnessPartitionManifestRecord, ...]:
        return tuple(item for item in self.partitions.list_all() if item.campaign_id == campaign_id)

    def list_budgets(self, campaign_id: str) -> tuple[HarnessBudgetRecord, ...]:
        return tuple(item for item in self.budgets.list_all() if item.campaign_id == campaign_id)

    def list_protected_results(
        self,
        campaign_id: str,
    ) -> tuple[RecordHarnessProtectedResult, ...]:
        return tuple(
            stored.proposal
            for stored in self.transactions.list_all()
            if stored.decision.accepted
            and isinstance(stored.proposal, RecordHarnessProtectedResult)
            and stored.proposal.result.campaign_id == campaign_id
        )

    def list_confounds(self, campaign_id: str) -> tuple[HarnessConfoundRecord, ...]:
        return tuple(item for item in self.confounds.list_all() if item.campaign_id == campaign_id)

    def list_metrics(self, campaign_id: str) -> tuple[HarnessMetricRecord, ...]:
        return tuple(item for item in self.metrics.list_all() if item.campaign_id == campaign_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if not isinstance(record, HarnessDecision):
            raise TypeError(f"unsupported harness decision record: {type(record)!r}")
        raise RuntimeError(
            "harness decisions require append_decision_with_report for authoritative lineage"
        )

    def append_decision_with_report(
        self,
        decision: HarnessDecision,
        report: HarnessCampaignReport,
    ) -> None:
        stored = HarnessDecisionRecord(
            decision_id=decision.decision_id,
            campaign_id=decision.campaign_id,
            status=decision.status,
            admitted=decision.admitted,
            rationale=decision.rationale,
            authority_id=decision.authority.actor_id,
            rollback_target_id=decision.rollback_target_id,
            evaluator_audit_id=decision.evaluator_audit_id,
            measurement_id=decision.measurement_id,
            metric_result_ids=tuple(
                result_id for metric in report.metrics for result_id in metric.result_ids
            ),
            confound_ids=tuple(item.confound_id for item in report.confounds),
            decided_at=decision.decided_at,
            governing_policy_hash=decision.governing_policy_hash,
        )
        self.decisions.add(stored.decision_id, stored, stored.decided_at)

    def update_projection(self, record: BaseModel) -> None:
        if not isinstance(record, HarnessDecision):
            raise TypeError(f"unsupported campaign head projection: {type(record)!r}")
        self.heads.set(record.campaign_id, record.decision_id, record.status)


type HarnessEvalCapabilities = (
    HarnessCampaignCapabilities
    | HarnessIterationCapabilities
    | HarnessProtectedResultCapabilities
    | HarnessConfoundCapabilities
    | HarnessDecisionCapabilities
)


def harness_eval_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
) -> HarnessEvalCapabilities:
    if isinstance(proposal, CreateHarnessCampaign):
        return HarnessCampaignCapabilities(
            active_policy,
            HarnessCampaignRepository(connection),
            HarnessPartitionManifestRepository(connection),
            HarnessBudgetRepository(connection),
        )
    if isinstance(proposal, RecordHarnessIteration):
        return HarnessIterationCapabilities(
            active_policy,
            HarnessCampaignRepository(connection),
            HarnessPartitionManifestRepository(connection),
            HarnessBudgetRepository(connection),
            HarnessObservationRepository(connection),
        )
    if isinstance(proposal, RecordHarnessProtectedResult):
        return HarnessProtectedResultCapabilities(
            active_policy,
            connection,
            HarnessCampaignRepository(connection),
            HarnessPartitionManifestRepository(connection),
            HarnessObservationRepository(connection),
            HarnessMetricRepository(connection),
        )
    if isinstance(proposal, RecordHarnessConfound):
        return HarnessConfoundCapabilities(
            active_policy,
            HarnessCampaignRepository(connection),
            HarnessConfoundRepository(connection),
        )
    if isinstance(proposal, DecideHarnessCampaign):
        return HarnessDecisionCapabilities(
            active_policy,
            HarnessCampaignRepository(connection),
            HarnessDecisionRepository(connection),
            HarnessCampaignHeadRepository(connection),
            HarnessPartitionManifestRepository(connection),
            HarnessBudgetRepository(connection),
            HarnessObservationRepository(connection),
            HarnessMetricRepository(connection),
            HarnessConfoundRepository(connection),
            EvaluatorAuditRepository(connection),
            SelfImprovementMeasurementRepository(connection),
            TransactionRepository(connection),
        )
    raise TypeError(f"no fixed harness-evaluation capability for proposal: {type(proposal)!r}")


def _campaign_record(campaign: HarnessCampaign) -> HarnessCampaignRecord:
    return HarnessCampaignRecord(
        campaign_id=campaign.campaign_id,
        version=campaign.version,
        variants=campaign.variants,
        model_id=campaign.model_id,
        model_version=campaign.model_version,
        adapter_id=campaign.adapter_id,
        baseline_variant=campaign.baseline_variant,
        candidate_variant=campaign.candidate_variant,
        baseline_harness_version_id=campaign.baseline_harness_version_id,
        candidate_harness_version_id=campaign.candidate_harness_version_id,
        rollback_harness_version_id=campaign.rollback_harness_version_id,
        evaluator_id=campaign.evaluator.actor_id,
        evaluator_version_id=campaign.evaluator_version_id,
        candidate_producer_id=campaign.candidate_producer.actor_id,
        canonical_campaign_hash=harness_campaign_hash(campaign),
        created_by=campaign.coordinator.actor_id,
        created_at=campaign.created_at,
        governing_policy_hash=campaign.governing_policy_hash,
    )


def _partition_record(manifest: object) -> HarnessPartitionManifestRecord:
    from super_scientist.domain.harness_eval.models import CampaignPartitionManifest

    if not isinstance(manifest, CampaignPartitionManifest):
        raise TypeError("invalid campaign partition manifest")
    return HarnessPartitionManifestRecord(
        partition_manifest_id=manifest.partition_manifest_id,
        campaign_id=manifest.campaign_id,
        campaign_version=manifest.campaign_version,
        partition=manifest.partition,
        task_ids=manifest.task_ids,
        manifest_hash=manifest.manifest_hash,
        protected_content_hash=manifest.protected_content_hash,
        created_at=manifest.created_at,
        governing_policy_hash=manifest.governing_policy_hash,
    )


def _campaign_id_for_manifest(
    repository: HarnessPartitionManifestRepository,
    manifest_id: str,
) -> str:
    manifest = repository.get(manifest_id)
    if manifest is None:
        raise RuntimeError("accepted iteration lost its campaign manifest")
    return manifest.campaign_id


__all__ = ["harness_eval_capabilities"]
