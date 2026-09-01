from __future__ import annotations

import re
from pathlib import Path

import pytest

from super_scientist.domain.harness_eval.traces import MetadataAvailability

REPOSITORY_ROOT = Path(__file__).parents[3]
METADATA_AVAILABILITY_DOCUMENTS = (
    "GOVERNANCE.md",
    "SECURITY.md",
    "REPRODUCIBILITY.md",
    "docs/USER_MANUAL.md",
    "docs/harness-evolution-evaluation.md",
)
CONTRACT_PATTERN = re.compile(r"Metadata availability states:\s+(?P<states>[^.]+)\.")


@pytest.mark.parametrize("document", METADATA_AVAILABILITY_DOCUMENTS)
def test_documented_metadata_availability_states_match_domain_enum(document: str) -> None:
    text = (REPOSITORY_ROOT / document).read_text(encoding="utf-8")
    matches = CONTRACT_PATTERN.findall(text)

    assert len(matches) == 1
    assert tuple(re.findall(r"`([A-Z_]+)`", matches[0])) == tuple(
        status.value for status in MetadataAvailability
    )
