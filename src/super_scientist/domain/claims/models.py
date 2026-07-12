from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from super_scientist.domain.primitives import UtcTimestamp


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
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    supporting_span: str = Field(min_length=1)


class AtomicClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    version: StrictInt = Field(ge=1)
    proposition: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    population_or_system: str = Field(min_length=1)
    epistemic_modality: str = Field(min_length=1)
    status: ClaimStatus
    evidence_links: tuple[EvidenceLink, ...] = ()
    assumptions: tuple[str, ...] = ()
    parent_version_id: str | None = None
    created_at: UtcTimestamp
    created_by: str

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
