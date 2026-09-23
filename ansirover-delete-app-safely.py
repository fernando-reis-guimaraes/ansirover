#!/usr/bin/env python3

import argparse
import contextlib
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse


DELETE_APP_API_PATH = "/user/apps/appDefinitions/delete"
APP_DEFINITIONS_API_PATH = "/user/apps/appDefinitions"


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


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

    if not password:
        fail(f"CapRover password env var is required but not set: {env_name}")

    return password


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
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )


def resolve_app_dir(current_dir, app_name, path_value):
    if path_value:
        return Path(path_value).expanduser().resolve()

    if current_dir.name.lower() == app_name.lower():
        return current_dir

    return current_dir / app_name


def run_caprover_api(server_name, api_path, method, data, allowed_statuses, return_full_response=False):
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
        output_path = output_file.name

    command.extend(["-o", output_path])

    try:
        info(f"[caprover api] {method} {api_path}")
        info(f"[caprover api] Server: {server_name}")

        completed_process = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if completed_process.returncode != 0:
            if completed_process.stdout:
                print(completed_process.stdout, file=sys.stderr, end="")

            if completed_process.stderr:
                print(completed_process.stderr, file=sys.stderr, end="")

            fail(f"CapRover CLI failed for {method} {api_path}")

        if completed_process.stderr:
            print(completed_process.stderr, file=sys.stderr, end="")

        response_text = Path(output_path).read_text(encoding="utf-8")

        if not response_text.strip():
            fail(f"CapRover API returned empty output for {method} {api_path}")

        try:
            response = json.loads(response_text)
        except json.JSONDecodeError:
            print(response_text[:2000], file=sys.stderr)
            fail(f"CapRover API returned non-JSON output for {method} {api_path}")

        status = response.get("status") if isinstance(response, dict) else None

        if status not in allowed_statuses:
            print(json.dumps(response, indent=2, ensure_ascii=False), file=sys.stderr)
            fail(f"CapRover API returned unexpected status for {method} {api_path}: {status}")

        if return_full_response:
            return response

        if isinstance(response, dict) and "data" in response:
            return response["data"]

        return response

    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(output_path)


def extract_app_definitions_object(response):
    if isinstance(response, dict):
        app_definitions = response.get("appDefinitions")

        if isinstance(app_definitions, list):
            return response

        nested_data = response.get("data")

        if isinstance(nested_data, dict) and isinstance(nested_data.get("appDefinitions"), list):
            return nested_data

    fail("CapRover appDefinitions response does not contain appDefinitions list")


def get_app_definitions_object(server_name):
    response = run_caprover_api(
        server_name=server_name,
        api_path=APP_DEFINITIONS_API_PATH,
        method="GET",
        data={},
        allowed_statuses={None, 100},
        return_full_response=False,
    )

    return extract_app_definitions_object(response)


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


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_json_to_ansirover(json_path, yaml_path, cwd):
    command = [
        "caprover-app-definition-json-to-ansirover",
        str(json_path),
        str(yaml_path),
        "--force",
    ]

    info("[convert] " + " ".join(command))

    completed_process = subprocess.run(
        command,
        cwd=str(cwd),
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
        fail("failed to convert remote app definition JSON to AnsiRover YAML")


def normalize_yaml_text(path):
    text = Path(path).read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def print_yaml_diff(local_yaml_path, remote_yaml_path):
    local_lines = normalize_yaml_text(local_yaml_path).splitlines(keepends=True)
    remote_lines = normalize_yaml_text(remote_yaml_path).splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            local_lines,
            remote_lines,
            fromfile=str(local_yaml_path),
            tofile=str(remote_yaml_path),
        )
    )

    if not diff_lines:
        return

    print("", file=sys.stderr)
    print("YAML DIFF:", file=sys.stderr)
    print("".join(diff_lines[:300]), file=sys.stderr, end="")

    if len(diff_lines) > 300:
        print(f"\n... diff truncated; {len(diff_lines) - 300} more lines ...", file=sys.stderr)


def validate_yaml_matches_remote(app_dir, app_name, app_definition):
    local_yaml_path = app_dir / f"{app_name}.yaml"

    if not local_yaml_path.exists():
        fail(f"local AnsiRover YAML not found: {local_yaml_path}")

    with tempfile.TemporaryDirectory(prefix="caprover-delete-safe-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        remote_json_path = temp_dir / f"{app_name}.json"
        remote_yaml_path = temp_dir / f"{app_name}.yaml"

        write_json(remote_json_path, app_definition)
        run_json_to_ansirover(remote_json_path, remote_yaml_path, cwd=temp_dir)

        local_yaml_text = normalize_yaml_text(local_yaml_path)
        remote_yaml_text = normalize_yaml_text(remote_yaml_path)

        if local_yaml_text != remote_yaml_text:
            print_yaml_diff(local_yaml_path, remote_yaml_path)
            fail(
                "remote app definition converted to AnsiRover YAML differs from local YAML; "
                "refusing to delete"
            )

    info("[check] YAML matches current remote app definition: " + str(local_yaml_path))


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
                "hostPath": str(host_path),
                "containerPath": str(volume.get("containerPath", "")),
            }
        )

    return bind_mounts


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


def validate_tar_gz_archive(archive_path, label):
    partial_path = Path(str(archive_path) + ".partial")

    if partial_path.exists():
        fail(f"{label} partial archive exists; refusing to delete: {partial_path}")

    if not archive_path.exists():
        fail(f"{label} archive not found: {archive_path}")

    if archive_path.stat().st_size == 0:
        fail(f"{label} archive is empty: {archive_path}")

    gzip_result = subprocess.run(
        ["gzip", "-t", str(archive_path)],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
        check=False,
    )

    if gzip_result.returncode != 0:
        fail(f"{label} archive failed gzip -t: {archive_path}")

    tar_result = subprocess.run(
        ["tar", "-tzf", str(archive_path)],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
        check=False,
    )

    if tar_result.returncode != 0:
        fail(f"{label} archive failed tar -tzf: {archive_path}")

    info(f"[check] {label} archive OK: {archive_path}")


def validate_volume_archives(app_dir, app_name, volume_names):
    if not volume_names:
        info("[check] No named Docker volumes declared by current remote app definition.")
        return

    for volume_name in volume_names:
        archive_path = app_dir / f"{app_name}.{volume_name}.tar.gz"
        validate_tar_gz_archive(archive_path, f"volume {volume_name}")


def validate_image_archive_if_needed(app_dir, app_name, app_definition, require_dockerhub_image, caprover_root_domain):
    deployed_image_name = get_deployed_image_name(app_definition)

    if not deployed_image_name:
        fail("cannot determine deployedImageName from current remote app definition; refusing to delete safely")

    if is_external_pullable_docker_image(deployed_image_name, caprover_root_domain) and not require_dockerhub_image:
        info("[check] Externally pullable image detected; local image archive is not required by default.")
        info("[check] Image: " + deployed_image_name)
        return

    archive_path = app_dir / f"{app_name}.image.tar.gz"
    validate_tar_gz_archive(archive_path, "image")


def print_bind_mount_warnings(bind_mounts):
    if not bind_mounts:
        return

    print("", file=sys.stderr)
    info("[warn] App has bind mounts. They are not validated as named-volume archives and will not be deleted.")

    for bind_mount in bind_mounts:
        host_path = bind_mount.get("hostPath", "")
        container_path = bind_mount.get("containerPath", "")

        if container_path:
            info(f"[warn] bind mount: {host_path} -> {container_path}")
        else:
            info(f"[warn] bind mount: {host_path}")


def confirm_volume_deletion(volume_names):
    for volume_name in volume_names:
        physical_volume_name = "captain--" + volume_name

        print("", file=sys.stderr)
        print("Volume to delete:", file=sys.stderr)
        print("  " + physical_volume_name, file=sys.stderr)
        print("", file=sys.stderr)
        typed_value = input("Type exactly this volume name to delete it: ").strip()

        if typed_value != physical_volume_name:
            fail(
                "volume deletion confirmation did not match. "
                f"Expected {physical_volume_name!r}, got {typed_value!r}"
            )


def print_plan(server_name, app_name, app_dir, volume_names, bind_mounts, deployed_image_name, delete_volumes, force, dry_run):
    info("")
    info("=" * 88)
    info("== delete app safely plan ==")
    info("=" * 88)
    info("[plan] Server: " + server_name)
    info("[plan] App: " + app_name)
    info("[plan] App backup dir: " + str(app_dir))
    info("[plan] Deployed image: " + deployed_image_name)
    info("[plan] Named volumes: " + (", ".join(volume_names) if volume_names else "(none)"))
    info("[plan] Bind mounts: " + (str(len(bind_mounts)) if bind_mounts else "(none)"))
    info("[plan] Delete app: yes")
    info("[plan] Delete volumes: " + ("yes" if delete_volumes else "no"))
    info("[plan] Force confirmations: " + str(force))
    info("[plan] Dry run: " + str(dry_run))
    info("=" * 88)


def delete_app(server_name, app_name, volume_names, dry_run):
    payload = {
        "volumes": list(volume_names),
        "appNames": [app_name],
    }

    info("[delete] API path: " + DELETE_APP_API_PATH)
    info("[delete] Payload: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    if dry_run:
        info("[dry-run] Not deleting app.")
        return

    response = run_caprover_api(
        server_name=server_name,
        api_path=DELETE_APP_API_PATH,
        method="POST",
        data=payload,
        allowed_statuses={None, 100, 102},
        return_full_response=True,
    )

    status = response.get("status") if isinstance(response, dict) else None
    description = response.get("description", "") if isinstance(response, dict) else ""

    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        data = response.get("data", {})
    elif isinstance(response, dict):
        data = response
    else:
        data = {}

    info("[delete] CapRover status: " + str(status))
    info("[delete] CapRover description: " + str(description))

    volumes_failed_to_delete = []

    if isinstance(data, dict) and isinstance(data.get("volumesFailedToDelete"), list):
        volumes_failed_to_delete = [str(item) for item in data.get("volumesFailedToDelete")]

    if status == 102 or volumes_failed_to_delete:
        print("", file=sys.stderr)
        print("WARNING: CapRover reports the app was deleted, but some volumes were not deleted.", file=sys.stderr)

        if volumes_failed_to_delete:
            print("Volumes failed to delete:", file=sys.stderr)

            for volume_name in volumes_failed_to_delete:
                print("  " + volume_name, file=sys.stderr)

        fail("partial delete result from CapRover; manual follow-up required")

    info("[done] App deleted successfully.")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Safely delete one CapRover app after validating the local AnsiRover YAML, "
            "image archive and named-volume archives."
        )
    )

    parser.add_argument("server_name", help="CapRover CLI machine name, e.g. frginbox or storage; @ is not allowed")
    parser.add_argument("app_name", help="CapRover app name to delete")
    parser.add_argument(
        "path",
        nargs="?",
        help=(
            "optional app backup folder path. If omitted, uses current directory when it is named "
            "<app-name>, otherwise ./<app-name>."
        ),
    )

    parser.add_argument("--delete-volumes", action="store_true", help="also delete named Docker volumes declared by the app")
    parser.add_argument("--force", action="store_true", help="do not ask per-volume confirmation when --delete-volumes is used")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the delete plan, but do not delete")
    parser.add_argument("--require-dockerhub-image", action="store_true", help="require <app>.image.tar.gz even for externally pullable images")

    return parser.parse_args()


def main():
    args = parse_args()

    validate_caprover_machine_name(args.server_name)
    require_captain_password(args.server_name)
    validate_simple_name("app name", args.app_name)

    current_dir = Path.cwd()
    app_dir = resolve_app_dir(current_dir, args.app_name, args.path)

    if not app_dir.exists():
        fail(f"app backup dir not found: {app_dir}")

    info("[plan] Action: delete CapRover app safely")
    info("[plan] Server: " + args.server_name)
    info("[plan] App: " + args.app_name)
    info("[plan] App backup dir: " + str(app_dir))

    app_definitions_object = get_app_definitions_object(args.server_name)
    app_definitions = app_definitions_object.get("appDefinitions", [])
    app_definition = find_app_definition(app_definitions, args.app_name)

    validate_yaml_matches_remote(app_dir, args.app_name, app_definition)

    volume_names = get_named_volume_names(app_definition)
    bind_mounts = get_bind_mounts(app_definition)
    deployed_image_name = get_deployed_image_name(app_definition)

    if not args.force:
        caprover_root_domain = get_caprover_root_domain(args.server_name)
        validate_volume_archives(app_dir, args.app_name, volume_names)
        validate_image_archive_if_needed(
            app_dir=app_dir,
            app_name=args.app_name,
            app_definition=app_definition,
            require_dockerhub_image=args.require_dockerhub_image,
            caprover_root_domain=caprover_root_domain,
        )

    print_bind_mount_warnings(bind_mounts)

    print_plan(
        server_name=args.server_name,
        app_name=args.app_name,
        app_dir=app_dir,
        volume_names=volume_names,
        bind_mounts=bind_mounts,
        deployed_image_name=deployed_image_name,
        delete_volumes=args.delete_volumes,
        force=args.force,
        dry_run=args.dry_run,
    )

    volumes_to_delete = volume_names if args.delete_volumes else []

    if args.delete_volumes and volume_names and not args.force and not args.dry_run:
        confirm_volume_deletion(volume_names)

    delete_app(
        server_name=args.server_name,
        app_name=args.app_name,
        volume_names=volumes_to_delete,
        dry_run=args.dry_run,
    )

    result("APP_DELETED=" + ("false" if args.dry_run else "true"))
    result("APP_NAME=" + args.app_name)
    result("VOLUMES_DELETED=" + ("false" if not args.delete_volumes or args.dry_run else "true"))


if __name__ == "__main__":
    main()
