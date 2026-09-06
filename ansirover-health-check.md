# ansirover-health-check

`ansirover-health-check` is a standalone, AnsiRover-folder-aware CapRover health-check CLI. It uses the stored CapRover CLI server configuration, checks Docker containers through SSH, and probes each public app's HTTPS root endpoint with `curl`.

## Usage

```bash
ansirover-health-check <server> <app|all> [path] [options]
```

Examples:

```bash
ansirover-health-check storage my-app ./my-app
ansirover-health-check storage all ./storage
ansirover-health-check storage all ./storage --ignore captain-dashboard,old-app
ansirover-health-check storage all ./storage --json
```

The app folder must contain `<app-name>.yaml`. In `all` mode, the base folder must contain one folder per CapRover app, each with its matching AnsiRover YAML file.

## Checks

For an app with `instanceCount` greater than zero, the command requires:

- At least `instanceCount` containers for `srv-captain--<app>` to be running.
- Every running container with a Docker healthcheck to report `healthy`.
- For web apps, `curl` to `https://<app>.<CapRover-root-domain>/` to return any HTTP `2xx` or `3xx` response.

Apps with `notExposeAsWebApp: true` do not receive an HTTPS check; their result is `SKIPPED_NOT_WEB_APP`.

An app with `instanceCount: 0` is reported as `STOPPED`. It does not fail the command by itself, but the command prints a large warning after all results because stopped apps are unexpected for this server.

## APR1 HTTP basic authentication

The local AnsiRover YAML supplies `httpAuth.user` and `httpAuth.passwordHashed`. Only CapRover APR1 password hashes are supported:

```yaml
httpAuth:
  user: admin
  passwordHashed: $apr1$KG1cW3ry$LAEcH2drUus77KPyBlwco1
```

The script looks up the exact encrypted hash in `.password-map.yaml`, using the closest matching file in this order:

1. The app folder.
2. Each parent folder, up to the filesystem root.
3. `~/.password-map.yaml`.
4. `~/bin/.password-map.yaml`.

Password maps use this format:

```yaml
apr1:
  $apr1$KG1cW3ry$LAEcH2drUus77KPyBlwco1: some-plain-password
```

When no mapping exists, the command prompts for the password without echoing it. It rebuilds the APR1 hash locally with `openssl` and compares it to the YAML hash. A valid password is stored in `<git-root>/.password-map.yaml` when the app is inside a Git working tree; otherwise it is stored in `<app-folder>/.password-map.yaml`. Password map files are written with `0600` permissions and are never committed automatically.

An unavailable mapping, a cancelled prompt, a bad password, or a non-APR1 hash fails the HTTPS health check.

## Options

- `--ssh-target root@example.com`: override the SSH target derived from the stored CapRover CLI configuration.
- `--ssh-timeout 60`: Docker-inspection SSH timeout in seconds.
- `--http-timeout 20`: `curl` connect and total timeout in seconds.
- `--ignore app1,app2`: skip named apps in `all` mode.
- `--json`: emit detailed JSON results.
- `--no-sort`: retain the CapRover API order in `all` mode.

## Exit codes

- `0`: all active apps passed; stopped apps may still have produced a warning.
- `1`: at least one active app is unhealthy or could not be inspected.
- `2`: invalid input or preflight/API configuration failure.

## Requirements

- `npx` and the CapRover CLI must be available.
- The server must be configured in `~/.config/configstore/caprover.json`.
- SSH access to the target CapRover host is required.
- `curl` must be available locally.
- `openssl` must be available locally when an APR1 password must be prompted for and verified.
- `<SERVER>_CAPTAIN_PASSWORD` is used when present; otherwise the saved CapRover CLI login is used.
