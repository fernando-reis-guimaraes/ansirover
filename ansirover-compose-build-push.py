#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ANSIROVER_DIR_NAME = "ansirover"
ANSIROVER_DIR_PREFIX = "ansirover."
COMPOSE_FILE_NAME = "compose.yaml"
README_FILE_NAME = "README.md"
DEPLOY_FILE_NAME = "DEPLOY.md"
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


def validate_environment_name(environment_name):
    if environment_name is None:
        return
    if not environment_name:
        fail("environment name must not be empty")
    if "\x00" in environment_name:
        fail("environment name must not contain NUL byte")
    if "/" in environment_name or "\\" in environment_name:
        fail(f"environment name must not contain path separators: {environment_name}")
    if environment_name in {".", ".."}:
        fail(f"environment name must not be {environment_name}")
    if any(character.isspace() for character in environment_name):
        fail(f"environment name must not contain whitespace: {environment_name}")


def environment_directory_name(environment_name):
    if environment_name is None:
        return ANSIROVER_DIR_NAME
    return ANSIROVER_DIR_PREFIX + environment_name


def resolve_layout(input_path, environment_name=None):
    validate_environment_name(environment_name)
    application_root = Path(input_path).expanduser().resolve()
    if not application_root.is_dir():
        fail(f"path is not a directory: {application_root}")

    application_git_root = git_root(application_root, "application")
    if application_git_root != application_root:
        fail(
            "path must be exactly the application Git root; resolved Git root is: "
            + str(application_git_root)
        )

    ansirover_dir = application_root / environment_directory_name(environment_name)
    if not ansirover_dir.is_dir():
        fail(f"AnsiRover environment directory does not exist: {ansirover_dir}")

    resolved_ansirover_dir = ansirover_dir.resolve()
    if relative_path(application_root, resolved_ansirover_dir) is None:
        fail(f"AnsiRover environment must resolve inside the application Git root: {ansirover_dir}")

    compose_path = ansirover_dir / COMPOSE_FILE_NAME
    if not compose_path.is_file():
        fail(f"expected AnsiRover Compose file: {compose_path}")

    return application_root, application_git_root, ansirover_dir, compose_path


def deployment_paths(application_root):
    paths = []
    for child in application_root.iterdir():
        if child.name != ANSIROVER_DIR_NAME and not child.name.startswith(ANSIROVER_DIR_PREFIX):
            continue
        if child.is_dir() or child.is_symlink():
            paths.append(child)
    return sorted(paths, key=lambda path: path.name)


def require_flat_ansirover(ansirover_dir):
    nested = sorted(
        child.name
        for child in ansirover_dir.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )
    if nested:
        fail("AnsiRover environment directory must be flat; subdirectories are not supported: " + ", ".join(nested))


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
        fail("AnsiRover compose.yaml must define exactly one service")
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


def required_ansirover_ignore_rules(context, application_root):
    required_rules = []
    for path in deployment_paths(application_root):
        resolved_path = path.resolve()
        if resolved_path == context or relative_path(resolved_path, context) is not None:
            fail(f"AnsiRover deployment directory cannot contain build.context: {path}")
        relative = relative_path(context, path)
        if relative is None:
            continue
        normalized = relative.as_posix().strip("/")
        if normalized and normalized not in required_rules:
            required_rules.append(normalized)
    return required_rules


def fail_dockerignore_exclusion(
    message,
    ignore_path,
    remediation_rules,
    application_root,
    environment_name,
):
    ignore_repository = git_root(ignore_path.parent, "Docker ignore")
    relative_ignore = relative_path(ignore_repository, ignore_path)
    if relative_ignore is None:
        fail(f"Docker ignore file is outside its Git repository: {ignore_path}")

    quoted_rules = " ".join(shlex.quote(rule + "/") for rule in remediation_rules)
    quoted_ignore_path = shlex.quote(str(ignore_path))
    quoted_repository = shlex.quote(str(ignore_repository))
    quoted_relative_ignore = shlex.quote(relative_ignore.as_posix())
    quoted_commit_message = shlex.quote("chore: exclude ansirover from Docker context")
    quoted_application_root = shlex.quote(str(application_root))

    print(f"ERROR: {message}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix and continue with:", file=sys.stderr)
    print(
        f"  printf '\\n%s\\n' {quoted_rules} >> {quoted_ignore_path}",
        file=sys.stderr,
    )
    print(
        f"  git -C {quoted_repository} add -- {quoted_relative_ignore}",
        file=sys.stderr,
    )
    print(
        f"  git -C {quoted_repository} commit --only -m {quoted_commit_message} -- "
        f"{quoted_relative_ignore}",
        file=sys.stderr,
    )
    print(f"  git -C {quoted_repository} push origin HEAD", file=sys.stderr)
    rerun = f"  ansirover-compose-build-push {quoted_application_root}"
    if environment_name is not None:
        rerun += " --environment " + shlex.quote(environment_name)
    print(rerun, file=sys.stderr)
    raise SystemExit(1)


def validate_dockerignore(context, application_root, service_name, definition, environment_name=None):
    required_rules = required_ansirover_ignore_rules(context, application_root)
    if not required_rules:
        return None

    dockerfile = dockerfile_path(context, service_name, definition)
    ignore_path = context / ".dockerignore"
    if dockerfile is not None:
        specific = dockerfile.with_name(dockerfile.name + ".dockerignore")
        if specific.is_file():
            ignore_path = specific

    if not ignore_path.is_file():
        fail_dockerignore_exclusion(
            f"effective Docker ignore file does not exist: {ignore_path}",
            ignore_path,
            required_rules,
            application_root,
            environment_name,
        )

    required_indexes = {rule: None for rule in required_rules}
    last_negation_index = -1
    for index, line in enumerate(ignore_path.read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("!"):
            last_negation_index = index
            continue
        normalized = normalized_ignore_rule(stripped)
        if normalized in required_indexes:
            required_indexes[normalized] = index

    remediation_rules = []
    for rule, rule_index in required_indexes.items():
        if rule_index is None or rule_index <= last_negation_index:
            remediation_rules.append(rule)

    if remediation_rules:
        readable_rules = ", ".join(rule + "/" for rule in remediation_rules)
        fail_dockerignore_exclusion(
            f"{ignore_path} must exclude these AnsiRover paths after all negation rules: {readable_rules}",
            ignore_path,
            remediation_rules,
            application_root,
            environment_name,
        )
    return ignore_path


def require_clean_source(source_git_root, application_root):
    command = [
        "git",
        "-C",
        str(source_git_root),
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
    ]
    for path in deployment_paths(application_root):
        relative = relative_path(source_git_root, path)
        if relative is not None:
            command.append(f":(exclude){relative.as_posix().strip('/')}")
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
    for file_name in (README_FILE_NAME, DEPLOY_FILE_NAME):
        path = ansirover_dir / file_name
        if path.is_file():
            paths.append(path)
    for suffix in SIDECAR_SUFFIXES:
        path = ansirover_dir / f"{service_name}{suffix}"
        if path.is_file():
            paths.append(path)
    for path in (actual_dockerfile, effective_ignore):
        if path is None or not path.is_file():
            continue
        if relative_path(ansirover_dir.resolve(), path.resolve()) is None:
            continue
        if path not in paths:
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
  ansirover-compose-build-push <application-git-root> [--environment <name>] [--dry-run]

Contract:
  the positional path must be exactly the application Git top-level
  one selected AnsiRover environment = one app inventory
  one compose.yaml = one service = one image
  build.context must belong to a Git repository
  that source Git repository must be clean
  all ansirover/ and ansirover.* paths inside build.context must be excluded

Environment selection:
  no --environment       use <application-git-root>/ansirover
  --environment staging  use <application-git-root>/ansirover.staging

The default ansirover path may be a symlink to another ansirover.<name>
directory inside the application Git root.

Examples:
  ansirover-compose-build-push .
  ansirover-compose-build-push . --environment production
  ansirover-compose-build-push . --dry-run
""")


def parse_args(arguments):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("path")
    parser.add_argument("--environment")
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
    application_root, application_git_root, ansirover_dir, compose_path = resolve_layout(
        args.path,
        args.environment,
    )
    require_flat_ansirover(ansirover_dir)

    resolved_ansirover_dir = ansirover_dir.resolve()
    ansirover_git_root = git_root(ansirover_dir, "AnsiRover")
    if ansirover_git_root not in (application_git_root, resolved_ansirover_dir):
        fail("selected AnsiRover environment must belong to the application repo or be its own submodule repo")

    ansirover_branch, ansirover_origin = publication_target(ansirover_git_root)
    application_branch = ansirover_branch
    application_origin = ansirover_origin
    if ansirover_git_root != application_git_root:
        application_branch, application_origin = publication_target(application_git_root)

    discovery = compose_model(ansirover_dir, compose_path, DISCOVERY_IMAGE_TAG)
    service_name, discovery_definition = single_service(discovery)
    app_inventory = inventory_path(ansirover_dir, service_name)
    context = build_context(ansirover_dir, service_name, discovery_definition)
    source_git_root = git_root(context, "build.context source")
    discovery_ignore = validate_dockerignore(
        context,
        application_root,
        service_name,
        discovery_definition,
        args.environment,
    )
    require_clean_source(source_git_root, application_root)
    tag = image_tag(source_git_root)

    resolved = compose_model(ansirover_dir, compose_path, tag)
    resolved_service_name, definition = single_service(resolved)
    if resolved_service_name != service_name:
        fail("Compose service name changed while applying IMAGE_TAG")
    resolved_context = build_context(ansirover_dir, service_name, definition)
    if resolved_context != context:
        fail("build.context must not depend on IMAGE_TAG")
    effective_ignore = validate_dockerignore(
        context,
        application_root,
        service_name,
        definition,
        args.environment,
    )
    if effective_ignore != discovery_ignore:
        fail("effective Docker ignore file must not depend on IMAGE_TAG")
    actual_dockerfile = dockerfile_path(context, service_name, definition)
    image_reference = validate_image(service_name, definition, tag)
    inventory_with_image(app_inventory, image_reference)

    environment_label = "default"
    if args.environment is not None:
        environment_label = args.environment
    info(f"[plan] environment: {environment_label}")
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
                [resolved_ansirover_dir],
                f"chore: update ansirover for {service_name} {tag}",
            )
            push_branch(application_git_root, application_branch)

    print("ENVIRONMENT=" + environment_label)
    print("ANSIROVER_DIR=" + str(ansirover_dir))
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