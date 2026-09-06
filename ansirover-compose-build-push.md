# ansirover-compose-build-push

Build and push the single Docker Compose image for one AnsiRover-managed application repository.

The command does not require `.envrc`. It derives `IMAGE_TAG` from the Git repository that owns the resolved Docker `build.context` and provides that tag only to Docker Compose subprocesses.

## Application repository contract

A deployable application repository follows this model:

```text
<application-git-root>/
├── ... application source ...
└── ansirover/
    ├── compose.yaml
    ├── <app>.yaml
    └── ... optional flat sidecars ...
```

Rules:

```text
1 Git repository = 1 deployable app
1 ansirover/ = 1 app inventory
1 compose.yaml = 1 service
1 service = 1 image
```

Monorepos are not supported. If a parent project contains multiple independently deployable applications, each application must be its own Git repository, normally composed into the parent with Git submodules.

`ansirover/` is intentionally flat. Per-app subdirectories inside `ansirover/` are not supported.

The `ansirover/` directory itself may become a Git submodule in the future. Image identity must not depend on the Git repository that stores the AnsiRover inventory.

## Compose contract

`ansirover/compose.yaml` must define exactly one service and that service must define `build:` and `image:`.

Example:

```yaml
services:
  tictactoe:
    image: cr.umapps.net/umapps/tictactoe:${IMAGE_TAG:-beta}
    build:
      context: ..
      dockerfile: Dockerfile
      platforms:
        - linux/amd64
        - linux/arm64
```

The Compose file may be minimal and exist only to build/publish the image. A project may also add configuration useful for local testing, but local runtime configuration is not required by AnsiRover.

## Image identity

The image tag is the short `HEAD` of the Git repository that owns the resolved `build.context`.

Conceptually:

```text
compose.yaml
  -> exactly one service
  -> resolve build.context
  -> git -C <build-context> rev-parse --show-toplevel
  -> git -C <source-git-root> rev-parse --short HEAD
  -> IMAGE_TAG
```

This rule is deliberate. The image tag identifies the source repository whose files are used as the Docker build context, not the repository that stores `compose.yaml` or the AnsiRover inventory.

For the common layout:

```text
application/
├── Dockerfile
└── ansirover/
    └── compose.yaml
```

with:

```yaml
build:
  context: ..
```

`IMAGE_TAG` comes from `application/`.

If `ansirover/` later becomes a submodule, the result is unchanged because the build context still points at the application source repository.

If the build context itself is a Git submodule, `IMAGE_TAG` comes from that submodule's `HEAD`.

Remote build contexts are rejected because AnsiRover cannot derive the required local Git identity from them.

## Two-phase Compose resolution

The CLI resolves Compose twice.

First pass:

```text
IMAGE_TAG=ansirover-discovery
  -> docker compose config --format json
  -> require exactly one service
  -> resolve build.context
```

Then:

```text
build.context
  -> source Git root
  -> short HEAD
  -> real IMAGE_TAG
```

Second pass:

```text
IMAGE_TAG=<short-source-head>
  -> docker compose config --format json
  -> validate final image reference
```

`build.context` must remain identical between the discovery pass and the final pass. It must not depend on `IMAGE_TAG`.

## Registry contract

The final image must resolve under:

```text
cr.umapps.net/umapps/
```

For example:

```text
cr.umapps.net/umapps/tictactoe:c225b95
```

The command fails before building when the single service:

- has no `build:`;
- has no `image:`;
- publishes outside `cr.umapps.net/umapps/`;
- has no explicit tag;
- resolves to a tag other than the short `HEAD` of the Git repository owning `build.context`.

Registry authentication is an external prerequisite. The CLI does not run `docker login`, store credentials, or modify Docker credential configuration.

## Build and push

After validation, the effective build/push operation is:

```bash
IMAGE_TAG="<short-source-head>" docker compose build --push
```

A successful `docker compose build --push` is treated as successful image publication. The CLI does not call Docker Buildx directly for a second registry inspection.

## Base image convention

Application Dockerfiles must use UltraMegaApps registry base images instead of public base images directly when an internal equivalent exists.

Use:

```dockerfile
FROM cr.umapps.net/umapps/node22:latest
```

instead of:

```dockerfile
FROM node:22
```

The established base-image repository naming example is:

```text
GitHub repository: fernando-reis-guimaraes/cr.umapps.net_umapps_node22
Registry image:    cr.umapps.net/umapps/node22
Compose service:   umapps_node22
```

A base-image repository may use a minimal Compose file:

```yaml
services:
  umapps_node22:
    image: cr.umapps.net/umapps/node22:${IMAGE_TAG:-beta}
    build:
      context: .
      dockerfile: Dockerfile
      platforms:
        - linux/amd64
        - linux/arm64
```

Creating a missing base-image repository or adding it as a Git submodule is outside this CLI's scope and belongs to the coding-agent deployment workflow.

## Usage

Calling the command without arguments prints usage and performs no action:

```bash
ansirover-compose-build-push
```

Build/push the current application repository explicitly:

```bash
ansirover-compose-build-push .
```

An explicit repository path also works:

```bash
ansirover-compose-build-push /Volumes/FrgGit/tictactoe
```

The application `ansirover/` directory itself may be supplied:

```bash
ansirover-compose-build-push ./ansirover
```

This remains valid if `ansirover/` is a Git submodule.

Validate without building or pushing:

```bash
ansirover-compose-build-push . --dry-run
```

## Flat `ansirover/` validation

Non-hidden subdirectories inside the application `ansirover/` directory are rejected.

Invalid:

```text
ansirover/
├── frontend/
└── backend/
```

Separate applications belong in separate repositories/submodules instead of adding another app-management layer inside `ansirover/`.

## Dirty source working tree

A dirty working tree in the Git repository owning `build.context` produces a warning but does not stop the command.

The warning matters because:

```text
IMAGE_TAG = committed source HEAD
build context = may include uncommitted source files
```

A clean source working tree is preferable when the image tag is expected to identify image contents exactly.

## Output

Logs and diagnostics are written to stderr. Machine-readable result lines are written to stdout.

Example:

```text
IMAGE_TAG=c225b95
APPLICATION_ROOT=/Volumes/FrgGit/tictactoe
APPLICATION_GIT_ROOT=/Volumes/FrgGit/tictactoe
ANSIROVER_DIR=/Volumes/FrgGit/tictactoe/ansirover
COMPOSE_FILE=/Volumes/FrgGit/tictactoe/ansirover/compose.yaml
SERVICE=tictactoe
BUILD_CONTEXT=/Volumes/FrgGit/tictactoe
SOURCE_GIT_ROOT=/Volumes/FrgGit/tictactoe
IMAGE_COUNT=1
IMAGE=cr.umapps.net/umapps/tictactoe:c225b95
BUILD_PUSH_COMPLETED=true
```

When `--dry-run` is used:

```text
BUILD_PUSH_COMPLETED=false
```

`SOURCE_GIT_ROOT` is the repository whose `HEAD` produced `IMAGE_TAG`.

## What this command does not do

This command stops at image publication. It does not:

- modify `<app>.yaml`;
- create an AnsiRover inventory;
- call `ansirover-restore-upload`;
- run `ansirover-health-check`;
- create registry repositories;
- create base-image repositories;
- add Git submodules;
- log in to the registry;
- deploy to CapRover.

Those actions belong to deployment orchestration rather than the build/push primitive.

## Prerequisites

- Git
- Docker
- Docker Compose v2
- authentication that permits pushing to `cr.umapps.net`
