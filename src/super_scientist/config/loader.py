import json
from pathlib import Path

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def load_policy(path: Path) -> PolicySnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    policy = GovernancePolicy.model_validate(raw)
    canonical_policy = policy.model_dump(mode="json")
    canonical_policy["human_approval_for"] = sorted(policy.human_approval_for)
    canonical = canonical_json_bytes(canonical_policy)
    return PolicySnapshot(policy_hash=sha256_hex(canonical), policy=policy)
