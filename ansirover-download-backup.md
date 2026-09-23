# ansirover-download-backup

Download CapRover app definitions into AnsiRover backup folders.

It can optionally download custom/local Docker image archives and named Docker volume archives.
Custom domains and their SSL state are included in each generated YAML inventory.

## Usage

```bash
ansirover-download-backup <server-name> <all|app-name> [path] [options]
```

## Positional arguments

### `server-name`

CapRover CLI machine name.

Examples:

```bash
ansirover-download-backup storage all
ansirover-download-backup frginbox ses
```

### `all|app-name`

Use `all` to back up every app from the CapRover server.

Use an app name to back up only one app.

When using `all`, apps are processed in alphabetical order by app name. This makes interrupted runs easier to understand and resume.

### `path`

Optional output path.

For a single app, `path` is the exact app backup folder.

For `all`, `path` is the exact server backup folder.

If omitted:

- single app mode uses the current directory if its name matches the app name;
- otherwise single app mode writes to `./<app-name>`;
- `all` mode uses the current directory if its name matches the server name;
- otherwise `all` mode writes to `./<server-name>`.

Examples:

```bash
# Writes to /Volumes/UmaGit/inventory-products/storage
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image

# Writes exactly to /Volumes/UmaGit/inventory-products/storage/web
ansirover-download-backup storage web /Volumes/UmaGit/inventory-products/storage/web --with-volumes --with-image

# If already inside the app folder, writes to current directory
cd /Volumes/UmaGit/inventory-products/storage/web
ansirover-download-backup storage web --with-volumes --with-image
```

## Options

### `--with-json`

Save the raw CapRover JSON definitions alongside the YAML backup. JSON files are not saved by default.

### `--with-image`

Download the deployed Docker image as:

```text
<app>/<app>.image.tar.gz
```

Docker Hub images are skipped by default.

Custom/local/private images are downloaded.

Examples of images that are downloaded:

```text
localhost:5000/captain/imported-img-captain-accounts:52
registry.ultra-mega-host.com:996/captain/img-captain-classcrud:81
```

Examples of images skipped by default:

```text
mysql:9.5.0
wordpress:6.9.0-php8.4-apache
roundcube/roundcubemail:1.6.11-apache
```

### `--include-dockerhub`

With `--with-image`, also archive Docker Hub images.

Normally this is not needed because Docker Hub images can be pulled again during restore.

### `--with-volumes`

Download named Docker volumes as:

```text
<app>/<app>.<volume-name>.tar.gz
```

Only named Docker volumes are downloaded.

Host bind mounts are not downloaded.

When a bind mount exists, the script writes a warning file inside the app backup folder:

```text
<app>.BIND-MOUNTS.txt
```

Example warning file content:

```text
WARNING: BIND MOUNTS WERE NOT BACKED UP

This app has host bind mounts configured in CapRover/Docker.
This backup saved the app definition, AnsiRover YAML, sidecars, custom images, and named Docker volumes.
It did not copy host bind-mount source paths.

Back up these host paths separately before relying on this backup for full disaster recovery.

App: web

Host path -> Container path
- /data/extra/Public/Web -> /web
```

### `--force`

Overwrite existing image and volume archives.

Without `--force`, existing final archives are skipped.

This enables resume behavior for interrupted runs.

Skipped examples:

```text
<app>/<app>.image.tar.gz
<app>/<app>.<volume-name>.tar.gz
```

Partial files are not treated as completed archives.

Lower-level image and volume scripts remove stale `.partial` files before re-downloading.

### `--limit <N>`

Only valid with `all`.

Process only the first `N` apps after alphabetical sorting.

Useful for smoke tests.

Example:

```bash
ansirover-download-backup storage all --with-volumes --with-image --limit 3
```

### `--ignore <app1,app2>`

Only valid with `all`.

Skip comma-separated app names.

Example:

```bash
ansirover-download-backup storage all --with-volumes --with-image --ignore web2,test-app
```

## What is backed up

For each app:

```text
<app>/<app>.yaml
<app>/<app>.env-vars.env
<app>/<app>.pre-deploy-function.js
<app>/<app>.service-update-override.yaml
<app>/<app>.custom-nginx-config.conf
<app>/<app>.image.tar.gz
<app>/<app>.<volume-name>.tar.gz
<app>/<app>.BIND-MOUNTS.txt
```

Only sidecars that exist in the CapRover app definition are written.

With `--with-json`, the raw app JSON is also written. For `all`, this also writes the server-level JSON:

```text
<server>/<server>.json
```

## AnsiRover policy round-trip

Explicit app policy stored in CapRover description metadata is decoded into the optional inventory mapping:

```yaml
ansirover:
  gracePeriod: 3m
```

The technical metadata block is removed from the exported human `description`. Only explicitly stored fields are emitted, so runtime defaults do not appear in an inventory that omitted them. A legacy app with no AnsiRover metadata continues to export without an `ansirover` section.

The download fails explicitly when the description contains duplicate metadata blocks, invalid Base64 or JSON, an unsupported schema version, or incompatible policy fields. It does not silently discard damaged metadata.

## Resume behavior

The script is safe to run again after interruption.

If a final image archive already exists and `--force` is not passed, image download is skipped.

If a final volume archive already exists and `--force` is not passed, that volume download is skipped.

If all named volume archives for an app already exist, the volume download step for that app is skipped.

This means a repeated command such as:

```bash
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image
```

continues from missing archives instead of redownloading everything.

## Volume behavior

Named volume downloads may temporarily scale the app to zero to avoid copying a live mutable filesystem.

The script restores the app instance count after the volume archive is completed.

The restore instance count is taken from the app definition.

If the app was already scaled to zero, it should remain zero.

## Bind mounts

Bind mounts are not archived.

The script prints warnings and writes:

```text
<app>.BIND-MOUNTS.txt
```

inside each app folder that has bind mounts.

These host paths must be backed up separately if they contain data needed for disaster recovery.

Common examples:

```text
/root/nexo -> /web
/opt/sms/data -> /app/data
/data/extra/Public/Web -> /web
```

System mounts such as these may be intentionally ignored:

```text
/etc/localtime -> /etc/localtime
/etc/timezone -> /etc/timezone
```

## Result lines

The script prints machine-readable result lines to stdout:

```text
SERVER_BACKUP_DIR=/path/to/server
APP_BACKUP_DIR=/path/to/server/app
APP_BACKUP_COUNT=18
```

With `--with-json`, `SERVER_JSON_FILE` is also printed for `all` backups.

Logs are printed to stderr with timestamps.

## Examples

Back up all apps from `storage`:

```bash
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image
```

Back up one app to an explicit folder:

```bash
ansirover-download-backup storage web /Volumes/UmaGit/inventory-products/storage/web --with-volumes --with-image
```

Back up one app from inside its folder:

```bash
cd /Volumes/UmaGit/inventory-products/storage/web
ansirover-download-backup storage web --with-volumes --with-image
```

Force re-download archives:

```bash
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image --force
```

Include Docker Hub images too:

```bash
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image --include-dockerhub
```

Smoke test the first app only:

```bash
cd /Volumes/UmaGit/inventory-products
ansirover-download-backup storage all --with-volumes --with-image --limit 1
```
