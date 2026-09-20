#!/usr/bin/env python3

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

from caprover_ansirover_policy import (
    AnsiRoverPolicyError,
    BEGIN_MARKER,
    duration_to_seconds,
    get_effective_policy,
    merge_description,
    split_description,
    split_description_metadata,
    validate_explicit_policy,
)

POSTGRES_URI_SCHEMES = {"postgres", "postgresql"}
APPLICATION_NAME_BYPASS_SUFFIX = "_ALLOW_MISSING_APPLICATION_NAME"
POST_DEPLOY_MONITOR_APPLICATION_NAME = "ansirover-postdeploy-monitor"
ALL_POST_DEPLOY_CHECKS_ENV = "CAPROVER_ANSIROVER_ALL_POST_DEPLOY_CHECKS"
ENV_TEMPLATE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_TEMPLATE_TOKEN_PATTERN = re.compile(r"\$\$?\{([^}]*)\}")


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def result(message):
    print(message, flush=True)


def step(label, message):
    info(f"[{label}] {message}")


def get_captain_password_env_name(server_name):
    normalized = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_CAPTAIN_PASSWORD"


def validate_simple_name(label, value):
    if not value:
        fail(f"{label} is required")

    if "/" in value:
        fail(f"{label} must not contain slash: {value}")

    if value in {".", ".."}:
        fail(f"{label} must not be . or ..: {value}")

    if any(character.isspace() for character in value):
        fail(f"{label} must not contain whitespace: {value}")

    if "\x00" in value:
        fail(f"{label} must not contain NUL byte")


def is_caprover_url(server_name):
    return str(server_name).startswith(("http://", "https://"))


def validate_server_target(server_name):
    if not is_caprover_url(server_name):
        validate_simple_name("server name", server_name)
        return

    if any(character.isspace() for character in server_name):
        fail(f"CapRover URL must not contain whitespace: {server_name}")

    if "\x00" in server_name:
        fail("CapRover URL must not contain NUL byte")

    parsed = urlparse(server_name)

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail(f"invalid CapRover URL: {server_name}")


def validate_server_mode_options(server_name, with_image, with_volumes):
    if not is_caprover_url(server_name):
        return

    if with_image:
        fail("--with-image is not supported when the server target is a CapRover URL")

    if with_volumes:
        fail("--with-volumes is not supported when the server target is a CapRover URL")


def should_preflight_deploy_image(server_name, with_image):
    return not with_image and not is_caprover_url(server_name)


CURRENT_APP_NAME = ""


def get_caprover_api_app_name(api_path, data):
    if isinstance(data, dict) and data.get("appName"):
        return str(data.get("appName"))

    match = re.search(r"/appData/([^/?]+)", str(api_path))

    if match:
        return match.group(1)

    return CURRENT_APP_NAME


def command_for_log(command):
    safe_command = []
    redact_next = False

    for item in command:
        if redact_next:
            safe_command.append("<redacted>")
            redact_next = False
            continue

        safe_command.append(str(item))

        if item in {"-p", "--password", "--caproverPassword", "--data"}:
            redact_next = True

    return " ".join(safe_command)


def redact_for_log(value):
    if isinstance(value, dict):
        redacted = {}

        for key, item_value in value.items():
            lower_key = str(key).lower()

            if key == "envVars" and isinstance(item_value, list):
                redacted_env_vars = []

                for env_var in item_value:
                    if isinstance(env_var, dict):
                        redacted_env_var = dict(env_var)

                        if "value" in redacted_env_var:
                            redacted_env_var["value"] = "<redacted>"

                        redacted_env_vars.append(redacted_env_var)
                    else:
                        redacted_env_vars.append("<redacted>")

                redacted[key] = redacted_env_vars
                continue

            if "password" in lower_key or "secret" in lower_key or "token" in lower_key:
                redacted[key] = "<redacted>"
                continue

            redacted[key] = redact_for_log(item_value)

        return redacted

    if isinstance(value, list):
        return [redact_for_log(item) for item in value]

    return value


def print_limited_block(title, text, max_chars=20000):
    if text is None:
        return

    text = str(text)

    if not text.strip():
        return

    print(title, file=sys.stderr)

    if len(text) > max_chars:
        print(text[:max_chars], file=sys.stderr)
        print("... truncated " + str(len(text) - max_chars) + " chars", file=sys.stderr)
        return

    print(text, file=sys.stderr)


def read_text_if_exists(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError:
        return "<response file exists but is not valid UTF-8>"


def print_caprover_api_failure_context(
    server_name,
    api_path,
    method,
    data,
    command,
    output_path,
    returncode,
    stdout_text,
    stderr_text,
):
    print("", file=sys.stderr)
    print("=" * 88, file=sys.stderr)
    print("CapRover API failure context", file=sys.stderr)
    print("=" * 88, file=sys.stderr)
    print("server: " + str(server_name), file=sys.stderr)
    print("method: " + str(method), file=sys.stderr)
    print("path: " + str(api_path), file=sys.stderr)

    api_app_name = get_caprover_api_app_name(api_path, data)

    if api_app_name:
        print("app: " + str(api_app_name), file=sys.stderr)

    print("returncode: " + str(returncode), file=sys.stderr)
    print("command: " + command_for_log(command), file=sys.stderr)
    print("output_file: " + str(output_path), file=sys.stderr)

    redacted_payload = redact_for_log(data)
    payload_text = json.dumps(redacted_payload, indent=2, ensure_ascii=False)
    print_limited_block("request payload redacted:", payload_text)

    print_limited_block("captured stdout:", stdout_text)
    print_limited_block("captured stderr:", stderr_text)

    response_text = read_text_if_exists(output_path)
    print_limited_block("response file content:", response_text)
    print("=" * 88, file=sys.stderr)
    print("", file=sys.stderr)


def is_transient_caprover_api_error(stdout_text, stderr_text, response_text=""):
    combined_text = "\n".join(
        [
            str(stdout_text or ""),
            str(stderr_text or ""),
            str(response_text or ""),
        ]
    ).lower()

    transient_patterns = [
        "socket hang up",
        "econnreset",
        "etimedout",
        "timeout",
        "gateway timeout",
        "bad gateway",
        "service unavailable",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "error code: 1108",
        "build in-progress",
        "build in progress",
        "please wait",
        "statuscodeerror: 500",
        "500 - \"internal server error\"",
        "internal server error",
    ]

    return any(pattern in combined_text for pattern in transient_patterns)


def get_caprover_api_retry_delays():
    raw_value = os.environ.get("CAPROVER_API_RETRY_DELAYS", "").strip()

    if raw_value:
        delays = []

        for raw_item in raw_value.split(","):
            raw_item = raw_item.strip()

            if not raw_item:
                continue

            delays.append(float(raw_item))

        if delays:
            return delays

    return [2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0]


def run_caprover_api(server_name, api_path, method, data):
    command = [
        "npx",
        "--yes",
        "caprover",
        "api",
    ]

    if is_caprover_url(server_name):
        password = os.environ.get("CAPROVER_PASSWORD", "")

        if not password:
            fail("CAPROVER_PASSWORD is required when the server target is a CapRover URL")

        info("[caprover api] Using URL target with CAPROVER_PASSWORD.")
        command.extend(["--caproverUrl", server_name, "-p", password])
    else:
        command.extend(["--caproverName", server_name])
        env_name = get_captain_password_env_name(server_name)
        password = os.environ.get(env_name, "")

        if password:
            info(f"[caprover api] Using password from environment variable: {env_name}")
            command.extend(["-p", password])
        else:
            info(f"[caprover api] Password env var not set: {env_name}. Using CapRover CLI saved login/config.")

    command.extend(
        [
            "--path",
            api_path,
            "--method",
            method,
            "--data",
            json.dumps(data, separators=(",", ":")),
        ]
    )

    retry_delays = get_caprover_api_retry_delays()
    max_attempts = len(retry_delays) + 1

    for attempt_index in range(1, max_attempts + 1):
        with tempfile.NamedTemporaryFile(prefix="caprover-api-", suffix=".json", delete=False) as output_file:
            output_path = output_file.name

        attempt_command = list(command)
        attempt_command.extend(["-o", output_path])

        try:
            info(f"[caprover api] {method} {api_path}")
            info(f"[caprover api] Server: {server_name}")

            api_app_name = get_caprover_api_app_name(api_path, data)

            if api_app_name:
                info(f"[caprover api] App: {api_app_name}")

            info(f"[caprover api] Attempt: {attempt_index}/{max_attempts}")

            completed_process = subprocess.run(
                attempt_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            if completed_process.stdout:
                print(completed_process.stdout, end="", file=sys.stderr)

            if completed_process.stderr:
                print(completed_process.stderr, end="", file=sys.stderr)

            response_text = read_text_if_exists(output_path)

            if completed_process.returncode != 0:
                if attempt_index < max_attempts:
                    delay_seconds = retry_delays[attempt_index - 1]
                    info(f"[caprover api] API failure detected; retrying after {delay_seconds}s.")
                    time.sleep(delay_seconds)
                    continue

                print_caprover_api_failure_context(
                    server_name=server_name,
                    api_path=api_path,
                    method=method,
                    data=data,
                    command=attempt_command,
                    output_path=output_path,
                    returncode=completed_process.returncode,
                    stdout_text=completed_process.stdout,
                    stderr_text=completed_process.stderr,
                )
                fail(f"CapRover CLI failed for {method} {api_path}")

            if not response_text.strip():
                if attempt_index < max_attempts:
                    delay_seconds = retry_delays[attempt_index - 1]
                    info(f"[caprover api] Empty API output; retrying after {delay_seconds}s.")
                    time.sleep(delay_seconds)
                    continue

                print_caprover_api_failure_context(
                    server_name=server_name,
                    api_path=api_path,
                    method=method,
                    data=data,
                    command=attempt_command,
                    output_path=output_path,
                    returncode=completed_process.returncode,
                    stdout_text=completed_process.stdout,
                    stderr_text=completed_process.stderr,
                )
                fail(f"CapRover API returned empty output for {method} {api_path}")

            try:
                response = json.loads(response_text)
            except json.JSONDecodeError:
                if attempt_index < max_attempts:
                    delay_seconds = retry_delays[attempt_index - 1]
                    info(f"[caprover api] Non-JSON API output; retrying after {delay_seconds}s.")
                    time.sleep(delay_seconds)
                    continue

                print_caprover_api_failure_context(
                    server_name=server_name,
                    api_path=api_path,
                    method=method,
                    data=data,
                    command=attempt_command,
                    output_path=output_path,
                    returncode=completed_process.returncode,
                    stdout_text=completed_process.stdout,
                    stderr_text=completed_process.stderr,
                )
                print(response_text[:2000], file=sys.stderr)
                fail(f"CapRover API returned non-JSON output for {method} {api_path}")

            if isinstance(response, dict) and response.get("status") not in [None, 100, 101]:
                response_as_text = json.dumps(response, indent=2, ensure_ascii=False)

                if attempt_index < max_attempts:
                    delay_seconds = retry_delays[attempt_index - 1]
                    info(f"[caprover api] Unexpected API status; retrying after {delay_seconds}s.")
                    time.sleep(delay_seconds)
                    continue

                print_caprover_api_failure_context(
                    server_name=server_name,
                    api_path=api_path,
                    method=method,
                    data=data,
                    command=attempt_command,
                    output_path=output_path,
                    returncode=completed_process.returncode,
                    stdout_text=completed_process.stdout,
                    stderr_text=completed_process.stderr,
                )
                print(response_as_text, file=sys.stderr)
                fail(f"CapRover API returned unexpected status for {method} {api_path}: {response.get('status')}")

            if isinstance(response, dict) and "data" in response:
                return response["data"]

            return response

        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(output_path)

    fail(f"CapRover CLI failed for {method} {api_path}")


def get_app_definitions(server_name):
    response = run_caprover_api(
        server_name=server_name,
        api_path="/user/apps/appDefinitions",
        method="GET",
        data={},
    )

    if isinstance(response, dict):
        app_definitions = response.get("appDefinitions")

        if isinstance(app_definitions, list):
            return app_definitions

        nested_data = response.get("data")

        if isinstance(nested_data, dict) and isinstance(nested_data.get("appDefinitions"), list):
            return nested_data["appDefinitions"]

    fail("CapRover appDefinitions response does not contain appDefinitions list")


def find_app_definition(server_name, app_name):
    for app_definition in get_app_definitions(server_name):
        if not isinstance(app_definition, dict):
            continue

        remote_app_name = str(app_definition.get("appName", ""))

        if remote_app_name.lower() == app_name.lower():
            return app_definition

    return None


def require_app_definition(server_name, app_name):
    app_definition = find_app_definition(server_name, app_name)

    if app_definition is None:
        fail(f"app not found in CapRover: {app_name}")

    return app_definition


def register_app(server_name, app_name, has_persistent_data):
    info(f"[register] Registering app {app_name} with hasPersistentData={has_persistent_data}")

    run_caprover_api(
        server_name=server_name,
        api_path="/user/apps/appDefinitions/register?detached=1",
        method="POST",
        data={
            "appName": app_name,
            "projectId": "",
            "hasPersistentData": has_persistent_data,
        },
    )


def enable_base_domain_ssl_if_needed(server_name, app_name, app_definition):
    if app_definition.get("hasDefaultSubDomainSsl", False):
        info(f"[ssl] Base-domain SSL already enabled for {app_name}.")
        return

    info(f"[ssl] Enabling base-domain SSL for {app_name}...")

    run_caprover_api(
        server_name=server_name,
        api_path="/user/apps/appDefinitions/enablebasedomainssl",
        method="POST",
        data={
            "appName": app_name,
        },
    )


def update_app_definition(server_name, payload):
    info(f"[config] Applying CapRover app settings for {payload.get('appName')}...")

    run_caprover_api(
        server_name=server_name,
        api_path="/user/apps/appDefinitions/update",
        method="POST",
        data=payload,
    )


def get_deploy_api_path(app_name, wait_deploy):
    if wait_deploy:
        info("[deploy mode] Waiting for deploy completion through the synchronous CapRover endpoint.")
        return f"/user/apps/appData/{app_name}"

    info("[deploy mode] --no-wait enabled: using detached CapRover deploy endpoint.")
    return f"/user/apps/appData/{app_name}?detached=1"


def submit_deploy_image(server_name, app_name, image_name, wait_deploy):
    if not image_name:
        fail("deployedImageName is empty; cannot deploy image")

    captain_definition_content = json.dumps(
        {
            "schemaVersion": 2,
            "imageName": image_name,
        },
        separators=(",", ":"),
    )

    info(f"[deploy] Deploying image for {app_name}: {image_name}")

    run_caprover_api(
        server_name=server_name,
        api_path=get_deploy_api_path(app_name, wait_deploy),
        method="POST",
        data={
            "captainDefinitionContent": captain_definition_content,
            "gitHash": "",
        },
    )


def env_vars_to_map(env_vars):
    result = {}

    for item in env_vars or []:
        if not isinstance(item, dict):
            continue

        key = str(item.get("key", ""))

        if not key:
            continue

        result[key] = str(item.get("value", ""))

    return result


def get_app_dir(args):
    return Path(args.path).expanduser().resolve()


def get_inventory_env_vars(args):
    app_dir = get_app_dir(args)
    return parse_env_vars_file(
        app_dir / f"{args.app_name}.env-vars.env",
        app_name=args.app_name,
    )


def get_inventory_env_var_templates(args):
    app_dir = get_app_dir(args)
    _env_vars, env_var_templates = parse_env_vars_file_with_templates(
        app_dir / f"{args.app_name}.env-vars.env",
        app_name=args.app_name,
    )
    return env_var_templates


def get_inventory_env_map(args):
    return env_vars_to_map(get_inventory_env_vars(args))


def get_expected_application_name(args):
    app_dir = get_app_dir(args)
    ansirover = load_ansirover_app(app_dir, args.app_name)
    return str(ansirover["appName"])


def is_true(value):
    return str(value).strip().lower() == "true"


def get_postgres_connection_envs(env_map):
    connections = []

    for env_name, raw_value in env_map.items():
        connection_string = str(raw_value).strip()

        if not connection_string:
            continue

        lower_value = connection_string.lower()

        if not lower_value.startswith(("postgres://", "postgresql://")):
            continue

        parsed = urlparse(connection_string)

        if parsed.scheme not in POSTGRES_URI_SCHEMES:
            continue

        connections.append((env_name, connection_string, parsed))

    return connections


def get_application_name_bypass_key(env_name):
    return env_name + APPLICATION_NAME_BYPASS_SUFFIX


def validate_postgres_application_name(env_map, env_name, parsed, expected_application_name):
    query = parse_qs(parsed.query, keep_blank_values=True)
    application_names = query.get("application_name", [])
    bypass_key = get_application_name_bypass_key(env_name)
    bypass_enabled = is_true(env_map.get(bypass_key, ""))

    if not application_names or not application_names[-1]:
        if bypass_enabled:
            info(
                "[database preflight] WARNING: "
                + env_name
                + " intentionally bypasses application_name enforcement via "
                + bypass_key
                + "=true."
            )
            return False

        fail(
            "database preflight failed: PostgreSQL URI in "
            + env_name
            + " must include application_name="
            + expected_application_name
            + ". If the client cannot accept application_name, explicitly set "
            + bypass_key
            + "=true."
        )

    if len(application_names) != 1:
        fail(
            "database preflight failed: PostgreSQL URI in "
            + env_name
            + " must contain exactly one application_name parameter"
        )

    actual_application_name = application_names[0]

    if actual_application_name != expected_application_name:
        fail(
            "database preflight failed: PostgreSQL URI in "
            + env_name
            + " has application_name="
            + actual_application_name
            + " but AnsiRover appName is "
            + expected_application_name
        )

    info(
        "[database preflight] PostgreSQL identity verified for "
        + env_name
        + ": application_name="
        + expected_application_name
    )
    return True


def get_database_preflight_timeout_seconds():
    raw_value = os.environ.get("CAPROVER_DB_PREFLIGHT_TIMEOUT_SECONDS", "10")

    try:
        timeout_seconds = int(raw_value)
    except ValueError:
        raise ValueError(
            "CAPROVER_DB_PREFLIGHT_TIMEOUT_SECONDS must be a positive integer"
        ) from None

    if timeout_seconds <= 0:
        raise ValueError(
            "CAPROVER_DB_PREFLIGHT_TIMEOUT_SECONDS must be a positive integer"
        )

    return timeout_seconds


def build_psql_environment(connection_string):
    parsed = urlparse(connection_string)

    if parsed.scheme not in POSTGRES_URI_SCHEMES:
        raise ValueError("connection string is not a PostgreSQL URL")

    database_name = unquote(parsed.path.lstrip("/"))

    if not parsed.hostname:
        raise ValueError("PostgreSQL URL has no hostname")

    if not database_name:
        raise ValueError("PostgreSQL URL has no database name")

    environment = os.environ.copy()
    environment["PGHOST"] = parsed.hostname
    environment["PGDATABASE"] = database_name
    environment["PGCONNECT_TIMEOUT"] = str(get_database_preflight_timeout_seconds())

    if parsed.port:
        environment["PGPORT"] = str(parsed.port)

    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)

    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)

    query = parse_qs(parsed.query, keep_blank_values=True)
    query_to_environment = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "channel_binding": "PGCHANNELBINDING",
        "application_name": "PGAPPNAME",
        "options": "PGOPTIONS",
    }

    for query_key, environment_key in query_to_environment.items():
        values = query.get(query_key)

        if values:
            environment[environment_key] = values[-1]

    ssl_mode_values = query.get("sslmode")
    ssl_root_cert_values = query.get("sslrootcert")

    if ssl_mode_values and ssl_mode_values[-1] == "verify-full" and not ssl_root_cert_values:
        environment["PGSSLROOTCERT"] = "system"

    return environment, database_name


def sanitize_postgres_error(error_output, connection_string):
    sanitized_output = str(error_output).strip()
    parsed = urlparse(connection_string)
    sensitive_values = []

    if parsed.password:
        sensitive_values.append(parsed.password)
        sensitive_values.append(unquote(parsed.password))

    for sensitive_value in sensitive_values:
        if sensitive_value:
            sanitized_output = sanitized_output.replace(sensitive_value, "<redacted>")

    return sanitized_output


def run_postgres_connection_preflight(env_name, connection_string):
    try:
        psql_environment, expected_database = build_psql_environment(connection_string)
    except ValueError as error:
        fail("database preflight failed for " + env_name + ": " + str(error))

    info(
        "[database preflight] Verifying PostgreSQL connection for "
        + env_name
        + " without printing credentials..."
    )
    command_timeout_seconds = get_database_preflight_timeout_seconds() + 5

    try:
        completed_process = subprocess.run(
            ["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", "SELECT current_database();"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=psql_environment,
            timeout=command_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        fail("database preflight timed out for " + env_name)

    if completed_process.returncode != 0:
        error_message = (
            "database preflight failed: PostgreSQL connection could not be established for "
            + env_name
        )
        error_detail = sanitize_postgres_error(
            completed_process.stderr,
            connection_string,
        )

        if error_detail:
            error_message += ": " + error_detail

        fail(error_message)

    actual_database_lines = completed_process.stdout.strip().splitlines()
    actual_database = ""

    if actual_database_lines:
        actual_database = actual_database_lines[-1].strip()

    if actual_database != expected_database:
        fail(
            "database preflight failed: PostgreSQL connected to a different database than "
            + env_name
            + " selects"
        )

    info("[database preflight] PostgreSQL connection verified for " + env_name + ".")


def preflight_postgres(args):
    env_map = get_inventory_env_map(args)
    expected_application_name = get_expected_application_name(args)
    connections = get_postgres_connection_envs(env_map)

    if not connections:
        info("[database preflight] No PostgreSQL URI found in inventory env vars.")
        return

    for env_name, _connection_string, parsed in connections:
        validate_postgres_application_name(
            env_map=env_map,
            env_name=env_name,
            parsed=parsed,
            expected_application_name=expected_application_name,
        )

    if shutil.which("psql") is None:
        info(
            "[database preflight] psql is not installed; URI identity checks passed, "
            "live PostgreSQL connection checks skipped."
        )
        return

    for env_name, connection_string, _parsed in connections:
        run_postgres_connection_preflight(env_name, connection_string)


def get_build_status(server_name, app_name):
    response = run_caprover_api(
        server_name=server_name,
        api_path=f"/user/apps/appData/{app_name}",
        method="GET",
        data={},
    )

    if not isinstance(response, dict):
        fail("CapRover build status response is not an object")

    return response


def get_build_log_entries(build_status):
    logs = build_status.get("logs")

    if not isinstance(logs, dict):
        return []

    lines = logs.get("lines")
    first_line_number = logs.get("firstLineNumber")

    if not isinstance(lines, list) or not isinstance(first_line_number, int):
        return []

    entries = []

    for index, line in enumerate(lines):
        text = str(line or "")

        if not text:
            continue

        entries.append((first_line_number + index, text))

    return entries


def print_new_build_log_entries(entries, printed_entries):
    for line_number, text in entries:
        entry_key = (line_number, text)

        if entry_key in printed_entries:
            continue

        print(text, file=sys.stderr, flush=True)
        printed_entries.add(entry_key)


def build_log_has_failed(entries):
    failure_markers = {
        "Deploy failed!",
        "Build has failed!",
    }

    return any(text.strip() in failure_markers for _, text in entries)


def get_deploy_poll_seconds():
    return float(os.environ.get("CAPROVER_DEPLOY_POLL_SECONDS", "2"))


def get_deploy_wait_timeout_seconds():
    return float(os.environ.get("CAPROVER_DEPLOY_WAIT_TIMEOUT_SECONDS", "1800"))


def deploy_image(server_name, app_name, image_name, wait_deploy):
    if not wait_deploy:
        return submit_deploy_image(
            server_name=server_name,
            app_name=app_name,
            image_name=image_name,
            wait_deploy=False,
        )

    if not image_name:
        fail("deployedImageName is empty; cannot deploy image")

    captain_definition_content = json.dumps(
        {
            "schemaVersion": 2,
            "imageName": image_name,
        },
        separators=(",", ":"),
    )

    info(f"[deploy] Deploying image for {app_name}: {image_name}")
    info("[deploy mode] Submitting detached, then following CapRover build status/logs until completion.")

    run_caprover_api(
        server_name=server_name,
        api_path=f"/user/apps/appData/{app_name}?detached=1",
        method="POST",
        data={
            "captainDefinitionContent": captain_definition_content,
            "gitHash": "",
        },
    )

    deadline = time.monotonic() + get_deploy_wait_timeout_seconds()
    printed_entries = set()
    observed_build_activity = False

    while True:
        build_status = get_build_status(server_name, app_name)
        entries = get_build_log_entries(build_status)
        print_new_build_log_entries(entries, printed_entries)

        is_building = bool(build_status.get("isAppBuilding"))

        if is_building or entries:
            observed_build_activity = True

        if build_log_has_failed(entries):
            fail(f"CapRover deploy failed for app: {app_name}")

        if observed_build_activity and not is_building:
            info(f"[deploy] CapRover deploy completed for {app_name}.")
            return

        if time.monotonic() >= deadline:
            fail(f"timed out waiting for CapRover deploy completion: {app_name}")

        time.sleep(get_deploy_poll_seconds())


def get_ssh_target(server_name):
    return "root@" + get_caprover_root_domain(server_name)


def run_remote_command(server_name, remote_command):
    completed_process = subprocess.run(
        ["ssh", "-n", get_ssh_target(server_name), remote_command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    return completed_process


def get_service_name(app_name):
    return "srv-captain--" + app_name


def get_task_state_snapshot(server_name, app_name):
    if is_caprover_url(server_name):
        return set()

    service_name = get_service_name(app_name)
    command = (
        "docker service ps "
        + shlex.quote(service_name)
        + " --no-trunc --format '{{.ID}}\t{{.CurrentState}}'"
    )
    completed_process = run_remote_command(server_name, command)

    if completed_process.returncode != 0:
        return set()

    return {
        line.split("\t", 1)[0]
        for line in completed_process.stdout.splitlines()
        if "\t" in line
    }


def apply_restart_delay(server_name, app_name, restart_delay):
    if is_caprover_url(server_name):
        info("[policy] URL target has no SSH context; restart-delay reconciliation skipped.")
        return

    service_name = get_service_name(app_name)
    info(f"[policy] Reconciling restart delay to {restart_delay}: {service_name}")
    command = (
        "docker service update --detach=true --restart-delay "
        + shlex.quote(restart_delay)
        + " "
        + shlex.quote(service_name)
    )
    completed_process = run_remote_command(server_name, command)

    if completed_process.returncode != 0:
        fail("restart-delay policy reconciliation failed for service: " + service_name)

    inspect_command = (
        "docker service inspect "
        + shlex.quote(service_name)
        + " --format '{{.Spec.TaskTemplate.RestartPolicy.Delay}}'"
    )
    inspect_process = run_remote_command(server_name, inspect_command)

    if inspect_process.returncode != 0:
        fail("could not verify effective restart-delay policy for service: " + service_name)

    effective_restart_delay = inspect_process.stdout.strip()

    try:
        desired_nanoseconds = round(
            duration_to_seconds("restartDelay", restart_delay) * 1_000_000_000
        )

        if effective_restart_delay.isdigit():
            effective_nanoseconds = int(effective_restart_delay)
        else:
            effective_nanoseconds = round(
                duration_to_seconds("restartDelay", effective_restart_delay)
                * 1_000_000_000
            )
    except AnsiRoverPolicyError as error:
        fail("could not parse effective restart-delay policy: " + str(error))

    if effective_nanoseconds != desired_nanoseconds:
        fail(
            "effective restart-delay policy does not match inventory: requested "
            + restart_delay
            + ", live "
            + effective_restart_delay
        )

    info(
        "[policy] restart delay = "
        + restart_delay
        + " OK; effective value="
        + effective_restart_delay
    )


def get_service_replica_counts(server_name, app_name):
    service_name = get_service_name(app_name)
    desired_command = (
        "docker service inspect "
        + shlex.quote(service_name)
        + " --format '{{.Spec.Mode.Replicated.Replicas}}'"
    )
    desired_process = run_remote_command(server_name, desired_command)

    if desired_process.returncode != 0:
        fail("post-deploy could not inspect service: " + service_name)

    try:
        desired = int(desired_process.stdout.strip())
    except ValueError:
        fail("post-deploy received invalid desired replica count for service: " + service_name)

    running_command = (
        "docker service ps "
        + shlex.quote(service_name)
        + " --filter desired-state=running --format '{{.CurrentState}}'"
    )
    running_process = run_remote_command(server_name, running_command)

    if running_process.returncode != 0:
        fail("post-deploy could not inspect running tasks for service: " + service_name)

    running = sum(
        1
        for line in running_process.stdout.splitlines()
        if line.strip().startswith("Running ")
    )

    return desired, running


def get_new_failed_tasks(server_name, app_name, baseline_task_ids):
    service_name = get_service_name(app_name)
    command = (
        "docker service ps "
        + shlex.quote(service_name)
        + " --no-trunc --format '{{.ID}}\t{{.CurrentState}}\t{{.Error}}'"
    )
    completed_process = run_remote_command(server_name, command)

    if completed_process.returncode != 0:
        fail("post-deploy could not inspect task history for service: " + service_name)

    failures = []

    for line in completed_process.stdout.splitlines():
        parts = line.split("\t", 2)

        if len(parts) < 2:
            continue

        task_id = parts[0]
        current_state = parts[1]

        if task_id in baseline_task_ids:
            continue

        if current_state.startswith("Failed ") or current_state.startswith("Rejected "):
            failures.append(task_id)

    return failures


def print_service_tasks(server_name, app_name):
    service_name = get_service_name(app_name)
    command = "docker service ps " + shlex.quote(service_name) + " --no-trunc"
    completed_process = run_remote_command(server_name, command)

    if completed_process.stdout:
        print(completed_process.stdout, end="", file=sys.stderr)

    if completed_process.stderr:
        print(completed_process.stderr, end="", file=sys.stderr)


def wait_for_swarm_convergence(server_name, app_name, baseline_task_ids):
    if is_caprover_url(server_name):
        info("[post-deploy] URL target has no SSH context; Swarm convergence/churn verification skipped.")
        return

    timeout_seconds = float(os.environ.get("CAPROVER_SWARM_WAIT_TIMEOUT_SECONDS", "180"))
    poll_seconds = float(os.environ.get("CAPROVER_SWARM_POLL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds
    service_name = get_service_name(app_name)

    info(f"[post-deploy] Waiting for Swarm service convergence: {service_name}")

    while True:
        desired, running = get_service_replica_counts(server_name, app_name)
        failures = get_new_failed_tasks(server_name, app_name, baseline_task_ids)

        if failures and running < desired:
            print_service_tasks(server_name, app_name)
            fail(
                "post-deploy task churn detected: new Failed/Rejected task appeared before convergence for service: "
                + service_name
            )

        if running == desired:
            info(f"[post-deploy] Swarm service converged: {running}/{desired} running.")
            print_service_tasks(server_name, app_name)
            return

        if time.monotonic() >= deadline:
            print_service_tasks(server_name, app_name)
            fail(
                f"post-deploy timed out waiting for service convergence: {service_name} ({running}/{desired} running)"
            )

        time.sleep(poll_seconds)


def describe_postgres_target(connection_string):
    parsed = urlparse(str(connection_string))

    if parsed.scheme not in POSTGRES_URI_SCHEMES or not parsed.hostname:
        return "<invalid-postgresql-url>"

    database_name = unquote(parsed.path.lstrip("/"))

    try:
        port = parsed.port or 5432
    except ValueError:
        return "<invalid-postgresql-url>"

    return (
        "host="
        + parsed.hostname
        + " port="
        + str(port)
        + " database="
        + (database_name or "<missing>")
    )


def describe_drift_value(_env_name, value):
    normalized_value = str(value).strip()

    if normalized_value.lower().startswith(("postgres://", "postgresql://")):
        return describe_postgres_target(normalized_value)

    return "<redacted>"


def build_live_env_drift_details(expected, actual):
    missing = sorted(set(expected) - set(actual))
    changed = sorted(
        key
        for key in set(expected) & set(actual)
        if expected[key] != actual[key]
    )
    details = []

    for key in missing:
        details.append(key + ": missing from live configuration")

    for key in changed:
        details.extend(
            (
                key + ":",
                "  inventory: " + describe_drift_value(key, expected[key]),
                "  live:      " + describe_drift_value(key, actual[key]),
                "  values differ",
            )
        )

    return details


def verify_live_env_drift(args):
    expected = get_inventory_env_map(args)
    app_definition = require_app_definition(args.server_name, args.app_name)
    actual = env_vars_to_map(app_definition.get("envVars", []))
    details = build_live_env_drift_details(expected, actual)

    if details:
        fail("live configuration drift\n\n" + "\n".join(details))

    info("[verify] All inventory environment variables match live state OK")


def verify_ansirover_metadata(args, expected_policy):
    inventory = load_ansirover_app(get_app_dir(args), args.app_name)
    expected_env_var_templates = get_inventory_env_var_templates(args)

    try:
        (
            expected_human_description,
            _inventory_embedded_policy,
            _inventory_embedded_templates,
        ) = split_description_metadata(inventory["description"])
        app_definition = require_app_definition(args.server_name, args.app_name)
        actual_description = app_definition.get("description", "")
        (
            actual_human_description,
            actual_policy,
            actual_env_var_templates,
        ) = split_description_metadata(actual_description)
    except AnsiRoverPolicyError as error:
        fail("could not verify persisted AnsiRover metadata: " + str(error))

    if actual_policy != expected_policy:
        fail("persisted AnsiRover metadata does not match the explicit inventory policy")

    if actual_env_var_templates != expected_env_var_templates:
        fail("persisted AnsiRover metadata does not match the inventory env var templates")

    if (
        not expected_policy
        and not expected_env_var_templates
        and BEGIN_MARKER in str(actual_description)
    ):
        fail("persisted CapRover description contains unexpected empty AnsiRover metadata")

    if actual_human_description != expected_human_description:
        fail("persisted CapRover human description does not match the inventory description")

    info("[verify] AnsiRover metadata persisted OK")


def get_monitorable_postgres_connections(args):
    env_map = get_inventory_env_map(args)
    expected_application_name = get_expected_application_name(args)
    monitorable = []
    seen_targets = set()

    for env_name, connection_string, parsed in get_postgres_connection_envs(env_map):
        query = parse_qs(parsed.query, keep_blank_values=True)
        application_names = query.get("application_name", [])

        if application_names != [expected_application_name]:
            continue

        target = (parsed.hostname, parsed.port or 5432)

        if target in seen_targets:
            continue

        seen_targets.add(target)
        monitorable.append((env_name, connection_string))

    return monitorable


def get_postgres_session_ids(env_name, connection_string, application_name):
    try:
        psql_environment, _database_name = build_psql_environment(connection_string)
    except ValueError as error:
        fail("post-deploy PostgreSQL monitor failed for " + env_name + ": " + str(error))

    psql_environment["PGAPPNAME"] = POST_DEPLOY_MONITOR_APPLICATION_NAME
    escaped_application_name = application_name.replace("'", "''")
    query = (
        "SELECT pid::text || '|' || backend_start::text "
        "FROM pg_stat_activity "
        "WHERE application_name = '"
        + escaped_application_name
        + "' AND pid <> pg_backend_pid() "
        "ORDER BY backend_start;"
    )
    command_timeout_seconds = get_database_preflight_timeout_seconds() + 5

    try:
        completed_process = subprocess.run(
            ["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", query],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=psql_environment,
            timeout=command_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        fail("post-deploy PostgreSQL session monitor timed out for " + env_name)

    if completed_process.returncode != 0:
        fail("post-deploy PostgreSQL session monitor query failed for " + env_name)

    return {
        line.strip()
        for line in completed_process.stdout.splitlines()
        if line.strip()
    }


def scale_service_to_zero(server_name, app_name):
    if is_caprover_url(server_name):
        fail(
            "reconnect loop detected but service cannot be quarantined automatically for a URL-only CapRover target"
        )

    service_name = get_service_name(app_name)
    info("[post-deploy] QUARANTINE: scaling service to zero: " + service_name)
    command = (
        "docker service update --detach=true --replicas 0 "
        + shlex.quote(service_name)
    )
    completed_process = run_remote_command(server_name, command)

    if completed_process.returncode != 0:
        fail("reconnect loop detected and automatic scale-to-zero failed for service: " + service_name)

    print_service_tasks(server_name, app_name)


def monitor_post_deploy_postgres_reconnects(args, effective_policy):
    monitor_seconds = duration_to_seconds(
        "gracePeriod",
        effective_policy["gracePeriod"],
    )

    if shutil.which("psql") is None:
        info("[post-deploy] psql is not installed; PostgreSQL reconnect monitor skipped.")
        return

    monitorable_connections = get_monitorable_postgres_connections(args)

    if not monitorable_connections:
        info("[post-deploy] No PostgreSQL URI with enforced application_name is available for reconnect monitoring.")
        return

    reconnect_threshold = int(effective_policy["postgresReconnectThreshold"])
    poll_seconds = duration_to_seconds(
        "postgresPollInterval",
        effective_policy["postgresPollInterval"],
    )

    expected_application_name = get_expected_application_name(args)
    seen_sessions_by_env = {}
    reconnect_wave_count = 0

    for env_name, connection_string in monitorable_connections:
        seen_sessions_by_env[env_name] = get_postgres_session_ids(
            env_name,
            connection_string,
            expected_application_name,
        )

    info(
        "[post-deploy] Monitoring PostgreSQL session churn for "
        + str(monitor_seconds)
        + "s; quarantine threshold="
        + str(reconnect_threshold)
        + " reconnect waves; poll interval="
        + str(poll_seconds)
        + "s."
    )
    deadline = time.monotonic() + monitor_seconds

    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        reconnect_wave_observed = False

        for env_name, connection_string in monitorable_connections:
            current_sessions = get_postgres_session_ids(
                env_name,
                connection_string,
                expected_application_name,
            )
            seen_sessions = seen_sessions_by_env[env_name]
            new_sessions = current_sessions - seen_sessions

            if new_sessions:
                reconnect_wave_observed = True
                seen_sessions.update(new_sessions)
                info(
                    "[post-deploy] PostgreSQL session churn observed for "
                    + env_name
                    + "; new sessions in this poll="
                    + str(len(new_sessions))
                )

        if reconnect_wave_observed:
            reconnect_wave_count += 1
            info(
                "[post-deploy] PostgreSQL reconnect wave observed; wave="
                + str(reconnect_wave_count)
                + "/"
                + str(reconnect_threshold)
            )

        if reconnect_wave_count >= reconnect_threshold:
            scale_service_to_zero(args.server_name, args.app_name)
            fail(
                "post-deploy PostgreSQL reconnect loop threshold reached; service quarantined with replicas=0"
            )

    info(
        "[post-deploy] PostgreSQL reconnect monitor passed; reconnect waves observed="
        + str(reconnect_wave_count)
    )




def strip_inline_comment(line):
    in_double_quote = False
    in_single_quote = False
    escaped = False

    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue

        if character == "\\":
            escaped = True
            continue

        if character == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue

        if character == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue

        if character == "#" and not in_double_quote and not in_single_quote:
            if index == 0 or line[index - 1].isspace():
                return line[:index].rstrip()

    return line.rstrip()


def parse_scalar(raw_value):
    value = raw_value.strip()

    if value == "":
        return ""

    if value == "[]":
        return []

    if value == "{}":
        return {}

    if value == "true":
        return True

    if value == "false":
        return False

    if value == "null":
        return None

    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)

    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            fail(f"invalid quoted YAML scalar: {value}: {error}")

    return value


def parse_key_value_line(line, context):
    if ":" not in line:
        fail(f"invalid YAML line in {context}: expected key: value: {line}")

    key, raw_value = line.split(":", 1)

    key = key.strip()

    if not key:
        fail(f"invalid YAML line in {context}: empty key: {line}")

    return key, parse_scalar(raw_value)


def parse_simple_ansirover_yaml(yaml_path):
    if not yaml_path.exists():
        fail(f"YAML file not found: {yaml_path}")

    raw_lines = yaml_path.read_text(encoding="utf-8").splitlines()
    lines = []

    for line_number, raw_line in enumerate(raw_lines, start=1):
        line = strip_inline_comment(raw_line.rstrip())

        if not line.strip():
            continue

        lines.append((line_number, line))

    result_object = {}
    index = 0

    while index < len(lines):
        line_number, line = lines[index]

        if line.startswith(" "):
            fail(f"unexpected indentation at line {line_number}: {line}")

        key, value = parse_key_value_line(line, f"line {line_number}")

        if value == []:
            result_object[key] = []
            index += 1
            continue

        if value == {}:
            result_object[key] = {}
            index += 1
            continue

        if key in {"volumes", "ports", "tags", "customDomain"} and value == "":
            items = []
            index += 1

            while index < len(lines):
                child_line_number, child_line = lines[index]

                if not child_line.startswith("  "):
                    break

                if child_line.startswith("    "):
                    fail(f"unexpected nested indentation at line {child_line_number}: {child_line}")

                stripped_child_line = child_line.strip()

                if not stripped_child_line.startswith("- "):
                    fail(f"expected list item at line {child_line_number}: {child_line}")

                item = {}
                first_item_line = stripped_child_line[2:].strip()

                if first_item_line:
                    item_key, item_value = parse_key_value_line(first_item_line, f"line {child_line_number}")
                    item[item_key] = item_value

                index += 1

                while index < len(lines):
                    grandchild_line_number, grandchild_line = lines[index]

                    if not grandchild_line.startswith("    "):
                        break

                    stripped_grandchild_line = grandchild_line.strip()
                    item_key, item_value = parse_key_value_line(
                        stripped_grandchild_line,
                        f"line {grandchild_line_number}",
                    )
                    item[item_key] = item_value
                    index += 1

                items.append(item)

            result_object[key] = items
            continue

        if key in {"httpAuth", "ansirover"} and value == "":
            nested_object = {}
            index += 1

            while index < len(lines):
                child_line_number, child_line = lines[index]

                if not child_line.startswith("  "):
                    break

                if child_line.startswith("    "):
                    fail(f"unexpected nested indentation at line {child_line_number}: {child_line}")

                stripped_child_line = child_line.strip()
                item_key, item_value = parse_key_value_line(stripped_child_line, f"line {child_line_number}")
                nested_object[item_key] = item_value
                index += 1

            result_object[key] = nested_object
            continue

        result_object[key] = value
        index += 1

    return result_object


def require_field(ansirover, field_name, expected_type):
    if field_name not in ansirover:
        fail(f"required field missing from YAML: {field_name}")

    value = ansirover[field_name]

    if not isinstance(value, expected_type):
        fail(
            f"field {field_name} has invalid type: "
            f"expected {expected_type.__name__}, got {type(value).__name__}"
        )

    return value


def load_ansirover_app(app_dir, app_name):
    yaml_path = app_dir / f"{app_name}.yaml"
    ansirover = parse_simple_ansirover_yaml(yaml_path)

    require_field(ansirover, "appName", str)
    require_field(ansirover, "deployedImageName", str)
    require_field(ansirover, "description", str)
    require_field(ansirover, "hasPersistentData", bool)
    require_field(ansirover, "instanceCount", int)
    require_field(ansirover, "containerHttpPort", int)
    require_field(ansirover, "notExposeAsWebApp", bool)
    require_field(ansirover, "websocketSupport", bool)

    if not isinstance(ansirover.get("volumes", []), list):
        fail("field volumes must be a list")

    if not isinstance(ansirover.get("ports", []), list):
        fail("field ports must be a list")

    if not isinstance(ansirover.get("tags", []), list):
        fail("field tags must be a list")

    if not isinstance(ansirover.get("httpAuth", {}), dict):
        fail("field httpAuth must be an object")

    ansirover["customDomain"] = normalize_custom_domains(
        ansirover.get("customDomain", [])
    )

    explicit_policy = ansirover.get("ansirover")

    if "ansirover" in ansirover and explicit_policy is None:
        fail("ansirover must be an object")

    try:
        ansirover["ansirover"] = validate_explicit_policy(explicit_policy)
    except AnsiRoverPolicyError as error:
        fail(str(error))

    return ansirover


def read_optional_sidecar(path):
    if not path.exists():
        return ""

    if not path.is_file():
        fail(f"sidecar path is not a file: {path}")

    return path.read_text(encoding="utf-8")


def expand_env_var_template(value, environment, context, app_name=None):
    source_value = str(value)
    templates_found = False

    def replace_token(match):
        nonlocal templates_found
        templates_found = True
        env_name = match.group(1)

        if not ENV_TEMPLATE_NAME_PATTERN.fullmatch(env_name):
            fail(
                "unsupported env var template in "
                + context
                + ": expected ${VAR} or escaped literal $${VAR}"
            )

        if match.group(0).startswith("$${"):
            return "${" + env_name + "}"

        if env_name in {"appname", "appName"}:
            if app_name is None:
                fail(
                    "app name is required to expand built-in env var template ${"
                    + env_name
                    + "} in "
                    + context
                )

            return str(app_name)

        if env_name not in environment:
            fail(
                "environment variable "
                + env_name
                + " referenced by env var template in "
                + context
                + " is not set"
            )

        return str(environment[env_name])

    expanded_value = ENV_TEMPLATE_TOKEN_PATTERN.sub(replace_token, source_value)
    residual_source = ENV_TEMPLATE_TOKEN_PATTERN.sub("", source_value)

    if "${" in residual_source:
        fail(
            "unsupported or unterminated env var template in "
            + context
            + ": only ${VAR} and escaped literal $${VAR} are supported"
        )

    return expanded_value, templates_found


def parse_env_vars_file_with_templates(path, environment=None, app_name=None):
    text = read_optional_sidecar(path)

    if not text:
        return [], {}

    if environment is None:
        environment = os.environ

    env_vars = []
    env_var_templates = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            fail(f"invalid env var line {line_number} in {path}: expected KEY=VALUE")

        key, value = line.split("=", 1)

        key = key.strip()

        if not key:
            fail(f"invalid env var line {line_number} in {path}: empty key")

        value = value.strip()

        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            with contextlib.suppress(json.JSONDecodeError):
                value = json.loads(value)

        template_value = str(value)
        expanded_value, has_template = expand_env_var_template(
            template_value,
            environment,
            f"line {line_number} in {path}",
            app_name=app_name,
        )

        if has_template:
            env_var_templates[key] = template_value
        else:
            env_var_templates.pop(key, None)

        env_vars.append(
            {
                "key": key,
                "value": expanded_value,
            }
        )

    return env_vars, env_var_templates


def parse_env_vars_file(path, environment=None, app_name=None):
    env_vars, _env_var_templates = parse_env_vars_file_with_templates(
        path,
        environment=environment,
        app_name=app_name,
    )
    return env_vars


def load_sidecars(app_dir, app_name):
    env_vars, env_var_templates = parse_env_vars_file_with_templates(
        app_dir / f"{app_name}.env-vars.env",
        app_name=app_name,
    )
    return {
        "envVars": env_vars,
        "envVarTemplates": env_var_templates,
        "preDeployFunction": read_optional_sidecar(app_dir / f"{app_name}.pre-deploy-function.js"),
        "serviceUpdateOverride": read_optional_sidecar(app_dir / f"{app_name}.service-update-override.yaml"),
        "customNginxConfig": read_optional_sidecar(app_dir / f"{app_name}.custom-nginx-config.conf"),
    }


def normalize_volumes(value):
    volumes = []

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"volumes[{index}] must be an object")

        normalized = {}

        container_path = item.get("containerPath")

        if not isinstance(container_path, str) or not container_path:
            fail(f"volumes[{index}] is missing containerPath")

        normalized["containerPath"] = container_path

        volume_name = item.get("volumeName")
        host_path = item.get("hostPath")

        if volume_name and host_path:
            fail(f"volumes[{index}] must not have both volumeName and hostPath")

        if volume_name:
            normalized["volumeName"] = str(volume_name)
        elif host_path:
            normalized["hostPath"] = str(host_path)
        else:
            fail(f"volumes[{index}] must have volumeName or hostPath")

        volumes.append(normalized)

    return volumes


def normalize_ports(value):
    ports = []

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"ports[{index}] must be an object")

        if "hostPort" not in item:
            fail(f"ports[{index}] is missing hostPort")

        if "containerPort" not in item:
            fail(f"ports[{index}] is missing containerPort")

        ports.append(
            {
                "hostPort": int(item["hostPort"]),
                "containerPort": int(item["containerPort"]),
            }
        )

    return ports


def normalize_tags(value):
    tags = []

    for index, item in enumerate(value):
        if isinstance(item, str):
            tag_name = item
        elif isinstance(item, dict):
            tag_name = item.get("tagName", "")
        else:
            fail(f"tags[{index}] must be object or string")

        tag_name = str(tag_name).strip()

        if not tag_name:
            continue

        tags.append({"tagName": tag_name})

    return tags


def normalize_http_auth(value):
    if not value:
        return {}

    normalized = {}

    for key in ["user", "passwordHashed"]:
        item_value = value.get(key)

        if item_value is None:
            continue

        item_value = str(item_value)

        if item_value == "":
            continue

        normalized[key] = item_value

    return normalized


def normalize_custom_domains(value):
    if not isinstance(value, list):
        fail("field customDomain must be a list")

    normalized_domains = []
    seen_domains = set()

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"customDomain[{index}] must be an object")

        public_domain = item.get("publicDomain")

        if not isinstance(public_domain, str) or not public_domain.strip():
            fail(f"customDomain[{index}].publicDomain must be a non-empty string")

        has_ssl = item.get("hasSsl", False)

        if not isinstance(has_ssl, bool):
            fail(f"customDomain[{index}].hasSsl must be a boolean")

        public_domain = public_domain.strip().lower()

        if public_domain in seen_domains:
            fail(f"customDomain contains duplicate publicDomain: {public_domain}")

        seen_domains.add(public_domain)
        normalized_domains.append(
            {
                "publicDomain": public_domain,
                "hasSsl": has_ssl,
            }
        )

    return normalized_domains


def configure_custom_domains(
    server_name,
    app_name,
    app_definition,
    desired_custom_domains,
):
    current_custom_domain_value = app_definition.get("customDomain")

    if current_custom_domain_value is None:
        current_custom_domain_value = []

    current_custom_domains = normalize_custom_domains(
        current_custom_domain_value
    )
    current_by_domain = {
        item["publicDomain"]: item for item in current_custom_domains
    }

    for desired_domain in desired_custom_domains:
        public_domain = desired_domain["publicDomain"]
        current_domain = current_by_domain.get(public_domain)

        if current_domain is None:
            info(f"[custom domain] Adding {public_domain} to {app_name}...")
            run_caprover_api(
                server_name=server_name,
                api_path="/user/apps/appDefinitions/customdomain",
                method="POST",
                data={
                    "appName": app_name,
                    "customDomain": public_domain,
                },
            )
            current_domain = {
                "publicDomain": public_domain,
                "hasSsl": False,
            }
        else:
            info(f"[custom domain] Domain already associated with {app_name}: {public_domain}")

        if desired_domain["hasSsl"] and not current_domain["hasSsl"]:
            info(f"[custom domain] Enabling SSL for {public_domain}...")
            run_caprover_api(
                server_name=server_name,
                api_path="/user/apps/appDefinitions/enablecustomdomainssl",
                method="POST",
                data={
                    "appName": app_name,
                    "customDomain": public_domain,
                },
            )
        elif desired_domain["hasSsl"]:
            info(f"[custom domain] SSL already enabled for {public_domain}.")
        elif current_domain["hasSsl"]:
            info(
                f"[custom domain] SSL is already enabled for {public_domain}; "
                "restore does not disable existing certificates."
            )


def get_named_volume_names(ansirover):
    names = []

    for volume in ansirover.get("volumes", []):
        if not isinstance(volume, dict):
            continue

        volume_name = volume.get("volumeName")

        if not volume_name:
            continue

        names.append(str(volume_name))

    return names


def validate_named_volumes_require_persistent_data(ansirover):
    named_volume_names = get_named_volume_names(ansirover)

    if not named_volume_names:
        return

    if ansirover["hasPersistentData"]:
        return

    fail(
        "AnsiRover YAML defines named Docker volumes but hasPersistentData is false. "
        "CapRover cannot change this after app creation."
    )


def run_command(command, cwd, capture_stdout=False):
    info("[run] cwd: " + str(cwd))
    info("[run] " + " ".join(command))

    if capture_stdout:
        completed_process = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            check=False,
        )

        if completed_process.stdout:
            print(completed_process.stdout, end="")

        if completed_process.returncode != 0:
            fail("command failed with exit code " + str(completed_process.returncode) + ": " + " ".join(command))

        return completed_process.stdout

    completed_process = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        check=False,
    )

    if completed_process.returncode != 0:
        fail("command failed with exit code " + str(completed_process.returncode) + ": " + " ".join(command))

    return ""


def get_caprover_root_domain(server_name):
    config_path = Path.home() / ".config" / "configstore" / "caprover.json"

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"CapRover CLI config not found: {config_path}")
    except json.JSONDecodeError as error:
        fail(f"invalid CapRover CLI config JSON: {config_path}: {error}")

    for machine in config.get("CapMachines", []):
        if not isinstance(machine, dict) or machine.get("name") != server_name:
            continue

        host = urlparse(str(machine.get("baseUrl", ""))).hostname

        if host and host.startswith("captain."):
            return host[len("captain."):]

        fail(f"could not derive CapRover root domain from machine URL: {machine.get('baseUrl', '')}")

    fail(f"CapRover machine not found in config: {server_name}")


def require_deploy_image_available(server_name, image_reference):
    if not image_reference:
        fail("deployedImageName is empty; cannot check image availability")

    caprover_root_domain = get_caprover_root_domain(server_name)
    ssh_target = "root@" + caprover_root_domain
    remote_command = "docker pull " + shlex.quote(image_reference)

    info(f"[image preflight] Pulling deploy image on {ssh_target}: {image_reference}")

    completed_process = subprocess.run(
        ["ssh", "-n", ssh_target, remote_command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed_process.returncode == 0:
        info("[image preflight] Deploy image is available.")
        return

    print_limited_block("docker pull stdout:", completed_process.stdout)
    print_limited_block("docker pull stderr:", completed_process.stderr)
    fail(
        "deploy image does not exist or cannot be pulled on the destination server: "
        + image_reference
        + ". Publish the image before restoring; for Docker Compose, use: "
        + "docker compose build --push"
    )


def is_external_pullable_docker_image(image_reference, caprover_root_domain):
    image_reference = str(image_reference or "").strip()

    if not image_reference:
        return False

    first_path_component = image_reference.split("/", 1)[0]

    if "/" not in image_reference:
        return True

    caprover_registry = "registry." + caprover_root_domain.lower()
    return first_path_component.lower() != caprover_registry


def restore_image_if_requested(server_name, app_name, app_dir, image_reference, with_image, with_dockerhub, caprover_root_domain):
    if not with_image:
        info("[image] --with-image not passed; using deployedImageName directly.")
        return image_reference

    if is_external_pullable_docker_image(image_reference, caprover_root_domain) and not with_dockerhub:
        info("[image] Externally pullable image detected; skipping image archive restore/upload.")
        info("[image] Image: " + image_reference)
        info("[image] Use --with-dockerhub to restore/upload this image archive anyway.")
        return image_reference

    image_archive_path = app_dir / f"{app_name}.image.tar.gz"

    if not image_archive_path.exists():
        fail(f"image archive not found: {image_archive_path}")

    output = run_command(
        [
            "caprover-image-restore-upload",
            server_name,
            image_reference,
            str(image_archive_path),
            "--force",
        ],
        cwd=app_dir,
        capture_stdout=True,
    )

    deploy_image = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if not line.startswith("DEPLOY_IMAGE="):
            continue

        deploy_image = line.split("=", 1)[1].strip()

    if not deploy_image:
        fail("caprover-image-restore-upload did not print DEPLOY_IMAGE=...")

    return deploy_image


def restore_volumes_if_requested(server_name, app_name, app_dir, ansirover, with_volumes):
    named_volume_names = get_named_volume_names(ansirover)

    if not named_volume_names:
        info("[volumes] No named Docker volumes to restore.")
        return

    if not with_volumes:
        info("[volumes] --with-volumes not passed; skipping named volume restore.")
        return

    command = [
        "caprover-volume-restore-upload",
        server_name,
        app_name,
    ]

    command.extend(named_volume_names)

    command.extend(
        [
            "--input-dir",
            str(app_dir),
            "--instance-count",
            str(ansirover["instanceCount"]),
            "--force",
        ]
    )

    run_command(command, cwd=app_dir)


def validate_existing_description_metadata(app_definition):
    try:
        split_description(app_definition.get("description", ""))
    except AnsiRoverPolicyError as error:
        fail("existing CapRover description has invalid AnsiRover metadata: " + str(error))


def build_persisted_description(ansirover, env_var_templates=None):
    try:
        return merge_description(
            ansirover["description"],
            ansirover.get("ansirover"),
            env_var_templates,
        )
    except AnsiRoverPolicyError as error:
        fail("inventory description has invalid AnsiRover metadata: " + str(error))


def build_update_payload(app_definition, app_name, ansirover, sidecars, force_ssl):
    not_expose_as_web_app = ansirover["notExposeAsWebApp"]

    if not_expose_as_web_app:
        selected_force_ssl = False
    else:
        selected_force_ssl = bool(force_ssl)

    payload = {
        "appName": app_name,
        "instanceCount": ansirover["instanceCount"],
        "captainDefinitionRelativeFilePath": app_definition.get(
            "captainDefinitionRelativeFilePath",
            "./captain-definition",
        ),
        "notExposeAsWebApp": not_expose_as_web_app,
        "forceSsl": selected_force_ssl,
        "websocketSupport": ansirover["websocketSupport"],
        "volumes": normalize_volumes(ansirover.get("volumes", [])),
        "ports": normalize_ports(ansirover.get("ports", [])),
        "containerHttpPort": ansirover["containerHttpPort"],
        "description": build_persisted_description(
            ansirover,
            sidecars.get("envVarTemplates", {}),
        ),
        "envVars": sidecars["envVars"],
        "tags": normalize_tags(ansirover.get("tags", [])),
        "redirectDomain": app_definition.get("redirectDomain", ""),
        "projectId": app_definition.get("projectId", ""),
        "customNginxConfig": sidecars["customNginxConfig"],
        "preDeployFunction": sidecars["preDeployFunction"],
        "serviceUpdateOverride": sidecars["serviceUpdateOverride"],
        "appDeployTokenConfig": app_definition.get("appDeployTokenConfig", {"enabled": False}),
    }

    http_auth = normalize_http_auth(ansirover.get("httpAuth", {}))

    if http_auth:
        payload["httpAuth"] = http_auth
    elif "httpAuth" in app_definition:
        payload["httpAuth"] = {}

    for optional_key in ["nodeId"]:
        if optional_key in app_definition:
            payload[optional_key] = app_definition[optional_key]

    return payload


def validate_app_identity(cli_app_name, yaml_app_name):
    if cli_app_name.lower() != yaml_app_name.lower():
        fail(
            "app name mismatch: CLI app name does not match YAML appName "
            f"case-insensitively: {cli_app_name} != {yaml_app_name}"
        )


def restore_one_app_steps(args):
    server_name = args.server_name
    app_name = args.app_name

    global CURRENT_APP_NAME
    CURRENT_APP_NAME = app_name

    app_dir = Path(args.path).expanduser().resolve()

    validate_server_target(server_name)
    validate_simple_name("app name", app_name)
    validate_server_mode_options(
        server_name=server_name,
        with_image=args.with_image,
        with_volumes=args.with_volumes,
    )

    if not app_dir.exists() or not app_dir.is_dir():
        fail(f"app path is not a directory: {app_dir}")

    if args.with_dockerhub and not args.with_image:
        fail("--with-dockerhub only makes sense together with --with-image")

    step("plan", "Action: AnsiRover restore upload")
    step("plan", "Server: " + server_name)
    step("plan", "App: " + app_name)
    step("plan", "App path: " + str(app_dir))
    step("plan", "With image: " + str(args.with_image))
    step("plan", "With externally pullable image archives: " + str(args.with_dockerhub))
    step("plan", "With volumes: " + str(args.with_volumes))
    step("plan", "Wait deploy: " + str(args.wait_deploy))

    step("restore 1/13", "Reading AnsiRover YAML and sidecars...")
    ansirover = load_ansirover_app(app_dir, app_name)
    validate_named_volumes_require_persistent_data(ansirover)
    sidecars = load_sidecars(app_dir, app_name)
    yaml_app_name = ansirover["appName"]

    step("restore 2/13", "Validating app identity...")
    validate_app_identity(app_name, yaml_app_name)

    # This preflight intentionally verifies the image before any app mutation.
    # Without it, config/SSL changes could be applied before deploy discovers a missing image,
    # leaving the destination app partially restored. URL mode skips it by design because that
    # mode is API-only and lets CapRover validate/pull the already-published registry image.
    if should_preflight_deploy_image(server_name, args.with_image) and getattr(args, "skip_checks", False) is not True:
        step("preflight", "Checking deploy image availability before changing the app...")
        require_deploy_image_available(server_name, ansirover["deployedImageName"])
    elif is_caprover_url(server_name):
        info("[image preflight] URL mode is API-only; skipping SSH docker pull preflight.")

    step("restore 3/13", "Checking whether app exists on destination CapRover...")
    app_definition = find_app_definition(server_name, app_name)

    if app_definition is None:
        step("restore 4/13", "App does not exist; registering app using hasPersistentData from YAML...")
        register_app(
            server_name=server_name,
            app_name=app_name,
            has_persistent_data=ansirover["hasPersistentData"],
        )
        app_definition = require_app_definition(server_name, app_name)
    else:
        remote_app_name = str(app_definition.get("appName", ""))
        validate_app_identity(remote_app_name, yaml_app_name)
        step("restore 4/13", "App already exists; not changing hasPersistentData.")

    validate_existing_description_metadata(app_definition)

    step("restore 5/13", "Applying initial app config with forceSsl=false...")
    payload = build_update_payload(
        app_definition=app_definition,
        app_name=app_name,
        ansirover=ansirover,
        sidecars=sidecars,
        force_ssl=False,
    )
    update_app_definition(server_name, payload)

    step("restore 6/13", "Configuring base and custom domains before restore/deploy work...")
    app_definition = require_app_definition(server_name, app_name)

    if ansirover["notExposeAsWebApp"]:
        info("[ssl] Skipping SSL because app is not exposed as web app.")
    else:
        enable_base_domain_ssl_if_needed(server_name, app_name, app_definition)

    configure_custom_domains(
        server_name=server_name,
        app_name=app_name,
        app_definition=app_definition,
        desired_custom_domains=ansirover["customDomain"],
    )

    step("restore 7/13", "Restoring named volumes if requested...")
    restore_volumes_if_requested(
        server_name=server_name,
        app_name=app_name,
        app_dir=app_dir,
        ansirover=ansirover,
        with_volumes=args.with_volumes,
    )

    step("restore 8/13", "Restoring/uploading image archive if requested...")
    caprover_root_domain = ""

    if args.with_image:
        caprover_root_domain = get_caprover_root_domain(server_name)

    deploy_image_name = restore_image_if_requested(
        server_name=server_name,
        app_name=app_name,
        app_dir=app_dir,
        image_reference=ansirover["deployedImageName"],
        with_image=args.with_image,
        with_dockerhub=args.with_dockerhub,
        caprover_root_domain=caprover_root_domain,
    )

    if args.with_image and getattr(args, "skip_checks", False) is not True:
        step("preflight", "Checking restored deploy image availability...")
        require_deploy_image_available(server_name, deploy_image_name)

    step("restore 9/13", "Deploying selected image...")
    deploy_image(
        server_name=server_name,
        app_name=app_name,
        image_name=deploy_image_name,
        wait_deploy=args.wait_deploy,
    )

    step("restore 10/13", "Reloading app definition after deploy...")
    app_definition = require_app_definition(server_name, app_name)

    if ansirover["notExposeAsWebApp"]:
        step("restore 11/13", "Skipping forceSsl=true because app is not exposed as web app.")
        step("restore 12/13", "No final SSL config needed.")
        step("restore 13/13", "Restore flow completed.")
        result("APP_NAME=" + app_name)
        result("DEPLOY_IMAGE=" + deploy_image_name)
        return

    step("restore 11/13", "Reapplying same app config with forceSsl=true...")
    payload = build_update_payload(
        app_definition=app_definition,
        app_name=app_name,
        ansirover=ansirover,
        sidecars=sidecars,
        force_ssl=True,
    )
    update_app_definition(server_name, payload)

    step("restore 12/13", "Final config applied.")
    step("restore 13/13", "Restore flow completed.")
    result("APP_NAME=" + app_name)
    result("DEPLOY_IMAGE=" + deploy_image_name)


def restore_one_app(args, run_post_deploy_checks=True):
    ansirover = load_ansirover_app(get_app_dir(args), args.app_name)
    explicit_policy = ansirover.get("ansirover", {})
    effective_policy = get_effective_policy(explicit_policy)
    skip_checks = getattr(args, "skip_checks", False) is True

    if skip_checks:
        info("[guardrails] --skip-checks selected; pre-deploy and post-deploy checks skipped.")
    else:
        info("[guardrails] Running semantic preflight checks...")
        preflight_postgres(args)

    baseline_task_ids = set()

    if run_post_deploy_checks and not skip_checks:
        baseline_task_ids = get_task_state_snapshot(args.server_name, args.app_name)

    restore_one_app_steps(args)
    apply_restart_delay(
        args.server_name,
        args.app_name,
        str(effective_policy["restartDelay"]),
    )
    if skip_checks:
        return
    verify_ansirover_metadata(args, explicit_policy)
    verify_live_env_drift(args)

    if not run_post_deploy_checks:
        return

    if not args.wait_deploy:
        info("[guardrails] --no-wait selected; post-deploy checks skipped because deploy is still asynchronous.")
        return

    info("[guardrails] Running post-deploy verification...")
    wait_for_swarm_convergence(args.server_name, args.app_name, baseline_task_ids)
    monitor_post_deploy_postgres_reconnects(args, effective_policy)


def should_run_all_post_deploy_checks():
    return is_true(os.environ.get(ALL_POST_DEPLOY_CHECKS_ENV, ""))

RESTORE_RUN_STATE_FILE_NAME = "ansirover-restore-upload.json"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_restore_run_event(state, message):
    events = state.setdefault("events", [])
    events.append(now_iso() + " " + message)
    state["events"] = events[-10:]


def write_restore_run_state(state_path, state):
    state["updatedAt"] = now_iso()

    temporary_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(state_path)


def get_restore_run_options(args, ignore_names):
    return {
        "withImage": bool(args.with_image),
        "withDockerHub": bool(args.with_dockerhub),
        "withVolumes": bool(args.with_volumes),
        "waitDeploy": bool(args.wait_deploy),
        "skipChecks": getattr(args, "skip_checks", False) is True,
        "limit": args.limit,
        "ignore": sorted(ignore_names),
    }


def update_restore_run_summary(state, app_names, ignored_app_names):
    apps = state.setdefault("apps", {})

    done_count = 0
    failed_count = 0
    running_count = 0
    pending_count = 0

    for app_name in app_names:
        app_state = apps.get(app_name, {})
        status = app_state.get("status", "pending")

        if status == "done":
            done_count += 1
        elif status == "failed":
            failed_count += 1
        elif status == "running":
            running_count += 1
        else:
            pending_count += 1

    state["summary"] = {
        "totalApps": len(app_names),
        "done": done_count,
        "failed": failed_count,
        "running": running_count,
        "pending": pending_count,
        "ignored": len(ignored_app_names),
    }


def require_restore_run_state_compatible(state_path, state, server_name, base_dir, options):
    expected = {
        "schemaVersion": 1,
        "cliName": "ansirover-restore-upload",
        "mode": "all",
        "serverName": server_name,
        "rootPath": str(base_dir),
        "options": options,
    }

    for key, expected_value in expected.items():
        actual_value = state.get(key)

        if actual_value == expected_value:
            continue

        fail(
            "existing restore run state is incompatible for key "
            + key
            + ". Delete this file manually to start a different restore run: "
            + str(state_path)
        )


def load_or_create_restore_run_state(state_path, server_name, base_dir, options, app_names, ignored_app_names):
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            fail("invalid restore run state JSON: " + str(state_path) + ": " + str(error))

        if state.get("status") == "completed":
            fail(
                "restore run state is already completed. Delete this file manually "
                "before starting another restore run: "
                + str(state_path)
            )

        require_restore_run_state_compatible(
            state_path=state_path,
            state=state,
            server_name=server_name,
            base_dir=base_dir,
            options=options,
        )

        append_restore_run_event(state, "resumed restore run")
        update_restore_run_summary(state, app_names, ignored_app_names)
        write_restore_run_state(state_path, state)

        return state

    state = {
        "schemaVersion": 1,
        "cliName": "ansirover-restore-upload",
        "status": "in_progress",
        "serverName": server_name,
        "mode": "all",
        "rootPath": str(base_dir),
        "startedAt": now_iso(),
        "updatedAt": now_iso(),
        "completedAt": None,
        "options": options,
        "summary": {},
        "apps": {},
        "events": [],
    }

    for ignored_app_name in ignored_app_names:
        state["apps"][ignored_app_name] = {
            "status": "ignored",
            "message": "ignored by --ignore",
        }

    append_restore_run_event(state, "started restore run")
    update_restore_run_summary(state, app_names, ignored_app_names)
    write_restore_run_state(state_path, state)

    return state


def get_restore_app_state(state, app_name):
    return state.setdefault("apps", {}).setdefault(app_name, {})


def mark_restore_app_running(state_path, state, app_name, app_names, ignored_app_names):
    app_state = get_restore_app_state(state, app_name)
    app_state["status"] = "running"
    app_state["startedAt"] = now_iso()
    app_state["finishedAt"] = None
    app_state["attempts"] = int(app_state.get("attempts", 0)) + 1
    app_state["message"] = "restore started"

    append_restore_run_event(state, "started " + app_name)
    update_restore_run_summary(state, app_names, ignored_app_names)
    write_restore_run_state(state_path, state)


def mark_restore_app_done(state_path, state, app_name, app_names, ignored_app_names):
    app_state = get_restore_app_state(state, app_name)
    app_state["status"] = "done"
    app_state["finishedAt"] = now_iso()
    app_state["message"] = "restore completed"

    append_restore_run_event(state, "done " + app_name)
    update_restore_run_summary(state, app_names, ignored_app_names)
    write_restore_run_state(state_path, state)


def mark_restore_app_failed(state_path, state, app_name, app_names, ignored_app_names, message):
    app_state = get_restore_app_state(state, app_name)
    app_state["status"] = "failed"
    app_state["finishedAt"] = now_iso()
    app_state["message"] = str(message)[:240]

    append_restore_run_event(state, "failed " + app_name + ": " + str(message)[:120])
    update_restore_run_summary(state, app_names, ignored_app_names)
    write_restore_run_state(state_path, state)


def mark_restore_run_completed(state_path, state, app_names, ignored_app_names):
    state["status"] = "completed"
    state["completedAt"] = now_iso()

    append_restore_run_event(state, "completed restore run")
    update_restore_run_summary(state, app_names, ignored_app_names)
    write_restore_run_state(state_path, state)


def parse_ignore_names(raw_value):
    if raw_value is None:
        return set()

    ignored_names = set()

    for raw_item in str(raw_value).split(","):
        item = raw_item.strip()

        if not item:
            continue

        ignored_names.add(item.lower())

    return ignored_names


def find_ansirover_app_dirs(base_dir):
    if not base_dir.exists():
        fail(f"server backup path does not exist: {base_dir}")

    if not base_dir.is_dir():
        fail(f"server backup path is not a directory: {base_dir}")

    app_dirs = []

    for child_path in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child_path.is_dir():
            continue

        if child_path.name.startswith("."):
            continue

        app_name = child_path.name
        yaml_path = child_path / f"{app_name}.yaml"

        if not yaml_path.exists():
            continue

        app_dirs.append(
            {
                "appName": app_name,
                "appDir": child_path,
                "yamlPath": yaml_path,
            }
        )

    return app_dirs


def restore_all_apps(args):
    server_name = args.server_name
    base_dir = Path(args.path).expanduser().resolve()

    validate_server_target(server_name)
    validate_server_mode_options(
        server_name=server_name,
        with_image=args.with_image,
        with_volumes=args.with_volumes,
    )

    if args.with_dockerhub and not args.with_image:
        fail("--with-dockerhub only makes sense together with --with-image")

    if args.limit is not None and args.limit < 1:
        fail("--limit must be greater than or equal to 1")

    ignore_names = parse_ignore_names(args.ignore)
    all_app_dirs = find_ansirover_app_dirs(base_dir)

    ignored_app_names = []

    if ignore_names:
        ignored_app_names = [
            app_entry["appName"]
            for app_entry in all_app_dirs
            if app_entry["appName"].lower() in ignore_names
        ]

        app_dirs = [
            app_entry
            for app_entry in all_app_dirs
            if app_entry["appName"].lower() not in ignore_names
        ]
    else:
        app_dirs = all_app_dirs

    if args.limit is not None:
        app_dirs = app_dirs[:args.limit]

    if not app_dirs:
        fail(f"no AnsiRover app directories found under: {base_dir}")

    app_names = [app_entry["appName"] for app_entry in app_dirs]
    restore_run_options = get_restore_run_options(args, ignore_names)
    restore_run_state_path = base_dir / RESTORE_RUN_STATE_FILE_NAME

    restore_run_state = load_or_create_restore_run_state(
        state_path=restore_run_state_path,
        server_name=server_name,
        base_dir=base_dir,
        options=restore_run_options,
        app_names=app_names,
        ignored_app_names=ignored_app_names,
    )

    step("plan", "Action: AnsiRover restore upload")
    step("plan", "Mode: all apps")
    step("plan", "Server: " + server_name)
    step("plan", "Server backup path: " + str(base_dir))
    step("plan", "Restore run state: " + str(restore_run_state_path))
    step("plan", "Apps found after --ignore/--limit: " + str(len(app_dirs)))

    if ignore_names:
        step("plan", "Ignored apps: " + ", ".join(sorted(ignore_names)))

    step("plan", "With image: " + str(args.with_image))
    step("plan", "With externally pullable image archives: " + str(args.with_dockerhub))
    step("plan", "With volumes: " + str(args.with_volumes))
    step("plan", "Wait deploy: " + str(args.wait_deploy))

    restored_apps = []
    skipped_apps = []

    for app_index, app_entry in enumerate(app_dirs, start=1):
        app_name = app_entry["appName"]
        app_dir = app_entry["appDir"]

        app_state = restore_run_state.get("apps", {}).get(app_name, {})

        if app_state.get("status") == "done":
            info("")
            info("=" * 88)
            info(f"== restore app {app_index}/{len(app_dirs)} ==")
            info(f"== app: {app_name} ==")
            info("=" * 88)
            info("[state] App already restored; skipping: " + app_name)
            skipped_apps.append(app_name)
            continue

        info("")
        info("=" * 88)
        info(f"== restore app {app_index}/{len(app_dirs)} ==")
        info(f"== app: {app_name} ==")
        info("=" * 88)

        mark_restore_app_running(
            state_path=restore_run_state_path,
            state=restore_run_state,
            app_name=app_name,
            app_names=app_names,
            ignored_app_names=ignored_app_names,
        )

        child_args = argparse.Namespace(
            server_name=server_name,
            app_name=app_name,
            path=str(app_dir),
            with_image=args.with_image,
            with_dockerhub=args.with_dockerhub,
            with_volumes=args.with_volumes,
            wait_deploy=args.wait_deploy,
            skip_checks=getattr(args, "skip_checks", False) is True,
            limit=None,
            ignore=None,
        )

        try:
            restore_one_app(
                child_args,
                run_post_deploy_checks=should_run_all_post_deploy_checks(),
            )
        except SystemExit:
            mark_restore_app_failed(
                state_path=restore_run_state_path,
                state=restore_run_state,
                app_name=app_name,
                app_names=app_names,
                ignored_app_names=ignored_app_names,
                message="restore failed; see terminal output",
            )
            raise
        except BaseException as error:
            mark_restore_app_failed(
                state_path=restore_run_state_path,
                state=restore_run_state,
                app_name=app_name,
                app_names=app_names,
                ignored_app_names=ignored_app_names,
                message=type(error).__name__ + ": " + str(error),
            )
            raise

        mark_restore_app_done(
            state_path=restore_run_state_path,
            state=restore_run_state,
            app_name=app_name,
            app_names=app_names,
            ignored_app_names=ignored_app_names,
        )

        restored_apps.append(app_name)

    mark_restore_run_completed(
        state_path=restore_run_state_path,
        state=restore_run_state,
        app_names=app_names,
        ignored_app_names=ignored_app_names,
    )

    info("")
    info("[done] AnsiRover all-app restore completed.")

    result("RESTORE_RUN_STATE_FILE=" + str(restore_run_state_path))
    result("RESTORED_APP_COUNT=" + str(len(restored_apps)))
    result("SKIPPED_APP_COUNT=" + str(len(skipped_apps)))

    for app_name in restored_apps:
        result("RESTORED_APP=" + app_name)

    for app_name in skipped_apps:
        result("SKIPPED_APP=" + app_name)


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(
        description=(
            "Restore/upload one app or all apps from AnsiRover YAML backup folders."
        )
    )

    parser.add_argument(
        "server_name",
        help=(
            "destination CapRover CLI server name or captain URL, e.g. storage or "
            "https://captain.example.com"
        ),
    )
    parser.add_argument("app_name", nargs="?", help="destination app name or all")
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help=(
            "optional backup path. For a single app, this is the app backup folder. "
            "For all, this is the server backup folder. Defaults to current directory."
        ),
    )
    parser.add_argument("--with-image", action="store_true", help="restore/upload image archive, except externally pullable images by default; not supported with a URL target")
    parser.add_argument("--with-dockerhub", action="store_true", help="with --with-image, also restore/upload externally pullable image archives")
    parser.add_argument("--with-volumes", action="store_true", help="restore named Docker volume archives; not supported with a URL target")
    parser.add_argument("--skip-checks", action="store_true", help="skip pre-deploy and post-deploy validation checks; deploy only")
    deploy_wait_group = parser.add_mutually_exclusive_group()
    deploy_wait_group.add_argument(
        "--wait-deploy",
        dest="wait_deploy",
        action="store_true",
        default=True,
        help="wait for deploy completion (default; retained for compatibility)",
    )
    deploy_wait_group.add_argument(
        "--no-wait",
        dest="wait_deploy",
        action="store_false",
        help="submit the deploy through the detached endpoint without waiting",
    )
    parser.add_argument("--limit", type=int, help="all mode only: restore only the first N discovered apps")
    parser.add_argument("--ignore", help="all mode only: comma-separated app names to skip, e.g. app1,app2")

    return parser.parse_args(arguments)


def infer_app_name_from_current_folder(args):
    app_dir = Path(args.path).expanduser().resolve()
    app_name = app_dir.name
    yaml_path = app_dir / f"{app_name}.yaml"

    if not yaml_path.exists():
        fail(f"YAML file not found for current folder: {yaml_path}")

    try:
        confirmation = input(
            f"No app name provided. Restore {app_name} from {app_dir}? [Y/n] "
        ).strip().lower()
    except EOFError:
        fail("confirmation is required when no app name is provided")

    if confirmation not in {"", "y", "yes"}:
        fail("restore cancelled")

    args.app_name = app_name


def main():
    args = parse_args()

    if args.app_name is None:
        infer_app_name_from_current_folder(args)

    if args.app_name.lower() == "all":
        restore_all_apps(args)
        return

    if args.limit is not None:
        fail("--limit can only be used when app_name is all")

    if args.ignore is not None:
        fail("--ignore can only be used when app_name is all")

    restore_one_app(args)


if __name__ == "__main__":
    main()
