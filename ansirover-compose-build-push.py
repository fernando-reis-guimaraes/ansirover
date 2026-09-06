#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ANSIROVER_DIR_NAME = "ansirover"
COMPOSE_FILE_NAME = "compose.yaml"
REGISTRY_PREFIX = "cr.umapps.net/umapps/"


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def result(message):
    print(message, flush=True)


def print_process_output(completed_process):
    if completed_process.stdout:
        print(completed_process.stdout, file=sys.stderr, end="")

    if completed_process.stderr:
        print(completed_process.stderr, file=sys.stderr, end="")


def run_command(command, cwd, env=None, capture_output=False, label=None):
    display_label = label or "run"
    info(f"[{display_label}] cwd: {cwd}")
    info(f"[{display_label}] " + " ".join(command))

    try:
        completed_process = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            check=False,
        )
    except FileNotFoundError:
        fail(f"command not found: {command[0]}")

    if capture_output and completed_process.stderr:
        print(completed_process.stderr, file=sys.stderr, end="")

    if completed_process.returncode != 0:
        if capture_output:
            print_process_output(completed_process)

        fail(f"command failed with exit status {completed_process.returncode}: {' '.join(command)}")

    return completed_process


def resolve_git_root(path):
    candidate = Path(path).expanduser().resolve()

    if not candidate.exists():
        fail(f"path does not exist: {candidate}")

    if not candidate.is_dir():
        fail(f"path is not a directory: {candidate}")

    completed_process = run_command(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        cwd=candidate,
        capture_output=True,
        label="git",
    )

    git_root_text = completed_process.stdout.strip()

    if not git_root_text:
        fail(f"could not determine Git repository root from: {candidate}")

    return Path(git_root_text).resolve()


def get_image_tag(git_root):
    completed_process = run_command(
        ["git", "-C", str(git_root), "rev-parse", "--short", "HEAD"],
        cwd=git_root,
        capture_output=True,
        label="git",
    )

    image_tag = completed_process.stdout.strip()

    if not image_tag:
        fail("git rev-parse --short HEAD returned an empty value")

    return image_tag


def warn_if_git_worktree_is_dirty(git_root):
    completed_process = run_command(
        ["git", "-C", str(git_root), "status", "--porcelain"],
        cwd=git_root,
        capture_output=True,
        label="git",
    )

    dirty_lines = [line for line in completed_process.stdout.splitlines() if line.strip()]

    if not dirty_lines:
        return

    info("[warn] Git working tree is dirty.")
    info("[warn] IMAGE_TAG identifies committed HEAD only; the Docker build context may include uncommitted changes.")
    info(f"[warn] Dirty paths reported by Git: {len(dirty_lines)}")


def get_compose_environment(image_tag):
    environment = os.environ.copy()
    environment["IMAGE_TAG"] = image_tag
    return environment


def load_compose_model(ansirover_dir, compose_path, image_tag):
    environment = get_compose_environment(image_tag)
    completed_process = run_command(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        cwd=ansirover_dir,
        env=environment,
        capture_output=True,
        label="compose config",
    )

    try:
        compose_model = json.loads(completed_process.stdout)
    except json.JSONDecodeError as error:
        print_process_output(completed_process)
        fail(f"docker compose config returned invalid JSON: {error}")

    if not isinstance(compose_model, dict):
        fail("docker compose config did not return an object")

    return compose_model


def validate_published_image(service_name, image_reference, image_tag):
    image_reference = str(image_reference or "").strip()

    if not image_reference:
        fail(f"Compose service {service_name!r} has build configuration but no image name")

    if not image_reference.startswith(REGISTRY_PREFIX):
        fail(
            f"Compose service {service_name!r} must publish under {REGISTRY_PREFIX}; "
            f"resolved image: {image_reference}"
        )

    if "@" in image_reference:
        fail(
            f"Compose service {service_name!r} resolved to a digest reference; "
            f"the build/push image must use IMAGE_TAG={image_tag}: {image_reference}"
        )

    repository, separator, resolved_tag = image_reference.rpartition(":")

    if not separator or not repository:
        fail(f"Compose service {service_name!r} image has no explicit tag: {image_reference}")

    if resolved_tag != image_tag:
        fail(
            f"Compose service {service_name!r} must resolve to the current short Git HEAD tag. "
            f"Expected :{image_tag}, got :{resolved_tag}. "
            "Use ${IMAGE_TAG:-beta} or another Compose interpolation that honors IMAGE_TAG."
        )

    return image_reference


def get_build_services(compose_model, image_tag):
    services = compose_model.get("services")

    if not isinstance(services, dict):
        fail("Compose model does not contain a services mapping")

    build_services = []

    for service_name, service_definition in services.items():
        if not isinstance(service_definition, dict):
            continue

        build_definition = service_definition.get("build")

        if build_definition is None or build_definition is False:
            continue

        image_reference = validate_published_image(
            service_name=service_name,
            image_reference=service_definition.get("image"),
            image_tag=image_tag,
        )
        build_services.append((service_name, image_reference))

    if not build_services:
        fail("compose.yaml has no service with build configuration")

    return build_services


def print_plan(git_root, ansirover_dir, compose_path, image_tag, build_services, dry_run):
    info("")
    info("=" * 88)
    info("== AnsiRover Compose build/push plan ==")
    info("=" * 88)
    info(f"[plan] Git root: {git_root}")
    info(f"[plan] AnsiRover directory: {ansirover_dir}")
    info(f"[plan] Compose file: {compose_path}")
    info(f"[plan] Registry prefix: {REGISTRY_PREFIX}")
    info(f"[plan] IMAGE_TAG: {image_tag}")
    info(f"[plan] Build services: {len(build_services)}")

    for service_name, image_reference in build_services:
        info(f"[plan] {service_name}: {image_reference}")

    info(f"[plan] Dry run: {dry_run}")
    info("=" * 88)


def build_and_push(ansirover_dir, compose_path, image_tag):
    environment = get_compose_environment(image_tag)
    run_command(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "build",
            "--push",
        ],
        cwd=ansirover_dir,
        env=environment,
        capture_output=False,
        label="build/push",
    )


def print_usage_and_guide():
    print("""Usage:
  ansirover-compose-build-push <path> [options]

Purpose:
  Build and push Docker Compose images defined in:

    <git-root>/ansirover/compose.yaml

Arguments:
  <path>
      Path inside the target Git repository.

      Use "." for the current repository.

Options:
  --dry-run
      Validate the repository and resolved Compose model and print
      the build/push plan without building or pushing.

  -h, --help
      Show this help.

Examples:
  ansirover-compose-build-push .
  ansirover-compose-build-push /Volumes/FrgGit/tictactoe
  ansirover-compose-build-push . --dry-run

Image tag:
  IMAGE_TAG=$(git rev-parse --short HEAD)

Registry:
  cr.umapps.net/umapps/

Behavior:
  Running this command without arguments only prints this usage
  message and performs no action.
""")


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Build and push Docker Compose build services from "
            "<git-root>/ansirover/compose.yaml using "
            "IMAGE_TAG=<short-git-head>."
        ),
    )
    parser.add_argument(
        "path",
        help="path inside the target Git repository; use . for the current repository",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the repository and Compose model and print the plan without building or pushing",
    )
    return parser.parse_args(arguments)


def main(arguments=None):
    raw_arguments = list(arguments) if arguments is not None else sys.argv[1:]

    if not raw_arguments:
        print_usage_and_guide()
        return 0

    if "-h" in raw_arguments or "--help" in raw_arguments:
        print_usage_and_guide()
        return 0

    args = parse_args(raw_arguments)
    git_root = resolve_git_root(args.path)
    ansirover_dir = git_root / ANSIROVER_DIR_NAME
    compose_path = ansirover_dir / COMPOSE_FILE_NAME

    if not ansirover_dir.exists() or not ansirover_dir.is_dir():
        fail(f"required AnsiRover directory not found: {ansirover_dir}")

    if not compose_path.exists() or not compose_path.is_file():
        fail(f"required Compose file not found: {compose_path}")

    image_tag = get_image_tag(git_root)
    warn_if_git_worktree_is_dirty(git_root)
    compose_model = load_compose_model(
        ansirover_dir=ansirover_dir,
        compose_path=compose_path,
        image_tag=image_tag,
    )
    build_services = get_build_services(compose_model, image_tag)

    print_plan(
        git_root=git_root,
        ansirover_dir=ansirover_dir,
        compose_path=compose_path,
        image_tag=image_tag,
        build_services=build_services,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        build_and_push(
            ansirover_dir=ansirover_dir,
            compose_path=compose_path,
            image_tag=image_tag,
        )

    result("IMAGE_TAG=" + image_tag)
    result("GIT_ROOT=" + str(git_root))
    result("ANSIROVER_DIR=" + str(ansirover_dir))
    result("COMPOSE_FILE=" + str(compose_path))
    result("IMAGE_COUNT=" + str(len(build_services)))

    for _service_name, image_reference in build_services:
        result("IMAGE=" + image_reference)

    result("BUILD_PUSH_COMPLETED=" + ("false" if args.dry_run else "true"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
