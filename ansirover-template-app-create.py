#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from datetime import datetime


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

    if "/" in value:
        fail(f"{label} must not contain slash: {value}")

    if value in {".", ".."}:
        fail(f"{label} must not be . or ..")

    if any(character.isspace() for character in value):
        fail(f"{label} must not contain whitespace: {value}")


def resolve_app_dir(current_dir, app_name):
    if current_dir.name.lower() == app_name.lower():
        return current_dir

    return current_dir / app_name


def write_text_file(path, content, force):
    if path.exists() and not force:
        fail(f"file already exists; use --force to overwrite: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    info(f"[write] {path}")


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
# These values are restored as CapRover app environment variables.

APP_NAME={app_name}
EXAMPLE_VALUE=change-me
"""


def render_custom_nginx(app_name):
    return """# DELETE THIS FILE IF THE APP DOES NOT NEED CUSTOM NGINX CONFIG.
# AnsiRover custom Nginx sidecar for __APP_NAME__.
# Keep this file valid as an Nginx config fragment.

# Default value assumed by bellow Caprover if not set in the AnsiRover:
<%
if (s.forceSsl) {
%>
    server {

        listen       80;

        server_name  <%-s.publicDomain%>;

        # Used by Lets Encrypt
        location /.well-known/acme-challenge/ {
            root <%-s.staticWebRoot%>;
        }

        # Used by CapRover for health check
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
        listen       80;
    <%
    }
    if (s.hasSsl) {
    %>
        listen              443 ssl;
        http2               on;
        ssl_certificate     <%-s.crtPath%>;
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

        server_name  <%-s.publicDomain%>;

        # 127.0.0.11 is DNS set up by Docker, see:
        # https://docs.docker.com/engine/userguide/networking/configure-dns/
        # https://github.com/moby/moby/issues/20026
        resolver 127.0.0.11 valid=10s;
        # IMPORTANT!! If you are here from an old thread to set a custom port, you do not need to modify this port manually here!!
        # Simply change the Container HTTP Port from the dashboard HTTP panel
        set $upstream http://<%-s.localDomain%>:<%-s.containerHttpPort%>;

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
                            auth_basic           "Restricted Access";
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

        # Used by Lets Encrypt
        location /.well-known/acme-challenge/ {
            root <%-s.staticWebRoot%>;
        }
        
        # Used by CapRover for health check
        location /.well-known/captain-identifier {
            root <%-s.staticWebRoot%>;
        }

        error_page 502 /captain_502_custom_error_page.html;
        location = /captain_502_custom_error_page.html {
                root <%-s.customErrorPagesDirectory%>;
                internal;
        }
}
""".replace("__APP_NAME__", app_name)

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


def render_readme(app_name, minimal):
    guide = render_ai_guide(app_name)

    if minimal:
        template_kind = "minimal AnsiRover app template"
        sidecar_section = """This minimal template intentionally creates only:

```text
{app_name}.yaml
README.md
```

Add sidecars only when the app needs them.
""".format(app_name=app_name)
    else:
        template_kind = "AnsiRover app template with examples"
        sidecar_section = """This template includes example sidecars so an AI or human can see the supported AnsiRover project shape:

```text
{app_name}.env-vars.env
{app_name}.custom-nginx-config.conf
{app_name}.pre-deploy-function.js
{app_name}.service-update-override.yaml
```

Delete any sidecar that the app does not need.
""".format(app_name=app_name)

    return f"""# {app_name}

This folder is a {template_kind}.

It is meant to describe one CapRover app in a local, editable, versionable format. The goal is that a human or AI can change the files in this folder and then deploy the app to CapRover using `ansirover-restore-upload`.

## Project purpose

Use this folder as the source of truth for the app named `{app_name}`.

Typical workflow:

```text
1. Create or edit the AnsiRover files locally.
2. Deploy/upload the app to a CapRover server.
3. Test the app.
4. Download/refresh the backup after the server state is correct.
5. Commit the text files to Git.
```

Large archives such as Docker images and volume backups are usually not committed to Git. They can be stored separately in backup storage while this folder keeps the readable app definition and sidecars.

## Files in this project

Main app definition:

```text
{app_name}.yaml
```

The YAML file defines the CapRover app name, image, web port, instance count, HTTP exposure, websocket support, volumes, extra ports, tags and HTTP auth.

The optional `ansirover` mapping defines app-level operational policy. Omitted
fields use runtime defaults but are not added to the YAML during download. Review
`restartDelay` for each app: a long delay may be appropriate for an admin tool but
dangerous for a critical application.

{sidecar_section}

## What an AI should edit first

When asked to create or modify this app, start with:

```text
{app_name}.yaml
```

Then edit sidecars only when needed:

```text
.env-vars.env                     environment variables
.custom-nginx-config.conf         custom Nginx fragment
.pre-deploy-function.js           JavaScript pre-deploy hook
.service-update-override.yaml     Docker service override fragment
```

Keep file names prefixed with the app name. For example:

```text
{app_name}.env-vars.env
```

Do not rename sidecars to generic names like `.env` or `nginx.conf`.

## Important AnsiRover rules

```text
appName:
  Must match the CapRover app name.

deployedImageName:
  Docker image that CapRover should deploy.

hasPersistentData:
  Used when creating a new app.
  Do not rely on it to update an existing app's persistence mode.

instanceCount:
  Desired CapRover instance count.

containerHttpPort:
  Port exposed by the app inside the container.

notExposeAsWebApp:
  true for background/private apps.
  false for normal web apps.

websocketSupport:
  true when the app needs websocket support through CapRover.

ansirover:
  Optional app-level operational policy.
  restartDelay controls Docker Swarm restart delay.
  gracePeriod is the post-convergence observation window.
  postgresReconnectThreshold counts reconnect waves, not individual sessions.
  postgresPollInterval controls the PostgreSQL observation interval.

volumes:
  Named volumes can be backed up/restored by AnsiRover volume backup.
  Bind mounts must be backed up separately.

ports:
  Extra host/container TCP or UDP mappings.
```

## Volumes and bind mounts

Named volume example:

```yaml
volumes:
  - volumeName: {app_name}-data
    containerPath: /data
```

Bind mount example:

```yaml
volumes:
  - hostPath: /opt/{app_name}/config
    containerPath: /config
```

Named volumes are handled by:

```text
--with-volumes
```

Bind mounts are not copied by the named-volume backup flow. If the app uses bind mounts, the host paths must be created and backed up separately on the CapRover server.

## Deploy this app

From the parent directory of this app folder:

```bash
ansirover-restore-upload <server-name> {app_name} ./{app_name} --with-image --with-volumes --wait-deploy
```

Example:

```bash
ansirover-restore-upload storage {app_name} ./{app_name} --with-image --with-volumes --wait-deploy
```

If the app uses only a public Docker Hub image and no volume archives, the restore command may not need `--with-image` or `--with-volumes`.

## Download/refresh backup from server

After the app is correct on CapRover, refresh this local folder from the server:

```bash
ansirover-download-backup <server-name> {app_name} ./{app_name} --with-image --with-volumes --force
```

Example:

```bash
ansirover-download-backup storage {app_name} ./{app_name} --with-image --with-volumes --force
```

This updates the local AnsiRover YAML, JSON, sidecars, image archive and named-volume archives when requested.

## Git recommendation

Commit text files:

```text
*.yaml
*.json
*.env-vars.env
*.custom-nginx-config.conf
*.pre-deploy-function.js
*.service-update-override.yaml
README.md
```

Usually ignore large/generated archives:

```text
*.tar.gz
*.partial
```

## AI handoff guide

{guide}
"""

def render_ai_guide(app_name):
    return f"""## AnsiRover quick guide for AI/tools

This folder is an AnsiRover app backup/template.

Main file:

```text
{app_name}.yaml
```

Sidecars, when present:

```text
{app_name}.env-vars.env
{app_name}.custom-nginx-config.conf
{app_name}.pre-deploy-function.js
{app_name}.service-update-override.yaml
```

Format summary:

```text
appName: CapRover app name.
deployedImageName: Docker image to deploy.
hasPersistentData: used when creating a new app.
instanceCount: desired CapRover instance count.
containerHttpPort: app HTTP port inside the container.
notExposeAsWebApp: true for non-web/background apps.
websocketSupport: enables CapRover websocket support.
ansirover: optional app-level runtime policy; omitted values use runtime defaults.
volumes: named volumes or bind mounts.
ports: extra TCP/UDP host port mappings.
```

Volume rules:

```text
Named volume:
  volumeName + containerPath
  backed up/restored by --with-volumes.

Bind mount:
  hostPath + containerPath
  not backed up by named-volume backup.
  must be handled separately on the server.
```

Deploy/restore this app to a CapRover server:

```bash
ansirover-restore-upload <server-name> {app_name} <path-to-this-folder> --with-image --with-volumes --wait-deploy
```

Download/refresh backup from a CapRover server:

```bash
ansirover-download-backup <server-name> {app_name} <path-to-this-folder> --with-image --with-volumes --force
```

Examples:

```bash
ansirover-restore-upload storage {app_name} ./{app_name} --with-image --with-volumes --wait-deploy
ansirover-download-backup storage {app_name} ./{app_name} --with-image --with-volumes --force
```
"""


def print_usage_and_guide():
    print("""Usage:
  ansirover-template-app-create <app-name> [options]

Options:
  --image <image>      Docker image for deployedImageName. Default: nginx:alpine
  --port <port>        containerHttpPort. Default: 80
  --minimal            Create only the minimal YAML + README and suppress post-create guide.
  --with-examples      Default behavior. Create YAML plus example sidecars.
  --force              Overwrite existing files.

Purpose:
  Create a local AnsiRover app template that an AI or human can edit, then deploy with
  ansirover-restore-upload.

Workflow:
  1. ansirover-template-app-create meu-app
  2. Edit meu-app/meu-app.yaml and sidecars.
  3. Deploy:

     ansirover-restore-upload <server-name> meu-app ./meu-app --with-image --with-volumes --wait-deploy

  4. Later, download/refresh the backup:

     ansirover-download-backup <server-name> meu-app ./meu-app --with-image --with-volumes --force

AnsiRover format:
  The main file is <app-name>.yaml. Sidecars are optional files with the same prefix:
  .env-vars.env, .custom-nginx-config.conf, .pre-deploy-function.js,
  .service-update-override.yaml.

  Named volumes use volumeName + containerPath and are handled by --with-volumes.
  Bind mounts use hostPath + containerPath and must be backed up separately.
""")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a local AnsiRover app template.",
        add_help=True,
    )

    parser.add_argument("app_name", nargs="?", help="CapRover app name to create")
    parser.add_argument("--image", default="nginx:alpine", help="Docker image for deployedImageName; default: nginx:alpine")
    parser.add_argument("--port", type=int, default=80, help="containerHttpPort; default: 80")
    parser.add_argument("--minimal", action="store_true", help="create only minimal YAML + README and suppress post-create guide")
    parser.add_argument("--with-examples", action="store_true", help="create example sidecars; default behavior")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")

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

    info("[plan] Action: create AnsiRover app template")
    info("[plan] App: " + args.app_name)
    info("[plan] Directory: " + str(app_dir))
    info("[plan] Image: " + args.image)
    info("[plan] Port: " + str(args.port))
    info("[plan] Minimal: " + str(args.minimal))
    info("[plan] Force: " + str(args.force))

    app_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = app_dir / f"{args.app_name}.yaml"
    readme_path = app_dir / "README.md"

    write_text_file(
        yaml_path,
        render_yaml(
            app_name=args.app_name,
            image=args.image,
            port=args.port,
            minimal=args.minimal,
        ),
        force=args.force,
    )

    if not args.minimal:
        write_text_file(app_dir / f"{args.app_name}.env-vars.env", render_env(args.app_name), force=args.force)
        write_text_file(app_dir / f"{args.app_name}.custom-nginx-config.conf", render_custom_nginx(args.app_name), force=args.force)
        write_text_file(app_dir / f"{args.app_name}.pre-deploy-function.js", render_pre_deploy(args.app_name), force=args.force)
        write_text_file(app_dir / f"{args.app_name}.service-update-override.yaml", render_service_override(args.app_name), force=args.force)

    write_text_file(readme_path, render_readme(args.app_name, minimal=args.minimal), force=args.force)

    print("APP_TEMPLATE_DIR=" + str(app_dir))
    print("APP_TEMPLATE_YAML=" + str(yaml_path))
    print("APP_NAME=" + args.app_name)

    if not args.minimal:
        print("")
        print(render_ai_guide(args.app_name))


if __name__ == "__main__":
    main()
