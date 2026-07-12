import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from super_scientist.config.loader import load_policy
from super_scientist.config.models import GovernancePolicy


def test_policy_hash_is_content_addressed(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required_claim_checks": ["source_exists", "evidence_span_exists"],
                "human_approval_for": ["governance_change", "adapter_promotion"],
            }
        ),
        encoding="utf-8",
    )
    first = load_policy(path)
    second = load_policy(path)
    assert first.policy_hash == second.policy_hash
    assert first.policy == second.policy


def test_policy_rejects_empty_required_checks() -> None:
    with pytest.raises(ValidationError):
        GovernancePolicy(
            schema_version=1,
            required_claim_checks=[],
            human_approval_for={"governance_change", "adapter_promotion"},
        )
