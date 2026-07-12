import json
from pathlib import Path

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def load_policy(path: Path) -> PolicySnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    policy = GovernancePolicy.model_validate(raw)
    canonical = canonical_json_bytes(policy.model_dump(mode="json"))
    return PolicySnapshot(policy_hash=sha256_hex(canonical), policy=policy)
