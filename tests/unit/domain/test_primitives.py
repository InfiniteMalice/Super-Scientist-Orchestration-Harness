import subprocess

import pytest
from pydantic import TypeAdapter, ValidationError

from super_scientist.domain import primitives
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_sha256_hex_hashes_bytes() -> None:
    assert sha256_hex(b"evidence") == (
        "ee8250fb76e094b34b471f13a73dbbe51d1ae142e9df59d7c0d31ec20f0a0a8e"
    )


@pytest.mark.parametrize("durable_type", [StableIdentifier, NonBlankText])
def test_durable_text_is_stripped_and_nonblank(durable_type: object) -> None:
    adapter = TypeAdapter(durable_type)

    assert adapter.validate_python("  stable-value  ") == "stable-value"
    with pytest.raises(ValidationError):
        adapter.validate_python(" \t\n ")


@pytest.mark.parametrize(
    "value",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, " sha256 "],
)
def test_sha256_type_requires_lowercase_64_hex(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Sha256Hex).validate_python(value)


def test_sha256_type_accepts_canonical_digest() -> None:
    assert TypeAdapter(Sha256Hex).validate_python("a" * 64) == "a" * 64


def test_git_object_id_contract_accepts_the_real_repository_head_and_sha256_format() -> None:
    git_object_id = getattr(primitives, "GitObjectId", None)
    assert git_object_id is not None, "GitObjectId contract is missing"
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    )
    repository_head = completed.stdout.strip()
    assert len(repository_head) == 40

    adapter = TypeAdapter(git_object_id)
    assert adapter.validate_python(repository_head) == repository_head
    assert adapter.validate_python("a" * 64) == "a" * 64
    for invalid in ("A" * 40, "a" * 39, "a" * 41, "g" * 40, " a" * 20):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
