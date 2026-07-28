from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "quality" / "imported-pattern-firewall-policy.json"


def _firewall() -> ModuleType:
    return importlib.import_module("super_scientist.quality.imported_pattern_firewall")


def _firewall_fixture(tmp_path: Path) -> tuple[ModuleType, Path, Path]:
    firewall = _firewall()
    root = tmp_path / "repository"
    policy_path = root / "quality" / POLICY_PATH.name
    policy_path.parent.mkdir(parents=True)
    shutil.copyfile(POLICY_PATH, policy_path)
    (root / "src").mkdir()
    return firewall, root, policy_path


@pytest.mark.parametrize(
    "mutation",
    ["remove_term", "broaden_allowed_path", "modify_policy", "mismatch_digest"],
)
def test_imported_pattern_policy_tampering_fails(tmp_path: Path, mutation: str) -> None:
    firewall, root, policy_path = _firewall_fixture(tmp_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    expected_digest = firewall.PINNED_POLICY_SHA256
    if mutation == "remove_term":
        payload["denied_terms"] = payload["denied_terms"][1:]
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "broaden_allowed_path":
        payload["allowed_attribution_paths"].append("docs")
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "modify_policy":
        payload["policy_version"] = 2
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        expected_digest = "0" * 64

    result = firewall._evaluate_imported_pattern_firewall(
        root,
        expected_policy_sha256=expected_digest,
        expected_allowed_paths=firewall.ALLOWED_ATTRIBUTION_PATHS,
    )

    assert result.passed is False
    assert result.findings


@pytest.mark.parametrize(
    "mutation",
    ["extra_field", "unsorted_terms", "duplicate_term", "broaden_allowed_path"],
)
def test_schema_or_allowlist_tampering_fails_even_if_attacker_updates_digest(
    tmp_path: Path,
    mutation: str,
) -> None:
    firewall, root, policy_path = _firewall_fixture(tmp_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if mutation == "extra_field":
        payload["unexpected"] = True
    elif mutation == "unsorted_terms":
        payload["denied_terms"] = list(reversed(payload["denied_terms"]))
    elif mutation == "duplicate_term":
        payload["denied_terms"].append(payload["denied_terms"][0])
    else:
        payload["allowed_attribution_paths"].append("docs")
    raw = json.dumps(payload, sort_keys=True).encode()
    policy_path.write_bytes(raw)

    result = firewall._evaluate_imported_pattern_firewall(
        root,
        expected_policy_sha256=hashlib.sha256(raw).hexdigest(),
        expected_allowed_paths=firewall.ALLOWED_ATTRIBUTION_PATHS,
    )

    assert result.passed is False
    assert result.findings


def test_policy_symlink_is_rejected_before_digest_or_schema_acceptance(tmp_path: Path) -> None:
    firewall = _firewall()
    root = tmp_path / "repository"
    policy_path = root / "quality" / POLICY_PATH.name
    policy_path.parent.mkdir(parents=True)
    outside = tmp_path / "reviewed-policy.json"
    shutil.copyfile(POLICY_PATH, outside)
    try:
        policy_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = firewall.run_imported_pattern_firewall(root)

    assert result.passed is False
    assert result.findings[0].code == "UNSAFE_SCAN_PATH"
    with pytest.raises(ValueError, match="symlink or reparse"):
        firewall.load_imported_pattern_policy(root)
