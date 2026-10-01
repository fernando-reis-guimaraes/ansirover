#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from caprover_ansirover_policy import amun_secret_exists, fetch_amun_secret


DELETE_APP_API_PATH = "/user/apps/appDefinitions/delete"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def password_env_name(server_name):
    normalized = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_CAPTAIN_PASSWORD"


def require_captain_password(server_name):
    env_name = password_env_name(server_name)
    password = os.environ.get(env_name, "")

    if password:
        return password

    if amun_secret_exists(env_name):
        info(
            f"[config] Environment variable {env_name} is not set; "
            f"reading secret '{env_name}' from Secure Enclave "
            "via amun-keychain-secrets (Touch ID/passcode prompt required)..."
        )
        secret_value, secret_error = fetch_amun_secret(env_name)

        if secret_value:
            info(
                f"[config] Using secret from Secure Enclave via amun-keychain-secrets: {env_name}"
            )
            os.environ[env_name] = secret_value
            return secret_value

        if secret_error:
            info(f"[config] Secure Enclave lookup failed for {env_name}: {secret_error}")

    fail(f"CapRover password env var is required but not set: {env_name}")


def validate_caprover_machine_name(server_name):
    if not server_name:
        fail("server name must not be empty")

    if "\x00" in server_name:
        fail("server name must not contain NUL byte")

    if "/" in server_name:
        fail(f"server name must not contain slash: {server_name}")

    if server_name in {".", ".."}:
        fail("server name must not be . or ..")

    if any(character.isspace() for character in server_name):
        fail(f"server name must not contain whitespace: {server_name}")

    if "@" in server_name:
        fail(
            "CapRover machine name must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Delete one CapRover app without requiring a local backup."
    )
    parser.add_argument("server_name", help="CapRover CLI machine name; @ is not allowed")
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
    password = require_captain_password(server_name)

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

    if not args.app_name:
        fail("app name must not be empty")

    validate_caprover_machine_name(args.server_name)
    require_captain_password(args.server_name)

    if not args.force:
        confirm(args.server_name, args.app_name)

    delete_app(args.server_name, args.app_name)
    print("APP_DELETED=true")
    print("APP_NAME=" + args.app_name)


if __name__ == "__main__":
    main()
