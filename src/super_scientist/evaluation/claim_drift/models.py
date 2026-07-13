from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CheckOutcome(StrEnum):
    # This is an outcome label, not a credential.
    PASS_DETERMINISTIC = "PASS_DETERMINISTIC"  # nosec B105
    FAIL_DETERMINISTIC = "FAIL_DETERMINISTIC"
    REQUIRES_INDEPENDENT_REVIEW = "REQUIRES_INDEPENDENT_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    outcome: CheckOutcome
    reason: str
    validator_version: str = "1"
