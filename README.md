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

## Deployable application repository contract

The build/publish workflow intentionally keeps application layout simple:

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

Monorepos are not supported. When a parent project contains multiple independently deployable applications, each application must be a separate Git repository, normally attached to the parent as a Git submodule.

The application `ansirover/` directory is flat. Do not add per-app subdirectories below it.

`ansirover/compose.yaml` defines exactly one buildable service. `ansirover-compose-build-push` derives `IMAGE_TAG` from the short `HEAD` of the Git repository that owns the resolved `build.context`. This keeps image identity tied to the source being built even if `ansirover/` itself later becomes a Git submodule.

See `ansirover-compose-build-push.md` for the complete build/publish contract.

## Typical deployment workflow

From the application Git repository:

```text
1. Keep the app inventory and build Compose file in ./ansirover/.
2. ansirover-compose-build-push .
3. Set ansirover/<app>.yaml deployedImageName to the published IMAGE value.
4. ansirover-restore-upload <server> <app> ./ansirover
5. ansirover-health-check <server> <app> ./ansirover
6. ansirover-download-backup <server> <app> ./ansirover --force
```

Image publication and CapRover deployment remain separate operations. `ansirover-compose-build-push` only builds and pushes the image; `ansirover-restore-upload` applies the inventory to CapRover.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Git

```bash
git push origin main
```

The main `bin` repository records the commit of this repository as a submodule gitlink. After pushing here, update the parent so the recorded commit tracks `origin/main`; `bin-update-submodules-and-links` prints an ASCII alert when the two drift.
