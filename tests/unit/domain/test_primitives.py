from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_sha256_hex_hashes_bytes() -> None:
    assert sha256_hex(b"evidence") == (
        "ee8250fb76e094b34b471f13a73dbbe51d1ae142e9df59d7c0d31ec20f0a0a8e"
    )
