from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CheckOutcome(StrEnum):
    PASS_DETERMINISTIC = "PASS_DETERMINISTIC"
    FAIL_DETERMINISTIC = "FAIL_DETERMINISTIC"
    REQUIRES_INDEPENDENT_REVIEW = "REQUIRES_INDEPENDENT_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    outcome: CheckOutcome
    reason: str
    validator_version: str = "1"
