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

The `~/bin` consumer exposes command names as symlinks pointing at these shims, so shims must resolve their own real location instead of assuming `$HOME/bin`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    SOURCE_DIRECTORY="$(CDPATH= cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    case "$SOURCE" in
        /*) ;;
        *) SOURCE="$SOURCE_DIRECTORY/$SOURCE" ;;
    esac
done

SCRIPT_DIR="$(CDPATH= cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
exec "$SCRIPT_DIR/ansirover-restore-upload.py" "$@"
```

Keep the shim free of argument parsing and business logic. Command-name and implementation choices stay reversible by editing only the shim target.

The deprecated `caprover-ansirover-*` wrappers live in the consumer repository, not here.

## CLI rules

- Run with no arguments: print usage and exit; never perform work or enter interactive mode.
- Named arguments use the single-token `--name=value` form.
- Implementations print timestamps and `[plan]`/`[config]`/`[check]` diagnostics to stderr; stdout carries results and log output.

## Machine resolution

Commands that target a CapRover server take a machine name resolved from `~/.config/configstore/caprover.json`. Strip a leading `captain.` subdomain from the machine `baseUrl` to derive the SSH host; SSH as `root`.

## Tests

Tests live in `tests/` and import the root-level `.py` files. Run:

```bash
python3 -m unittest discover -s tests
```

User-visible or safety-sensitive behavior changes require focused regression coverage in `tests/`.

## Safety

- Deleting an app requires a local backup, typed confirmation of `<server>/<app>`, or an explicit `--force`.
- Prefer commands that terminate over open-ended streams; use bounded log reads and explicit timeouts.
- Never merge, squash, or force-push a pull request unless the user explicitly requests that specific operation.
