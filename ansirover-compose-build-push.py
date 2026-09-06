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
DISCOVERY_IMAGE_TAG = "ansirover-discovery"


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
    display_label = "run"

    if label:
        display_label = label

    info(f"[{display_label}] cwd: {cwd}")
    info(f"[{display_label}] " + " ".join(command))

    stdout_target = None
    stderr_target = None

    if capture_output:
        stdout_target = subprocess.PIPE
        stderr_target = subprocess.PIPE

    try:
        completed_process = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=stdout_target,
            stderr=stderr_target,
            check=False,
        )
    except FileNotFoundError:
        fail(f"command not found: {command[0]}")

    if capture_output and completed_process.stderr:
        print(completed_process.stderr, file=sys.stderr, end="")

    if completed_process.returncode != 0:
        if capture_output:
            print_process_output(completed_process)

        fail(
            f"command failed with exit status {completed_process.returncode}: "
            + " ".join(command)
        )

    return completed_process


def resolve_existing_directory(path):
    candidate = Path(path).expanduser().resolve()

    if not candidate.exists():
        fail(f"path does not exist: {candidate}")

    if not candidate.is_dir():
        fail(f"path is not a directory: {candidate}")

    return candidate


def resolve_git_root(path, purpose):
    completed_process = run_command(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        label="git",
    )

    git_root_text = completed_process.stdout.strip()

    if not git_root_text:
        fail(f"could not determine {purpose} Git root from: {path}")

    return Path(git_root_text).resolve()


def resolve_application_layout(path):
    candidate = resolve_existing_directory(path)

    if candidate.name == ANSIROVER_DIR_NAME:
        direct_compose_path = candidate / COMPOSE_FILE_NAME

        if direct_compose_path.is_file():
            application_root = candidate.parent
            ansirover_dir = candidate
            application_git_root = resolve_git_root(
                application_root,
                "application",
            )

            if application_git_root != application_root:
                fail(
                    "the parent of ansirover/ must be the root of its application Git "
                    f"repository: {application_root}"
                )

            return (
                application_root,
                application_git_root,
                ansirover_dir,
                direct_compose_path,
            )

    candidate_git_root = resolve_git_root(candidate, "application")
    candidate_ansirover_dir = candidate_git_root / ANSIROVER_DIR_NAME
    candidate_compose_path = candidate_ansirover_dir / COMPOSE_FILE_NAME

    if candidate_compose_path.is_file():
        return (
            candidate_git_root,
            candidate_git_root,
            candidate_ansirover_dir,
            candidate_compose_path,
        )

    if (
        candidate_git_root.name == ANSIROVER_DIR_NAME
        and (candidate_git_root / COMPOSE_FILE_NAME).is_file()
    ):
        application_root = candidate_git_root.parent
        application_git_root = resolve_git_root(
            application_root,
            "application",
        )

        if application_git_root != application_root:
            fail(
                "the parent of the ansirover submodule must be the root of its "
                f"application Git repository: {application_root}"
            )

        return (
            application_root,
            application_git_root,
            candidate_git_root,
            candidate_git_root / COMPOSE_FILE_NAME,
        )

    fail(
        "could not find the application AnsiRover Compose file. Expected "
        f"<application-git-root>/{ANSIROVER_DIR_NAME}/{COMPOSE_FILE_NAME}"
    )


def ensure_ansirover_is_flat(ansirover_dir):
    nested_directories = []

    for child in ansirover_dir.iterdir():
        if not child.is_dir():
            continue

        if child.name.startswith("."):
            continue

        nested_directories.append(child.name)

    if not nested_directories:
        return

    nested_directories.sort()
    fail(
        "ansirover/ must be flat; subdirectories are not supported: "
        + ", ".join(nested_directories)
    )


def get_image_tag(source_git_root):
    completed_process = run_command(
        ["git", "-C", str(source_git_root), "rev-parse", "--short", "HEAD"],
        cwd=source_git_root,
        capture_output=True,
        label="git",
    )

    image_tag = completed_process.stdout.strip()

    if not image_tag:
        fail("git rev-parse --short HEAD returned an empty value")

    return image_tag


def warn_if_git_worktree_is_dirty(source_git_root):
    completed_process = run_command(
        ["git", "-C", str(source_git_root), "status", "--porcelain"],
        cwd=source_git_root,
        capture_output=True,
        label="git",
    )

    dirty_lines = []

    for line in completed_process.stdout.splitlines():
        if line.strip():
            dirty_lines.append(line)

    if not dirty_lines:
        return

    info("[warn] Source Git working tree is dirty.")
    info(
        "[warn] IMAGE_TAG identifies committed HEAD only; the Docker build context "
        "may include uncommitted changes."
    )
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


def get_single_build_service(compose_model):
    services = compose_model.get("services")

    if not isinstance(services, dict):
        fail("Compose model does not contain a services mapping")

    if len(services) != 1:
        fail(
            "ansirover/compose.yaml must define exactly one service. "
            "AnsiRover contract: one Compose file = one app = one service."
        )

    service_name = next(iter(services))
    service_definition = services[service_name]

    if not isinstance(service_definition, dict):
        fail(f"Compose service {service_name!r} is not an object")

    build_definition = service_definition.get("build")

    if build_definition is None or build_definition is False:
        fail(f"Compose service {service_name!r} must define build configuration")

    return service_name, service_definition


def get_build_context_value(service_name, service_definition):
    build_definition = service_definition.get("build")

    if isinstance(build_definition, str):
        return build_definition

    if isinstance(build_definition, dict):
        context_value = build_definition.get("context")

        if context_value is None:
            return "."

        return str(context_value)

    fail(f"Compose service {service_name!r} has unsupported build configuration")


def resolve_build_context(ansirover_dir, service_name, service_definition):
    context_value = get_build_context_value(
        service_name,
        service_definition,
    )
    normalized_value = context_value.strip()

    remote_prefixes = (
        "http://",
        "https://",
        "git://",
        "ssh://",
        "git@",
    )

    if normalized_value.startswith(remote_prefixes):
        fail(
            f"Compose service {service_name!r} must use a local build.context so "
            "AnsiRover can derive IMAGE_TAG from the owning Git repository: "
            f"{normalized_value}"
        )

    context_path = Path(normalized_value).expanduser()

    if not context_path.is_absolute():
        context_path = ansirover_dir / context_path

    resolved_context = context_path.resolve()

    if not resolved_context.exists():
        fail(
            f"Compose service {service_name!r} build.context does not exist: "
            f"{resolved_context}"
        )

    if not resolved_context.is_dir():
        fail(
            f"Compose service {service_name!r} build.context is not a directory: "
            f"{resolved_context}"
        )

    return resolved_context


def validate_published_image(service_name, image_reference, image_tag):
    normalized_image_reference = str(image_reference or "").strip()

    if not normalized_image_reference:
        fail(
            f"Compose service {service_name!r} has build configuration but no image name"
        )

    if not normalized_image_reference.startswith(REGISTRY_PREFIX):
        fail(
            f"Compose service {service_name!r} must publish under {REGISTRY_PREFIX}; "
            f"resolved image: {normalized_image_reference}"
        )

    if "@" in normalized_image_reference:
        fail(
            f"Compose service {service_name!r} resolved to a digest reference; "
            f"the build/push image must use IMAGE_TAG={image_tag}: "
            f"{normalized_image_reference}"
        )

    repository, separator, resolved_tag = normalized_image_reference.rpartition(":")

    if not separator or not repository:
        fail(
            f"Compose service {service_name!r} image has no explicit tag: "
            f"{normalized_image_reference}"
        )

    if resolved_tag != image_tag:
        fail(
            f"Compose service {service_name!r} must resolve to the short HEAD of the "
            "Git repository that owns build.context. "
            f"Expected :{image_tag}, got :{resolved_tag}. "
            "Use ${IMAGE_TAG:-beta} or another Compose interpolation that honors "
            "IMAGE_TAG."
        )

    return normalized_image_reference


def print_plan(
    application_root,
    application_git_root,
    ansirover_dir,
    compose_path,
    service_name,
    build_context,
    source_git_root,
    image_tag,
    image_reference,
    dry_run,
):
    info("")
    info("=" * 88)
    info("== AnsiRover Compose build/push plan ==")
    info("=" * 88)
    info(f"[plan] Application root: {application_root}")
    info(f"[plan] Application Git root: {application_git_root}")
    info(f"[plan] AnsiRover directory: {ansirover_dir}")
    info(f"[plan] Compose file: {compose_path}")
    info(f"[plan] Service: {service_name}")
    info(f"[plan] Build context: {build_context}")
    info(f"[plan] Source Git root: {source_git_root}")
    info(f"[plan] Registry prefix: {REGISTRY_PREFIX}")
    info(f"[plan] IMAGE_TAG: {image_tag}")
    info(f"[plan] Image: {image_reference}")
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
  Build and push the single Docker Compose service defined by:

    <application-git-root>/ansirover/compose.yaml

Arguments:
  <path>
      Path inside the target application Git repository, or its ansirover/
      directory.

      Use "." for the current application repository.

Options:
  --dry-run
      Validate the repository and resolved Compose model and print
      the build/push plan without building or pushing.

  -h, --help
      Show this help.

Repository contract:
  One Git repository = one deployable app.
  Monorepos are not supported.
  Multiple apps under a parent project must be separate Git repositories,
  normally composed as Git submodules.
  ansirover/ is flat; per-app subdirectories are not supported.
  ansirover/compose.yaml defines exactly one service.
  One Compose file = one AnsiRover app = one service = one image.

Image tag:
  IMAGE_TAG is the short HEAD of the Git repository that owns the resolved
  build.context, not necessarily the repository that owns ansirover/.

Examples:
  ansirover-compose-build-push .
  ansirover-compose-build-push /Volumes/FrgGit/tictactoe
  ansirover-compose-build-push ./ansirover
  ansirover-compose-build-push . --dry-run

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
            "Build and push the single Docker Compose service from "
            "<application-git-root>/ansirover/compose.yaml."
        ),
    )
    parser.add_argument(
        "path",
        help=(
            "path inside the target application Git repository or its ansirover/ "
            "directory; use . for the current application repository"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "validate the repository and Compose model and print the plan without "
            "building or pushing"
        ),
    )
    return parser.parse_args(arguments)


def main(arguments=None):
    raw_arguments = []

    if arguments is None:
        raw_arguments = sys.argv[1:]
    else:
        raw_arguments = list(arguments)

    if not raw_arguments:
        print_usage_and_guide()
        return 0

    if "-h" in raw_arguments or "--help" in raw_arguments:
        print_usage_and_guide()
        return 0

    args = parse_args(raw_arguments)
    (
        application_root,
        application_git_root,
        ansirover_dir,
        compose_path,
    ) = resolve_application_layout(args.path)

    ensure_ansirover_is_flat(ansirover_dir)

    discovery_model = load_compose_model(
        ansirover_dir=ansirover_dir,
        compose_path=compose_path,
        image_tag=DISCOVERY_IMAGE_TAG,
    )
    service_name, discovery_service_definition = get_single_build_service(
        discovery_model
    )
    build_context = resolve_build_context(
        ansirover_dir,
        service_name,
        discovery_service_definition,
    )
    source_git_root = resolve_git_root(
        build_context,
        "build.context source",
    )
    image_tag = get_image_tag(source_git_root)
    warn_if_git_worktree_is_dirty(source_git_root)

    compose_model = load_compose_model(
        ansirover_dir=ansirover_dir,
        compose_path=compose_path,
        image_tag=image_tag,
    )
    resolved_service_name, service_definition = get_single_build_service(compose_model)

    if resolved_service_name != service_name:
        fail(
            "Compose service name changed while resolving IMAGE_TAG; "
            "service discovery must be deterministic"
        )

    resolved_build_context = resolve_build_context(
        ansirover_dir,
        resolved_service_name,
        service_definition,
    )

    if resolved_build_context != build_context:
        fail(
            "build.context changed when IMAGE_TAG was applied. "
            "build.context must not depend on IMAGE_TAG."
        )

    image_reference = validate_published_image(
        service_name=resolved_service_name,
        image_reference=service_definition.get("image"),
        image_tag=image_tag,
    )

    print_plan(
        application_root=application_root,
        application_git_root=application_git_root,
        ansirover_dir=ansirover_dir,
        compose_path=compose_path,
        service_name=resolved_service_name,
        build_context=build_context,
        source_git_root=source_git_root,
        image_tag=image_tag,
        image_reference=image_reference,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        build_and_push(
            ansirover_dir=ansirover_dir,
            compose_path=compose_path,
            image_tag=image_tag,
        )

    result("IMAGE_TAG=" + image_tag)
    result("APPLICATION_ROOT=" + str(application_root))
    result("APPLICATION_GIT_ROOT=" + str(application_git_root))
    result("ANSIROVER_DIR=" + str(ansirover_dir))
    result("COMPOSE_FILE=" + str(compose_path))
    result("SERVICE=" + resolved_service_name)
    result("BUILD_CONTEXT=" + str(build_context))
    result("SOURCE_GIT_ROOT=" + str(source_git_root))
    result("IMAGE_COUNT=1")
    result("IMAGE=" + image_reference)

    build_push_completed = "true"

    if args.dry_run:
        build_push_completed = "false"

    result("BUILD_PUSH_COMPLETED=" + build_push_completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
