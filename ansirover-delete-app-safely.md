# ansirover-delete-app-safely

Safely delete one CapRover app after validating the local AnsiRover backup/template.

This command is intentionally conservative. It does not download missing archives, does not update the remote app, and does not delete named Docker volumes unless explicitly requested.

## Usage

```bash
ansirover-delete-app-safely <server> <app-name> [path]
```

Examples:

```bash
ansirover-delete-app-safely frginbox picoshare
ansirover-delete-app-safely frginbox picoshare ./frginbox/picoshare
ansirover-delete-app-safely frginbox picoshare ./frginbox/picoshare --dry-run
```

Delete the app and keep volumes:

```bash
ansirover-delete-app-safely frginbox picoshare ./frginbox/picoshare
```

Delete the app and named volumes:

```bash
ansirover-delete-app-safely frginbox picoshare ./frginbox/picoshare --delete-volumes
```

Automation mode, no per-volume confirmation:

```bash
ansirover-delete-app-safely frginbox picoshare ./frginbox/picoshare --delete-volumes --force
```

## Safety checks

Before deleting, the command verifies:

```text
1. The remote app exists in CapRover appDefinitions.
2. The current remote app definition converted to AnsiRover YAML matches the local <app>.yaml.
3. Named volumes declared by the current remote definition have valid local archives:
   <app>.<volume>.tar.gz
4. No corresponding .partial archive exists.
5. gzip -t succeeds for each required archive.
6. tar -tzf succeeds for each required archive.
7. The image archive exists and is valid when required:
   <app>.image.tar.gz
```

The command compares only the AnsiRover YAML file:

```text
<app>.yaml
```

It does not compare:

```text
<app>.json
```

The JSON is treated as raw CapRover data. The YAML is the local canonical AnsiRover format.

## Image archive rule

For Docker Hub images, the image archive is not required by default because the download backup flow skips Docker Hub images unless explicitly requested.

To require a local image archive even for Docker Hub images:

```bash
ansirover-delete-app-safely <server> <app-name> [path] --require-dockerhub-image
```

For private/custom/local images, the command requires:

```text
<app>.image.tar.gz
```

## Volume archive rule

For every named volume in the current remote app definition, the command requires:

```text
<app>.<volume>.tar.gz
```

Example:

```text
picoshare.picoshare-data.tar.gz
```

Bind mounts are not named volumes. They are warned about, but they are not validated as local `.tar.gz` archives and are not deleted by `--delete-volumes`.

## Deleting volumes

By default, volumes are kept.

With:

```bash
--delete-volumes
```

the command asks the user to confirm each physical Docker volume name:

```text
Volume to delete:
  captain--picoshare-data

Type exactly this volume name to delete it:
```

The user must type:

```text
captain--picoshare-data
```

With:

```bash
--delete-volumes --force
```

the command skips confirmation. This is intended for automation.

## Dry run

```bash
ansirover-delete-app-safely <server> <app-name> [path] --dry-run
```

Dry run performs all validations and prints the delete plan, but does not call the delete endpoint.

## CapRover API routes used

The delete routes below were captured from the HAR file for deleting apps in CapRover.

### Refresh current app definition

```http
GET /user/apps/appDefinitions
```

Used to fetch the current remote app definition. The command converts that remote definition to temporary AnsiRover YAML and compares it with the local `<app>.yaml`.

### Delete app while keeping volumes

```http
POST /user/apps/appDefinitions/delete
```

Payload:

```json
{
  "volumes": [],
  "appNames": ["<app-name>"]
}
```

Observed response:

```json
{
  "status": 100,
  "description": "App is deleted",
  "data": {}
}
```

### Delete app and selected named volumes

```http
POST /user/apps/appDefinitions/delete
```

Payload:

```json
{
  "volumes": ["<volume-name-1>", "<volume-name-2>"],
  "appNames": ["<app-name>"]
}
```

Important:

```text
The payload uses CapRover logical volume names without the captain-- prefix.
The confirmation prompt shows the physical Docker volume name with the captain-- prefix.
```

Observed partial-success response:

```json
{
  "status": 102,
  "description": "App is deleted. Some volumes were not safe to delete. Delete skipped for: captain--teste-to-be-deleted-har",
  "data": {
    "volumesFailedToDelete": ["captain--teste-to-be-deleted-har"]
  }
}
```

This command treats status `102` as a partial delete requiring manual follow-up.

## Non-goals

This command does not:

```text
download missing volumes
download missing images
modify the remote app before deletion
compare <app>.json
delete bind mount host paths
retry the destructive delete call
```
