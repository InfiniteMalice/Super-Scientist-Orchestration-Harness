# Behavior Handbook

The behavior handbook is a **derived index** over a strict, human-authored manifest.
It helps a reader move from public behavioral contracts to governing rule-version
identifiers, Python modules and symbols, exact source lines, tests, and SHA-256 hashes.
It does not infer behavioral truth from Python syntax.

The source, tests, governance policy, and active rules remain authoritative. A handbook
entry cannot override any of them, authorize a proposal, promote an evaluator, or
change durable state.

## Source-controlled inputs and outputs

- [`docs/handbook/behaviors.json`](handbook/behaviors.json) is the strict behavior
  manifest. Humans state summaries, contracts, inputs, outputs, preconditions,
  postconditions, failure modes, state read and written, tools, permissions,
  dependencies, governing rule versions, source symbols, tests, and related behaviors.
- [`docs/handbook/manifest.schema.json`](handbook/manifest.schema.json) is the
  deterministic JSON Schema projection of the strict Pydantic manifest contract.
- [`docs/handbook/handbook.json`](handbook/handbook.json) and
  [`docs/handbook/handbook.md`](handbook/handbook.md) are byte-reproducible generated
  projections with four disclosure levels.

Each source binding uses the strict `GitObjectId` contract, so SHA-1 and SHA-256 Git
object formats are accepted while arbitrary SHA-256-looking repository identifiers are
not silently substituted for a Git contract. The initial manifest binds sources to the
real Git commit `c5de7d14f530e172216f35d8a5453057aa257f61`. Generated artifacts live in
a later commit, so this is deliberately a source-snapshot identifier rather than an
impossible self-reference to the commit containing the generated files. Verification
requires that the bound repository-canonical source bytes still match both that commit
and the manifest hashes. Python source is hashed after the same CRLF-to-LF text
normalization Git applies, so Windows and POSIX checkouts reproduce the same artifact;
all other bytes remain exact.

## Deterministic build and verification

The builder is a pure read operation. It resolves the declared repository root,
rejects absolute paths and parent traversal, rejects every static symlink or Windows
reparse point in a declared path, requires regular files, hashes exact bytes, and parses
Python with the standard-library AST. It never imports or executes a declared module.

The AST inventory verifies only these location facts:

- the declared module, class, function, async function, or method exists;
- its exact line range can be located;
- its file and symbol bytes have stable SHA-256 hashes; and
- reverse source-to-behavior and rule-to-behavior navigation can be derived.

To reproduce both projections from an unchanged checkout:

```python
from pathlib import Path

from super_scientist.handbook import BehaviorManifest, build_handbook, verify_handbook

root = Path.cwd()
manifest = BehaviorManifest.model_validate_json(
    (root / "docs/handbook/behaviors.json").read_bytes()
)
build = build_handbook(root, manifest)
result = verify_handbook(
    root,
    manifest,
    repository_commit=manifest.repository_commit,
    expected_json_bytes=(root / "docs/handbook/handbook.json").read_bytes(),
    expected_markdown_bytes=(root / "docs/handbook/handbook.md").read_bytes(),
)
assert result.valid
```

The verification result can be projected into Task 13's canonical append-only
`HandbookVerificationRecord`. That record retains the manifest hash, strict repository
commit, unique source hashes, combined generated-artifact hash, stale locations,
missing symbols, outcome, verification time, and governing policy hash.

## Failure modes

Verification fails closed when a source or test is missing or not a regular file, a
Python file cannot be decoded or parsed, a symbol is absent, source bytes are stale,
the trusted repository commit differs, or supplied generated artifacts are not
byte-identical. Hash drift identifies the affected behaviors and then their governing
rule-version identifiers.

An escaped path is stronger than an ordinary stale finding: parent traversal, absolute
paths, symlink traversal, and Windows reparse-point traversal raise
`PathContainmentError`. This keeps untrusted manifest paths from turning the handbook
builder into a general filesystem reader.

The static namespace checks match the repository's local-filesystem threat boundary.
A separate process with authority to replace filesystem entries concurrently remains
outside this component's guarantees.
