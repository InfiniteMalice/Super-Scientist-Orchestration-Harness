from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import (
    GitObjectId,
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class QualityPolicyProposal(_StrictFrozenModel):
    """Immutable pending request to change the versioned quality policy."""

    schema_version: Literal[1] = 1
    proposal_id: StableIdentifier
    proposer: ActorIdentity
    prior_registry_hash: Sha256Hex
    proposed_registry_hash: Sha256Hex
    source_diff_hash: Sha256Hex
    firewall_policy_sha256: Sha256Hex
    allowed_attribution_paths: tuple[NonBlankText, ...] = Field(min_length=1)
    governing_policy_hash: Sha256Hex
    quality_policy_hash: Sha256Hex
    measurement_id: StableIdentifier
    evaluator_audit_id: StableIdentifier
    rationale: NonBlankText
    regression_tests: tuple[NonBlankText, ...] = Field(min_length=1)
    rollback_commit: GitObjectId

    @model_validator(mode="after")
    def validate_governed_change(self) -> Self:
        if self.prior_registry_hash == self.proposed_registry_hash:
            raise ValueError("proposed registry must differ from prior registry")
        if self.allowed_attribution_paths != tuple(sorted(set(self.allowed_attribution_paths))):
            raise ValueError("allowed attribution paths must be sorted and unique")
        if not all(_is_exact_relative_file_path(path) for path in self.allowed_attribution_paths):
            raise ValueError("allowed attribution paths must name exact relative files")
        if self.regression_tests != tuple(sorted(set(self.regression_tests))):
            raise ValueError("regression tests must be sorted and unique")
        if not all(_is_exact_relative_file_path(path) for path in self.regression_tests):
            raise ValueError("regression tests must name exact relative files")

        from super_scientist.quality.imported_pattern_firewall import quality_policy_hash

        expected_hash = quality_policy_hash(
            registry_hash=self.proposed_registry_hash,
            firewall_policy_sha256=self.firewall_policy_sha256,
            allowed_attribution_paths=self.allowed_attribution_paths,
        )
        if self.quality_policy_hash != expected_hash:
            raise ValueError("quality policy hash does not bind the proposed policy inputs")
        return self


class QualityPolicyApprovalRecord(_StrictFrozenModel):
    """Immutable human approval linked to exact supporting record bytes."""

    schema_version: Literal[1] = 1
    approval_id: StableIdentifier
    proposal_id: StableIdentifier
    proposal_sha256: Sha256Hex
    measurement_id: StableIdentifier
    measurement_sha256: Sha256Hex
    evaluator_audit_id: StableIdentifier
    evaluator_audit_sha256: Sha256Hex
    approver: ActorIdentity
    approval_text: Literal["push to pr."]
    approved_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_human_approver(self) -> Self:
        if self.approver.kind is not ActorKind.HUMAN:
            raise ValueError("quality policy approval requires a stable human approver")
        return self


type QualityPolicyRecord = (
    QualityPolicyProposal
    | SelfImprovementMeasurementRecord
    | EvaluatorAuditRecord
    | QualityPolicyApprovalRecord
)


class QualityPolicyRecordConflict(ValueError):
    """A stable governance record identifier was reused with changed content."""


class QualityPolicyRecordIntegrityError(ValueError):
    """A persisted governance record failed canonical or content-address verification."""


class QualityPolicyRecordLedger:
    """Append/read boundary for content-addressed quality-policy records."""

    def __init__(self, root: Path) -> None:
        self._root = root.absolute()
        self._artifacts = FileArtifactStore(self._root / "artifacts")

    def append(self, record: QualityPolicyRecord) -> ArtifactRef:
        model_type, kind, identifier_field = _record_descriptor(record)
        validated = model_type.model_validate(record.model_dump(mode="python"))
        if isinstance(validated, QualityPolicyApprovalRecord):
            self._admit_persisted_approval(validated)
        data = canonical_record_bytes(validated)
        record_id = getattr(validated, identifier_field)
        target = self._record_path(kind, record_id)
        if target.exists():
            return self._require_same_content(target, data, record_id)

        ref = self._artifacts.put(data, "application/json")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(self._artifacts.resolve(ref), target)
        except FileExistsError:
            return self._require_same_content(target, data, record_id)
        return ref

    def verify_approval(self, approval_id: str) -> QualityPolicyApprovalRecord:
        approval = self._read(QualityPolicyApprovalRecord, "approvals", "approval_id", approval_id)
        if not isinstance(approval, QualityPolicyApprovalRecord):
            raise QualityPolicyRecordIntegrityError("approval record type mismatch")
        return self._admit_persisted_approval(approval)

    def _admit_persisted_approval(
        self,
        approval: QualityPolicyApprovalRecord,
    ) -> QualityPolicyApprovalRecord:
        proposal = self._read(
            QualityPolicyProposal,
            "proposals",
            "proposal_id",
            approval.proposal_id,
        )
        measurement = self._read(
            SelfImprovementMeasurementRecord,
            "measurements",
            "measurement_id",
            approval.measurement_id,
        )
        audit = self._read(
            EvaluatorAuditRecord,
            "evaluator-audits",
            "evaluator_audit_id",
            approval.evaluator_audit_id,
        )
        return admit_quality_policy_records(
            proposal=proposal if isinstance(proposal, QualityPolicyProposal) else None,
            measurement=(
                measurement if isinstance(measurement, SelfImprovementMeasurementRecord) else None
            ),
            evaluator_audit=audit if isinstance(audit, EvaluatorAuditRecord) else None,
            approval=approval,
        )

    def _read(
        self,
        model_type: type[BaseModel],
        kind: str,
        identifier_field: str,
        record_id: str,
    ) -> BaseModel:
        target = self._record_path(kind, record_id)
        try:
            _require_regular_record_file(target)
            raw = target.read_bytes()
            record = model_type.model_validate_json(raw)
            if canonical_record_bytes(record) != raw:
                raise ValueError("record bytes are not canonical")
            if getattr(record, identifier_field) != record_id:
                raise ValueError("record identifier mismatch")
            digest = sha256_hex(raw)
            ref = ArtifactRef(
                sha256=digest,
                size_bytes=len(raw),
                media_type="application/json",
                relative_path=f"sha256/{digest[:2]}/{digest}",
            )
            if self._artifacts.read(ref) != raw:
                raise ValueError("record and artifact bytes differ")
        except (OSError, ValueError) as error:
            raise QualityPolicyRecordIntegrityError(
                f"content-addressed record {record_id!r} failed verification"
            ) from error
        return record

    def _record_path(self, kind: str, record_id: str) -> Path:
        digest = sha256_hex(record_id.encode("utf-8"))
        target = self._root / "records" / kind / f"{digest}.json"
        _require_record_path_containment(self._root, target)
        return target

    def _require_same_content(
        self,
        target: Path,
        expected: bytes,
        record_id: str,
    ) -> ArtifactRef:
        _require_regular_record_file(target)
        existing = target.read_bytes()
        if existing != expected:
            raise QualityPolicyRecordConflict(
                f"record {record_id!r} already has different canonical content"
            )
        return self._artifacts.put(existing, "application/json")


def _record_descriptor(
    record: QualityPolicyRecord,
) -> tuple[type[BaseModel], str, str]:
    if isinstance(record, QualityPolicyProposal):
        return QualityPolicyProposal, "proposals", "proposal_id"
    if isinstance(record, SelfImprovementMeasurementRecord):
        return SelfImprovementMeasurementRecord, "measurements", "measurement_id"
    if isinstance(record, EvaluatorAuditRecord):
        return EvaluatorAuditRecord, "evaluator-audits", "evaluator_audit_id"
    if isinstance(record, QualityPolicyApprovalRecord):
        return QualityPolicyApprovalRecord, "approvals", "approval_id"
    raise TypeError("unsupported quality policy governance record")


def _require_record_path_containment(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise QualityPolicyRecordIntegrityError(
            "record path escapes configured ledger root"
        ) from error
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise QualityPolicyRecordIntegrityError(
                "record namespace contains a symlink or reparse point"
            )
    if not target.resolve().is_relative_to(root.resolve()):
        raise QualityPolicyRecordIntegrityError("record path escapes configured ledger root")


def _require_regular_record_file(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise QualityPolicyRecordIntegrityError("record is unavailable") from error
    if not stat.S_ISREG(mode):
        raise QualityPolicyRecordIntegrityError("record path must be a regular file")


def canonical_record_bytes(record: BaseModel) -> bytes:
    """Return the unique JSON byte representation used for content addressing."""

    return canonical_json_bytes(record.model_dump(mode="json"))


def canonical_record_hash(record: BaseModel) -> str:
    return sha256_hex(canonical_record_bytes(record))


def admit_quality_policy_records(
    *,
    proposal: QualityPolicyProposal | None,
    measurement: SelfImprovementMeasurementRecord | None,
    evaluator_audit: EvaluatorAuditRecord | None,
    approval: QualityPolicyApprovalRecord | None,
) -> QualityPolicyApprovalRecord:
    """Admit one complete immutable quality-policy governance record chain."""

    if not (
        isinstance(proposal, QualityPolicyProposal)
        and isinstance(measurement, SelfImprovementMeasurementRecord)
        and isinstance(evaluator_audit, EvaluatorAuditRecord)
        and isinstance(approval, QualityPolicyApprovalRecord)
    ):
        raise ValueError("quality policy admission requires a complete canonical record chain")
    if not (
        approval.proposal_id == proposal.proposal_id
        and approval.proposal_sha256 == canonical_record_hash(proposal)
        and approval.measurement_id == proposal.measurement_id == measurement.measurement_id
        and approval.measurement_sha256 == canonical_record_hash(measurement)
        and approval.evaluator_audit_id
        == proposal.evaluator_audit_id
        == measurement.evaluator_audit_id
        == evaluator_audit.evaluator_audit_id
        and approval.evaluator_audit_sha256 == canonical_record_hash(evaluator_audit)
        and measurement.change_id == proposal.proposal_id
        and measurement.proposer == proposal.proposer == evaluator_audit.proposer
        and evaluator_audit.candidate_producer == proposal.proposer
        and measurement.evaluator == evaluator_audit.evaluator
        and measurement.evaluator_version == evaluator_audit.evaluator_version
        and measurement.baseline_version_id == proposal.prior_registry_hash
        and measurement.candidate_version_id == proposal.proposed_registry_hash
        and measurement.rollback_target_id == proposal.rollback_commit
        and approval.governing_policy_hash
        == proposal.governing_policy_hash
        == measurement.governing_policy_hash
        == evaluator_audit.governing_policy_hash
    ):
        raise ValueError("quality policy record linkage is incomplete or mismatched")
    if not (
        evaluator_audit.result is AssessmentOutcome.PASSED
        and measurement.decision is MeasurementDecision.ACCEPTED
        and measurement.decision_authority.kind is ActorKind.HUMAN
        and approval.approver == measurement.decision_authority
    ):
        raise ValueError(
            "quality policy admission requires a passed audit, accepted decision, "
            "and independent human authority"
        )
    audited_actors = (
        evaluator_audit.evaluator,
        evaluator_audit.proposer,
        evaluator_audit.candidate_producer,
    )
    if not (
        all(are_independent(evaluator_audit.auditor, actor) for actor in audited_actors)
        and all(
            relationship is ActorRelationship.INDEPENDENT
            for relationship in (
                evaluator_audit.auditor_to_evaluator,
                evaluator_audit.auditor_to_proposer,
                evaluator_audit.auditor_to_candidate_producer,
            )
        )
        and all(
            are_independent(approval.approver, actor)
            for actor in (*audited_actors, evaluator_audit.auditor)
        )
    ):
        raise ValueError("quality policy admission rejects self or circular record authority")
    return approval


def _is_exact_relative_file_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and len(path.parts) >= 2
        and all(part not in {"", ".", ".."} for part in path.parts)
        and not any(character in value for character in "*?[]")
        and ":" not in value
        and bool(path.suffix)
    )
