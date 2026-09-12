# ansirover-restore-upload

Restore CapRover apps from AnsiRover backup folders.

It can restore app configuration, sidecars, selected image archives, and named Docker volume archives.

## Usage

```bash
ansirover-restore-upload <server-name-or-url> <all|app-name> [path] [options]
```

## Positional arguments

### `server-name-or-url`

Destination CapRover CLI machine name or Captain URL.

Machine-name mode preserves the existing behavior and uses a CapRover CLI machine configured locally.

URL mode is stateless/API-only and is intended for environments such as GitHub Actions:

```bash
CAPROVER_PASSWORD='...' \
ansirover-restore-upload \
  https://captain.example.com \
  ses \
  ./backup/ses
```

When the value starts with `http://` or `https://`, the script uses `caprover api --caproverUrl` and requires:

```text
CAPROVER_PASSWORD
```

URL mode does not read `~/.config/configstore/caprover.json`, does not perform the SSH `docker pull` image preflight, and does not support `--with-image` or `--with-volumes`.

Examples:

```bash
ansirover-restore-upload storage ses
ansirover-restore-upload storage all
ansirover-restore-upload https://captain.example.com ses ./backup/ses
```

### `all|app-name`

Use `all` to restore every app found in a server backup folder.

Use an app name to restore only one app.

### `path`

Optional backup path.

For a single app, `path` is the app backup folder.

For `all`, `path` is the server backup folder.

If omitted, it defaults to the current directory.

Examples:

```bash
# Restore a single app from inside its app folder
cd /Volumes/UmaGit/inventory-products/storage/ses
ansirover-restore-upload storage ses

# Restore a single app from an explicit app folder
ansirover-restore-upload storage ses /Volumes/UmaGit/inventory-products/storage/ses

# Restore all apps from inside the server backup folder
cd /Volumes/UmaGit/inventory-products/frginbox
ansirover-restore-upload storage all
```

## Options

### `--with-image`

Restore/upload image archives when available.

Without `--with-image`, the restore uses `deployedImageName` from the YAML directly.

This is fine for Docker Hub images and for image names that the destination server can already pull.

Use `--with-image` when restoring custom/local/private image archives.

`--with-image` is not supported when the server target is a Captain URL.

### `--with-dockerhub`

With `--with-image`, also restore/upload Docker Hub image archives.

Docker Hub image archives are skipped by default because they can normally be pulled directly from Docker Hub.

### `--with-volumes`

Restore named Docker volume archives.

The script looks for files like:

```text
<app>/<app>.<volume-name>.tar.gz
```

Bind mounts are not restored by this option.

If the backup folder contains:

```text
WARNING-BIND-MOUNTS-NOT-BACKED-UP.txt
```

read it before relying on the restore for disaster recovery.

`--with-volumes` is not supported when the server target is a Captain URL.

### `--wait-deploy`

Retained for compatibility. Waiting for deploy completion is already the default, so passing this option does not change behavior.

### `--no-wait`

Use the detached CapRover deploy endpoint and continue without waiting for deploy completion.

### `--limit <N>`

Only valid with `all`.

Restore only the first `N` discovered apps.

Useful for smoke tests.

Example:

```bash
ansirover-restore-upload storage all /Volumes/UmaGit/inventory-products/frginbox --limit 1
```

### `--ignore <app1,app2>`

Only valid with `all`.

Skip comma-separated app names.

Example:

```bash
ansirover-restore-upload storage all /Volumes/UmaGit/inventory-products/frginbox --ignore ses,secbyte
```

## Optional app policy

The inventory YAML may contain:

```yaml
ansirover:
  restartDelay: 1h
  gracePeriod: 3m
  postgresReconnectThreshold: 3
  postgresPollInterval: 1s
```

The mapping is optional. Unknown fields and invalid values fail validation. Durations use positive Docker-style values such as `30s`, `3m`, `1h`, or `1h30m`.

Runtime defaults are:

```text
restartDelay               1h
gracePeriod                3m
postgresReconnectThreshold 3
postgresPollInterval       1s
```

Defaults affect runtime behavior but are not materialized into downloaded YAML. Review `restartDelay` per app: one hour may be useful for a noncritical administration tool but dangerous for a critical application.

`restartDelay` controls `Spec.TaskTemplate.RestartPolicy.Delay`. The other three fields configure the bounded PostgreSQL reconnect observation after Swarm convergence. The reconnect guard tracks connection generations rather than raw new-session counts. Initial connection establishment, pure pool expansion, and partial pool churn with at least one surviving prior session do not count as reconnect waves. A reconnect wave requires a previously observed connection generation to be fully replaced, either directly or after a full disconnect.

Explicit policy is stored in the CapRover description as internal versioned metadata. Human description text is preserved. The metadata contains only policy fields and never environment values or credentials.

## Restore flow

For each app, the script performs this flow:

```text
1. Parse and validate the inventory and its explicit app policy
2. Perform the bounded PostgreSQL semantic preflight before app mutation
3. Read sidecars and validate app identity
4. In machine-name mode, verify through SSH that the destination can pull the selected image
5. Check whether the app exists and register it when missing
6. Reject malformed or duplicate AnsiRover metadata already stored in the description
7. Apply initial app config with forceSsl=false and persist explicit policy metadata
8. Enable base-domain SSL early
9. Restore named volumes and image archives when requested
10. Deploy the selected image and wait for completion by default
11. Reload the app definition and reapply final config with forceSsl=true
12. Apply Docker restart delay through a detached service update
13. Verify persisted AnsiRover metadata and every inventory-declared environment variable
14. When enabled, verify Swarm convergence, task churn, and PostgreSQL reconnect waves
```

In URL mode, step 3 is intentionally skipped. CapRover itself validates/pulls the already-published registry image during deploy. This accepts the trade-off that an unavailable image is detected at deploy time instead of before the first app mutation.

If the app already exists, the script does not change `hasPersistentData`.

If the app is missing, `hasPersistentData` from the backup is used when registering the app.

The environment verification is a subset comparison: every key declared by the inventory must exist live with the exact same value. CapRover may add extra live keys. General values are redacted in drift output, and PostgreSQL URLs display only host, port, and database.

The PostgreSQL semantic preflight makes one bounded real connection per discovered PostgreSQL URI to prove that the database exists. This can wake a suspended serverless compute. Credentials are passed through process environment variables and never command arguments or logs. `CAPROVER_DB_PREFLIGHT_TIMEOUT_SECONDS` is an internal CLI timeout and is not app policy.

For a waiting single-app restore, the reconnect guard uses `pg_stat_activity` and requires the application connection URI to declare the expected `application_name`. If the first PostgreSQL observation is empty, the first later non-empty session set establishes the baseline and does not count as a reconnect. After a baseline exists, a complete session-generation replacement counts as a reconnect wave; a full disconnect stays pending until a replacement generation appears. If the reconnect-wave threshold is reached, the command scales the service to zero with a detached update and fails.

Swarm task convergence/churn remains a separate guardrail and does not depend on a PostgreSQL session baseline, so initialization crash loops are still detected independently. Restart delay handles processes that exit; scale-to-zero isolates processes that remain alive while repeatedly reconnecting.

The `all` mode always applies restart delay, verifies metadata, and verifies inventory-declared environment variables. It skips the slower convergence, churn, and reconnect checks by default. Set `CAPROVER_ANSIROVER_ALL_POST_DEPLOY_CHECKS=true` to run the slower checks for every app. `--no-wait` also skips those slow checks because deploy completion is asynchronous.

## Run state for `all`

When restoring `all`, the script creates a run-state file inside the server backup folder:

```text
ansirover-restore-upload.json
```

This file records progress per app.

Example statuses:

```text
pending
running
done
failed
```

The run-state file allows interrupted `all` restores to resume.

Already completed apps are skipped.

Failed or running apps can be retried when the same command is run again.

## Completed run refusal

If the run-state file exists and has:

```json
{
  "status": "completed"
}
```

the script refuses to start a new `all` restore using that same backup folder.

This prevents accidentally re-running a completed restore.

To run the same restore again, delete the state file manually:

```bash
rm -f /Volumes/UmaGit/inventory-products/frginbox/ansirover-restore-upload.json
```

Then run the restore command again.

## Run state compatibility

When resuming an incomplete `all` restore, the script validates that the current command matches the existing state.

The state is tied to details such as:

```text
serverName
rootPath
withImage
withDockerHub
withVolumes
waitDeploy
limit
ignore
```

If those do not match, delete the state file manually only if you intentionally want a fresh run.

## Image behavior

Without `--with-image`:

```text
DEPLOY_IMAGE=<deployedImageName from YAML>
```

In machine-name mode, before changing the app the script runs `docker pull` through SSH on the destination server. This preflight exists to avoid applying configuration/SSL changes and only later discovering during deploy that the image is unavailable.

In URL mode, the SSH preflight is intentionally skipped. The restore is API-only and relies on CapRover to pull the `deployedImageName` during deploy.

With `--with-image` in machine-name mode:

- custom/local/private image archives can be loaded and deployed;
- Docker Hub image archives are skipped unless `--with-dockerhub` is also passed.

After restoring or publishing the archive, the script performs the same destination-side image preflight before deploy.

This avoids unnecessary archiving/restoring of public base images.

## Volume behavior

With `--with-volumes` in machine-name mode, the script restores named Docker volume archives before deploying the selected image.

Bind mounts are not restored.

If a bind mount contains real app data, it must be restored separately.

## Result lines

The script prints machine-readable result lines to stdout:

```text
APP_NAME=ses
DEPLOY_IMAGE=roundcube/roundcubemail:1.6.11-apache
```

Logs are printed to stderr with timestamps.

## Examples

Restore one app through a configured CapRover machine using the image directly:

```bash
cd /Volumes/UmaGit/inventory-products/storage/ses
ansirover-restore-upload storage ses
```

Restore one app in stateless/API-only URL mode:

```bash
CAPROVER_PASSWORD='...' \
ansirover-restore-upload \
  https://captain.example.com \
  ses \
  ./backup/ses
```

Restore all apps in stateless/API-only URL mode:

```bash
CAPROVER_PASSWORD='...' \
ansirover-restore-upload \
  https://captain.example.com \
  all \
  ./backup
```

Restore one app with volumes in machine-name mode:

```bash
ansirover-restore-upload storage ses /Volumes/UmaGit/inventory-products/storage/ses --with-volumes
```

Restore one app with custom image archive in machine-name mode:

```bash
ansirover-restore-upload storage accounts /Volumes/UmaGit/inventory-products/frginbox/accounts --with-image
```

Smoke test `all` restore:

```bash
rm -f /Volumes/UmaGit/inventory-products/frginbox/ansirover-restore-upload.json

ansirover-restore-upload \
  storage \
  all \
  /Volumes/UmaGit/inventory-products/frginbox \
  --limit 1 \
  --with-image
```

Restore all apps, skipping known apps:

```bash
rm -f /Volumes/UmaGit/inventory-products/frginbox/ansirover-restore-upload.json

ansirover-restore-upload \
  storage \
  all \
  /Volumes/UmaGit/inventory-products/frginbox \
  --ignore ses,secbyte \
  --with-image \
  --with-volumes
```

Re-run a completed `all` restore intentionally:

```bash
rm -f /Volumes/UmaGit/inventory-products/frginbox/ansirover-restore-upload.json

ansirover-restore-upload \
  storage \
  all \
  /Volumes/UmaGit/inventory-products/frginbox \
  --with-image \
  --with-volumes
```
