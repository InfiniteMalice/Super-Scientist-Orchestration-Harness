from pathlib import Path

from hypothesis import HealthCheck, given, settings
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
