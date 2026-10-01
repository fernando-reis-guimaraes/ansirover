#!/usr/bin/env python3

import argparse
import contextlib
import getpass
import hmac
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from caprover_ansirover_policy import amun_secret_exists, fetch_amun_secret


APP_DEFINITIONS_API_PATH = "/user/apps/appDefinitions"


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message, exit_code=2):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def result(message):
    print(message, flush=True)


def get_captain_password_env_name(server_name):
    normalized = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_CAPTAIN_PASSWORD"


def require_captain_password(server_name):
    env_name = get_captain_password_env_name(server_name)
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


def validate_simple_name(label, value):
    if not value:
        fail(f"{label} must not be empty")

    if "\x00" in value:
        fail(f"{label} must not contain NUL byte: {value!r}")

    if "/" in value:
        fail(f"{label} must not contain slash: {value}")

    if value in {".", ".."}:
        fail(f"{label} must not be . or ..")

    if any(character.isspace() for character in value):
        fail(f"{label} must not contain whitespace: {value}")


def validate_caprover_machine_name(server_name):
    validate_simple_name("server name", server_name)

    if "@" in server_name:
        fail(
            "CapRover machine name must not contain @; "
            "use --ssh-target=user@host for an explicit SSH target"
        )


def validate_ssh_target(ssh_target):
    if not ssh_target:
        fail("--ssh-target must not be empty")

    if "\x00" in ssh_target:
        fail("--ssh-target must not contain NUL byte")

    if any(character.isspace() for character in ssh_target):
        fail(f"--ssh-target must not contain whitespace: {ssh_target}")

    if ssh_target.count("@") != 1:
        fail(
            "--ssh-target must use user@host with exactly one @, "
            f"got: {ssh_target}"
        )

    user_name, host_name = ssh_target.split("@", 1)

    if not user_name or not host_name:
        fail(f"--ssh-target must use non-empty user@host components: {ssh_target}")

    if "/" in user_name or "/" in host_name:
        fail(f"--ssh-target must use user@host, not a URL or path: {ssh_target}")


def resolve_single_app_dir(current_dir, app_name, path_value):
    if path_value:
        return Path(path_value).expanduser().resolve()

    if current_dir.name.lower() == app_name.lower():
        return current_dir

    return current_dir / app_name


def resolve_all_apps_base_dir(current_dir, server_name, path_value):
    if path_value:
        return Path(path_value).expanduser().resolve()

    if current_dir.name.lower() == server_name.lower():
        return current_dir

    return current_dir / server_name


def parse_yaml_scalar(raw_value):
    value = raw_value.strip()

    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid quoted YAML value: {error}") from error

    if value == "{}":
        return {}

    return value


def load_ansirover_http_auth(app_dir, app_name):
    yaml_path = app_dir / f"{app_name}.yaml"

    if not yaml_path.exists():
        raise ValueError(f"AnsiRover YAML not found: {yaml_path}")

    if not yaml_path.is_file():
        raise ValueError(f"AnsiRover YAML is not a file: {yaml_path}")

    http_auth = {}
    in_http_auth = False

    for line_number, raw_line in enumerate(yaml_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.rstrip()

        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if not line.startswith(" "):
            in_http_auth = line.startswith("httpAuth:")

            if not in_http_auth:
                continue

            raw_value = line.split(":", 1)[1].strip()

            if raw_value and raw_value != "{}":
                raise ValueError(f"httpAuth must be an object in {yaml_path}:{line_number}")

            continue

        if not in_http_auth:
            continue

        if not line.startswith("  ") or line.startswith("    "):
            raise ValueError(f"invalid httpAuth indentation in {yaml_path}:{line_number}")

        if ":" not in line:
            raise ValueError(f"invalid httpAuth value in {yaml_path}:{line_number}")

        key, raw_value = line.strip().split(":", 1)
        http_auth[key.strip()] = parse_yaml_scalar(raw_value)

    return http_auth


def read_password_map(path):
    sections = {}

    if not path.exists():
        return sections

    if not path.is_file():
        raise ValueError(f"password map is not a file: {path}")

    current_section = ""

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.rstrip()

        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if not line.startswith(" "):
            if not line.endswith(":"):
                raise ValueError(f"invalid password map section in {path}:{line_number}")

            current_section = line[:-1].strip()

            if not current_section:
                raise ValueError(f"empty password map section in {path}:{line_number}")

            sections.setdefault(current_section, {})
            continue

        if not line.startswith("  ") or line.startswith("    "):
            raise ValueError(f"invalid password map indentation in {path}:{line_number}")

        if not current_section:
            raise ValueError(f"password map key outside a section in {path}:{line_number}")

        if ":" not in line:
            raise ValueError(f"invalid password map value in {path}:{line_number}")

        raw_encrypted_password, raw_password = line.strip().split(":", 1)
        encrypted_password = parse_yaml_scalar(raw_encrypted_password)

        if not isinstance(encrypted_password, str) or not encrypted_password:
            raise ValueError(f"empty password map key in {path}:{line_number}")

        plaintext_password = parse_yaml_scalar(raw_password)

        if not isinstance(plaintext_password, str):
            raise ValueError(f"password map value must be a string in {path}:{line_number}")

        sections[current_section][encrypted_password] = plaintext_password

    return sections


def password_map_paths(app_dir):
    paths = []
    current_path = app_dir.resolve()

    while True:
        paths.append(current_path / ".password-map.yaml")

        if current_path.parent == current_path:
            break

        current_path = current_path.parent

    paths.append(Path.home() / ".password-map.yaml")
    paths.append(Path.home() / "bin" / ".password-map.yaml")

    unique_paths = []
    seen_paths = set()

    for path in paths:
        resolved_path = path.resolve()

        if resolved_path in seen_paths:
            continue

        seen_paths.add(resolved_path)
        unique_paths.append(path)

    return unique_paths


def find_apr1_password(app_dir, password_hash):
    for map_path in password_map_paths(app_dir):
        if not map_path.exists():
            continue

        password_map = read_password_map(map_path)
        apr1_passwords = password_map.get("apr1", {})

        if password_hash not in apr1_passwords:
            continue

        info("[auth] Using APR1 password mapping from: " + str(map_path))
        return apr1_passwords[password_hash]

    return ""


def verify_apr1_password(password_hash, plaintext_password):
    hash_parts = password_hash.split("$")

    if len(hash_parts) != 4 or hash_parts[0] != "" or hash_parts[1] != "apr1":
        return False

    if shutil.which("openssl") is None:
        raise ValueError("openssl is required to verify CapRover APR1 passwords")

    salt = hash_parts[2]
    completed_process = subprocess.run(
        ["openssl", "passwd", "-apr1", "-salt", salt, "-stdin"],
        input=plaintext_password + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed_process.returncode != 0:
        raise ValueError("openssl failed to verify the APR1 password")

    rebuilt_hash = completed_process.stdout.strip()
    return hmac.compare_digest(rebuilt_hash, password_hash)


def yaml_string(value):
    if value and re.fullmatch(r"[A-Za-z0-9_./:@+=,$\-]+", value):
        return value

    return json.dumps(value, ensure_ascii=False)


def get_git_root(app_dir):
    completed_process = subprocess.run(
        ["git", "-C", str(app_dir), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    if completed_process.returncode != 0:
        return None

    git_root = completed_process.stdout.strip()

    if not git_root:
        return None

    return Path(git_root)


def store_apr1_password(app_dir, password_hash, plaintext_password):
    git_root = get_git_root(app_dir)

    if git_root:
        map_path = git_root / ".password-map.yaml"
    else:
        map_path = app_dir / ".password-map.yaml"

    password_map = read_password_map(map_path)
    password_map.setdefault("apr1", {})[password_hash] = plaintext_password
    lines = []

    for section_name, entries in password_map.items():
        lines.append(section_name + ":")

        for encrypted_password, plain_password in entries.items():
            lines.append("  " + encrypted_password + ": " + yaml_string(plain_password))

    temporary_path = map_path.with_suffix(map_path.suffix + ".tmp")
    temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_path.chmod(0o600)
    temporary_path.replace(map_path)
    map_path.chmod(0o600)
    info("[auth] Saved verified APR1 password mapping to: " + str(map_path))


def get_apr1_basic_auth_credential(app_dir, http_auth):
    user = http_auth.get("user", "")
    password_hash = http_auth.get("passwordHashed", "")

    if not user and not password_hash:
        return None, ""

    if not isinstance(user, str) or not user:
        return None, "AnsiRover httpAuth.user is required when basic auth is configured"

    if not isinstance(password_hash, str) or not password_hash:
        return None, "AnsiRover httpAuth.passwordHashed is required when basic auth is configured"

    if not password_hash.startswith("$apr1$"):
        return None, "unsupported basic-auth hash format; only CapRover APR1 hashes are supported"

    plaintext_password = find_apr1_password(app_dir, password_hash)

    if plaintext_password:
        return (user, plaintext_password), ""

    try:
        plaintext_password = getpass.getpass(
            f"Basic-auth password for {user} ({app_dir.name}): "
        )
    except (EOFError, KeyboardInterrupt):
        return None, "basic-auth password prompt was cancelled"

    if not plaintext_password:
        return None, "basic-auth password must not be empty"

    try:
        password_is_valid = verify_apr1_password(password_hash, plaintext_password)
    except ValueError as error:
        return None, str(error)

    if not password_is_valid:
        return None, "entered basic-auth password does not match the APR1 hash"

    store_apr1_password(app_dir, password_hash, plaintext_password)
    return (user, plaintext_password), ""


def load_caprover_config():
    config_path = Path.home() / ".config" / "configstore" / "caprover.json"

    if not config_path.exists():
        fail(f"CapRover CLI config not found: {config_path}")

    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"invalid CapRover CLI config JSON: {config_path}: {error}")


def find_captain_url_by_machine(config, server_name):
    cap_machines = config.get("CapMachines", [])

    if not isinstance(cap_machines, list):
        fail("CapRover CLI config does not contain CapMachines list")

    for machine in cap_machines:
        if not isinstance(machine, dict):
            continue

        if machine.get("name") != server_name:
            continue

        base_url = str(machine.get("baseUrl", ""))

        if not base_url:
            fail(f"CapRover machine has empty baseUrl: {server_name}")

        return base_url

    fail(f"CapRover machine not found in config: {server_name}")


def get_caprover_root_domain(server_name):
    captain_url = find_captain_url_by_machine(load_caprover_config(), server_name)
    host = urlparse(captain_url).hostname

    if not host:
        host = re.sub(r"^https?://", "", captain_url)
        host = host.split("/", 1)[0]
        host = host.split(":", 1)[0]

    if host.startswith("captain."):
        host = host[len("captain."):]

    if not host:
        fail(f"could not derive CapRover root domain from machine URL: {captain_url}")

    return host


def derive_ssh_target_from_captain_url(captain_url):
    host = urlparse(captain_url).hostname

    if not host:
        host = re.sub(r"^https?://", "", captain_url)
        host = host.split("/", 1)[0]
        host = host.split(":", 1)[0]

    if host.startswith("captain."):
        host = host[len("captain."):]

    if not host:
        fail(f"could not derive SSH host from CapRover URL: {captain_url}")

    return f"root@{host}"


def resolve_ssh_target(server_name, override_ssh_target):
    if override_ssh_target:
        validate_ssh_target(override_ssh_target)
        return override_ssh_target

    captain_url = find_captain_url_by_machine(load_caprover_config(), server_name)
    info(f"[config] CapRover URL for {server_name}: {captain_url}")
    return derive_ssh_target_from_captain_url(captain_url)


def run_caprover_api(server_name, api_path, method, data):
    command = [
        "npx",
        "--yes",
        "caprover",
        "api",
        "--caproverName",
        server_name,
        "--path",
        api_path,
        "--method",
        method,
        "--data",
        json.dumps(data, separators=(",", ":")),
    ]

    env_name = get_captain_password_env_name(server_name)
    password = require_captain_password(server_name)

    info(f"[caprover api] Using password from environment variable: {env_name}")
    command.extend(["-p", password])

    with tempfile.NamedTemporaryFile(prefix="caprover-api-", suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)

    command.extend(["-o", str(output_path)])

    try:
        info(f"[caprover api] {method} {api_path}")
        info(f"[caprover api] Server: {server_name}")

        completed_process = subprocess.run(
            command,
            text=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=False,
        )

        if completed_process.returncode != 0:
            fail(f"CapRover CLI failed for {method} {api_path}")

        response_text = output_path.read_text(encoding="utf-8")

        if not response_text.strip():
            fail(f"CapRover API returned empty output for {method} {api_path}")

        try:
            response = json.loads(response_text)
        except json.JSONDecodeError:
            print(response_text[:2000], file=sys.stderr)
            fail(f"CapRover API returned non-JSON output for {method} {api_path}")

        if isinstance(response, dict) and response.get("status") not in {None, 100, 101}:
            print(json.dumps(response, indent=2, ensure_ascii=False), file=sys.stderr)
            fail(f"CapRover API returned unexpected status for {method} {api_path}: {response.get('status')}")

        if isinstance(response, dict) and "data" in response:
            return response["data"]

        return response
    finally:
        with contextlib.suppress(FileNotFoundError):
            output_path.unlink()


def get_app_definitions(server_name):
    response = run_caprover_api(server_name, APP_DEFINITIONS_API_PATH, "GET", {})

    if isinstance(response, dict):
        app_definitions = response.get("appDefinitions")

        if isinstance(app_definitions, list):
            return app_definitions

        nested_data = response.get("data")

        if isinstance(nested_data, dict):
            app_definitions = nested_data.get("appDefinitions")

            if isinstance(app_definitions, list):
                return app_definitions

    fail("CapRover appDefinitions response does not contain appDefinitions list")


def find_app_definition(app_definitions, app_name):
    for app_definition in app_definitions:
        if not isinstance(app_definition, dict):
            continue

        remote_app_name = str(app_definition.get("appName", ""))

        if remote_app_name.lower() == app_name.lower():
            return app_definition

    fail(f"app not found in CapRover: {app_name}")


def parse_instance_count(value):
    if isinstance(value, bool):
        return 1

    if isinstance(value, int) and value >= 0:
        return value

    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        return int(value.strip())

    fail(f"app instanceCount is invalid: {value!r}")


def get_deployed_image_name(app_definition):
    deployed_version = app_definition.get("deployedVersion")
    versions = app_definition.get("versions")

    if not isinstance(versions, list):
        return ""

    for version in versions:
        if not isinstance(version, dict):
            continue

        if str(version.get("version")) != str(deployed_version):
            continue

        return str(version.get("deployedImageName", ""))

    return ""


def run_remote_bash(ssh_target, script, timeout_seconds):
    command = [
        "ssh",
        "-n",
        "-o",
        "ConnectTimeout=30",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        ssh_target,
        "bash -lc " + shlex.quote(script),
    ]

    try:
        completed_process = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return "", f"SSH command timed out after {timeout_seconds}s", 124

    if completed_process.returncode != 0:
        error_text = completed_process.stderr.strip()

        if not error_text:
            error_text = completed_process.stdout.strip()

        return completed_process.stdout, error_text, completed_process.returncode

    return completed_process.stdout, "", 0


def inspect_containers(ssh_target, app_name, timeout_seconds):
    service_name = f"srv-captain--{app_name}"
    script = (
        "docker ps -a "
        f"--filter {shlex.quote('name=' + service_name)} "
        "--format '{{.ID}}' "
        "| while IFS= read -r container_id; do "
        "[ -n \"$container_id\" ] || continue; "
        "status=$(docker inspect --format '{{.State.Status}}' \"$container_id\"); "
        "health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \"$container_id\"); "
        "name=$(docker inspect --format '{{.Name}}' \"$container_id\" | sed 's#^/##'); "
        "printf '%s|%s|%s|%s\\n' \"$container_id\" \"$name\" \"$status\" \"$health\"; "
        "done"
    )
    output, error_text, return_code = run_remote_bash(ssh_target, script, timeout_seconds)

    if return_code != 0:
        return [], error_text

    containers = []

    for raw_line in output.splitlines():
        parts = raw_line.split("|", 3)

        if len(parts) != 4:
            continue

        containers.append(
            {
                "id": parts[0],
                "name": parts[1],
                "status": parts[2],
                "health": parts[3],
            }
        )

    return containers, ""


def check_https_root(app_name, root_domain, app_definition, app_dir, http_auth, http_timeout):
    if app_definition.get("notExposeAsWebApp", False):
        return {
            "url": "",
            "statusCode": "SKIPPED_NOT_WEB_APP",
            "error": "",
            "ok": True,
        }

    url = f"https://{app_name}.{root_domain}/"
    credential, auth_error = get_apr1_basic_auth_credential(app_dir, http_auth)

    if auth_error:
        return {
            "url": url,
            "statusCode": "AUTH_CREDENTIAL_UNAVAILABLE",
            "error": auth_error,
            "ok": False,
        }

    command = [
        "curl",
        "--silent",
        "--show-error",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--connect-timeout",
        str(http_timeout),
        "--max-time",
        str(http_timeout),
    ]

    if credential is not None:
        user, password = credential
        command.extend(["--user", user + ":" + password])

    command.append(url)

    completed_process = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    status_code = completed_process.stdout.strip()

    if completed_process.returncode != 0:
        return {
            "url": url,
            "statusCode": status_code or "CURL_FAILED",
            "error": completed_process.stderr.strip() or "curl failed",
            "ok": False,
        }

    is_success = bool(re.fullmatch(r"[23][0-9][0-9]", status_code))
    error = ""

    if not is_success:
        error = "expected an HTTP 2xx or 3xx response"

    return {
        "url": url,
        "statusCode": status_code,
        "error": error,
        "ok": is_success,
    }


def evaluate_app(ssh_target, root_domain, app_definition, app_dir, ssh_timeout, http_timeout):
    app_name = str(app_definition.get("appName", ""))
    expected_count = parse_instance_count(app_definition.get("instanceCount", 1))
    image_name = get_deployed_image_name(app_definition)

    try:
        http_auth = load_ansirover_http_auth(app_dir, app_name)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return {
            "appName": app_name,
            "expectedCount": expected_count,
            "runningCount": 0,
            "containerHealth": "UNKNOWN",
            "http": {
                "url": "",
                "statusCode": "ANSIROVER_YAML_UNAVAILABLE",
                "error": str(error),
                "ok": False,
            },
            "image": image_name,
            "status": "UNKNOWN",
            "error": str(error),
        }

    if expected_count == 0:
        return {
            "appName": app_name,
            "expectedCount": expected_count,
            "runningCount": 0,
            "containerHealth": "SKIPPED_STOPPED",
            "http": {
                "url": "",
                "statusCode": "SKIPPED_STOPPED",
                "error": "",
                "ok": True,
            },
            "image": image_name,
            "status": "STOPPED",
            "error": "instanceCount is 0",
        }

    http_result = check_https_root(
        app_name=app_name,
        root_domain=root_domain,
        app_definition=app_definition,
        app_dir=app_dir,
        http_auth=http_auth,
        http_timeout=http_timeout,
    )

    containers, ssh_error = inspect_containers(ssh_target, app_name, ssh_timeout)

    if ssh_error:
        return {
            "appName": app_name,
            "expectedCount": expected_count,
            "runningCount": 0,
            "containerHealth": "UNKNOWN",
            "http": http_result,
            "image": image_name,
            "status": "UNKNOWN",
            "error": ssh_error,
        }

    running_containers = [container for container in containers if container["status"] == "running"]
    unhealthy_containers = [
        container
        for container in running_containers
        if container["health"] not in {"none", "healthy"}
    ]
    running_count = len(running_containers)
    container_health = "HEALTHY"
    container_error = ""

    if running_count < expected_count:
        container_health = "UNHEALTHY"
        container_error = f"expected at least {expected_count} running containers, found {running_count}"
    elif unhealthy_containers:
        container_health = "UNHEALTHY"
        container_error = "one or more running containers have a non-healthy Docker health state"

    status = "HEALTHY"
    errors = []

    if container_health != "HEALTHY":
        status = "UNHEALTHY"
        errors.append(container_error)

    if not http_result["ok"]:
        status = "UNHEALTHY"
        errors.append(http_result["error"])

    return {
        "appName": app_name,
        "expectedCount": expected_count,
        "runningCount": running_count,
        "containerHealth": container_health,
        "http": http_result,
        "image": image_name,
        "status": status,
        "error": "; ".join(error for error in errors if error),
    }


def parse_ignore_names(raw_value):
    if raw_value is None:
        return set()

    return {
        value.strip().lower()
        for value in raw_value.split(",")
        if value.strip()
    }


def print_table(results):
    headers = ["APP", "EXPECTED", "RUNNING", "CONTAINERS", "HTTPS", "STATUS"]
    rows = []

    for app_result in results:
        rows.append(
            {
                "APP": app_result["appName"],
                "EXPECTED": str(app_result["expectedCount"]),
                "RUNNING": str(app_result["runningCount"]),
                "CONTAINERS": app_result["containerHealth"],
                "HTTPS": str(app_result["http"]["statusCode"]),
                "STATUS": app_result["status"],
            }
        )

    widths = {header: len(header) for header in headers}

    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(row[header]))

    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))

    for row in rows:
        print("  ".join(row[header].ljust(widths[header]) for header in headers))


def print_stopped_apps_warning(stopped_results):
    if not stopped_results:
        return

    print("", file=sys.stderr)
    print("########################################################################################", file=sys.stderr)
    print("#                                                                                      #", file=sys.stderr)
    print("#  WARNING: ONE OR MORE CAPROVER APPS ARE INTENTIONALLY SCALED TO ZERO                #", file=sys.stderr)
    print("#                                                                                      #", file=sys.stderr)
    print("#  This health-check run exits successfully, but stopped apps are not expected here.  #", file=sys.stderr)
    print("#                                                                                      #", file=sys.stderr)
    print("########################################################################################", file=sys.stderr)

    for app_result in stopped_results:
        print("# STOPPED APP: " + app_result["appName"], file=sys.stderr)

    print("########################################################################################", file=sys.stderr)


def print_result_values(results, single_app):
    healthy_count = sum(1 for app_result in results if app_result["status"] == "HEALTHY")
    unhealthy_count = sum(1 for app_result in results if app_result["status"] == "UNHEALTHY")
    unknown_count = sum(1 for app_result in results if app_result["status"] == "UNKNOWN")
    stopped_count = sum(1 for app_result in results if app_result["status"] == "STOPPED")

    result("HEALTHY_APP_COUNT=" + str(healthy_count))
    result("UNHEALTHY_APP_COUNT=" + str(unhealthy_count))
    result("UNKNOWN_APP_COUNT=" + str(unknown_count))
    result("STOPPED_APP_COUNT=" + str(stopped_count))

    for app_result in results:
        result("APP_STATUS=" + app_result["appName"] + "=" + app_result["status"])

    if not single_app:
        return

    app_result = results[0]
    result("APP_NAME=" + app_result["appName"])
    result("APP_HEALTH=" + app_result["status"])
    result("EXPECTED_INSTANCE_COUNT=" + str(app_result["expectedCount"]))
    result("RUNNING_CONTAINER_COUNT=" + str(app_result["runningCount"]))
    result("CONTAINER_HEALTH=" + app_result["containerHealth"])
    result("HTTPS_STATUS=" + str(app_result["http"]["statusCode"]))


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Check CapRover app container health and the HTTPS root endpoint. "
            "Stopped apps emit a warning but do not fail the run."
        )
    )

    parser.add_argument("server_name", help="CapRover CLI machine name, e.g. storage; @ is not allowed")
    parser.add_argument("target", help="CapRover app name or all")
    parser.add_argument(
        "path",
        nargs="?",
        help=(
            "AnsiRover app folder for one app, or server backup folder for all. "
            "Defaults to the current folder when it is named after the app/server; "
            "otherwise ./<app> or ./<server>."
        ),
    )
    parser.add_argument(
        "--ssh-target",
        help="override SSH target; must use user@host, e.g. --ssh-target=root@example.com",
    )
    parser.add_argument("--ssh-timeout", type=int, default=60, help="remote Docker inspection timeout in seconds; default: 60")
    parser.add_argument("--http-timeout", type=int, default=20, help="curl connect and total timeout in seconds; default: 20")
    parser.add_argument("--ignore", help="all mode only: comma-separated app names to skip")
    parser.add_argument("--json", action="store_true", help="print check results as JSON")
    parser.add_argument("--no-sort", action="store_true", help="all mode only: keep CapRover API order")

    return parser.parse_args()


def main():
    args = parse_args()
    validate_caprover_machine_name(args.server_name)
    require_captain_password(args.server_name)

    if args.ssh_timeout < 1:
        fail("--ssh-timeout must be >= 1")

    if args.http_timeout < 1:
        fail("--http-timeout must be >= 1")

    all_mode = args.target.lower() == "all"
    ignore_names = parse_ignore_names(args.ignore)

    if not all_mode:
        validate_simple_name("app name", args.target)

        if ignore_names:
            fail("--ignore can only be used when target is all")

    current_dir = Path.cwd()

    if all_mode:
        base_dir = resolve_all_apps_base_dir(current_dir, args.server_name, args.path)
        info("[plan] AnsiRover server backup path: " + str(base_dir))
    else:
        app_dir = resolve_single_app_dir(current_dir, args.target, args.path)
        info("[plan] AnsiRover app path: " + str(app_dir))

    info("[plan] Action: CapRover AnsiRover app health check")
    info("[plan] Server: " + args.server_name)
    info("[plan] Target: " + args.target)

    app_definitions = get_app_definitions(args.server_name)

    if all_mode:
        selected_definitions = [
            app_definition
            for app_definition in app_definitions
            if isinstance(app_definition, dict)
            and str(app_definition.get("appName", "")).lower() not in ignore_names
        ]

        if not args.no_sort:
            selected_definitions.sort(key=lambda app_definition: str(app_definition.get("appName", "")).lower())

        if not selected_definitions:
            fail("no apps selected for health check")
    else:
        selected_definitions = [find_app_definition(app_definitions, args.target)]

    ssh_target = resolve_ssh_target(args.server_name, args.ssh_target)
    root_domain = get_caprover_root_domain(args.server_name)
    info("[plan] SSH target: " + ssh_target)
    info("[plan] Root domain: " + root_domain)

    results = []

    for app_definition in selected_definitions:
        app_name = str(app_definition.get("appName", ""))

        if all_mode:
            app_dir = base_dir / app_name

        info("[check] App: " + app_name)
        info("[check] AnsiRover app path: " + str(app_dir))
        results.append(
            evaluate_app(
                ssh_target=ssh_target,
                root_domain=root_domain,
                app_definition=app_definition,
                app_dir=app_dir,
                ssh_timeout=args.ssh_timeout,
                http_timeout=args.http_timeout,
            )
        )

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print_table(results)

    print_result_values(results, single_app=not all_mode)

    stopped_results = [app_result for app_result in results if app_result["status"] == "STOPPED"]
    print_stopped_apps_warning(stopped_results)

    failed_results = [
        app_result
        for app_result in results
        if app_result["status"] in {"UNHEALTHY", "UNKNOWN"}
    ]

    if failed_results:
        for app_result in failed_results:
            info("[failed] " + app_result["appName"] + ": " + app_result["error"])

        raise SystemExit(1)


if __name__ == "__main__":
    main()
