import json
import os
import subprocess
import sys
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


def test_policy_collections_are_deeply_immutable() -> None:
    policy = GovernancePolicy(
        required_claim_checks=[" source_exists "],
        human_approval_for=["governance_change"],
    )

    assert policy.required_claim_checks == ("source_exists",)
    assert isinstance(policy.required_claim_checks, tuple)
    assert isinstance(policy.human_approval_for, frozenset)

    with pytest.raises(ValidationError):
        policy.required_claim_checks = ("other_check",)
    with pytest.raises(TypeError):
        policy.required_claim_checks[0] = "other_check"
    with pytest.raises(AttributeError):
        policy.human_approval_for.add("other_approval")


@pytest.mark.parametrize("checks", [[], [""], [" \t"]])
def test_policy_rejects_blank_required_checks(checks: list[str]) -> None:
    with pytest.raises(ValidationError):
        GovernancePolicy(required_claim_checks=checks)


def test_policy_hash_is_stable_across_python_hash_seeds(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required_claim_checks": ["source_exists"],
                "human_approval_for": [
                    "governance_change",
                    "adapter_promotion",
                    "policy_activation",
                ],
            }
        ),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[3]
    script = (
        "from pathlib import Path; "
        "import sys; "
        "from super_scientist.config.loader import load_policy; "
        "print(load_policy(Path(sys.argv[1])).policy_hash)"
    )
    hashes: list[str] = []
    for seed in ("1", "2"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(repo_root / "src"), environment.get("PYTHONPATH")])
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            check=True,
            cwd=repo_root,
            env=environment,
            text=True,
        )
        hashes.append(result.stdout.strip())

    assert hashes[0] == hashes[1]
