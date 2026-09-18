#!/usr/bin/env python3

import argparse
import sys
from datetime import datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent
UUIDGETSET_NGINX_SNIPPET = REPOSITORY_ROOT / "templates" / "nginx-uuidgetset-snippet.conf"


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(f"[{log_timestamp()}] {message}", flush=True)


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


def resolve_app_dir(current_dir, app_name):
    current_dir_name = current_dir.name.lower()
    is_ansirover_environment = (
        current_dir_name == "ansirover" or current_dir_name.startswith("ansirover.")
    )
    if current_dir_name == app_name.lower() or is_ansirover_environment:
        return current_dir
    return current_dir / app_name


def require_writable_paths(paths, force):
    if force:
        return
    for path in paths:
        if path.exists():
            fail(f"file already exists; use --force to overwrite: {path}")


def write_text_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    info(f"[write] {path}")


def write_user_owned_file_if_missing(path, content):
    if path.exists():
        info(f"[keep] user-owned file already exists: {path}")
        return False
    write_text_file(path, content)
    return True


def render_yaml(app_name, image, port, minimal):
    if minimal:
        return f"""appName: {app_name}
deployedImageName: {image}
description: Minimal AnsiRover app template
hasPersistentData: false
instanceCount: 1
containerHttpPort: {port}
notExposeAsWebApp: false
websocketSupport: false
tags: []
httpAuth:
  isEnabled: false
  user: ""
  password: ""
volumes: []
ports: []
"""

    return f"""appName: {app_name}
deployedImageName: {image}
description: Example AnsiRover app template
hasPersistentData: true
instanceCount: 1
containerHttpPort: {port}
notExposeAsWebApp: false
websocketSupport: false
tags:
  - ansirover-template
ansirover:
  restartDelay: 1h
  gracePeriod: 3m
  postgresReconnectThreshold: 3
  postgresPollInterval: 1s
httpAuth:
  isEnabled: false
  user: ""
  password: ""
volumes:
  - volumeName: {app_name}-data
    containerPath: /data
  - hostPath: /opt/{app_name}/config
    containerPath: /config
ports:
  - containerPort: 8080
    hostPort: 8080
    protocol: tcp
"""


def render_env(app_name):
    return f"""# DELETE THIS FILE IF THE APP DOES NOT NEED ENVIRONMENT VARIABLES.
# AnsiRover env-vars sidecar for {app_name}.
# One KEY=value per line.
# Values may reference process environment variables with ${VAR}.
# Use ${VAR} to keep a literal ${VAR}. Expansion is one-pass and nonrecursive.
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

version: "3.7"

services:
  srv-captain--{app_name}:
    environment:
      EXAMPLE_FROM_SERVICE_OVERRIDE: "change-me"
"""


def render_readme(app_name):
    return f"""# {app_name} AnsiRover deployment

This directory contains the versioned AnsiRover deployment definition for `{app_name}`. `compose.yaml`, when present, is the canonical image build/publication definition; `DEPLOY.md` explains the deployment intent and architecture for humans and agents.

| File | Responsibility |
| --- | --- |
| `compose.yaml` | Canonical one-service image build/publication definition. Created or adapted by the deployment workflow. |
| `DEPLOY.md` | Deployment intent, runtime boundaries, external prerequisites, and project-specific guidance. |
| `{app_name}.yaml` | CapRover runtime inventory and desired app state. |
| `{app_name}.env-vars.env` | Optional CapRover environment variables. |
| `{app_name}.custom-nginx-config.conf` | Optional custom Nginx fragment. |
| `{app_name}.pre-deploy-function.js` | Optional pre-deploy hook. |
| `{app_name}.service-update-override.yaml` | Optional Docker service override. |

Delete optional example sidecars that this deployment does not need. Keep `DEPLOY.md` free of credentials and other secrets.
"""


def render_deploy(app_name):
    return f"""# Deployment intent for {app_name}

This file complements `compose.yaml`. The Compose file is the canonical build/publication definition; this file explains why the selected deployment model is correct and records project-specific constraints for humans and agents.

## Bootstrap ownership rule

If this file existed before the current bootstrap, treat it as user-owned and do not rewrite it automatically.

If `ansirover-template-app-create` created this file during the current bootstrap, the deployment agent must inspect the repository and adapt this template to the actual project before bootstrap is complete. Remove generic guidance that does not apply and record the real deployment model.

## Deployment unit

AnsiRover publishes one deployment as one image/service. Do not infer deployment units merely from directories, packages, or workspaces. Infer them from production build and runtime boundaries.

A repository may contain `frontend/`, `backend/`, `shared/`, workers, or multiple package-manager workspaces and still represent one deployable application when the production build packages them into one image/service.

A common preferred pattern is for the production backend build to compile the frontend and serve the built frontend from the same runtime process. In that case, document that the frontend is not an independent deployment and keep one AnsiRover service.

If the repository already contains `compose.yaml` in this AnsiRover environment, use that as the default declared deployment boundary and use this file as complementary context.

## Container build

Record the actual production container build definition here after inspection. A Dockerfile may have any name or location; do not require a suffix such as `Dockerfile.production`.

Document at least:

```text
Build context:
Dockerfile or other build definition:
Runtime entrypoint:
Container HTTP port:
```

The Docker build context is not necessarily the directory containing the Dockerfile. It must include every file the build needs.

## External services

External services are deployment prerequisites. Identify them from runtime configuration, but do not provision provider-specific infrastructure as part of the AnsiRover workflow.

Use an appropriate provider-specific skill/tool when available, then place the resulting connection/configuration values in the AnsiRover environment inventory. AnsiRover may validate required configuration, but ownership of creating and managing the external service remains outside AnsiRover.

Document provider intent without secrets. For example, one project may use local or self-hosted PostgreSQL/MinIO for staging and Neon/Backblaze B2 for production.

## Multiple source applications or components

If several components already converge into one aggregate production image/service, document that aggregate and use it.

If no aggregate deployment exists and several independent applications are discovered, do not silently invent an architecture. Explain the ambiguity to the user. Options may include:

- selecting an existing aggregate application/build;
- creating an explicit aggregate deployment artifact;
- putting independent applications in separate Git repositories and composing them from a parent Git superproject with Git submodules.

Git submodules are a recommended clean boundary, not a mandatory solution.

## Temporary container aggregation

A container aggregator or Docker-in-Docker design can technically reproduce a multi-container environment as one deployment unit. Do not choose this automatically. It adds security, lifecycle, observability, and operational complexity and is generally not recommended for production. It may be acceptable for short-lived tests or staging when reproducing an existing environment is more important than production-grade isolation.

## Environment

The default deployment directory is `ansirover/`. Named deployment environments use `ansirover.<name>/`, such as `ansirover.production/` or `ansirover.staging/`. Environment names are user-defined; AnsiRover does not assign special meaning to `production`, `staging`, or `local`.

The default `ansirover/` path may be a symlink to a named environment inside the application Git root.
"""


def print_usage_and_guide():
    print("""Usage:
  ansirover-template-app-create <app-name> [options]

Options:
  --image <image>      Docker image for deployedImageName. Default: nginx:alpine
  --port <port>        containerHttpPort. Default: 80
  --minimal            Create minimal YAML + README + DEPLOY and no example sidecars.
  --with-examples      Default behavior. Create YAML plus example sidecars.
  --force              Overwrite generated files except an existing DEPLOY.md.

Directory behavior:
  If the current directory is ansirover or ansirover.<environment>, create files directly there.
  If the current directory already matches <app-name>, create files directly there.
  Otherwise create a new <app-name>/ directory under the current directory.

The generator creates inventory documentation and deployment-intent scaffolding. It does
not create compose.yaml; the deployment workflow must inspect the application and create
or repair the canonical one-service Compose build definition separately.
""")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a local AnsiRover app template.",
        add_help=True,
    )
    parser.add_argument("app_name", nargs="?", help="CapRover app name to create")
    parser.add_argument(
        "--image",
        default="nginx:alpine",
        help="Docker image for deployedImageName; default: nginx:alpine",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=80,
        help="containerHttpPort; default: 80",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="create minimal YAML + README + DEPLOY and no example sidecars",
    )
    parser.add_argument(
        "--with-examples",
        action="store_true",
        help="create example sidecars; default behavior",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite generated files except an existing DEPLOY.md",
    )
    return parser.parse_args()


def main():
    if len(sys.argv) == 1:
        print_usage_and_guide()
        return

    args = parse_args()
    if not args.app_name:
        print_usage_and_guide()
        return
    if args.port < 1 or args.port > 65535:
        fail("--port must be between 1 and 65535")
    if args.minimal and args.with_examples:
        fail("--minimal and --with-examples cannot be used together")

    validate_simple_name("app name", args.app_name)

    current_dir = Path.cwd()
    app_dir = resolve_app_dir(current_dir, args.app_name)
    yaml_path = app_dir / f"{args.app_name}.yaml"
    readme_path = app_dir / "README.md"
    deploy_path = app_dir / "DEPLOY.md"

    generated_paths = [yaml_path, readme_path]
    if not args.minimal:
        generated_paths.extend(
            [
                app_dir / f"{args.app_name}.env-vars.env",
                app_dir / f"{args.app_name}.custom-nginx-config.conf",
                app_dir / f"{args.app_name}.pre-deploy-function.js",
                app_dir / f"{args.app_name}.service-update-override.yaml",
            ]
        )
    require_writable_paths(generated_paths, args.force)

    info("[plan] Action: create AnsiRover app template")
    info("[plan] App: " + args.app_name)
    info("[plan] Directory: " + str(app_dir))
    info("[plan] Image: " + args.image)
    info("[plan] Port: " + str(args.port))
    info("[plan] Minimal: " + str(args.minimal))
    info("[plan] Force: " + str(args.force))

    app_dir.mkdir(parents=True, exist_ok=True)
    write_text_file(
        yaml_path,
        render_yaml(
            app_name=args.app_name,
            image=args.image,
            port=args.port,
            minimal=args.minimal,
        ),
    )

    if not args.minimal:
        write_text_file(app_dir / f"{args.app_name}.env-vars.env", render_env(args.app_name))
        write_text_file(
            app_dir / f"{args.app_name}.custom-nginx-config.conf",
            render_custom_nginx(args.app_name),
        )
        write_text_file(
            app_dir / f"{args.app_name}.pre-deploy-function.js",
            render_pre_deploy(args.app_name),
        )
        write_text_file(
            app_dir / f"{args.app_name}.service-update-override.yaml",
            render_service_override(args.app_name),
        )

    write_text_file(readme_path, render_readme(args.app_name))
    deploy_created = write_user_owned_file_if_missing(deploy_path, render_deploy(args.app_name))

    print("APP_TEMPLATE_DIR=" + str(app_dir))
    print("APP_TEMPLATE_YAML=" + str(yaml_path))
    print("APP_TEMPLATE_DEPLOY=" + str(deploy_path))
    print("APP_NAME=" + args.app_name)
    print("DEPLOY_TEMPLATE_CREATED=" + str(deploy_created).lower())

    if deploy_created:
        print("")
        print("Next: inspect the repository, adapt DEPLOY.md to the actual deployment model, then create or repair compose.yaml.")


if __name__ == "__main__":
    main()
