from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from super_scientist.providers.storage.artifacts import FileArtifactStore


@given(st.binary(max_size=4096))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_repeated_put_never_changes_artifact(tmp_path: Path, payload: bytes) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(payload, "application/octet-stream")
    path = store.resolve(ref)
    original_stat = path.stat()

    repeated_ref = store.put(payload, "application/octet-stream")

    assert repeated_ref == ref
    assert path.stat().st_ino == original_stat.st_ino
    assert store.read(ref) == payload


@given(
    st.binary(min_size=1, max_size=4096),
    st.binary(min_size=1, max_size=4096),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_distinct_put_cannot_overwrite_a_retained_artifact(
    tmp_path: Path,
    original: bytes,
    attacker_payload: bytes,
) -> None:
    assume(original != attacker_payload)
    store = FileArtifactStore(tmp_path)
    original_ref = store.put(original, "application/octet-stream")
    original_path = store.resolve(original_ref)
    original_stat = original_path.stat()

    attacker_ref = store.put(attacker_payload, "application/octet-stream")

    assert attacker_ref != original_ref
    assert store.resolve(attacker_ref) != original_path
    assert store.read(original_ref) == original
    assert original_path.stat().st_ino == original_stat.st_ino
    assert original_path.stat().st_mtime_ns == original_stat.st_mtime_ns
