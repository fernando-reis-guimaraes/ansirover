# ansirover

AnsiRover toolset for CapRover: image build/publish, backup, restore, health-check, log printing, app deletion, and template creation, driven by local editable deployment inventories plus optional sidecars.

The toolset is installed into `~/bin` as a Git submodule under `.packages/ansirover`, with root-level symlinks exposing command names. Older `caprover-ansirover-*` names are kept as thin deprecation wrappers outside this repository.

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

AnsiRover publishes one selected deployment environment as one image/service.

```text
1 selected AnsiRover environment = 1 app inventory
1 compose.yaml = 1 service
1 service = 1 image
```

The application may contain multiple source folders, packages, workspaces, or components. Those source boundaries do not automatically become deployment boundaries. What matters is whether the selected production build converges to one image/service.

A typical repository may look like:

```text
<application-git-root>/
├── backend/
├── frontend/
├── shared/
├── .dockerignore
├── ansirover/
│   ├── README.md
│   ├── DEPLOY.md
│   ├── compose.yaml
│   └── <app>.yaml
└── ansirover.production/
    ├── README.md
    ├── DEPLOY.md
    ├── compose.yaml
    └── <app>.yaml
```

The default deployment directory is `ansirover/`. Arbitrary named environments use `ansirover.<name>/`. The default `ansirover/` path may be a symlink to another named environment inside the application Git root.

`compose.yaml` is the canonical image build/publication definition. `DEPLOY.md` records deployment intent, runtime boundaries, external prerequisites, and project-specific context for humans and agents. `README.md` is intentionally short and human-facing.

Each selected AnsiRover environment remains flat; do not create per-app subdirectories inside it.

## Build/publish transaction

`ansirover-compose-build-push` is non-interactive and requires its positional path to be exactly the application Git top-level:

```bash
ansirover-compose-build-push <application-git-root> [--environment <name>] [--dry-run]
```

Conceptually it performs:

```text
1. Validate the exact application Git root.
2. Select ansirover/ or ansirover.<environment>/.
3. Resolve the single Compose service and build.context.
4. Find the Git repository that owns build.context.
5. Require that source Git repository to be clean.
6. Require all ansirover/ and ansirover.* paths inside build.context to be Docker-ignored.
7. Derive IMAGE_TAG from the source Git short HEAD.
8. docker compose build --push.
9. Update <service>.yaml deployedImageName.
10. Commit only the AnsiRover allowlist, including README.md and DEPLOY.md.
11. Push the selected AnsiRover Git repository.
12. If that environment is a submodule, commit/push its gitlink in the application repository.
```

The Dockerfile may live anywhere. Its directory is not necessarily the Docker build context. `IMAGE_TAG` always comes from the Git repository that owns the resolved `build.context`.

Unknown extra files inside the selected AnsiRover directory are not automatically added to Git. Git pushes are non-force and non-interactive.

See `ansirover-compose-build-push.md` for the full contract.

## Template bootstrap

`ansirover-template-app-create` creates the runtime inventory, optional sidecar examples, a short human `README.md`, and a deployment-intent `DEPLOY.md` template.

It does not create `compose.yaml`; the deployment workflow must inspect the repository and create or repair that canonical build definition separately.

If `DEPLOY.md` already exists, the generator preserves it. If it creates a new `DEPLOY.md`, an agent should adapt that new template to the actual repository before bootstrap is complete.

See `ansirover-template-app-create.md` for details.

## External services

Provider-managed dependencies such as PostgreSQL, S3-compatible object storage, SMTP, or other external services are deployment prerequisites rather than AnsiRover-owned infrastructure.

AnsiRover can carry the resulting connection/configuration values in the environment inventory and can validate application behavior, but provisioning and lifecycle ownership remain with provider-specific workflows.

## Typical deployment workflow

Default environment:

```text
1. Keep the selected inventory and build Compose file in ./ansirover/.
2. Ensure source changes are committed.
3. ansirover-compose-build-push .
4. ansirover-restore-upload <server> <app> ./ansirover
5. ansirover-health-check <server> <app> ./ansirover
```

Named environment:

```text
1. ansirover-compose-build-push . --environment production
2. ansirover-restore-upload <server> <app> ./ansirover.production
3. ansirover-health-check <server> <app> ./ansirover.production
```

Image/Git publication and CapRover deployment remain separate operations.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Git

```bash
git push origin main
```

The main `bin` repository records this repository as a submodule gitlink. After pushing here, update the parent so the recorded commit tracks `origin/main`; `bin-update-submodules-and-links` prints an ASCII alert when the two drift.
