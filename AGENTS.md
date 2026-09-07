# AGENTS.md

## Scope

This repository hosts the AnsiRover toolset for CapRover. It is consumed as a submodule by `~/bin` under `.packages/ansirover`.

## Language policy

- Use English only in source code, code comments, tests, user-facing messages, and project documentation.
- Portuguese is allowed only in chat between the user and coding agents.

## Layout

Each CLI has an extensionless executable shim and an executable `*.py` implementation in the repository root, plus an optional `*.md` sidecar:

```text
ansirover-restore-upload          shim
ansirover-restore-upload.py       implementation (#!/usr/bin/env python3)
ansirover-restore-upload.md       sidecar documentation
```

`caprover_ansirover_policy.py` is a shared module imported by implementations. It is not a user-facing CLI and has no extensionless shim.

The `~/bin` consumer exposes command names as symlinks pointing at these shims. Shims follow the consumer's canonical two-line base-shim convention and always exec the same-basename implementation directly:

```sh
#!/bin/sh
exec "$HOME/bin/.packages/ansirover/ansirover-restore-upload.py" "$@"
```

The static package path is intentional. Shims contain no argument parsing, interpreter selection, validation, configuration, or business logic. Each implementation selects its own interpreter through its shebang. Executing the implementation through its real package path keeps `sys.path[0]` inside this repository so sibling imports such as `caprover_ansirover_policy` resolve.

The deprecated `caprover-ansirover-*` wrappers live in the consumer repository, not here.

## CLI rules

- Run with no arguments: print usage and exit; never perform work or enter interactive mode.
- Named arguments use the single-token `--name=value` form when practical; preserve existing public signatures when compatibility matters.
- Implementations print timestamps and `[plan]`/`[config]`/`[check]` diagnostics to stderr; stdout carries results and command output where streaming subprocesses require it.
- Agent-facing commands must fail rather than prompt. Git publication must disable terminal/credential-manager prompts and use SSH batch mode when the caller has not supplied its own `GIT_SSH_COMMAND`.

## Build/publish application contract

For `ansirover-compose-build-push` and deployment automation, preserve these invariants:

```text
1 selected AnsiRover environment = 1 deployable app inventory
1 compose.yaml = 1 service
1 service = 1 image
```

- The positional path passed to `ansirover-compose-build-push` must be exactly the application Git top-level. Do not accept arbitrary subdirectories and infer upward from them.
- The default deployment environment is the top-level `ansirover/` path.
- Named environments use top-level `ansirover.<name>/` paths. Environment names are user-defined; do not assign special semantics to `production`, `staging`, `local`, or similar names.
- The default `ansirover/` path may be a symlink to a named `ansirover.<name>/` path inside the application Git root.
- A selected AnsiRover environment may belong to the application repository or be its own Git submodule.
- Keep each selected AnsiRover environment flat; do not add per-app subdirectories below it.
- `compose.yaml` is the canonical image build/publication definition.
- `DEPLOY.md` is complementary deployment intent and architectural context. If it existed before a bootstrap, treat it as user-owned and do not rewrite it automatically. If the template generator created it during the current bootstrap, an agent should adapt it to the actual repository before bootstrap is complete.
- `README.md` is a short human-facing index and file-responsibility table suitable for GitHub rendering.
- Do not infer deployment units from source directories, package-manager workspaces, or package names. Infer from production build/runtime boundaries.
- Source-level monorepo/workspace layouts are valid when their production build converges to one image/service.
- If multiple independent deployment units exist and no aggregate deployment is declared, explain the ambiguity rather than inventing an architecture. A Git superproject with one application repository per submodule is a recommended clean option, not a mandatory solution.
- A container aggregator or Docker-in-Docker approach is technically possible but must not be selected automatically; it is generally unsuitable for production and may only be reasonable for short-lived testing/staging scenarios.
- `compose.yaml` must define exactly one buildable service. The matching inventory file is `<selected-environment>/<service-name>.yaml`.
- The Dockerfile location does not define `build.context`; inspect what the build actually needs.
- Derive `IMAGE_TAG` from the short `HEAD` of the Git repository that owns the resolved `build.context`, not from the repository that stores the AnsiRover inventory.
- The source Git repository must be clean before image publication.
- Discover all top-level `ansirover` and `ansirover.*` paths inside `build.context` and require the effective Docker ignore file to exclude each one. Dockerfile-specific ignore files take precedence over root `.dockerignore` files.
- Place every required AnsiRover exclusion after all Docker-ignore negation rules so later exceptions cannot re-include deployment metadata.
- A remote `build.context` is invalid because the CLI must resolve a local source Git repository.
- After a successful image push, update `<service>.yaml` `deployedImageName` to the exact immutable image reference.
- Commit only the AnsiRover allowlist: `compose.yaml`, `README.md`, `DEPLOY.md`, the app inventory, known app sidecars, and the actual Dockerfile/effective Dockerfile-specific ignore file when they live inside the selected environment.
- Do not automatically add unknown extra files from an AnsiRover environment.
- Push the selected AnsiRover repository after its allowlisted commit.
- If the selected environment is a Git submodule, commit only its resolved gitlink in the application repository and push that repository after the AnsiRover push succeeds.
- Never force-push from `ansirover-compose-build-push`.
- Keep CapRover deployment separate: image/Git publication uses `ansirover-compose-build-push`; applying inventory uses `ansirover-restore-upload`.

## External service boundary

Provider-managed services are prerequisites, not AnsiRover-owned infrastructure.

- Inspect runtime configuration to identify required PostgreSQL, S3-compatible storage, SMTP, or other external dependencies.
- Do not provision provider-specific infrastructure inside the AnsiRover deployment workflow.
- Use provider-specific tooling/skills when available, then place resulting connection/configuration values in the selected AnsiRover environment inventory.
- AnsiRover may validate required configuration or runtime connectivity, but service creation and lifecycle remain owned by the provider-specific workflow.
- Never put provider credentials or secrets in `DEPLOY.md` or `README.md`.

## Target grammar and machine resolution

These rules apply to `ansirover-*` commands. Do not impose this lookup behavior on lower-level `caprover-*` helpers, which may intentionally work with direct SSH/server targets.

- A bare server value such as `srv1` is always a CapRover machine name. Resolve it from `~/.config/configstore/caprover.json`.
- Never fall back from a missing CapRover machine name to DNS, mDNS, Tailscale, `/etc/hosts`, or another hostname lookup. If `srv1` is both a valid hostname and a configured CapRover machine, the CapRover machine interpretation wins.
- An AnsiRover CapRover machine name must not contain `@`. Reserve `@` for explicit SSH targets even if the upstream CapRover CLI would accept `@` in a machine name.
- Target grammar validation belongs in each implementation after argument parsing. It must validate the parsed machine/URL value, not a raw `argv` position, so options placed before positionals cannot bypass the rule.
- A direct SSH target is valid only on commands/options that explicitly support one, such as `--ssh-target=user@host`.
- An explicit SSH target must use `user@host` with exactly one `@` and non-empty user and host components. Bare hostnames such as `storage`, `10.0.0.5`, or `storage.example.com` are not explicit SSH targets.
- A direct Captain URL is valid only on commands that explicitly support URL mode. It must use `http://` or `https://`, must not contain `@`, and is distinct from both machine-name lookup and SSH addressing.
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
- exact application Git top-level enforcement;
- default and named environment selection;
- default `ansirover/` symlink handling;
- the single-service invariant;
- source Git identity derived from `build.context`;
- hard clean-state enforcement with all AnsiRover environment paths excluded;
- Docker-ignore enforcement for all `ansirover`/`ansirover.*` paths and Dockerfile-specific precedence;
- inventory update behavior;
- allowlisted commits including `README.md`/`DEPLOY.md` while leaving unknown files out;
- non-interactive Git publication settings;
- AnsiRover submodule layout handling.

For target resolution, regression tests must cover:

- bare CapRover machine names remain machine names and are resolved through CapRover config;
- CapRover machine names containing `@` are rejected by AnsiRover;
- explicit SSH targets require the exact `user@host` shape;
- a missing machine name is never reinterpreted as a hostname;
- target validation remains effective when options appear before positional arguments;
- public server-target shims use the canonical direct same-basename exec form and contain no target grammar logic.

## Safety

- Deleting an app requires a local backup, typed confirmation of `<server>/<app>`, or an explicit `--force`.
- Prefer commands that terminate over open-ended streams; use bounded log reads and explicit timeouts.
- Never merge, squash, or force-push a pull request unless the user explicitly requests that specific operation.
