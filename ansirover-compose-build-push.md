# ansirover-compose-build-push

Build, push, record, commit, and publish the single Docker image for one AnsiRover-managed application repository.

The command is designed for non-interactive coding agents. It never asks the user for input. Missing Git identity, registry authentication, Git authentication, repository structure, source cleanliness, or Docker-ignore safety causes a hard failure.

## Application repository contract

A deployable application follows this model:

```text
<application-git-root>/
├── ... application source ...
├── .dockerignore
└── ansirover/
    ├── compose.yaml
    ├── <app>.yaml
    └── ... optional flat AnsiRover files ...
```

Rules:

```text
1 Git repository = 1 deployable app
1 ansirover/ = 1 app inventory
1 compose.yaml = 1 service
1 service = 1 image
```

Monorepos are not supported. When a parent project contains multiple independently deployable applications, each application must be its own Git repository, normally attached to the parent as a Git submodule.

The application `ansirover/` directory is flat. Per-app subdirectories below `ansirover/` are not supported.

The `ansirover/` directory may itself be a Git submodule. The command supports both layouts:

```text
application/                 application/
└── ansirover/               └── ansirover/  <- Git submodule
```

## Compose contract

`ansirover/compose.yaml` must define exactly one service. That service must define both `build:` and `image:`.

The service name identifies the AnsiRover app and requires a matching inventory file:

```text
ansirover/<service-name>.yaml
```

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

The Compose file may exist only for image publication, or it may contain additional configuration useful for local testing. It still represents exactly one deployable AnsiRover app.

## Image identity

`IMAGE_TAG` is the short `HEAD` of the Git repository that owns the resolved `build.context`.

Conceptually:

```text
compose.yaml
  -> exactly one service
  -> resolve build.context
  -> git -C <build-context> rev-parse --show-toplevel
  -> source Git repository
  -> require source repository clean
  -> git -C <source-git-root> rev-parse --short HEAD
  -> IMAGE_TAG
```

The image tag therefore identifies the Git source repository used as the Docker build context. It does not come from the Git repository that happens to store `ansirover/`.

If `ansirover/` is a Git submodule, the image tag still comes from the source repository that owns `build.context`.

If `build.context` itself is another Git submodule, the image tag comes from that submodule's `HEAD`.

Remote build contexts are rejected because AnsiRover requires a local Git repository to derive deterministic image identity.

## Source clean-state requirement

The Git repository that owns `build.context` must be clean before image publication.

This is a hard requirement, not a warning. Without it, an image tagged with a commit hash could contain uncommitted source changes that are not represented by that hash.

AnsiRover changes are excluded from the clean-state check only when the `ansirover/` path cannot enter the Docker build context. This is true when either:

- `ansirover/` is outside `build.context`; or
- the effective Docker ignore file has passed the AnsiRover exclusion validation described below.

The effective source identity is therefore:

```text
committed source content that can enter build.context
```

not deployment metadata that Docker cannot receive.

## Docker-ignore requirement

`ansirover/` must never enter the Docker build context.

When `ansirover/` is inside the resolved `build.context`, the command requires an effective Docker ignore file and validates an explicit rule for the relative AnsiRover directory.

For the normal layout, the canonical rule is:

```text
ansirover/
```

If the rule is missing, the command fails before building.

The required exclusion must appear after all `!` negation rules in that ignore file. This intentionally makes the AnsiRover exclusion the final authority against re-inclusion.

Dockerfile-specific ignore files are honored. If a Dockerfile-specific ignore file exists, it is the effective file and must contain the AnsiRover exclusion; a root `.dockerignore` does not override it.

Example:

```text
application/
├── .dockerignore
└── ansirover/
    └── build.Dockerfile
```

If this file exists:

```text
ansirover/build.Dockerfile.dockerignore
```

it takes precedence for that Dockerfile and must contain the required `ansirover/` exclusion.

If `ansirover/` is physically outside `build.context`, no ignore rule is required because Docker cannot include that directory.

`ansirover/` itself cannot be used as `build.context`, because the AnsiRover directory must be excluded from image input.

### Docker-ignore remediation output

When the effective Docker ignore file is missing, does not contain the required AnsiRover exclusion, or has a later negation rule that can re-include it, the CLI exits with an error and prints copy/paste commands to fix the repository and continue the build.

The remediation uses a leading newline deliberately:

```bash
printf '\n%s\n' ansirover/ >> /path/to/.dockerignore
```

This is safe when the existing ignore file has no trailing newline. It prevents the new rule from being concatenated to the previous rule.

The generated Git commands are path-limited to the effective Docker ignore file:

```text
git -C <ignore-repository> add -- <ignore-file>
git -C <ignore-repository> commit --only -m 'chore: exclude ansirover from Docker context' -- <ignore-file>
git -C <ignore-repository> push origin HEAD
ansirover-compose-build-push <absolute-application-root>
```

This avoids accidentally committing unrelated application or AnsiRover changes while fixing the publication prerequisite. If a Dockerfile-specific ignore file is effective, the generated commands target that file instead of the root `.dockerignore`.

## Two-phase Compose resolution

The command resolves Compose twice but performs only one actual Docker build.

Discovery pass:

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

The command does not call Docker Buildx directly for a second registry inspection. A successful `docker compose build --push` is treated as successful image publication.

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
- resolves to a tag other than the source Git short `HEAD`.

Registry authentication is an external prerequisite. The CLI does not run `docker login`, store credentials, or modify Docker credential configuration.

## Inventory update

After the image push succeeds, the command updates:

```text
ansirover/<service-name>.yaml
```

The file must contain exactly one top-level:

```yaml
deployedImageName: ...
```

AnsiRover replaces that value with the exact published image reference.

Example:

```yaml
deployedImageName: cr.umapps.net/umapps/tictactoe:c225b95
```

The image is built first. If Docker build/push fails, the inventory is not changed.

## AnsiRover commit allowlist

After updating the inventory, the command commits only known AnsiRover deployment files. Unknown extra files inside `ansirover/` are deliberately left untouched and uncommitted.

The allowlist is:

```text
compose.yaml
<service>.yaml
<service>.env-vars.env                         if present
<service>.custom-nginx-config.conf              if present
<service>.pre-deploy-function.js                if present
<service>.service-update-override.yaml          if present
<actual Dockerfile>                             if it lives inside ansirover/
<effective Dockerfile-specific .dockerignore>   if it lives inside ansirover/
```

A generic unknown file such as:

```text
ansirover/notes.tmp
```

is never added merely because it is inside the AnsiRover directory.

The command uses path-limited Git commits so unrelated staged or unstaged files are not included in the automatic AnsiRover commit.

Commit message:

```text
chore: publish <service> <image-tag>
```

## Git push behavior

The AnsiRover Git repository is pushed automatically after its allowlisted commit.

The command requires before the Docker build:

- a named current Git branch;
- an `origin` remote;
- `user.name`;
- `user.email`.

Pushes are explicit and non-force:

```text
git push origin HEAD:<current-branch>
```

Git authentication is forced into non-interactive mode. The command sets:

```text
GIT_TERMINAL_PROMPT=0
GCM_INTERACTIVE=Never
```

and supplies SSH `BatchMode=yes` when the caller has not already defined `GIT_SSH_COMMAND`.

If authentication is unavailable, the command fails instead of opening an interactive prompt.

## AnsiRover submodule gitlink

When `ansirover/` is its own Git submodule repository, publishing its new commit changes the gitlink recorded by the application repository.

The command therefore performs this sequence:

```text
1. commit/push AnsiRover repo
2. commit only the ansirover gitlink in the application repo
3. push the application repo
```

Parent commit message:

```text
chore: update ansirover for <service> <image-tag>
```

Unknown or unrelated application files are not added to this gitlink commit.

When `ansirover/` belongs to the same Git repository as the application, there is no separate gitlink commit. The allowlisted AnsiRover commit is the application-repository commit and that repository is pushed once.

A deployment-record commit may make the repository `HEAD` newer than the commit used as `IMAGE_TAG`. That is expected: the image tag identifies the source revision used for the image, while the later commit records which image was published.

## Dry run

```bash
ansirover-compose-build-push . --dry-run
```

Dry-run validates the workflow, including:

- repository layout;
- one-service contract;
- inventory existence and `deployedImageName` shape;
- source Git identity;
- source clean state;
- Docker-ignore safety;
- image naming/tagging;
- Git branch, `origin`, `user.name`, and `user.email` publication prerequisites.

It does not:

- build or push an image;
- change the inventory;
- create Git commits;
- push Git repositories.

## Usage

Calling the command without arguments prints usage and performs no action:

```bash
ansirover-compose-build-push
```

Build/publish the current application repository explicitly:

```bash
ansirover-compose-build-push .
```

Use another application repository:

```bash
ansirover-compose-build-push /Volumes/FrgGit/tictactoe
```

The application's AnsiRover directory itself may be supplied:

```bash
ansirover-compose-build-push ./ansirover
```

This remains valid when `ansirover/` is a Git submodule.

## Output

The command emits machine-readable result lines after a successful run.

Representative output:

```text
IMAGE_TAG=c225b95
IMAGE=cr.umapps.net/umapps/tictactoe:c225b95
SOURCE_GIT_ROOT=/Volumes/FrgGit/tictactoe
ANSIROVER_GIT_ROOT=/Volumes/FrgGit/tictactoe/ansirover
ANSIROVER_BRANCH=main
ANSIROVER_ORIGIN=git@github.com:example/tictactoe-ansirover.git
APPLICATION_BRANCH=main
APPLICATION_ORIGIN=git@github.com:example/tictactoe.git
BUILD_PUSH_COMPLETED=true
ANSIROVER_COMMITTED=true
APPLICATION_GITLINK_COMMITTED=true
GIT_PUBLICATION_COMPLETED=true
```

When `ansirover/` belongs to the application repository, `ANSIROVER_GIT_ROOT` and the application Git repository are the same and `APPLICATION_GITLINK_COMMITTED=false`.

With `--dry-run`, mutation completion values are false.

## What this command does not do

This command stops after image publication and Git publication of the AnsiRover deployment state. It does not:

- call `ansirover-restore-upload`;
- deploy or mutate a CapRover app;
- run `ansirover-health-check`;
- create a missing AnsiRover inventory;
- create registry base-image repositories;
- create Git submodules;
- perform `docker login`;
- prompt for credentials or confirmation.

CapRover deployment remains a separate transaction.

## Base image convention

Application Dockerfiles should use UltraMegaApps registry base images rather than public base images directly when an internal equivalent exists.

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
GitHub repository: fernando-reis-guimaraes/cr_umapps_net_umapps_node22
Registry image:    cr.umapps.net/umapps/node22
Compose service:   umapps_node22
```

Creating a missing base-image repository or adding it as a Git submodule remains outside this CLI's scope and belongs to the coding-agent deployment workflow.

## Prerequisites

- Git
- Docker
- Docker Compose v2
- configured Git `user.name` and `user.email`
- an `origin` remote for every repository the CLI must push
- non-interactive Git authentication
- authentication that permits pushing to `cr.umapps.net`
