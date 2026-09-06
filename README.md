# ansirover

AnsiRover toolset for CapRover: backup, restore, health-check, log printing, app deletion, and template creation, driven by a local editable inventory of YAML app definitions plus optional sidecars.

The toolset is installed into `~/bin` as a Git submodule under `.packages/ansirover`, with root-level symlinks exposing the command names. Older `caprover-ansirover-*` names are kept as thin deprecation wrappers outside this repository.

## Command inventory

```text
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

## Typical workflow

```text
1. ansirover-template-app-create example-app
2. Edit example-app/example-app.yaml and sidecars.
3. ansirover-restore-upload <server> example-app ./example-app --with-image --with-volumes --wait-deploy
4. ansirover-health-check <server> example-app ./example-app
5. ansirover-download-backup <server> example-app ./example-app --with-image --with-volumes --force
```

## Tests

```bash
python3 -m unittest discover -s tests
```

## Git

```bash
git push origin main
```

The main repository records the commit of this repository as a submodule gitlink. After pushing here, update the parent so the recorded commit tracks `origin/main`; `bin-update-submodules-and-links` prints an ASCII alert when the two drift.
