from __future__ import annotations

import re
from pathlib import Path


def test_s21_through_s29_have_complete_non_reproduction_metadata() -> None:
    text = Path("docs/sources/source-register.yaml").read_text(encoding="utf-8")
    blocks = {
        match.group("id"): match.group("body")
        for match in re.finditer(
            r"(?ms)^  - id: (?P<id>S\d{2})\n(?P<body>.*?)(?=^  - id: S\d{2}\n|\Z)",
            text,
        )
    }
    for identifier in (f"S{number}" for number in range(21, 30)):
        body = blocks[identifier]
        for key in (
            "version_consulted",
            "license",
            "source_proposal",
            "source_evidence",
            "project_adaptation",
            "project_original_synthesis",
            "limitations",
        ):
            assert re.search(rf"(?m)^    {key}:($|\s)", body)
        assert "reproduction_status: not_reproduced" in body


def test_governed_adaptation_docs_deny_self_promotion_and_reproduction_claims() -> None:
    governed = Path("docs/governed-adaptation.md").read_text(encoding="utf-8").lower()
    inspirations = Path("docs/research-inspirations.md").read_text(encoding="utf-8").lower()

    assert "candidate policy cannot authorize" in governed
    assert "no automatic promotion" in governed
    assert "not reproduced" in governed
    assert all(f"s{number}" in inspirations for number in range(21, 30))
