# ansirover-compose-bootstrap

Create a 90% AnsiRover scaffold for one deployment environment. A human or agent verifies the result before first publish.

This command exists because `ansirover-compose-build-push` never creates missing inventories and `ansirover-template-app-create` intentionally never creates `compose.yaml`. Bootstrap inspects the application repository and generates the canonical one-service Compose build definition plus inventory and Docker-ignore coverage.

## Usage

```bash
ansirover-compose-bootstrap <application-git-root> [options]
```

Options:

```text
--environment=<name>   use <application-git-root>/ansirover.<name>/ (default: ansirover/)
--app-name=<name>      CapRover app/service name (default: derived from package.json or directory)
--image=<image>        image template under cr.umapps.net/umapps/ (default: cr.umapps.net/umapps/<app>:${IMAGE_TAG:-beta})
--port=<port>          containerHttpPort (default: auto-detected, else 3000 for Node or 80)
--context=<path>       build context relative to application root (default: .)
--dockerfile=<path>    Dockerfile relative to application root (default: auto-detected)
--minimal              create minimal YAML + compose + .envrc (default)
--with-examples        also create example env-vars/nginx/pre-deploy/override sidecars
--force                overwrite existing generated files
--dry-run              print detection plan without writing files
```

When called without arguments it prints usage and performs no writes.

Examples:

```bash
ansirover-compose-bootstrap .
ansirover-compose-bootstrap . --environment=production
ansirover-compose-bootstrap . --app-name=crosspaste --port=3000 --force
ansirover-compose-bootstrap . --dry-run
```

The positional path must resolve to exactly the application Git top-level, matching `ansirover-compose-build-push`.

## Detection heuristics

Bootstrap aims for a correct-by-default scaffold, not a perfect architecture decision:

```text
app name   --app-name, else package.json name, else sanitized directory name
Dockerfile first existing candidate under the application root, else --dockerfile
context    application root (.. from the AnsiRover directory), else --context
port       --port, else .env.example APP_PORT, else Dockerfile ENV/EXPOSE,
           else root compose.yaml hint, else 3000 for Node or 80
persistent true when root compose.yaml or Dockerfile declares volumes/VOLUME
volumes    container paths from the first buildable root-compose service,
           backed by named CapRover volumes (<app>-<name>)
image      cr.umapps.net/umapps/<app>:${IMAGE_TAG:-beta} unless --image overrides
```

Dockerfile candidates checked in order:

```text
Dockerfile
Dockerfile.production
Dockerfile.prod
backend/Dockerfile
backend/Dockerfile.production
app/Dockerfile
src/Dockerfile
web/Dockerfile
server/Dockerfile
docker/Dockerfile
```

Root `compose.yaml` observations (single versus multiple buildable services, Caddy edge-proxy hints) are printed as `[config]` diagnostics for review. A public `FROM` image triggers the internal base-image convention note.

## Generated files

Default minimal output:

```text
ansirover/
├── compose.yaml       canonical one-service build/publication definition
├── <app>.yaml         CapRover runtime inventory with detected port/image/volumes
└── .envrc             local direnv file exporting IMAGE_TAG (see below)
```

## `.envrc` and `IMAGE_TAG`

The generated `.envrc` uses exactly this format:

```bash
export REPO_ROOT="$PWD"
export COMPOSE_FILE="$REPO_ROOT/compose.yaml"
export IMAGE_TAG=$(git rev-parse --short HEAD)
```

It requires [direnv](https://direnv.net); run `direnv allow` in the AnsiRover directory. A repository may additionally keep the same file at its root for project-wide commands; direnv loads both, parent first. The AnsiRover `.envrc` stays local-only: `ansirover-compose-build-push` never commits unknown environment files (allowlist).

The inventory follows the canonical field order (`appName`, `deployedImageName`, `description`, `ansirover`, `hasPersistentData`, `instanceCount`, `containerHttpPort`, `notExposeAsWebApp`, `websocketSupport`, `customDomain`, `tags`, `httpAuth`, `volumes`, `ports`). It includes an explicit `ansirover:` policy block, an empty `customDomain: []` with a commented `publicDomain` example, and pre-filled `volumes:` from the root compose buildable service. Bootstrap never creates `README.md` or `DEPLOY.md`; deployment rationale lives with the user.

With `--with-examples`, bootstrap also creates the optional sidecars:

```text
<app>.env-vars.env
<app>.custom-nginx-config.conf
<app>.pre-deploy-function.js
<app>.service-update-override.yaml
```

## Docker-ignore coverage

Bootstrap discovers top-level `ansirover` and `ansirover.*` paths inside the resolved build context and ensures the effective Docker ignore file excludes each one after all negation rules. A Dockerfile-specific `<Dockerfile>.dockerignore` takes precedence over the root `.dockerignore`, matching `ansirover-compose-build-push` semantics. Missing rules are appended automatically (reported only with `--dry-run`).

## Output

Successful runs emit machine-readable result lines such as:

```text
ENVIRONMENT=default
ANSIROVER_DIR=/path/to/application/ansirover
APP_NAME=crosspaste
IMAGE_TEMPLATE=cr.umapps.net/umapps/crosspaste:${IMAGE_TAG:-beta}
CONTAINER_PORT=3000
BUILD_CONTEXT=/path/to/application
DOCKERFILE=/path/to/application/Dockerfile
COMPOSE_PATH=/path/to/application/ansirover/compose.yaml
INVENTORY_PATH=/path/to/application/ansirover/crosspaste.yaml
DOCKER_IGNORE=/path/to/application/.dockerignore
BOOTSTRAP_COMPLETED=true
NEEDS_REVIEW=true
```

## What this command does not do

This command stops after scaffold creation and Docker-ignore repair. It does not:

- build or push an image;
- update `deployedImageName` with an immutable reference;
- commit or push Git repositories;
- call `ansirover-restore-upload`;
- deploy or mutate a CapRover app;
- run `ansirover-health-check`;
- provision PostgreSQL, object storage, SMTP, or another external provider service;
- create registry base-image repositories;
- create Git submodules;
- prompt for credentials or confirmation.

Typical workflow after review:

```text
1. Review ansirover/<app>.yaml and adapt project-specific sections.
2. Delete unneeded example sidecars when --with-examples was used.
3. ansirover-compose-build-push . --dry-run
4. ansirover-compose-build-push .
5. ansirover-restore-upload <server> <app> ./ansirover
6. ansirover-health-check <server> <app> ./ansirover
```
