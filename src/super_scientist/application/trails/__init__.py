"""Admission handlers and immutable-version helpers for evidence trails."""

from super_scientist.application.trails.service import (
    BindReportSentenceHandler,
    EvidenceTrailVersionBuilder,
    RecordEvidenceTrailVersionHandler,
)

__all__ = [
    "BindReportSentenceHandler",
    "EvidenceTrailVersionBuilder",
    "RecordEvidenceTrailVersionHandler",
]
