from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from super_scientist.domain.primitives import NonBlankText, StableIdentifier, UtcTimestamp


class ClaimStatus(StrEnum):
    PROPOSED = "PROPOSED"
    EVIDENCE_LINKED = "EVIDENCE_LINKED"
    TESTABLE = "TESTABLE"
    REPRODUCED = "REPRODUCED"
    CORROBORATED = "CORROBORATED"
    CONSTRAINT_VALIDATED = "CONSTRAINT_VALIDATED"
    FALSIFIED = "FALSIFIED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class EvidenceLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: StableIdentifier
    supporting_span: NonBlankText


class AtomicClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: StableIdentifier
    version: StrictInt = Field(ge=1)
    proposition: NonBlankText
    scope: NonBlankText
    population_or_system: NonBlankText
    epistemic_modality: NonBlankText
    status: ClaimStatus
    evidence_links: tuple[EvidenceLink, ...] = ()
    assumptions: tuple[NonBlankText, ...] = ()
    parent_version_id: StableIdentifier | None = None
    created_at: UtcTimestamp
    created_by: StableIdentifier

    @model_validator(mode="after")
    def validate_lineage_and_evidence_links(self) -> AtomicClaim:
        if self.version == 1:
            if self.parent_version_id is not None:
                raise ValueError("version 1 claims must not have a parent_version_id")
        else:
            expected_parent_version_id = f"{self.claim_id}:{self.version - 1}"
            if self.parent_version_id != expected_parent_version_id:
                raise ValueError(
                    f"parent_version_id must be {expected_parent_version_id!r} "
                    f"for version {self.version}"
                )

        if not self.evidence_links and self.status not in {
            ClaimStatus.PROPOSED,
            ClaimStatus.WITHDRAWN,
        }:
            raise ValueError("evidence links are required for this claim status")

        link_pairs = {(link.evidence_id, link.supporting_span) for link in self.evidence_links}
        if len(link_pairs) != len(self.evidence_links):
            raise ValueError("duplicate evidence links are not allowed")
        return self
