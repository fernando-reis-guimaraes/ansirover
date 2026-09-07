# ansirover

AnsiRover toolset for CapRover: image build/publish, backup, restore, health-check, log printing, app deletion, and template creation, driven by a local editable inventory of YAML app definitions plus optional sidecars.

The toolset is installed into `~/bin` as a Git submodule under `.packages/ansirover`, with root-level symlinks exposing the command names. Older `caprover-ansirover-*` names are kept as thin deprecation wrappers outside this repository.

## Command inventory

```text
ansirover-compose-build-push
ansirover-delete-app-safely
ansirover-delete-app-without-local-backup
ansirover-download-backup
ansirover-health-check
ansirover-logs-print
ansirover-postgres-application-name-migrate
ansirover-restore-upload
ansirover-template-app-create
caprover-app-definition-json-to-ansirover
```

Each command ships as an extensionless executable shim plus an executable `*.py` implementation and, where useful, a `*.md` sidecar. Run any command without arguments to see its usage.

Shims use `#!/bin/sh` so subprocess callers can execute them directly. A shellcheck directive alone does not select an interpreter and causes an `Exec format error` during backup conversion.

## Deployable application repository contract

The build/publish workflow intentionally keeps application layout simple:

```text
<application-git-root>/
├── ... application source ...
├── .dockerignore
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

Monorepos are not supported. When a parent project contains multiple independently deployable applications, each application must be a separate Git repository, normally attached to the parent as a Git submodule.

The application `ansirover/` directory is flat. Do not add per-app subdirectories below it.

`ansirover/compose.yaml` defines exactly one buildable service. The matching inventory is `ansirover/<service>.yaml`.

`ansirover-compose-build-push` derives `IMAGE_TAG` from the short `HEAD` of the Git repository that owns the resolved `build.context`. The source repository must be clean before publication, excluding only AnsiRover files that cannot enter the Docker build context.

When `ansirover/` is inside `build.context`, the effective Docker ignore file must explicitly exclude it. Dockerfile-specific ignore files take precedence over the root `.dockerignore`.

See `ansirover-compose-build-push.md` for the complete build/publish contract.

## Build/publish transaction

`ansirover-compose-build-push` is non-interactive and performs one transaction up to Git publication:

```text
1. Validate application/AnsiRover layout.
2. Resolve the single Compose service and build.context.
3. Require the source Git repository to be clean.
4. Require ansirover/ to be excluded from the Docker build context.
5. Derive IMAGE_TAG from the source Git short HEAD.
6. docker compose build --push.
7. Update ansirover/<service>.yaml deployedImageName.
8. Commit only the AnsiRover allowlist.
9. Push the AnsiRover Git repository.
10. If ansirover/ is a submodule, commit/push its gitlink in the application repo.
```

Unknown extra files inside `ansirover/` are not automatically added to Git.

Git pushes are non-force and non-interactive. Missing credentials or Git publication prerequisites cause a hard failure instead of a prompt.

## Typical deployment workflow

From the application Git repository:

```text
1. Keep the app inventory and build Compose file in ./ansirover/.
2. Ensure source changes are committed and ansirover/ is excluded by the effective Docker ignore file.
3. ansirover-compose-build-push .
4. ansirover-restore-upload <server> <app> ./ansirover
5. ansirover-health-check <server> <app> ./ansirover
6. ansirover-download-backup <server> <app> ./ansirover --force
```

Image/Git publication and CapRover deployment remain separate operations. `ansirover-compose-build-push` publishes the immutable image and records it in Git; `ansirover-restore-upload` applies the inventory to CapRover.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Git

```bash
git push origin main
```

The main `bin` repository records the commit of this repository as a submodule gitlink. After pushing here, update the parent so the recorded commit tracks `origin/main`; `bin-update-submodules-and-links` prints an ASCII alert when the two drift.
