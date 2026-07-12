from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from super_scientist.domain.claims.models import ClaimStatus

TERMINAL: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.SUPERSEDED, ClaimStatus.WITHDRAWN}
)
ALLOWED: Mapping[ClaimStatus, frozenset[ClaimStatus]] = MappingProxyType(
    {
        ClaimStatus.PROPOSED: frozenset(
            {
                ClaimStatus.EVIDENCE_LINKED,
                ClaimStatus.FALSIFIED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.EVIDENCE_LINKED: frozenset(
            {
                ClaimStatus.TESTABLE,
                ClaimStatus.CORROBORATED,
                ClaimStatus.CONSTRAINT_VALIDATED,
                ClaimStatus.FALSIFIED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.TESTABLE: frozenset(
            {
                ClaimStatus.REPRODUCED,
                ClaimStatus.CORROBORATED,
                ClaimStatus.CONSTRAINT_VALIDATED,
                ClaimStatus.FALSIFIED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.REPRODUCED: frozenset(
            {
                ClaimStatus.CORROBORATED,
                ClaimStatus.FALSIFIED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.CORROBORATED: frozenset(
            {
                ClaimStatus.FALSIFIED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.CONSTRAINT_VALIDATED: frozenset(
            {
                ClaimStatus.CORROBORATED,
                ClaimStatus.FALSIFIED,
                ClaimStatus.SUPERSEDED,
                ClaimStatus.WITHDRAWN,
            }
        ),
        ClaimStatus.FALSIFIED: frozenset({ClaimStatus.SUPERSEDED}),
        ClaimStatus.SUPERSEDED: frozenset(),
        ClaimStatus.WITHDRAWN: frozenset(),
    }
)


class TransitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str | None = None


def validate_transition(current: ClaimStatus, target: ClaimStatus) -> TransitionResult:
    if target in ALLOWED[current]:
        return TransitionResult(allowed=True)
    return TransitionResult(allowed=False, reason="illegal claim status transition")
