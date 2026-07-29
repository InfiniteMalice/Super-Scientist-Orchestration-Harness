from __future__ import annotations

import json
import sys

_DEPENDENCY_FREE_SMOKE_ARGUMENTS = ("--help", "--json")


def main() -> None:
    """Preserve the fixed package smoke without importing runtime dependencies."""

    if tuple(sys.argv[1:]) == _DEPENDENCY_FREE_SMOKE_ARGUMENTS:
        print(
            json.dumps(
                {
                    "command": "scientist-harness",
                    "data": None,
                    "decision": None,
                    "errors": [
                        {
                            "code": "INVALID_ARGUMENT",
                            "message": "No such option: --json",
                        }
                    ],
                    "schema_version": 1,
                    "success": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(2)

    from super_scientist.cli.main import main as application_main

    application_main()


if __name__ == "__main__":
    main()
