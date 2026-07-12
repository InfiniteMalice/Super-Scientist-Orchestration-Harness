from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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
    version: int = Field(ge=1)
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
