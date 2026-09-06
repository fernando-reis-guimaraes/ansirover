# ansirover-compose-build-push

Build and push Docker Compose images for an AnsiRover-managed repository.

The command removes `.envrc` from the deployment contract. It derives `IMAGE_TAG` directly from the current Git commit and provides it only to the Docker Compose subprocess.

## Repository contract

A deployable repository must have this shape:

```text
<git-root>/
├── ansirover/
│   ├── compose.yaml
│   ├── Dockerfile
│   └── <app>.yaml
└── ... application source ...
```

`Dockerfile` may be elsewhere when `compose.yaml` points to another path. The CLI requires only the `ansirover/` directory and `ansirover/compose.yaml`.

The command can be run from the repository root, from `ansirover/`, or from another directory inside the repository.

## Image tag

The image tag is always:

```bash
git rev-parse --short HEAD
```

The CLI exports that value as `IMAGE_TAG` only for Docker Compose commands.

No `.envrc` is required.

Recommended Compose image declaration:

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

The `${IMAGE_TAG:-beta}` fallback remains useful for manual/local Compose use. During `ansirover-compose-build-push`, `IMAGE_TAG` is always supplied and the resolved image must end with the current short Git HEAD.

## Registry contract

Every Compose service that has `build:` must also have `image:` and must resolve under:

```text
cr.umapps.net/umapps/
```

For example:

```text
cr.umapps.net/umapps/tictactoe:c225b95
```

The command fails before building if a buildable service:

- has no `image:`;
- publishes outside `cr.umapps.net/umapps/`;
- has no explicit tag;
- resolves to a tag other than the current short Git HEAD.

Registry authentication is an external prerequisite. The CLI does not perform `docker login`, store credentials, or modify Docker credential configuration.

## Build and push

The underlying happy path is equivalent to:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)" docker compose build --push
```

The CLI first renders the Compose model using:

```bash
docker compose config --format json
```

This validates interpolation and lets the CLI inspect the exact resolved image names before any build or push starts.

A successful `docker compose build --push` is treated as successful image publication.

## Usage

Calling the command without arguments prints the usage guide and performs no action:

```bash
ansirover-compose-build-push
```

Run against the current repository explicitly:

```bash
ansirover-compose-build-push .
```

Using another repository:

```bash
ansirover-compose-build-push /path/to/repository
```

Validate without building or pushing:

```bash
ansirover-compose-build-push . --dry-run
```

The explicit path prevents a bare invocation from accidentally building or pushing the current repository.

## Dirty working tree

A dirty working tree does not stop the command, but it produces a warning.

This matters because the image tag identifies committed `HEAD`, while the Docker build context may contain uncommitted files. A clean working tree is preferable when the short commit hash is intended to identify image contents exactly.

## Output

Logs are written to stderr. Machine-readable result lines are written to stdout.

Example:

```text
IMAGE_TAG=c225b95
GIT_ROOT=/Volumes/FrgGit/tictactoe
ANSIROVER_DIR=/Volumes/FrgGit/tictactoe/ansirover
COMPOSE_FILE=/Volumes/FrgGit/tictactoe/ansirover/compose.yaml
IMAGE_COUNT=1
IMAGE=cr.umapps.net/umapps/tictactoe:c225b95
BUILD_PUSH_COMPLETED=true
```

With `--dry-run`:

```text
BUILD_PUSH_COMPLETED=false
```

## What this command does not do

This command intentionally stops at image publication. It does not:

- modify `<app>.yaml`;
- create an AnsiRover inventory;
- call `ansirover-restore-upload`;
- run `ansirover-health-check`;
- create registry base-image repositories;
- add Git submodules;
- log in to the registry.

Those actions belong to the deployment orchestration/agent workflow rather than the Compose build/push primitive.

## Base image convention

Application Dockerfiles should use UltraMegaApps registry base images rather than public base images directly.

Example:

```dockerfile
FROM cr.umapps.net/umapps/node22:latest
```

Do not use this directly in an application Dockerfile when the internal equivalent exists:

```dockerfile
FROM node:22
```

The established base-image repository example is:

```text
GitHub repository: fernando-reis-guimaraes/cr.umapps.net_umapps_node22
Registry image:    cr.umapps.net/umapps/node22
Compose service:   umapps_node22
```

A base-image repository can be intentionally minimal:

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

Creating a missing base-image repository and linking it as a submodule are outside this CLI's scope and should be handled by the coding-agent deployment skill.

## Prerequisites

- Git
- Docker
- Docker Compose v2
- authentication that permits pushing to `cr.umapps.net`
