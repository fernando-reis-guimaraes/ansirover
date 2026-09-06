#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


DELETE_APP_API_PATH = "/user/apps/appDefinitions/delete"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def password_env_name(server_name):
    normalized = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_CAPTAIN_PASSWORD"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Delete one CapRover app without requiring a local backup."
    )
    parser.add_argument("server_name")
    parser.add_argument("app_name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete without requiring typed confirmation",
    )
    return parser.parse_args()


def confirm(server_name, app_name):
    expected = f"{server_name}/{app_name}"
    typed_value = input(f"Type exactly {expected} to delete the app: ").strip()

    if typed_value != expected:
        fail(f"confirmation did not match. Expected {expected!r}, got {typed_value!r}")


def delete_app(server_name, app_name):
    command = [
        "npx",
        "--yes",
        "caprover",
        "api",
        "--caproverName",
        server_name,
        "--path",
        DELETE_APP_API_PATH,
        "--method",
        "POST",
        "--data",
        json.dumps({"volumes": [], "appNames": [app_name]}, separators=(",", ":")),
    ]
    password = os.environ.get(password_env_name(server_name))

    if password:
        command.extend(["-p", password])

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)

    command.extend(["-o", str(output_path)])

    try:
        completed = subprocess.run(command, check=False)

        if completed.returncode != 0:
            fail("CapRover CLI failed to delete the app")

        response = json.loads(output_path.read_text(encoding="utf-8"))

        if response.get("status") not in {None, 100}:
            fail("CapRover did not confirm app deletion: " + json.dumps(response))
    finally:
        output_path.unlink(missing_ok=True)


def main():
    args = parse_args()

    if not args.server_name or not args.app_name:
        fail("server name and app name must not be empty")

    if not args.force:
        confirm(args.server_name, args.app_name)

    delete_app(args.server_name, args.app_name)
    print("APP_DELETED=true")
    print("APP_NAME=" + args.app_name)


if __name__ == "__main__":
    main()
