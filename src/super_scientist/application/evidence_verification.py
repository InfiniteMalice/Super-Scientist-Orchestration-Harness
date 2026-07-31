from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.providers.storage.artifacts import ArtifactStore


def verified_artifact_bytes(
    evidence: EvidenceRecord,
    artifact_store: ArtifactStore,
) -> bytes:
    """Return retained bytes only after their artifact and extracted span verify."""

    data = artifact_store.read(evidence.artifact)
    span = evidence.extracted_span
    if evidence.artifact.media_type.startswith("text/"):
        artifact_text = data.decode("utf-8")
        if span is not None and artifact_text[span.start : span.end] != span.text:
            raise ValueError("evidence span does not match artifact text")
    elif span is not None:
        raise ValueError("binary evidence cannot carry a text span")
    return data


def verify_artifact_binding(evidence: EvidenceRecord, artifact_store: ArtifactStore) -> None:
    verified_artifact_bytes(evidence, artifact_store)
