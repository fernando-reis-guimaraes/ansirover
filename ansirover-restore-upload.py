#!/usr/bin/env python3

from pathlib import Path


ENTRYPOINT_MODULE_NAME = __name__
CORE_PATH = Path(__file__).with_name("ansirover_restore_upload_core.py")
CORE_SOURCE = CORE_PATH.read_text(encoding="utf-8")

globals()["__name__"] = "ansirover_restore_upload_core"
exec(compile(CORE_SOURCE, str(CORE_PATH), "exec"), globals(), globals())
globals()["__name__"] = ENTRYPOINT_MODULE_NAME

original_validate_server_target = validate_server_target


def validate_server_target(server_name):
    normalized_server_name = str(server_name)

    if "@" in normalized_server_name:
        fail(
            "CapRover machine/URL target must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )

    original_validate_server_target(normalized_server_name)


if ENTRYPOINT_MODULE_NAME == "__main__":
    main()
