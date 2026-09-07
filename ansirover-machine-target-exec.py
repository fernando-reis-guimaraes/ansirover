#!/usr/bin/env python3

import os
import sys
from pathlib import Path


ALLOWED_IMPLEMENTATIONS = {
    "ansirover-delete-app-safely.py",
    "ansirover-delete-app-without-local-backup.py",
    "ansirover-download-backup.py",
    "ansirover-health-check.py",
    "ansirover-logs-print.py",
    "ansirover-restore-upload.py",
}


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def validate_caprover_target_argument(arguments):
    if not arguments:
        return

    first_argument = arguments[0]

    if first_argument.startswith("-"):
        return

    if "@" in first_argument:
        fail(
            "CapRover machine/URL target must not contain @. "
            "AnsiRover reserves @ for explicit SSH targets in user@host form."
        )


def main(arguments=None):
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)

    if not raw_arguments:
        fail("internal target runner requires an implementation name")

    implementation_name = raw_arguments[0]

    if implementation_name not in ALLOWED_IMPLEMENTATIONS:
        fail(f"unsupported AnsiRover implementation: {implementation_name}")

    implementation_arguments = raw_arguments[1:]
    validate_caprover_target_argument(implementation_arguments)

    implementation_path = Path(__file__).resolve().parent / implementation_name

    if not implementation_path.is_file():
        fail(f"AnsiRover implementation not found: {implementation_path}")

    os.execv(
        sys.executable,
        [sys.executable, str(implementation_path), *implementation_arguments],
    )


if __name__ == "__main__":
    main()
