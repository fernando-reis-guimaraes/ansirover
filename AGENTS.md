# AGENTS.md

## Scope

This repository hosts the AnsiRover toolset for CapRover. It is consumed as a submodule by `~/bin` under `.packages/ansirover`.

## Language policy

- Use English only in source code, code comments, tests, user-facing messages, and project documentation.
- Portuguese is allowed only in chat between the user and coding agents.

## Layout

Each CLI has an extensionless executable shim and an executable `*.py` implementation in the repository root, plus an optional `*.md` sidecar:

```text
ansirover-restore-upload          shim (self-locating, see below)
ansirover-restore-upload.py       implementation (#!/usr/bin/env python3)
ansirover-restore-upload.md       sidecar documentation
```

`caprover_ansirover_policy.py` is a shared module imported by the implementations. It is not a CLI and has no shim.

The `~/bin` consumer exposes command names as symlinks pointing at these shims. Shims follow the consumer's two-line base-shim convention and exec the same-basename implementation file at the consumer's submodule checkout path:

```bash
# shellcheck shell=bash
exec "$HOME/bin/.packages/ansirover/ansirover-restore-upload.py" "$@"
```

The static path is intentional: it keeps the shim free of argument parsing, path resolution, and business logic, and executing the implementation through its real package path keeps `sys.path[0]` inside this repository so sibling imports such as `caprover_ansirover_policy` resolve.

The deprecated `caprover-ansirover-*` wrappers live in the consumer repository, not here.

## CLI rules

- Run with no arguments: print usage and exit; never perform work or enter interactive mode.
- Named arguments use the single-token `--name=value` form.
- Implementations print timestamps and `[plan]`/`[config]`/`[check]` diagnostics to stderr; stdout carries results and log output.

## Build/publish application contract

For `ansirover-compose-build-push` and deployment automation, preserve these invariants:

```text
1 Git repository = 1 deployable app
1 top-level ansirover/ = 1 app inventory
1 ansirover/compose.yaml = 1 service
1 service = 1 image
```

- Monorepos are not supported.
- Multiple independently deployable apps under one parent project must be separate Git repositories, normally attached as Git submodules.
- Do not add per-app subdirectories below an application's `ansirover/`; keep the inventory flat.
- `ansirover/compose.yaml` must define exactly one buildable service.
- Derive `IMAGE_TAG` from the short `HEAD` of the Git repository that owns the resolved `build.context`, not from the repository that stores the AnsiRover inventory.
- The application `ansirover/` directory may itself become a Git submodule; build identity must remain based on `build.context`.
- A remote `build.context` is invalid for this workflow because the CLI must resolve a local source Git repository.
- Keep image publication separate from CapRover deployment: build/push with `ansirover-compose-build-push`, then deploy inventory with `ansirover-restore-upload`.

## Machine resolution

Commands that target a CapRover server take a machine name resolved from `~/.config/configstore/caprover.json`. Strip a leading `captain.` subdomain from the machine `baseUrl` to derive the SSH host; SSH as `root`.

## Tests

Tests live in `tests/` and import the root-level `.py` files. Run:

```bash
python3 -m unittest discover -s tests
```

User-visible or safety-sensitive behavior changes require focused regression coverage in `tests/`.

For `ansirover-compose-build-push`, regression tests must cover the single-service invariant and source Git identity derived from `build.context`, including the case where `ansirover/` is addressed as a submodule directory.

## Safety

- Deleting an app requires a local backup, typed confirmation of `<server>/<app>`, or an explicit `--force`.
- Prefer commands that terminate over open-ended streams; use bounded log reads and explicit timeouts.
- Never merge, squash, or force-push a pull request unless the user explicitly requests that specific operation.
