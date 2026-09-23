#!/usr/bin/env python3

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def result(message):
    print(message, flush=True)


def get_retry_delays():
    return [2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0]


def get_max_attempts():
    return len(get_retry_delays()) + 1


def sleep_before_retry(attempt_index, failure_label):
    retry_delays = get_retry_delays()
    delay_seconds = retry_delays[attempt_index - 1]

    info(f"[retry] {failure_label}")
    info(f"[retry] waiting {delay_seconds}s before next attempt")
    time.sleep(delay_seconds)


def get_ssh_command_prefix(ssh_target):
    return [
        "ssh",
        "-n",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "TCPKeepAlive=yes",
        "-o",
        "ConnectTimeout=30",
        ssh_target,
    ]


def get_captain_password_env_name(server_name):
    normalized = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_CAPTAIN_PASSWORD"


def require_captain_password(server_name):
    env_name = get_captain_password_env_name(server_name)
    password = os.environ.get(env_name, "")

    if not password:
        fail(f"CapRover password env var is required but not set: {env_name}")

    return password


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


def validate_caprover_machine_name(server_name):
    validate_simple_name("server name", server_name)

    if "@" in server_name:
        fail(
            "CapRover machine name must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )


CURRENT_APP_NAME = ""


def get_caprover_api_app_name(api_path, data):
    if isinstance(data, dict) and data.get("appName"):
        return str(data.get("appName"))

    match = re.search(r"/appData/([^/?]+)", str(api_path))

    if match:
        return match.group(1)

    return CURRENT_APP_NAME


def run_caprover_api(server_name, api_path, method, data):
    base_command = [
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
    base_command.extend(["-p", password])

    max_attempts = get_max_attempts()
    last_error = ""

    for attempt_index in range(1, max_attempts + 1):
        with tempfile.NamedTemporaryFile(prefix="caprover-api-", suffix=".json", delete=False) as output_file:
            output_path = output_file.name

        command = list(base_command)
        command.extend(["-o", output_path])

        try:
            info(f"[caprover api] {method} {api_path}")
            info(f"[caprover api] Server: {server_name}")
            api_app_name = get_caprover_api_app_name(api_path, data)

            if api_app_name:
                info(f"[caprover api] App: {api_app_name}")

            info(f"[caprover api] Attempt: {attempt_index}/{max_attempts}")

            completed_process = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            if completed_process.stdout:
                print(completed_process.stdout, file=sys.stderr, end="")

            if completed_process.stderr:
                print(completed_process.stderr, file=sys.stderr, end="")

            if completed_process.returncode != 0:
                raise RuntimeError(f"CapRover CLI failed for {method} {api_path}")

            response_text = Path(output_path).read_text(encoding="utf-8")

            if not response_text.strip():
                raise RuntimeError(f"CapRover API returned empty output for {method} {api_path}")

            try:
                response = json.loads(response_text)
            except json.JSONDecodeError:
                print(response_text[:2000], file=sys.stderr)
                raise RuntimeError(f"CapRover API returned non-JSON output for {method} {api_path}")

            if isinstance(response, dict) and response.get("status") not in [None, 100, 101]:
                print(json.dumps(response, indent=2, ensure_ascii=False), file=sys.stderr)
                raise RuntimeError(
                    f"CapRover API returned unexpected status for {method} {api_path}: {response.get('status')}"
                )

            if isinstance(response, dict) and "data" in response:
                return response["data"]

            return response

        except Exception as error:
            last_error = str(error)

            if attempt_index >= max_attempts:
                fail(last_error)

            sleep_before_retry(
                attempt_index,
                f"CapRover API attempt {attempt_index}/{max_attempts} failed: {last_error}",
            )

        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(output_path)

    fail(last_error or f"CapRover API failed for {method} {api_path}")


def extract_app_definitions_object(response):
    if isinstance(response, dict):
        app_definitions = response.get("appDefinitions")

        if isinstance(app_definitions, list):
            return response

        nested_data = response.get("data")

        if isinstance(nested_data, dict) and isinstance(nested_data.get("appDefinitions"), list):
            return nested_data

    fail("CapRover appDefinitions response does not contain appDefinitions list")


def find_app_definition(app_definitions, app_name):
    for app_definition in app_definitions:
        if not isinstance(app_definition, dict):
            continue

        if str(app_definition.get("appName", "")).lower() == app_name.lower():
            return app_definition

    available_app_names = [
        app_definition.get("appName", "")
        for app_definition in app_definitions
        if isinstance(app_definition, dict) and app_definition.get("appName")
    ]

    preview = ", ".join(sorted(available_app_names)[:50])

    if len(available_app_names) > 50:
        preview += ", ..."

    fail(
        "app not found in CapRover appDefinitions: "
        + app_name
        + ("\nAvailable apps: " + preview if preview else "")
    )


def get_deployed_image_name(app_definition):
    deployed_version = app_definition.get("deployedVersion")
    versions = app_definition.get("versions")

    if deployed_version is None or deployed_version == "":
        return ""

    if not isinstance(versions, list):
        return ""

    for version_item in versions:
        if not isinstance(version_item, dict):
            continue

        if str(version_item.get("version")) != str(deployed_version):
            continue

        deployed_image_name = version_item.get("deployedImageName", "")

        if deployed_image_name is None:
            return ""

        return str(deployed_image_name)

    return ""


def get_instance_count(app_definition):
    instance_count = app_definition.get("instanceCount")

    if isinstance(instance_count, bool):
        return 1

    if isinstance(instance_count, int):
        return instance_count

    if isinstance(instance_count, str) and re.fullmatch(r"[0-9]+", instance_count.strip()):
        return int(instance_count.strip())

    return 1


def get_named_volume_names(app_definition):
    volumes = app_definition.get("volumes")

    if not isinstance(volumes, list):
        return []

    names = []

    for volume in volumes:
        if not isinstance(volume, dict):
            continue

        volume_name = volume.get("volumeName")

        if not volume_name:
            continue

        names.append(str(volume_name))

    return names


def get_bind_mounts(app_definition):
    volumes = app_definition.get("volumes")

    if not isinstance(volumes, list):
        return []

    bind_mounts = []

    for volume in volumes:
        if not isinstance(volume, dict):
            continue

        host_path = volume.get("hostPath")

        if not host_path:
            continue

        bind_mounts.append(
            {
                "containerPath": volume.get("containerPath", ""),
                "hostPath": host_path,
            }
        )

    return bind_mounts


def write_bind_mounts_warning_file(app_dir, app_name, app_definition):
    warning_file_path = app_dir / (app_name + ".BIND-MOUNTS.txt")
    bind_mounts = get_bind_mounts(app_definition)

    if not bind_mounts:
        if warning_file_path.exists():
            warning_file_path.unlink()
        return

    lines = [
        "WARNING: BIND MOUNTS WERE NOT BACKED UP",
        "",
        "This app has host bind mounts configured in CapRover/Docker.",
        "This backup saved the app definition, AnsiRover YAML, sidecars, custom images, and named Docker volumes.",
        "It did not copy host bind-mount source paths.",
        "",
        "Back up these host paths separately before relying on this backup for full disaster recovery.",
        "",
        "App: " + app_name,
        "",
        "Host path -> Container path",
    ]

    for bind_mount in bind_mounts:
        host_path = str(bind_mount.get("hostPath", ""))
        container_path = str(bind_mount.get("containerPath", ""))

        if container_path:
            lines.append("- " + host_path + " -> " + container_path)
        else:
            lines.append("- " + host_path)

    lines.append("")

    warning_file_path.write_text("\n".join(lines), encoding="utf-8")
    info("[warn] bind mount warning file: " + str(warning_file_path))


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


def is_external_pullable_docker_image(image_reference, caprover_root_domain):
    image_reference = str(image_reference or "").strip()

    if not image_reference:
        return False

    first_path_component = image_reference.split("/", 1)[0]

    if "/" not in image_reference:
        return True

    caprover_registry = "registry." + caprover_root_domain.lower()
    return first_path_component.lower() != caprover_registry


def run_command(command, cwd):
    info("[run] cwd: " + str(cwd))
    info("[run] " + " ".join(command))

    completed_process = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        check=False,
    )

    if completed_process.returncode != 0:
        fail("command failed with exit code " + str(completed_process.returncode) + ": " + " ".join(command))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_single_app_target_dir(current_dir, app_name):
    if current_dir.name.lower() == app_name.lower():
        return current_dir

    return current_dir / app_name


def resolve_all_apps_base_dir(current_dir, server_name):
    if current_dir.name.lower() == server_name.lower():
        return current_dir

    return current_dir / server_name


def download_one_app(server_name, app_definition, app_dir, with_json, with_image, include_dockerhub, with_volumes, force, caprover_root_domain):
    app_name = app_definition.get("appName")

    if not app_name:
        fail("app definition has no appName")

    app_name = str(app_name)

    global CURRENT_APP_NAME
    CURRENT_APP_NAME = app_name

    app_dir.mkdir(parents=True, exist_ok=True)

    json_path = app_dir / f"{app_name}.json"
    yaml_path = app_dir / f"{app_name}.yaml"

    info("")
    info("=" * 88)
    info("== app: " + app_name + " ==")
    info("=" * 88)
    info("[app: " + app_name + "] Directory: " + str(app_dir))
    info("[app: " + app_name + "] YAML: " + str(yaml_path))

    if with_json:
        info("[app: " + app_name + "] JSON: " + str(json_path))
        write_json(json_path, app_definition)
        converter_json_path = json_path
    else:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temporary_json_file:
            converter_json_path = Path(temporary_json_file.name)

        write_json(converter_json_path, app_definition)

    try:
        run_command(
            [
                "caprover-app-definition-json-to-ansirover",
                str(converter_json_path),
                str(yaml_path),
                "--force",
            ],
            cwd=app_dir,
        )
    finally:
        if not with_json:
            converter_json_path.unlink(missing_ok=True)

    if with_image:
        deployed_image_name = get_deployed_image_name(app_definition)

        if not deployed_image_name:
            fail("cannot download image for app without deployedImageName: " + app_name)

        if is_external_pullable_docker_image(deployed_image_name, caprover_root_domain) and not include_dockerhub:
            info("[image] Externally pullable image detected; skipping image archive.")
            info("[image] Image: " + deployed_image_name)
            info("[image] Use --include-dockerhub to archive this image anyway.")
        else:
            image_archive_path = app_dir / f"{app_name}.image.tar.gz"

            if image_archive_path.exists() and not force:
                info("[image] Archive already exists; skipping image download: " + str(image_archive_path))
            else:
                command = [
                    "caprover-image-download-backup",
                    server_name,
                    deployed_image_name,
                    str(image_archive_path),
                ]

                if force:
                    command.append("--force")

                run_command(command, cwd=app_dir)

    if with_volumes:
        named_volume_names = get_named_volume_names(app_definition)
        bind_mounts = get_bind_mounts(app_definition)

        if bind_mounts:
            for bind_mount in bind_mounts:
                info(
                    "[warn] bind mount is not downloaded by volume backup: "
                    + str(bind_mount.get("hostPath", ""))
                    + " -> "
                    + str(bind_mount.get("containerPath", ""))
                )

        if named_volume_names:
            selected_volume_names = []

            for volume_name in named_volume_names:
                archive_path = app_dir / f"{app_name}.{volume_name}.tar.gz"

                if archive_path.exists() and not force:
                    info("[volumes] Archive already exists; skipping volume download: " + str(archive_path))
                    continue

                selected_volume_names.append(volume_name)

            if selected_volume_names:
                command = [
                    "caprover-volume-download-backup",
                    server_name,
                    app_name,
                ]

                command.extend(selected_volume_names)

                command.extend(
                    [
                        "--output-dir",
                        str(app_dir),
                        "--instance-count",
                        str(get_instance_count(app_definition)),
                    ]
                )

                if force:
                    command.append("--force")

                run_command(command, cwd=app_dir)
            else:
                info("[volumes] All named volume archives already exist; skipping volume download for app: " + app_name)
        else:
            info("[volumes] No named Docker volumes to download for app: " + app_name)

    write_bind_mounts_warning_file(app_dir, app_name, app_definition)

    result("APP_BACKUP_DIR=" + str(app_dir))


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


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Download CapRover app definitions into AnsiRover YAML backup folders. "
            "Optionally downloads image and named Docker volume archives."
        )
    )

    parser.add_argument("server_name", help="CapRover CLI machine name, e.g. frginbox or storage; @ is not allowed")
    parser.add_argument("target", nargs="?", help="app name or all")
    parser.add_argument(
        "path",
        nargs="?",
        help=(
            "optional output path. For a single app, this is the app backup folder. "
            "For all, this is the server backup folder."
        ),
    )
    parser.add_argument("--with-image", action="store_true", help="download deployed image archive, except externally pullable images by default")
    parser.add_argument("--with-json", action="store_true", help="save CapRover JSON definitions alongside the YAML backup")
    parser.add_argument("--include-dockerhub", action="store_true", help="with --with-image, also archive externally pullable images")
    parser.add_argument("--with-volumes", action="store_true", help="download named Docker volume archives")
    parser.add_argument("--limit", type=int, default=0, help="only for all: limit number of apps for a smoke test")
    parser.add_argument("--ignore", help="only for all: comma-separated app names to skip, e.g. app1,app2")
    parser.add_argument("--force", action="store_true", help="overwrite existing image and volume archives")

    return parser.parse_args()


def infer_target_from_current_folder(args):
    target = Path.cwd().name

    try:
        confirmation = input(
            f"No app name provided. Download backup for {target} into the current folder? [Y/n] "
        ).strip().lower()
    except EOFError:
        fail("confirmation is required when no app name is provided")

    if confirmation not in {"", "y", "yes"}:
        fail("download cancelled")

    args.target = target


def main():
    args = parse_args()

    validate_caprover_machine_name(args.server_name)
    require_captain_password(args.server_name)

    if args.target is None:
        infer_target_from_current_folder(args)

    global CURRENT_APP_NAME

    if args.target != "all":
        validate_simple_name("app name", args.target)
        CURRENT_APP_NAME = args.target
    else:
        CURRENT_APP_NAME = ""

    ignore_names = parse_ignore_names(args.ignore)

    if args.target != "all" and ignore_names:
        fail("--ignore can only be used when target is all")

    current_dir = Path.cwd()

    info("[plan] Action: AnsiRover download backup")
    info("[plan] Server: " + args.server_name)
    info("[plan] Target: " + args.target)

    if ignore_names:
        info("[plan] Ignore: " + ", ".join(sorted(ignore_names)))

    info("[plan] With image: " + str(args.with_image))
    info("[plan] With JSON: " + str(args.with_json))
    info("[plan] Include externally pullable images: " + str(args.include_dockerhub))
    info("[plan] With volumes: " + str(args.with_volumes))
    info("[plan] Force overwrite archives: " + str(args.force))
    info("[plan] Current directory: " + str(current_dir))

    if args.path:
        info("[plan] Explicit output path: " + str(Path(args.path).expanduser()))

    response = run_caprover_api(
        server_name=args.server_name,
        api_path="/user/apps/appDefinitions",
        method="GET",
        data={},
    )

    app_definitions_object = extract_app_definitions_object(response)
    app_definitions = app_definitions_object.get("appDefinitions", [])

    caprover_root_domain = ""

    if args.with_image:
        caprover_root_domain = get_caprover_root_domain(args.server_name)

    if args.target == "all":
        if args.path:
            base_dir = Path(args.path).expanduser().resolve()
        else:
            base_dir = resolve_all_apps_base_dir(current_dir, args.server_name)

        base_dir.mkdir(parents=True, exist_ok=True)

        info("[all] Server backup directory: " + str(base_dir))
        result("SERVER_BACKUP_DIR=" + str(base_dir))

        if args.with_json:
            server_json_path = base_dir / f"{args.server_name}.json"
            write_json(server_json_path, app_definitions_object)
            info("[all] Server JSON: " + str(server_json_path))
            result("SERVER_JSON_FILE=" + str(server_json_path))

        processed_count = 0

        sorted_app_definitions = sorted(
            [
                app_definition
                for app_definition in app_definitions
                if isinstance(app_definition, dict)
            ],
            key=lambda app_definition: str(app_definition.get("appName", "")).lower(),
        )

        for app_definition in sorted_app_definitions:
            app_name = app_definition.get("appName")

            if not app_name:
                continue

            app_name = str(app_name)

            if app_name.lower() in ignore_names:
                info("[all] Ignoring app: " + app_name)
                continue

            app_dir = base_dir / app_name

            download_one_app(
                server_name=args.server_name,
                app_definition=app_definition,
                app_dir=app_dir,
                with_json=args.with_json,
                with_image=args.with_image,
                include_dockerhub=args.include_dockerhub,
                with_volumes=args.with_volumes,
                force=args.force,
                caprover_root_domain=caprover_root_domain,
            )

            processed_count += 1

            if args.limit > 0 and processed_count >= args.limit:
                info("[all] Reached --limit " + str(args.limit))
                break

        result("APP_BACKUP_COUNT=" + str(processed_count))
        info("[done] AnsiRover all-apps download backup completed.")
        return

    app_definition = find_app_definition(app_definitions, args.target)
    app_name = str(app_definition.get("appName"))

    if args.path:
        app_dir = Path(args.path).expanduser().resolve()
    else:
        app_dir = resolve_single_app_target_dir(current_dir, app_name)

    download_one_app(
        server_name=args.server_name,
        app_definition=app_definition,
        app_dir=app_dir,
        with_json=args.with_json,
        with_image=args.with_image,
        include_dockerhub=args.include_dockerhub,
        with_volumes=args.with_volumes,
        force=args.force,
        caprover_root_domain=caprover_root_domain,
    )

    info("[done] AnsiRover single-app download backup completed.")


if __name__ == "__main__":
    main()
