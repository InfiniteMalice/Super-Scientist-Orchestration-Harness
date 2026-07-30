from __future__ import annotations

import json
import sys

from super_scientist import __version__

_DEPENDENCY_FREE_VERSION_ARGUMENTS = ("--version", "--json")


def main() -> None:
    """Preserve the fixed package smoke without importing runtime dependencies."""

    if tuple(sys.argv[1:]) == _DEPENDENCY_FREE_VERSION_ARGUMENTS:
        print(
            json.dumps(
                {
                    "command": "version",
                    "data": {"version": __version__},
                    "decision": None,
                    "errors": [],
                    "schema_version": 1,
                    "success": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return

    from super_scientist.cli.main import main as application_main

    application_main()


if __name__ == "__main__":
    main()
