#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ANSIROVER_DIR_NAME = "ansirover"
COMPOSE_FILE_NAME = "compose.yaml"
REGISTRY_PREFIX = "cr.umapps.net/umapps/"
DISCOVERY_IMAGE_TAG = "ansirover-discovery"
SIDECAR_SUFFIXES = (
    ".env-vars.env",
    ".custom-nginx-config.conf",
    ".pre-deploy-function.js",
    ".service-update-override.yaml",
)


def info(message):
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command, cwd, environment=None, capture=False, label="run"):
    info(f"[{label}] cwd: {cwd}")
    info(f"[{label}] " + " ".join(command))
    stdout_target = None
    stderr_target = None
    if capture:
        stdout_target = subprocess.PIPE
        stderr_target = subprocess.PIPE
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=environment,
            text=True,
            stdout=stdout_target,
            stderr=stderr_target,
            check=False,
        )
    except FileNotFoundError:
        fail(f"command not found: {command[0]}")
    if completed.returncode != 0:
        if capture and completed.stdout:
            print(completed.stdout, file=sys.stderr, end="")
        if capture and completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        fail(f"command failed with exit status {completed.returncode}: {' '.join(command)}")
    if capture and completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    return completed


def git_text(repository, *arguments):
    completed = run(
        ["git", "-C", str(repository), *arguments],
        cwd=repository,
        capture=True,
        label="git",
    )
    return completed.stdout.strip()


def git_root(path, purpose):
    value = git_text(path, "rev-parse", "--show-toplevel")
    if not value:
        fail(f"could not determine {purpose} Git root from: {path}")
    return Path(value).resolve()


def relative_path(parent, child):
    try:
        return child.relative_to(parent)
    except ValueError:
        return None


def resolve_layout(input_path):
    candidate = Path(input_path).expanduser().resolve()
    if not candidate.is_dir():
        fail(f"path is not a directory: {candidate}")

    if candidate.name == ANSIROVER_DIR_NAME and (candidate / COMPOSE_FILE_NAME).is_file():
        application_root = candidate.parent
        application_git_root = git_root(application_root, "application")
        if application_git_root != application_root:
            fail(f"the parent of ansirover/ must be the application Git root: {application_root}")
        return application_root, application_git_root, candidate, candidate / COMPOSE_FILE_NAME

    candidate_git_root = git_root(candidate, "application")
    ansirover_dir = candidate_git_root / ANSIROVER_DIR_NAME
    compose_path = ansirover_dir / COMPOSE_FILE_NAME
    if compose_path.is_file():
        return candidate_git_root, candidate_git_root, ansirover_dir, compose_path

    if candidate_git_root.name == ANSIROVER_DIR_NAME and (candidate_git_root / COMPOSE_FILE_NAME).is_file():
        application_root = candidate_git_root.parent
        application_git_root = git_root(application_root, "application")
        if application_git_root != application_root:
            fail(f"the parent of the ansirover submodule must be the application Git root: {application_root}")
        return application_root, application_git_root, candidate_git_root, candidate_git_root / COMPOSE_FILE_NAME

    fail(f"expected <application-git-root>/{ANSIROVER_DIR_NAME}/{COMPOSE_FILE_NAME}")


def require_flat_ansirover(ansirover_dir):
    nested = sorted(
        child.name
        for child in ansirover_dir.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )
    if nested:
        fail("ansirover/ must be flat; subdirectories are not supported: " + ", ".join(nested))


def compose_model(ansirover_dir, compose_path, image_tag):
    environment = os.environ.copy()
    environment["IMAGE_TAG"] = image_tag
    completed = run(
        ["docker", "compose", "-f", str(compose_path), "config", "--format", "json"],
        cwd=ansirover_dir,
        environment=environment,
        capture=True,
        label="compose config",
    )
    try:
        model = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"docker compose config returned invalid JSON: {error}")
    if not isinstance(model, dict):
        fail("docker compose config did not return an object")
    return model


def single_service(model):
    services = model.get("services")
    if not isinstance(services, dict) or len(services) != 1:
        fail("ansirover/compose.yaml must define exactly one service")
    service_name = next(iter(services))
    definition = services[service_name]
    if not isinstance(definition, dict) or definition.get("build") in (None, False):
        fail(f"Compose service {service_name!r} must define build configuration")
    return service_name, definition


def build_context(ansirover_dir, service_name, definition):
    build_definition = definition.get("build")
    if isinstance(build_definition, str):
        context_value = build_definition
    elif isinstance(build_definition, dict):
        context_value = str(build_definition.get("context", "."))
    else:
        fail(f"Compose service {service_name!r} has unsupported build configuration")
    normalized = context_value.strip()
    if normalized.startswith(("http://", "https://", "git://", "ssh://", "git@")):
        fail(f"Compose service {service_name!r} must use a local build.context")
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = ansirover_dir / path
    path = path.resolve()
    if not path.is_dir():
        fail(f"Compose service {service_name!r} build.context is not a directory: {path}")
    return path


def dockerfile_path(context, service_name, definition):
    build_definition = definition.get("build")
    if not isinstance(build_definition, dict):
        return context / "Dockerfile"
    if build_definition.get("dockerfile_inline") is not None:
        return None
    value = str(build_definition.get("dockerfile", "Dockerfile")).strip()
    if not value:
        fail(f"Compose service {service_name!r} has an empty build.dockerfile")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context / path
    return path.resolve()


def normalized_ignore_rule(line):
    value = line.strip()
    while value.startswith("./"):
        value = value[2:]
    return value.replace("\\", "/").strip("/")


def validate_dockerignore(context, ansirover_dir, service_name, definition):
    relative_ansirover = relative_path(context, ansirover_dir)
    if relative_ansirover is None:
        return None
    if str(relative_ansirover) == ".":
        fail("ansirover/ cannot itself be build.context")

    dockerfile = dockerfile_path(context, service_name, definition)
    ignore_path = context / ".dockerignore"
    if dockerfile is not None:
        specific = dockerfile.with_name(dockerfile.name + ".dockerignore")
        if specific.is_file():
            ignore_path = specific
    if not ignore_path.is_file():
        fail(f"effective Docker ignore file does not exist: {ignore_path}")

    required_rule = relative_ansirover.as_posix().strip("/")
    required_index = None
    negation_indexes = []
    for index, line in enumerate(ignore_path.read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        is_negation = stripped.startswith("!")
        rule = stripped
        if is_negation:
            rule = stripped[1:]
        if is_negation:
            negation_indexes.append(index)
        if not is_negation and normalized_ignore_rule(rule) == required_rule:
            required_index = index
    if required_index is None:
        fail(f"{ignore_path} must explicitly exclude {required_rule}/")
    if any(index > required_index for index in negation_indexes):
        fail(f"move the {required_rule}/ exclusion after all negation rules in {ignore_path}")
    return ignore_path


def require_clean_source(source_git_root, ansirover_dir):
    command = [
        "git", "-C", str(source_git_root), "status", "--porcelain", "--untracked-files=all", "--", "."
    ]
    relative_ansirover = relative_path(source_git_root, ansirover_dir)
    if relative_ansirover is not None:
        command.append(f":(exclude){relative_ansirover.as_posix().strip('/')}")
    completed = run(command, cwd=source_git_root, capture=True, label="git")
    if completed.stdout.strip():
        print(completed.stdout, file=sys.stderr, end="")
        fail("the Git repository that owns build.context must be clean before image publication")


def image_tag(source_git_root):
    value = git_text(source_git_root, "rev-parse", "--short", "HEAD")
    if not value:
        fail("git rev-parse --short HEAD returned an empty value")
    return value


def validate_image(service_name, definition, expected_tag):
    image_reference = str(definition.get("image") or "").strip()
    if not image_reference:
        fail(f"Compose service {service_name!r} must define image")
    if not image_reference.startswith(REGISTRY_PREFIX):
        fail(f"Compose service {service_name!r} must publish under {REGISTRY_PREFIX}")
    if "@" in image_reference:
        fail(f"Compose service {service_name!r} must use a tagged image reference")
    repository, separator, resolved_tag = image_reference.rpartition(":")
    if not separator or not repository:
        fail(f"Compose service {service_name!r} image has no explicit tag")
    if resolved_tag != expected_tag:
        fail(f"Compose service {service_name!r} must resolve to :{expected_tag}, got :{resolved_tag}")
    return image_reference


def inventory_path(ansirover_dir, service_name):
    path = ansirover_dir / f"{service_name}.yaml"
    if not path.is_file():
        fail(f"matching AnsiRover inventory not found: {path}")
    return path


def inventory_with_image(path, image_reference):
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^deployedImageName:\s*.*$", re.MULTILINE)
    if len(pattern.findall(text)) != 1:
        fail(f"{path} must contain exactly one top-level deployedImageName entry")
    return pattern.sub("deployedImageName: " + image_reference, text, count=1)


def allowlisted_paths(ansirover_dir, service_name, actual_dockerfile, effective_ignore):
    paths = [ansirover_dir / COMPOSE_FILE_NAME, ansirover_dir / f"{service_name}.yaml"]
    for suffix in SIDECAR_SUFFIXES:
        path = ansirover_dir / f"{service_name}{suffix}"
        if path.is_file():
            paths.append(path)
    for path in (actual_dockerfile, effective_ignore):
        if path is not None and path.is_file() and relative_path(ansirover_dir, path) is not None and path not in paths:
            paths.append(path)
    return paths


def publication_target(repository):
    branch = git_text(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    origin = git_text(repository, "remote", "get-url", "origin")
    git_text(repository, "config", "--get", "user.name")
    git_text(repository, "config", "--get", "user.email")
    return branch, origin


def noninteractive_git_environment():
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    if "GIT_SSH_COMMAND" not in environment:
        environment["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return environment


def commit_paths(repository, paths, message):
    relative_paths = []
    for path in paths:
        relative = relative_path(repository, path.resolve())
        if relative is None:
            fail(f"path is outside Git repository {repository}: {path}")
        relative_paths.append(relative.as_posix())
    status = git_text(repository, "status", "--porcelain", "--untracked-files=all", "--", *relative_paths)
    if not status:
        return False
    run(["git", "-C", str(repository), "add", "--", *relative_paths], cwd=repository, label="git add")
    run(
        ["git", "-C", str(repository), "commit", "--only", "-m", message, "--", *relative_paths],
        cwd=repository,
        label="git commit",
    )
    return True


def push_branch(repository, branch):
    run(
        ["git", "-C", str(repository), "push", "origin", f"HEAD:{branch}"],
        cwd=repository,
        environment=noninteractive_git_environment(),
        label="git push",
    )


def build_and_push(ansirover_dir, compose_path, tag):
    environment = os.environ.copy()
    environment["IMAGE_TAG"] = tag
    run(
        ["docker", "compose", "-f", str(compose_path), "build", "--push"],
        cwd=ansirover_dir,
        environment=environment,
        label="build/push",
    )


def print_usage():
    print("""Usage:
  ansirover-compose-build-push <path> [--dry-run]

Contract:
  one Git repository = one deployable app
  one ansirover/ = one app inventory
  one compose.yaml = one service = one image
  source Git repository must be clean
  ansirover/ must be excluded from build.context

Behavior:
  Builds and pushes the image, updates <service>.yaml deployedImageName,
  commits/pushes only known AnsiRover files, and updates/pushes the parent
  gitlink when ansirover/ is a submodule. Never prompts for input.

Examples:
  ansirover-compose-build-push .
  ansirover-compose-build-push ./ansirover
  ansirover-compose-build-push . --dry-run
""")


def parse_args(arguments):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("path")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(arguments)


def main(arguments=None):
    raw_arguments = sys.argv[1:]
    if arguments is not None:
        raw_arguments = list(arguments)
    if not raw_arguments or "-h" in raw_arguments or "--help" in raw_arguments:
        print_usage()
        return 0

    args = parse_args(raw_arguments)
    application_root, application_git_root, ansirover_dir, compose_path = resolve_layout(args.path)
    require_flat_ansirover(ansirover_dir)
    ansirover_git_root = git_root(ansirover_dir, "AnsiRover")
    if ansirover_git_root not in (application_git_root, ansirover_dir):
        fail("ansirover/ must belong to the application repo or be its own submodule repo")

    ansirover_branch, ansirover_origin = publication_target(ansirover_git_root)
    application_branch, application_origin = ansirover_branch, ansirover_origin
    if ansirover_git_root != application_git_root:
        application_branch, application_origin = publication_target(application_git_root)

    discovery = compose_model(ansirover_dir, compose_path, DISCOVERY_IMAGE_TAG)
    service_name, discovery_definition = single_service(discovery)
    app_inventory = inventory_path(ansirover_dir, service_name)
    context = build_context(ansirover_dir, service_name, discovery_definition)
    source_git_root = git_root(context, "build.context source")
    discovery_ignore = validate_dockerignore(context, ansirover_dir, service_name, discovery_definition)
    require_clean_source(source_git_root, ansirover_dir)
    tag = image_tag(source_git_root)

    resolved = compose_model(ansirover_dir, compose_path, tag)
    resolved_service_name, definition = single_service(resolved)
    if resolved_service_name != service_name:
        fail("Compose service name changed while applying IMAGE_TAG")
    resolved_context = build_context(ansirover_dir, service_name, definition)
    if resolved_context != context:
        fail("build.context must not depend on IMAGE_TAG")
    effective_ignore = validate_dockerignore(context, ansirover_dir, service_name, definition)
    if effective_ignore != discovery_ignore:
        fail("effective Docker ignore file must not depend on IMAGE_TAG")
    actual_dockerfile = dockerfile_path(context, service_name, definition)
    image_reference = validate_image(service_name, definition, tag)
    inventory_with_image(app_inventory, image_reference)

    info(f"[plan] service: {service_name}")
    info(f"[plan] source Git root: {source_git_root}")
    info(f"[plan] IMAGE_TAG: {tag}")
    info(f"[plan] image: {image_reference}")

    ansirover_committed = False
    gitlink_committed = False
    if not args.dry_run:
        build_and_push(ansirover_dir, compose_path, tag)
        app_inventory.write_text(inventory_with_image(app_inventory, image_reference), encoding="utf-8")
        paths = allowlisted_paths(ansirover_dir, service_name, actual_dockerfile, effective_ignore)
        ansirover_committed = commit_paths(
            ansirover_git_root,
            paths,
            f"chore: publish {service_name} {tag}",
        )
        push_branch(ansirover_git_root, ansirover_branch)
        if ansirover_git_root != application_git_root:
            gitlink_committed = commit_paths(
                application_git_root,
                [ansirover_dir],
                f"chore: update ansirover for {service_name} {tag}",
            )
            push_branch(application_git_root, application_branch)

    print("IMAGE_TAG=" + tag)
    print("IMAGE=" + image_reference)
    print("SOURCE_GIT_ROOT=" + str(source_git_root))
    print("ANSIROVER_GIT_ROOT=" + str(ansirover_git_root))
    print("ANSIROVER_BRANCH=" + ansirover_branch)
    print("ANSIROVER_ORIGIN=" + ansirover_origin)
    print("APPLICATION_BRANCH=" + application_branch)
    print("APPLICATION_ORIGIN=" + application_origin)
    print("BUILD_PUSH_COMPLETED=" + str(not args.dry_run).lower())
    print("ANSIROVER_COMMITTED=" + str(ansirover_committed).lower())
    print("APPLICATION_GITLINK_COMMITTED=" + str(gitlink_committed).lower())
    print("GIT_PUBLICATION_COMPLETED=" + str(not args.dry_run).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
