# Kernel Vertical Slice

Run the deterministic, fully offline example from the repository root. Because
this repository uses a `src` layout, first install the project into the active
Python environment, for example with `python -m pip install -e .`.

```bash
python examples/kernel_vertical_slice.py
```

The program creates a temporary workspace and invokes only the public
`python -m super_scientist.cli.main` command boundary. It prints schema-version-1
JSON envelopes for these outcomes:

- accepted evidence from the local fixture file;
- rejected self-approved claim proposal with the `SELF_APPROVAL` reason code;
- accepted claim proposal and one `PROPOSED` history record; and
- a valid audit verification result with exactly three checked events.

It exits nonzero when an expected public-contract result differs. The example
does not access a network, model API, shell executor, or repository internals.

This kernel example is not the complete scientific-run demonstration planned
for a later subsystem.
