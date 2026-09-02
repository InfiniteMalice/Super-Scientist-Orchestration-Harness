from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_s21_through_s29_have_complete_non_reproduction_metadata() -> None:
    text = (REPO_ROOT / "docs/sources/source-register.yaml").read_text(encoding="utf-8")
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
    text = (REPO_ROOT / "docs/sources/source-register.yaml").read_text(encoding="utf-8")
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
    governed = (REPO_ROOT / "docs/governed-adaptation.md").read_text(encoding="utf-8").lower()
    inspirations = (REPO_ROOT / "docs/research-inspirations.md").read_text(encoding="utf-8").lower()

    assert "candidate policy cannot authorize" in governed
    assert "no automatic promotion" in governed
    assert "not reproduced" in governed
    assert all(f"s{number}" in inspirations for number in range(21, 30))


def test_s30_through_s35_have_exact_ids_versions_and_research_boundaries() -> None:
    register = yaml.safe_load(
        (REPO_ROOT / "docs/sources/source-register.yaml").read_text(encoding="utf-8")
    )
    sources = register["sources"]
    new_sources = sources[-6:]

    assert tuple(source["id"] for source in new_sources) == (
        "S30",
        "S31",
        "S32",
        "S33",
        "S34",
        "S35",
    )
    assert tuple(source["version_consulted"] for source in new_sources) == (
        "arXiv:2608.11924v1",
        "arXiv:2608.13567v1",
        "arXiv:2608.17271v1",
        "arXiv:2608.17253v1",
        "arXiv:2608.17282v1",
        "arXiv:2608.17393v1",
    )
    assert all(source["year"] == 2026 for source in new_sources)
    assert all(source["accessed_at"] == "2026-08-23" for source in new_sources)
    assert all(source["adoption_status"] in {"adapted", "inspired"} for source in new_sources)
    assert all(source["reproduction_status"] == "not_reproduced" for source in new_sources)
    for source in new_sources:
        assert source["source_proposal"]
        assert source["source_evidence"]
        assert source["project_adaptation"]
        assert source["project_original_synthesis"]
        assert source["limitations"]
        assert source["notes"]
        assert any("no source code" in note.lower() for note in source["notes"])

    assert new_sources[0]["repository"].endswith("/commit/c17149def034bc777462de612926c8e3b6d01b8c")
    assert new_sources[1]["repository"].endswith("/commit/e3ac7fbb3a6caea05c88343a8de6ec04a4035db8")
    assert new_sources[2]["repository"].endswith("/commit/9a86de643331d2b3a3d95744040881a95aa3fdc6")
    assert new_sources[3]["repository"].endswith("/commit/ff476f06e42eeca4d5c198b93eadd7547876e5e5")
    assert new_sources[4]["repository"] is None
    assert "code unavailable until acceptance" in new_sources[4]["notes"]
    assert new_sources[5]["repository"].endswith("/commit/58f89aa039373afc962ad836d67eca8436b48af6")
