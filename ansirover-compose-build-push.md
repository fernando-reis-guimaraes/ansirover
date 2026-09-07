# ansirover-compose-build-push

Build, push, record, commit, and publish the single Docker image for one AnsiRover-managed application deployment.

The command is designed for non-interactive coding agents. It never asks the user for input. Missing Git identity, registry authentication, Git authentication, repository structure, source cleanliness, or Docker-ignore safety causes a hard failure.

## Usage

```bash
ansirover-compose-build-push <application-git-root> [--environment <name>] [--dry-run]
```

Examples:

```bash
ansirover-compose-build-push .
ansirover-compose-build-push . --environment production
ansirover-compose-build-push . --dry-run
```

The positional path must resolve to **exactly** the application Git top-level. Passing `./backend`, `./ansirover`, or another directory inside the repository is rejected even when Git could discover the parent repository.

This keeps the first argument unambiguous: it identifies the application repository being published.

## Deployment environments

Without `--environment`, the selected deployment directory is:

```text
<application-git-root>/ansirover/
```

With:

```bash
--environment production
```

the selected directory is:

```text
<application-git-root>/ansirover.production/
```

Environment names are user-defined. AnsiRover gives no special behavior to names such as `production`, `staging`, `local`, or `test`.

The default `ansirover/` path may be a symlink to another top-level `ansirover.<name>/` path, provided the symlink resolves inside the application Git root. This lets a repository choose its default deployment environment without global configuration.

A selected AnsiRover environment may belong directly to the application Git repository or be its own Git submodule.

## Application deployment contract

One invocation publishes one deployment unit:

```text
1 selected AnsiRover environment = 1 app inventory
1 compose.yaml = 1 service
1 service = 1 image
```

AnsiRover does not infer deployment units from source folders, npm workspaces, packages, or other repository layout conventions. A repository may contain multiple source components as long as the selected `compose.yaml` builds one image/service.

The selected environment directory is flat. Per-app subdirectories below it are not supported.

## Compose contract

The selected environment must contain:

```text
compose.yaml
```

It must define exactly one service with both `build:` and `image:`. The service name identifies the AnsiRover app and requires a matching inventory:

```text
<selected-ansirover-directory>/<service-name>.yaml
```

Example:

```yaml
services:
  threadline:
    image: cr.umapps.net/umapps/threadline:${IMAGE_TAG:-beta}
    build:
      context: ..
      dockerfile: backend/Dockerfile.production
      platforms:
        - linux/amd64
        - linux/arm64
```

The Dockerfile location does not define the build context. In this example the Dockerfile lives under `backend/`, while `context: ..` gives the build access to the application repository root.

Remote build contexts are rejected because AnsiRover requires a local Git repository to derive deterministic image identity.

## Image identity

`IMAGE_TAG` is the short `HEAD` of the Git repository that owns the resolved `build.context`.

Conceptually:

```text
selected compose.yaml
  -> exactly one service
  -> resolve build.context
  -> git -C <build-context> rev-parse --show-toplevel
  -> source Git repository
  -> require source repository clean
  -> git -C <source-git-root> rev-parse --short HEAD
  -> IMAGE_TAG
```

The source Git repository may be the application repository itself or another Git repository such as a source submodule.

Do not derive the image tag from:

- the selected AnsiRover inventory repository when it is separate;
- a parent Git superproject merely because it contains the source repository;
- a guessed release number;
- an uncommitted source state.

## Source clean-state requirement

The Git repository that owns `build.context` must be clean before image publication.

This is a hard requirement. An image tagged with a commit hash must not contain source changes that are not represented by that hash.

AnsiRover deployment directories are excluded from the source-cleanliness check because the Docker-ignore validation described below ensures they cannot enter the Docker image when they are inside the build context.

## Docker-ignore requirement

No top-level AnsiRover deployment directory may enter the Docker build context.

The command discovers all top-level deployment paths whose names are:

```text
ansirover
ansirover.*
```

For every one that lies inside the resolved build context, the effective Docker ignore file must explicitly exclude its relative path.

For example, with:

```text
ansirover -> ansirover.staging
ansirover.staging/
ansirover.production/
```

and the application root as build context, the effective ignore file must protect the relevant paths, for example:

```text
ansirover/
ansirover.staging/
ansirover.production/
```

A selected AnsiRover directory, or any directory inside one, cannot itself be `build.context`.

Dockerfile-specific ignore files are honored. If `<Dockerfile>.dockerignore` exists, it is the effective ignore file and takes precedence over the root `.dockerignore`.

Each required AnsiRover exclusion must occur after all `!` negation rules so a later exception cannot re-include deployment metadata.

### Docker-ignore remediation

When the effective ignore file is missing or unsafe, the CLI exits before building and prints copy/paste remediation commands. The remediation:

- appends only the AnsiRover rules that need a final exclusion;
- commits only the effective ignore file;
- pushes non-force;
- reruns the same application root and preserves `--environment` when one was selected.

Follow that remediation rather than recreating the ignore/Git logic independently.

## Two-phase Compose resolution

The command resolves Compose twice but performs only one actual Docker build.

Discovery pass:

```text
IMAGE_TAG=ansirover-discovery
  -> docker compose config --format json
  -> require exactly one service
  -> resolve build.context and effective ignore
```

Then:

```text
build.context
  -> source Git root
  -> clean-state validation
  -> short HEAD
  -> real IMAGE_TAG
```

Final pass:

```text
IMAGE_TAG=<short-source-head>
  -> docker compose config --format json
  -> verify service identity
  -> verify build.context did not change
  -> verify effective Docker ignore did not change
  -> validate final image reference
```

Only after those checks does the command execute:

```bash
IMAGE_TAG="<short-source-head>" docker compose build --push
```

## Registry contract

The final image must resolve under:

```text
cr.umapps.net/umapps/
```

For example:

```text
cr.umapps.net/umapps/threadline:c225b95
```

The single service must have an explicit tag that resolves to the source Git short `HEAD`.

Registry authentication is an external prerequisite. The CLI does not run `docker login`, store credentials, or modify Docker credential configuration.

## Inventory update

After image publication succeeds, the command updates exactly one top-level entry in:

```text
<selected-ansirover-directory>/<service-name>.yaml
```

The required entry is:

```yaml
deployedImageName: ...
```

It is replaced with the exact immutable image reference that was just published.

If image publication fails, the inventory is not changed.

## AnsiRover commit allowlist

After updating the inventory, the command commits only known deployment files from the selected environment.

The allowlist is:

```text
compose.yaml
README.md                                             if present
DEPLOY.md                                             if present
<service>.yaml
<service>.env-vars.env                                if present
<service>.custom-nginx-config.conf                    if present
<service>.pre-deploy-function.js                      if present
<service>.service-update-override.yaml                if present
<actual Dockerfile>                                   only if it lives inside the selected AnsiRover directory
<effective Dockerfile-specific .dockerignore>         only if it lives inside the selected AnsiRover directory
```

Unknown extra files are never added merely because they live in the selected deployment directory.

Commit message:

```text
chore: publish <service> <image-tag>
```

## DEPLOY.md and README.md

`compose.yaml` is the canonical build/publication definition.

`DEPLOY.md` is complementary deployment intent and architectural context. It can explain why multiple source components form one deployment, external prerequisites, environment-specific provider choices, and constraints future agents should preserve.

`README.md` is a short human-facing index suitable for GitHub rendering.

Both are allowlisted so a newly bootstrapped deployment model can be committed with the rest of the selected AnsiRover state.

## Git publication

Before Docker build, the command requires every Git repository it will publish to have:

- a named current branch;
- an `origin` remote;
- `user.name`;
- `user.email`.

Pushes are explicit and non-force:

```text
git push origin HEAD:<current-branch>
```

Git authentication is forced into non-interactive mode with terminal prompts disabled and SSH batch mode enabled when the caller has not supplied its own `GIT_SSH_COMMAND`.

## AnsiRover submodule gitlink

When the selected AnsiRover environment is its own Git submodule, publishing its new commit changes the gitlink recorded by the application repository.

The command performs:

```text
1. commit/push selected AnsiRover environment repository
2. commit only that environment's gitlink in the application repository
3. push the application repository
```

If default `ansirover/` is a symlink to an `ansirover.<name>/` submodule, the gitlink that is committed is the resolved named environment submodule.

A deployment-record commit may make the application repository `HEAD` newer than the commit used as `IMAGE_TAG`. That is expected: the image tag identifies the source revision used for the image, while the later commit records deployment state.

## Dry run

```bash
ansirover-compose-build-push . --dry-run
ansirover-compose-build-push . --environment production --dry-run
```

Dry run validates repository layout, environment selection, Compose, inventory shape, source Git identity, source cleanliness, Docker-ignore safety, image naming, and Git publication prerequisites.

It does not build/push an image, change inventory, create commits, or push Git repositories.

## Output

Successful runs emit machine-readable result lines such as:

```text
ENVIRONMENT=default
ANSIROVER_DIR=/path/to/application/ansirover
IMAGE_TAG=c225b95
IMAGE=cr.umapps.net/umapps/threadline:c225b95
SOURCE_GIT_ROOT=/path/to/application
ANSIROVER_GIT_ROOT=/path/to/application
ANSIROVER_BRANCH=main
ANSIROVER_ORIGIN=git@github.com:example/application.git
APPLICATION_BRANCH=main
APPLICATION_ORIGIN=git@github.com:example/application.git
BUILD_PUSH_COMPLETED=true
ANSIROVER_COMMITTED=true
APPLICATION_GITLINK_COMMITTED=false
GIT_PUBLICATION_COMPLETED=true
```

With `--environment production`, `ENVIRONMENT=production` and `ANSIROVER_DIR` points at `ansirover.production`.

## What this command does not do

This command stops after image publication and Git publication of AnsiRover deployment state. It does not:

- call `ansirover-restore-upload`;
- deploy or mutate a CapRover app;
- run `ansirover-health-check`;
- create a missing AnsiRover inventory;
- infer an aggregate production architecture;
- provision PostgreSQL, object storage, SMTP, or another external provider service;
- create registry base-image repositories;
- create Git submodules;
- perform `docker login`;
- prompt for credentials or confirmation.

CapRover deployment remains a separate transaction.

## Base image convention

Application Dockerfiles should use UltraMegaApps registry base images rather than public base images directly when an internal equivalent exists.

Use an actually published internal tag, for example:

```dockerfile
FROM cr.umapps.net/umapps/node22:<published-tag>
```

instead of:

```dockerfile
FROM node:22
```

The established base-image repository naming example is:

```text
GitHub repository: fernando-reis-guimaraes/cr_umapps_net_umapps_node22
Registry image:    cr.umapps.net/umapps/node22
Compose service:   umapps_node22
```

Creating or publishing a missing base-image repository remains outside this CLI's scope.

## Prerequisites

- Git
- Docker
- Docker Compose v2
- configured Git `user.name` and `user.email`
- an `origin` remote for every repository the CLI must push
- non-interactive Git authentication
- authentication that permits pushing to `cr.umapps.net`
