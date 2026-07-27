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


def test_s21_through_s29_pin_exact_source_and_reuse_boundaries() -> None:
    text = Path("docs/sources/source-register.yaml").read_text(encoding="utf-8")
    blocks = {
        match.group("id"): match.group("body")
        for match in re.finditer(
            r"(?ms)^  - id: (?P<id>S\d{2})\n(?P<body>.*?)(?=^  - id: S\d{2}\n|\Z)",
            text,
        )
    }
    required_fragments = {
        "S21": ("2607.07663v1", "CC BY 4.0"),
        "S22": (
            "2607.13104v1",
            "06a48f9beddeb0ff711a3f63be857e3e95709923",
            "MIT",
        ),
        "S23": ("2607.08964v2", "CC BY 4.0"),
        "S24": ("2607.09328v1", "arXiv non-exclusive distribution license"),
        "S25": ("2607.09560v1", "arXiv non-exclusive distribution license"),
        "S26": (
            "2607.12227v1",
            "CC BY 4.0",
            "ffd1ba1c2c3e31099264f630b9ed44aec63a86a7",
            "no code reuse",
        ),
        "S27": ("2607.13091v1", "arXiv non-exclusive distribution license"),
        "S28": ("2607.13285v1", "CC BY 4.0"),
        "S29": (
            "d907a3c18ac97fe6bf7b0bbe43ba938acb023b72",
            "no license",
            "not peer reviewed",
            "not independently verified",
            "no code reuse",
        ),
    }

    assert set(required_fragments).issubset(blocks)
    for identifier, fragments in required_fragments.items():
        body = blocks[identifier]
        assert all(fragment.lower() in body.lower() for fragment in fragments)


def test_governed_adaptation_docs_deny_self_promotion_and_reproduction_claims() -> None:
    governed = Path("docs/governed-adaptation.md").read_text(encoding="utf-8").lower()
    inspirations = Path("docs/research-inspirations.md").read_text(encoding="utf-8").lower()

    assert "candidate policy cannot authorize" in governed
    assert "no automatic promotion" in governed
    assert "not reproduced" in governed
    assert all(f"s{number}" in inspirations for number in range(21, 30))
