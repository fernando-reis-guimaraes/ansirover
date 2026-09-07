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
- Implementations print timestamps and `[plan]`/`[config]`/`[check]` diagnostics to stderr; stdout carries results and command output where streaming subprocesses require it.
- Agent-facing commands must fail rather than prompt. Git publication must disable terminal/credential-manager prompts and use SSH batch mode when the caller has not supplied its own `GIT_SSH_COMMAND`.

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
- The matching inventory file is `ansirover/<service-name>.yaml`.
- Derive `IMAGE_TAG` from the short `HEAD` of the Git repository that owns the resolved `build.context`, not from the repository that stores the AnsiRover inventory.
- The source Git repository must be clean before image publication. Exclude only `ansirover/` changes that cannot enter the Docker build context.
- When `ansirover/` is inside `build.context`, the effective Docker ignore file must explicitly exclude its relative path. Dockerfile-specific ignore files take precedence over root `.dockerignore` files.
- Place the required AnsiRover exclusion after all Docker-ignore negation rules so later exceptions cannot re-include it.
- The application `ansirover/` directory may itself be a Git submodule; build identity must remain based on `build.context`.
- A remote `build.context` is invalid because the CLI must resolve a local source Git repository.
- After a successful image push, update `<service>.yaml` `deployedImageName` to the exact immutable image reference.
- Commit only the AnsiRover allowlist: `compose.yaml`, the app inventory, known app sidecars, and the actual Dockerfile/effective Dockerfile-specific ignore file when they live inside `ansirover/`.
- Do not automatically add unknown extra files from `ansirover/`.
- Push the AnsiRover repository after its allowlisted commit.
- If `ansirover/` is a Git submodule, commit only its gitlink in the application repository and push that repository after the AnsiRover push succeeds.
- Never force-push from `ansirover-compose-build-push`.
- Keep CapRover deployment separate: image/Git publication uses `ansirover-compose-build-push`; applying inventory uses `ansirover-restore-upload`.

## Target grammar and machine resolution

These rules apply to `ansirover-*` commands. Do not impose this lookup behavior on lower-level `caprover-*` helpers, which may intentionally work with direct SSH/server targets.

- A bare server value such as `srv1` is always a CapRover machine name. Resolve it from `~/.config/configstore/caprover.json`.
- Never fall back from a missing CapRover machine name to DNS, mDNS, Tailscale, `/etc/hosts`, or another hostname lookup. If `srv1` is both a valid hostname and a configured CapRover machine, the CapRover machine interpretation wins.
- An AnsiRover CapRover machine name must not contain `@`. Reserve `@` for explicit SSH targets even if the upstream CapRover CLI would accept `@` in a machine name.
- A direct SSH target is valid only on commands/options that explicitly support one, such as `--ssh-target=user@host`.
- An explicit SSH target must use `user@host` with exactly one `@` and non-empty user and host components. Bare hostnames such as `storage`, `10.0.0.5`, or `storage.example.com` are not explicit SSH targets.
- A direct Captain URL is valid only on commands that explicitly support URL mode. It must use `http://` or `https://` and is distinct from both machine-name lookup and SSH addressing.
- When a machine name is resolved from CapRover config, strip a leading `captain.` subdomain from its `baseUrl` hostname to derive the default SSH host; SSH as `root` unless the command has an explicit `--ssh-target=user@host` override.

Examples:

```text
srv1
  -> CapRover machine-name lookup

root@srv1
  -> explicit SSH target, only where an SSH-target option is supported

https://captain.srv1.umapps.net
  -> explicit Captain URL, only where URL mode is supported
```

## Tests

Tests live in `tests/` and import the root-level `.py` files. Run:

```bash
python3 -m unittest discover -s tests
```

User-visible or safety-sensitive behavior changes require focused regression coverage in `tests/`.

For `ansirover-compose-build-push`, regression tests must cover:

- no-argument usage-only behavior;
- the single-service invariant;
- source Git identity derived from `build.context`;
- hard clean-state enforcement with AnsiRover exclusion;
- Docker-ignore enforcement and Dockerfile-specific precedence;
- inventory update behavior;
- allowlisted commits that leave unknown files out;
- non-interactive Git publication settings;
- AnsiRover submodule layout handling.

For target resolution, regression tests must cover:

- bare CapRover machine names remain machine names and are resolved through CapRover config;
- CapRover machine names containing `@` are rejected by AnsiRover;
- explicit SSH targets require the exact `user@host` shape;
- a missing machine name is never reinterpreted as a hostname.

## Safety

- Deleting an app requires a local backup, typed confirmation of `<server>/<app>`, or an explicit `--force`.
- Prefer commands that terminate over open-ended streams; use bounded log reads and explicit timeouts.
- Never merge, squash, or force-push a pull request unless the user explicitly requests that specific operation.
