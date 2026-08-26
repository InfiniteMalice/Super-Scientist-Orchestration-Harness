from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
IMPLEMENTATION_BASELINE = "d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450"
EXPECTED_COGNITIVE_ROLES = (
    ("RESEARCH-COORDINATOR", "Research Coordinator"),
    ("CAPABILITY-GROUNDER", "Capability Grounder"),
    ("PEER-REASONER", "Peer Reasoner"),
    ("PROCEDURE-COMPILER", "Procedure Compiler"),
    ("PROCEDURE-VALIDATOR", "Procedure Validator"),
    ("COHORT-DIVERSITY-AUDITOR", "Cohort/Diversity Auditor"),
    ("HARNESS-TRACE-RECORDER", "Harness Trace Recorder"),
)
EXPECTED_ROLE_FIELDS = (
    "Capability status",
    "Purpose",
    "Recommended actor type",
    "Suggested model type",
    "Required capabilities",
    "Authority",
    "Independence requirement",
    "Inputs",
    "Outputs",
    "Common failures",
    "Resolution",
    "Unsuitable model types",
    "Source references",
)
EXPECTED_WORKFLOW_ACTIONS = (
    "Declare capability requirements",
    "Record grounded profiles",
    "Select a bounded cohort",
    "Inspect diversity separately from independence",
    "Open a collaboration session",
    "Compile a candidate method",
    "Inspect invalid or inconclusive findings",
    "Bind only a valid procedure",
    "Record matched evaluations, traces, and reward validity",
    "Inspect records",
    "Verify, export, import, and replay",
)
EXPECTED_MAN16_SOURCE_PATHS = (
    "src/super_scientist/application/cognitive/service.py",
    "src/super_scientist/domain/cognition/grounding.py",
    "src/super_scientist/domain/cognition/diversity.py",
    "src/super_scientist/application/collaboration/service.py",
    "src/super_scientist/domain/procedures/compiler.py",
    "src/super_scientist/application/procedures/service.py",
    "src/super_scientist/application/harness_eval/extensions.py",
    "src/super_scientist/application/cognitive/reader.py",
    "src/super_scientist/application/cognitive/integrity.py",
    "src/super_scientist/application/workspace_exchange.py",
)
EXPECTED_MAN16_TEST_PATHS = (
    "tests/integration/application/test_cognitive_service.py",
    "tests/integration/application/test_collaboration_service.py",
    "tests/integration/application/test_procedure_service.py",
    "tests/integration/application/test_harness_eval_extensions.py",
    "tests/integration/application/test_cognitive_workspace_integrity.py",
    "tests/integration/application/test_cognitive_workspace_exchange.py",
    "tests/integration/application/test_transaction_coordinator.py",
    "tests/integration/cli/test_cognitive_cli.py",
    "tests/e2e/test_governed_cognitive_procedure_vertical_slice.py",
)
EXPECTED_CAPABILITY_BOUNDARY_SENTENCES = (
    "Strict contracts, pure computations, governed persistence, read-only inspection, "
    "integrity verification, workspace exchange, and replay are implemented.",
    "Live LLM grounding, live peer adapters, provider-native metadata ingestion, and "
    "training-payload handoff are interface only.",
    "Diversity, guidance, model-by-harness interaction, and reward-hacking diagnostics "
    "are experimental.",
)

_ROLE_HEADING = re.compile(r"(?m)^#### `ROLE-(?P<id>[A-Z-]+)` — (?P<name>[^\n]+)$")
_ROLE_FIELD = re.compile(r"(?m)^\*\*(?P<label>[^*:\n]+):\*\*")
_WORKFLOW_ACTION = re.compile(
    r"(?ms)^(?P<number>\d+)\. \*\*(?P<title>[^*\n]+)\.\*\* "
    r"(?P<body>.*?)(?=^\d+\. \*\*|^### |\Z)"
)


@pytest.fixture
def manual_text() -> str:
    return (REPOSITORY_ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")


def _document_control_baseline(manual_text: str) -> str:
    match = re.search(
        r"(?m)^\| Repository commit \| `(?P<commit>[0-9a-f]{40})` \|$",
        manual_text,
    )
    assert match is not None
    return match.group("commit")


def _git_show_text(commit: str, relative_path: str) -> str:
    return subprocess.run(
        ("git", "show", f"{commit}:{relative_path}"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _manual_section(manual_text: str, heading: str) -> str:
    heading_match = re.search(rf"(?m)^## {re.escape(heading)}$", manual_text)
    assert heading_match is not None, f"missing manual section: {heading}"
    next_heading = re.search(r"(?m)^## ", manual_text[heading_match.end() :])
    end = len(manual_text) if next_heading is None else heading_match.end() + next_heading.start()
    return manual_text[heading_match.start() : end]


def _role_blocks(manual_text: str) -> dict[str, tuple[str, str]]:
    section = _manual_section(manual_text, "`MAN-05` — LLM and Human Roles")
    matches = tuple(_ROLE_HEADING.finditer(section))
    blocks: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        role_id = match.group("id")
        assert role_id not in blocks, f"duplicate role: {role_id}"
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks[role_id] = (match.group("name"), section[match.start() : end])
    return blocks


def _workflow_actions(manual_text: str) -> tuple[tuple[int, str, str], ...]:
    section = _manual_section(
        manual_text,
        "MAN-16 — Cognitive Cohorts and Procedure Compilation",
    )
    workflow = re.search(
        r"(?ms)^### Ordered workflow\n(?P<body>.*?)(?=^### )",
        section,
    )
    assert workflow is not None, "MAN-16 ordered workflow is missing"
    return tuple(
        (int(match.group("number")), match.group("title"), match.group("body"))
        for match in _WORKFLOW_ACTION.finditer(workflow.group("body"))
    )


def _assert_manual_structure(manual_text: str) -> None:
    roles = _role_blocks(manual_text)
    for role_id, expected_name in EXPECTED_COGNITIVE_ROLES:
        assert role_id in roles, f"missing role: {role_id}"
        actual_name, block = roles[role_id]
        assert actual_name == expected_name
        fields = tuple(match.group("label") for match in _ROLE_FIELD.finditer(block))
        assert fields == EXPECTED_ROLE_FIELDS, f"invalid fields for {role_id}"

    actions = _workflow_actions(manual_text)
    assert tuple(number for number, _, _ in actions) == tuple(range(1, 12))
    assert tuple(title for _, title, _ in actions) == EXPECTED_WORKFLOW_ACTIONS
    assert all(body.strip() for _, _, body in actions)

    man16 = _manual_section(
        manual_text,
        "MAN-16 — Cognitive Cohorts and Procedure Compilation",
    )
    for path in (*EXPECTED_MAN16_SOURCE_PATHS, *EXPECTED_MAN16_TEST_PATHS):
        assert man16.count(path) == 1, f"missing, duplicate, or stale MAN-16 reference: {path}"

    status_match = re.search(
        r"(?ms)^\*\*Capability status:\*\* (?P<body>.*?)(?=^\*\*Authority boundary:\*\*)",
        man16,
    )
    assert status_match is not None, "MAN-16 capability boundary is missing"
    normalized_status = " ".join(status_match.group("body").split())
    for sentence in EXPECTED_CAPABILITY_BOUNDARY_SENTENCES:
        assert sentence in normalized_status


def _remove_role(manual_text: str, role_id: str) -> str:
    blocks = _role_blocks(manual_text)
    _, block = blocks[role_id]
    return manual_text.replace(block, "", 1)


def _remove_role_field(manual_text: str, role_id: str, field: str) -> str:
    _, block = _role_blocks(manual_text)[role_id]
    changed_block = block.replace(f"**{field}:**", f"**Removed {field}:**", 1)
    return manual_text.replace(block, changed_block, 1)


def _duplicate_role_field(manual_text: str, role_id: str, field: str) -> str:
    _, block = _role_blocks(manual_text)[role_id]
    marker = f"**{field}:**"
    changed_block = block.replace(marker, f"{marker}\n\n{marker}", 1)
    return manual_text.replace(block, changed_block, 1)


def _swap_first_two_workflow_actions(manual_text: str) -> str:
    actions = _workflow_actions(manual_text)
    first = f"1. **{actions[0][1]}.** {actions[0][2]}"
    second = f"2. **{actions[1][1]}.** {actions[1][2]}"
    placeholder = "__MANUAL_WORKFLOW_ACTION_PLACEHOLDER__"
    return (
        manual_text.replace(first, placeholder, 1)
        .replace(second, first, 1)
        .replace(
            placeholder,
            second,
            1,
        )
    )


def _replace_in_man16(manual_text: str, old: str, new: str) -> str:
    section = _manual_section(
        manual_text,
        "MAN-16 — Cognitive Cohorts and Procedure Compilation",
    )
    assert old in section
    changed_section = section.replace(old, new, 1)
    return manual_text.replace(section, changed_section, 1)


def test_manual_maps_cognitive_workflow_and_exact_baseline(manual_text: str) -> None:
    baseline = _document_control_baseline(manual_text)
    baseline_pyproject = _git_show_text(baseline, "pyproject.toml")

    assert baseline == IMPLEMENTATION_BASELINE
    assert "MAN-16 — Cognitive Cohorts and Procedure Compilation" in manual_text
    assert 'version = "0.3.0"' in baseline_pyproject
    assert "Operational diversity does not satisfy reviewer independence." in manual_text
    assert "Hidden chain-of-thought is not persisted." in manual_text
    _assert_manual_structure(manual_text)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-role",
        "missing-field",
        "duplicate-field",
        "reordered-workflow",
        "missing-workflow-action",
        "stale-source-reference",
        "missing-test-reference",
        "wrong-capability-boundary",
    ),
)
def test_manual_structure_rejects_representative_mutations(
    manual_text: str,
    mutation: str,
) -> None:
    if mutation == "missing-role":
        changed = _remove_role(manual_text, "CAPABILITY-GROUNDER")
    elif mutation == "missing-field":
        changed = _remove_role_field(manual_text, "PROCEDURE-COMPILER", "Authority")
    elif mutation == "duplicate-field":
        changed = _duplicate_role_field(
            manual_text,
            "HARNESS-TRACE-RECORDER",
            "Inputs",
        )
    elif mutation == "reordered-workflow":
        changed = _swap_first_two_workflow_actions(manual_text)
    elif mutation == "missing-workflow-action":
        changed = manual_text.replace("6. **Compile a candidate method.**", "", 1)
    elif mutation == "stale-source-reference":
        changed = _replace_in_man16(
            manual_text,
            EXPECTED_MAN16_SOURCE_PATHS[0],
            "src/super_scientist/application/cognitive/legacy_service.py",
        )
    elif mutation == "missing-test-reference":
        changed = _replace_in_man16(manual_text, EXPECTED_MAN16_TEST_PATHS[-1], "")
    else:
        assert mutation == "wrong-capability-boundary"
        changed = _replace_in_man16(
            manual_text,
            "interface only.",
            "implemented.",
        )

    with pytest.raises(AssertionError):
        _assert_manual_structure(changed)
