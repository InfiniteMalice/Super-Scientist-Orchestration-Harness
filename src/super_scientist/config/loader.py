from collections.abc import Mapping
from pathlib import Path

from pydantic import TypeAdapter

from super_scientist.config.models import PolicyDocument, PolicySnapshot
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

POLICY_DOCUMENT_ADAPTER: TypeAdapter[PolicyDocument] = TypeAdapter(PolicyDocument)


def policy_hash(policy: PolicyDocument) -> str:
    canonical_policy = policy.model_dump(mode="json")
    if policy.schema_version == 1:
        canonical_policy["human_approval_for"] = sorted(policy.human_approval_for)
    return sha256_hex(canonical_json_bytes(canonical_policy))


def load_policy(path: Path) -> PolicySnapshot:
    raw = path.read_text(encoding="utf-8")
    policy = POLICY_DOCUMENT_ADAPTER.validate_json(raw)
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def load_policy_document(document: Mapping[str, object]) -> PolicySnapshot:
    policy = POLICY_DOCUMENT_ADAPTER.validate_json(canonical_json_bytes(document))
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
