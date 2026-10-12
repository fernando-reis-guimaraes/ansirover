#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ANSIROVER_DIR_NAME = "ansirover"
ANSIROVER_DIR_PREFIX = "ansirover."
COMPOSE_FILE_NAME = "compose.yaml"
REGISTRY_PREFIX = "cr.umapps.net/umapps/"
SIDECAR_SUFFIXES = (
    ".env-vars.env",
    ".custom-nginx-config.conf",
    ".pre-deploy-function.js",
    ".service-update-override.yaml",
)

DOCKERFILE_CANDIDATES = [
    "Dockerfile",
    "Dockerfile.production",
    "Dockerfile.prod",
    "backend/Dockerfile",
    "backend/Dockerfile.production",
    "app/Dockerfile",
    "src/Dockerfile",
    "web/Dockerfile",
    "server/Dockerfile",
    "docker/Dockerfile",
]

REPOSITORY_ROOT = Path(__file__).resolve().parent
UUIDGETSET_NGINX_SNIPPET = REPOSITORY_ROOT / "templates" / "nginx-uuidgetset-snippet.conf"


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def validate_simple_name(label, value):
    if not value:
        fail(f"{label} must not be empty")
    if "\x00" in value:
        fail(f"{label} must not contain NUL byte: {value!r}")
    if "/" in value or "\\" in value:
        fail(f"{label} must not contain path separators: {value}")
    if value in {".", ".."}:
        fail(f"{label} must not be . or ..")
    if any(character.isspace() for character in value):
        fail(f"{label} must not contain whitespace: {value}")


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


def git_text(repository, *arguments):
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        fail("command not found: git")
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, file=sys.stderr, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        fail(f"command failed with exit status {completed.returncode}: git {' '.join(arguments)}")
    return completed.stdout.strip()


def git_root(path, purpose):
    value = git_text(path, "rev-parse", "--show-toplevel")
    if not value:
        fail(f"could not determine {purpose} Git root from: {path}")
    return Path(value).resolve()


def resolve_application_root(input_path):
    application_root = Path(input_path).expanduser().resolve()
    if not application_root.is_dir():
        fail(f"path is not a directory: {application_root}")
    application_git_root = git_root(application_root, "application")
    if application_git_root != application_root:
        fail(
            "path must be exactly the application Git root; resolved Git root is: "
            + str(application_git_root)
        )
    return application_root


def sanitize_app_name(raw):
    value = (raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9-_]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-_")
    return value


def detect_app_name(application_root, override):
    if override:
        validate_simple_name("app name", override)
        return override
    package_file = application_root / "package.json"
    if package_file.is_file():
        try:
            data = json.loads(package_file.read_text(encoding="utf-8"))
            candidate = str(data.get("name", "")).strip()
            if candidate:
                # npm names may contain slashes for scopes; keep only final segment.
                candidate = candidate.split("/")[-1].strip()
                sanitized = sanitize_app_name(candidate)
                if sanitized:
                    try:
                        validate_simple_name("app name", sanitized)
                        return sanitized
                    except SystemExit:
                        pass
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
    fallback = sanitize_app_name(application_root.name)
    if not fallback:
        fail("could not derive app name; use --app-name=<name>")
    validate_simple_name("app name", fallback)
    return fallback


def detect_dockerfile(application_root, context_override, dockerfile_override):
    if dockerfile_override:
        candidate = Path(dockerfile_override).expanduser()
        if not candidate.is_absolute():
            candidate = (application_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if not candidate.is_file():
            fail(f"override Dockerfile does not exist: {candidate}")
        context = application_root.resolve()
        if context_override:
            context = Path(context_override).expanduser()
            if not context.is_absolute():
                context = (application_root / context).resolve()
            else:
                context = context.resolve()
            if not context.is_dir():
                fail(f"override build context is not a directory: {context}")
        try:
            dockerfile_rel = candidate.relative_to(context).as_posix()
        except ValueError:
            fail(f"Dockerfile is outside build context: {candidate} not in {context}")
        return context, candidate, dockerfile_rel

    for candidate_rel in DOCKERFILE_CANDIDATES:
        candidate = application_root / candidate_rel
        if candidate.is_file():
            context = application_root.resolve()
            if context_override:
                context = Path(context_override).expanduser()
                if not context.is_absolute():
                    context = (application_root / context).resolve()
                else:
                    context = context.resolve()
                if not context.is_dir():
                    fail(f"override build context is not a directory: {context}")
            try:
                dockerfile_rel = candidate.resolve().relative_to(context).as_posix()
            except ValueError:
                continue
            return context, candidate.resolve(), dockerfile_rel

    fail(
        "no Dockerfile found; checked "
        + ", ".join(DOCKERFILE_CANDIDATES)
        + "; use --dockerfile=<path> to point at one"
    )


def detect_port(application_root, override):
    if override is not None:
        if override < 1 or override > 65535:
            fail("--port must be between 1 and 65535")
        return override
    # .env.example APP_PORT=3000
    env_example = application_root / ".env.example"
    if env_example.is_file():
        try:
            for line in env_example.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s*APP_PORT\s*=\s*(\d{2,5})\s*", line)
                if match:
                    port = int(match.group(1))
                    if 1 <= port <= 65535:
                        return port
        except OSError:
            pass
    # Dockerfile ENV APP_PORT / EXPOSE
    for candidate_rel in DOCKERFILE_CANDIDATES:
        dockerfile = application_root / candidate_rel
        if not dockerfile.is_file():
            continue
        try:
            text = dockerfile.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"APP_PORT\s*=\s*(\d{2,5})", text)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
        match = re.search(r"^EXPOSE\s+(\d{2,5})\b", text, re.MULTILINE)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
    # Root compose.yaml APP_PORT fallback or first host mapping.
    root_compose = application_root / COMPOSE_FILE_NAME
    if root_compose.is_file():
        try:
            text = root_compose.read_text(encoding="utf-8")
            match = re.search(r"APP_PORT:-(\d{2,5})", text)
            if match:
                port = int(match.group(1))
                if 1 <= port <= 65535:
                    return port
            match = re.search(r'"(\d{2,5}):\d{2,5}"', text)
            if match:
                port = int(match.group(1))
                if 1 <= port <= 65535:
                    return port
        except OSError:
            pass
    if (application_root / "package.json").is_file():
        return 3000
    return 80


def detect_persistent(application_root):
    root_compose = application_root / COMPOSE_FILE_NAME
    if root_compose.is_file():
        try:
            text = root_compose.read_text(encoding="utf-8")
            if re.search(r"^\s*volumes\s*:", text, re.MULTILINE):
                return True
        except OSError:
            pass
    for candidate_rel in DOCKERFILE_CANDIDATES:
        dockerfile = application_root / candidate_rel
        if not dockerfile.is_file():
            continue
        try:
            text = dockerfile.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"^\s*VOLUME\b", text, re.MULTILINE):
            return True
    return False


def detect_volumes(application_root, app_name):
    """Extract container data paths from the first buildable root-compose service.

    Returns a list of {"volumeName": ..., "containerPath": ...} mappings in the
    CapRover inventory shape. Local bind sources (./data) are not carried over;
    only the container path is kept, backed by a named CapRover volume.
    """
    root_compose = application_root / COMPOSE_FILE_NAME
    if not root_compose.is_file():
        return []
    try:
        lines = root_compose.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    current_service = None
    service_has_build = False
    in_volumes = False
    items = []
    services = {}

    def flush():
        if current_service is not None and current_service not in services:
            services[current_service] = (service_has_build, list(items))

    for line in lines:
        service_match = re.match(r"^  (\S+):\s*$", line)
        if service_match:
            flush()
            current_service = service_match.group(1)
            service_has_build = False
            in_volumes = False
            items = []
            continue
        if current_service is None:
            continue
        if re.match(r"^    build\s*:", line):
            service_has_build = True
            in_volumes = False
            continue
        if re.match(r"^    volumes\s*:\s*$", line):
            in_volumes = True
            continue
        if re.match(r"^    \S", line):
            in_volumes = False
            continue
        if in_volumes:
            item_match = re.match(r"^      -\s*(.+?)\s*$", line)
            if item_match:
                items.append(item_match.group(1).strip().strip("'\""))
    flush()
    for _service, (has_build, entries) in services.items():
        if not has_build:
            continue
        volumes = []
        seen = set()
        for entry in entries:
            parts = entry.split(":")
            if len(parts) < 2:
                continue
            container_path = parts[1].strip()
            if not container_path.startswith("/") or container_path in seen:
                continue
            seen.add(container_path)
            base = sanitize_app_name(container_path.rsplit("/", 1)[-1]) or "data"
            volumes.append(
                {"volumeName": f"{app_name}-{base}", "containerPath": container_path}
            )
        return volumes
    return []


def detect_root_compose_notes(application_root):
    root_compose = application_root / COMPOSE_FILE_NAME
    if not root_compose.is_file():
        return []
    try:
        text = root_compose.read_text(encoding="utf-8")
    except OSError:
        return []
    notes = []
    services = re.findall(r"^\s{2}(\S+):\s*$", text, re.MULTILINE)
    # Crude multi-service detection: count top-level service-like keys.
    build_count = len(re.findall(r"^\s+build\s*:", text, re.MULTILINE))
    if build_count == 0:
        notes.append("root compose.yaml defines no buildable service; bootstrap picked the discovered Dockerfile.")
    elif build_count == 1:
        notes.append("root compose.yaml defines one buildable service; bootstrap reused its Dockerfile intent.")
    else:
        notes.append(
            f"root compose.yaml defines {build_count} buildable-looking services; "
            "AnsiRover supports one service per environment, so bootstrap kept only the "
            "discovered Dockerfile service. Record here why the other containers "
            "(for example an edge proxy such as Caddy) are not separate AnsiRover deployments."
        )
    if re.search(r"caddy", text, re.IGNORECASE):
        notes.append(
            "Root deployment uses Caddy as an edge proxy. CapRover provides ingress; "
            "do not add Caddy as a second AnsiRover service."
        )
    return notes


def detect_public_base_image(dockerfile_path):
    try:
        text = dockerfile_path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        # Ignore internal registry base images.
        if REGISTRY_PREFIX in stripped:
            continue
        return True
    return False


def default_image(app_name, override):
    if override:
        image = override.strip()
        if not image.startswith(REGISTRY_PREFIX):
            fail(f"--image must publish under {REGISTRY_PREFIX}, got: {image}")
        if "@" in image:
            fail("--image must use a tagged reference, not a digest")
        return image
    return f"{REGISTRY_PREFIX}{app_name}" + ":${IMAGE_TAG:-beta}"


def render_compose(app_name, image, dockerfile_rel):
    return f"""# AnsiRover canonical build/publication definition (generated by ansirover-compose-bootstrap).
# One service = one image. Verify build.context and dockerfile before first publish.
# IMAGE_TAG is supplied by ansirover-compose-build-push from the source Git short HEAD.
services:
  {app_name}:
    image: {image}
    build:
      context: ..
      dockerfile: {dockerfile_rel}
      platforms:
        - linux/amd64
        - linux/arm64
"""


def render_yaml(app_name, image, port, persistent, volumes):
    # Strip the ${IMAGE_TAG} template for the initial inventory placeholder;
    # build-push replaces deployedImageName with the immutable published reference.
    placeholder = image.replace("${IMAGE_TAG:-beta}", "beta").replace("${IMAGE_TAG}", "beta")
    if volumes:
        volumes_section = "volumes:\n" + "\n".join(
            f"  - volumeName: {entry['volumeName']}\n    containerPath: {entry['containerPath']}"
            for entry in volumes
        )
    else:
        volumes_section = "volumes: []"
    return f"""appName: {app_name}
deployedImageName: {placeholder}
description: {app_name} AnsiRover deployment (generated by ansirover-compose-bootstrap; verify before publish)
ansirover:
  gracePeriod: 3m
  postgresReconnectThreshold: 3
  postgresPollInterval: 1s
hasPersistentData: {"true" if persistent else "false"}
instanceCount: 1
containerHttpPort: {port}
notExposeAsWebApp: false
websocketSupport: false
customDomain: []
# To expose a public domain, replace the line above with:
# customDomain:
#   - publicDomain: app.example.com
#     hasSsl: true
tags: []
httpAuth:
  isEnabled: false
  user: ""
  password: ""
{volumes_section}
ports: []
"""


def render_env(app_name):
    return f"""# DELETE THIS FILE IF THE APP DOES NOT NEED ENVIRONMENT VARIABLES.
# AnsiRover env-vars sidecar for {app_name}.
# One KEY=value per line.
# Values may reference process environment variables with ${{VAR}}.
# Use $${{VAR}} to keep a literal ${{VAR}}. Expansion is one-pass and nonrecursive.
# These values are restored as CapRover app environment variables.

APP_NAME={app_name}
EXAMPLE_VALUE=change-me
"""


def render_uuidgetset_nginx_guard():
    if not UUIDGETSET_NGINX_SNIPPET.is_file():
        fail(f"missing canonical Nginx guard: {UUIDGETSET_NGINX_SNIPPET}")
    return UUIDGETSET_NGINX_SNIPPET.read_text(encoding="utf-8").rstrip()


def render_custom_nginx(app_name):
    uuidgetset_guard = render_uuidgetset_nginx_guard()
    template = """# DELETE THIS FILE IF THE APP DOES NOT NEED CUSTOM NGINX CONFIG.
# AnsiRover custom Nginx sidecar for __APP_NAME__.
# Keep this file valid as an Nginx config fragment.
# Collections ending in _uuidgetset automatically use the UUID-only item guard below.

<%
if (s.forceSsl) {
%>
    server {
        listen 80;
        server_name <%-s.publicDomain%>;

        location /.well-known/acme-challenge/ {
            root <%-s.staticWebRoot%>;
        }

        location /.well-known/captain-identifier {
            root <%-s.staticWebRoot%>;
        }

        location / {
            return 302 https://$http_host$request_uri;
        }
    }
<%
}
%>

server {
    <%
    if (!s.forceSsl) {
    %>
        listen 80;
    <%
    }
    if (s.hasSsl) {
    %>
        listen 443 ssl;
        http2 on;
        ssl_certificate <%-s.crtPath%>;
        ssl_certificate_key <%-s.keyPath%>;
    <%
    }
    if (s.logAccessPath) {
    %>
        access_log <%-s.logAccessPath%>;
    <%
    }
    %>

    client_max_body_size 500m;
    server_name <%-s.publicDomain%>;
    resolver 127.0.0.11 valid=10s;
    set $upstream http://<%-s.localDomain%>:<%-s.containerHttpPort%>;

__UUIDGETSET_GUARD__

    location / {
        <%
        if (s.redirectToPath) {
        %>
            return 302 <%-s.redirectToPath%>$request_uri;
        <%
        } else {
        %>
            <%
            if (s.httpBasicAuthPath) {
            %>
                auth_basic "Restricted Access";
                auth_basic_user_file <%-s.httpBasicAuthPath%>;
            <%
            }
            %>

            proxy_pass $upstream;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            <%
            if (s.websocketSupport) {
            %>
                proxy_set_header Upgrade $http_upgrade;
                proxy_set_header Connection "upgrade";
                proxy_http_version 1.1;
            <%
            }
            %>
        <%
        }
        %>
    }

    location /.well-known/acme-challenge/ {
        root <%-s.staticWebRoot%>;
    }

    location /.well-known/captain-identifier {
        root <%-s.staticWebRoot%>;
    }

    error_page 502 /captain_502_custom_error_page.html;
    location = /captain_502_custom_error_page.html {
        root <%-s.customErrorPagesDirectory%>;
        internal;
    }
}
"""
    indented_guard = "\n".join(f"    {line}" if line else "" for line in uuidgetset_guard.splitlines())
    return template.replace("__APP_NAME__", app_name).replace("__UUIDGETSET_GUARD__", indented_guard)


def render_pre_deploy(app_name):
    return f"""// DELETE THIS FILE IF THE APP DOES NOT NEED A PRE-DEPLOY FUNCTION.
// AnsiRover pre-deploy function sidecar for {app_name}.
// Keep this as valid JavaScript.

module.exports = async function preDeployFunction() {{
  return true;
}};
"""


def render_service_override(app_name):
    return f"""# DELETE THIS FILE IF THE APP DOES NOT NEED DOCKER SERVICE OVERRIDES.
# AnsiRover service update override sidecar for {app_name}.
# Keep this as a Docker Compose service fragment compatible with CapRover.
# New CapRover uses the bare service name below; old CapRover used srv-captain--{app_name}.

version: "3.7"

services:
  {app_name}:
    environment:
      EXAMPLE_FROM_SERVICE_OVERRIDE: "change-me"
"""


def deployment_paths(application_root):
    paths = []
    for child in application_root.iterdir():
        if child.name != ANSIROVER_DIR_NAME and not child.name.startswith(ANSIROVER_DIR_PREFIX):
            continue
        if child.is_dir() or child.is_symlink():
            paths.append(child)
    return sorted(paths, key=lambda path: path.name)


def required_ignore_rules(application_root, context, selected_env_name):
    required = []
    candidates = {ANSIROVER_DIR_NAME, environment_directory_name(selected_env_name)}
    for child in application_root.iterdir():
        if child.name != ANSIROVER_DIR_NAME and not child.name.startswith(ANSIROVER_DIR_PREFIX):
            continue
        if child.is_dir() or child.is_symlink():
            candidates.add(child.name)
    context_resolved = context.resolve()
    for name in sorted(candidates):
        path = application_root / name
        resolved = path.resolve() if path.is_symlink() else path
        # Skip the symlink itself when it points inside the context; the target covers it.
        try:
            rel = resolved.relative_to(context_resolved) if resolved.exists() else Path(name)
            # If path does not exist yet (selected env to be created), treat as top-level name.
            if not path.exists():
                rel = Path(name)
        except ValueError:
            continue
        normalized = rel.as_posix().strip("/")
        if normalized and normalized not in required:
            required.append(normalized)
    # Filter to paths actually inside the build context.
    filtered = []
    for rule in required:
        candidate = (context_resolved / rule).resolve()
        try:
            candidate.relative_to(context_resolved)
            filtered.append(rule)
        except ValueError:
            continue
    return filtered


def effective_ignore_path(context, dockerfile_path):
    specific = dockerfile_path.with_name(dockerfile_path.name + ".dockerignore")
    if specific.is_file():
        return specific
    return context / ".dockerignore"


def normalized_ignore_rule(line):
    value = line.strip()
    while value.startswith("./"):
        value = value[2:]
    return value.replace("\\", "/").strip("/")


def missing_ignore_rules(ignore_path, required_rules):
    if not ignore_path.is_file():
        return list(required_rules)
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return list(required_rules)
    required_indexes = {rule: None for rule in required_rules}
    last_negation_index = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("!"):
            last_negation_index = index
            continue
        normalized = normalized_ignore_rule(stripped)
        if normalized in required_indexes:
            required_indexes[normalized] = index
    missing = []
    for rule, rule_index in required_indexes.items():
        if rule_index is None or rule_index <= last_negation_index:
            missing.append(rule)
    return missing


def write_text_file(path, content, dry_run):
    if dry_run:
        info(f"[dry-run] would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    info(f"[write] {path}")


def print_usage():
    print("""Usage:
  ansirover-compose-bootstrap <application-git-root> [options]

Contract:
  the positional path must be exactly the application Git top-level
  generates a 90% AnsiRover scaffold; a human or agent verifies before publish
  one selected AnsiRover environment = one app inventory
  one compose.yaml = one service = one image

Options:
  --environment=<name>   use <application-git-root>/ansirover.<name>/ (default: ansirover/)
  --app-name=<name>      CapRover app/service name (default: derived from package.json or directory)
  --image=<image>        image template under cr.umapps.net/umapps/ (default: cr.umapps.net/umapps/<app>:${IMAGE_TAG:-beta})
  --port=<port>          containerHttpPort (default: auto-detected, else 3000 for Node or 80)
  --context=<path>       build context relative to application root (default: .)
  --dockerfile=<path>    Dockerfile relative to application root (default: auto-detected)
  --minimal              create minimal YAML + compose (default)
  --with-examples        also create example env-vars/nginx/pre-deploy/override sidecars
  --force                overwrite existing generated files
  --dry-run              print detection plan without writing files

Examples:
  ansirover-compose-bootstrap .
  ansirover-compose-bootstrap . --environment=production
  ansirover-compose-bootstrap . --app-name=crosspaste --port=3000 --force
  ansirover-compose-bootstrap . --dry-run
""")


def parse_args(arguments):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("path", nargs="?")
    parser.add_argument("--environment")
    parser.add_argument("--app-name")
    parser.add_argument("--image")
    parser.add_argument("--port", type=int)
    parser.add_argument("--context")
    parser.add_argument("--dockerfile")
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--with-examples", action="store_true")
    parser.add_argument("--force", action="store_true")
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
    if not args.path:
        print_usage()
        return 0
    if args.minimal and args.with_examples:
        fail("--minimal and --with-examples cannot be used together")

    validate_environment_name(args.environment)
    application_root = resolve_application_root(args.path)
    env_dir_name = environment_directory_name(args.environment)
    ansirover_dir = application_root / env_dir_name

    app_name = detect_app_name(application_root, args.app_name)
    context, dockerfile_abs, dockerfile_rel = detect_dockerfile(
        application_root, args.context, args.dockerfile
    )
    port = detect_port(application_root, args.port)
    persistent = detect_persistent(application_root)
    image = default_image(app_name, args.image)
    notes = detect_root_compose_notes(application_root)
    uses_public_base = detect_public_base_image(dockerfile_abs)
    volumes = detect_volumes(application_root, app_name)
    persistent = persistent or bool(volumes)

    try:
        context_rel = context.resolve().relative_to(application_root.resolve()).as_posix()
        if context_rel == "." or not context_rel:
            context_rel = "."
            compose_context = ".."
        else:
            compose_context = f"../{context_rel}"
    except ValueError:
        fail(f"build context must be inside the application root: {context}")
    # Bootstrap v1 always uses application-root context for the canonical compose file
    # unless an explicit --context override points inside the application root.
    if args.context:
        compose_context = context_rel if Path(compose_context).is_absolute() else compose_context
    else:
        compose_context = ".."
        context = application_root.resolve()
        try:
            dockerfile_rel = dockerfile_abs.relative_to(context).as_posix()
        except ValueError:
            fail(f"Dockerfile is outside build context: {dockerfile_abs}")

    if ansirover_dir.is_symlink():
        resolved = ansirover_dir.resolve()
        if application_root.resolve() not in resolved.parents and resolved != application_root.resolve():
            fail(f"AnsiRover environment must resolve inside the application Git root: {ansirover_dir}")
        info(f"[config] ansirover symlink resolves to: {resolved}")

    compose_path = ansirover_dir / COMPOSE_FILE_NAME
    inventory_path = ansirover_dir / f"{app_name}.yaml"
    sidecar_paths = {
        f"{app_name}{suffix}": ansirover_dir / f"{app_name}{suffix}"
        for suffix in SIDECAR_SUFFIXES
    }

    with_examples = bool(args.with_examples)
    generated = [compose_path, inventory_path]
    if with_examples:
        generated.extend(sidecar_paths.values())

    if not args.force:
        for path in generated:
            if path.exists():
                fail(f"file already exists; use --force to overwrite: {path}")

    required_rules = required_ignore_rules(application_root, context, args.environment)
    ignore_path = effective_ignore_path(context, dockerfile_abs)
    missing_rules = missing_ignore_rules(ignore_path, required_rules) if required_rules else []

    environment_label = args.environment if args.environment is not None else "default"
    info(f"[plan] application root: {application_root}")
    info(f"[plan] environment: {environment_label} -> {ansirover_dir}")
    info(f"[plan] app: {app_name}")
    info(f"[plan] image: {image}")
    info(f"[plan] port: {port}")
    info(f"[plan] persistent: {persistent}")
    info(f"[plan] build context: {context} (compose: {compose_context})")
    info(f"[plan] dockerfile: {dockerfile_abs} (compose: {dockerfile_rel})")
    info(f"[plan] with-examples: {with_examples}")
    info(f"[plan] volumes: {len(volumes)} detected")
    if uses_public_base:
        info("[config] Dockerfile uses a public base image; prefer an internal registry base when an equivalent exists")
    for note in notes:
        info(f"[config] {note}")
    if missing_rules:
        info(f"[config] Docker ignore {ignore_path} misses: " + ", ".join(r + "/" for r in missing_rules))
    else:
        info(f"[check] Docker ignore coverage OK: {ignore_path}")

    if args.dry_run:
        info("[dry-run] no files written")
    else:
        ansirover_dir.mkdir(parents=True, exist_ok=True)
        compose_content = render_compose(app_name, image, dockerfile_rel)
        # Allow explicit compose context override.
        if compose_context != "..":
            compose_content = compose_content.replace("context: ..", f"context: {compose_context}")
        write_text_file(compose_path, compose_content, False)
        write_text_file(
            inventory_path, render_yaml(app_name, image, port, persistent, volumes), False
        )
        if with_examples:
            write_text_file(sidecar_paths[f"{app_name}.env-vars.env"], render_env(app_name), False)
            write_text_file(
                sidecar_paths[f"{app_name}.custom-nginx-config.conf"],
                render_custom_nginx(app_name),
                False,
            )
            write_text_file(
                sidecar_paths[f"{app_name}.pre-deploy-function.js"],
                render_pre_deploy(app_name),
                False,
            )
            write_text_file(
                sidecar_paths[f"{app_name}.service-update-override.yaml"],
                render_service_override(app_name),
                False,
            )
        if missing_rules:
            lines = []
            if ignore_path.is_file():
                try:
                    existing = ignore_path.read_text(encoding="utf-8")
                    lines.append(existing.rstrip() + "\n")
                except OSError:
                    pass
            else:
                lines.append("# Docker ignore (extended by ansirover-compose-bootstrap).\n")
            lines.append("\n# AnsiRover deployment directories must not enter the Docker build context.\n")
            for rule in missing_rules:
                lines.append(rule + "/\n")
            ignore_path.parent.mkdir(parents=True, exist_ok=True)
            ignore_path.write_text("".join(lines), encoding="utf-8")
            info(f"[write] Docker ignore updated: {ignore_path}")

    print("ENVIRONMENT=" + environment_label)
    print("ANSIROVER_DIR=" + str(ansirover_dir))
    print("APP_NAME=" + app_name)
    print("IMAGE_TEMPLATE=" + image)
    print("CONTAINER_PORT=" + str(port))
    print("BUILD_CONTEXT=" + str(context))
    print("DOCKERFILE=" + str(dockerfile_abs))
    print("COMPOSE_PATH=" + str(compose_path))
    print("INVENTORY_PATH=" + str(inventory_path))
    print("DOCKER_IGNORE=" + str(ignore_path))
    print("BOOTSTRAP_COMPLETED=" + str(not args.dry_run).lower())
    print("NEEDS_REVIEW=true")
    print("")
    print("Next: review ansirover/<app>.yaml, then run:")
    print(f"  ansirover-compose-build-push {application_root}" + (f" --environment {args.environment}" if args.environment else "") + " --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
