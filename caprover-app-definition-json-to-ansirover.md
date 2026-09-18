# caprover-app-definition-json-to-ansirover

Converts one CapRover app definition JSON object to the AnsiRover YAML format.

This is not a generic JSON-to-YAML converter.

It keeps only the AnsiRover-supported fields, emits them in the AnsiRover order, derives `deployedImageName` from `versions` + `deployedVersion`, and writes multiline fields as sidecars.

## Usage

```bash
caprover-app-definition-json-to-ansirover <app-json-file> <app-yaml-file>
```

Example:

```bash
caprover-app-definition-json-to-ansirover ./secbyte.json ./secbyte.yaml --force
```

## YAML field order

```yaml
appName: xpto
deployedImageName: registry.example.com/captain/img-captain-xpto:1
description: ""
ansirover:
  restartDelay: 30m
hasPersistentData: false
instanceCount: 1
containerHttpPort: 80
notExposeAsWebApp: false
websocketSupport: true
tags:
  - tagName: tag-a
  - tagName: tag-b
httpAuth:
  user: admin
  passwordHashed: "$apr1$cTzqC0j6$Up3JGQHPTKz3me4DuiPnk1"
volumes: []
ports: []

# envVars = xpto.env-vars.env sidecar
# preDeployFunction = xpto.pre-deploy-function.js sidecar
# serviceUpdateOverride = xpto.service-update-override.yaml sidecar
# customNginxConfig = xpto.custom-nginx-config.conf sidecar
```

## AnsiRover metadata

The CapRover app model does not expose a separate arbitrary metadata field in the app definition used by these tools. Explicit AnsiRover policy is therefore stored inside `description` between the exact internal markers:

```text
# ANSIROVER-BEGIN #
<single-line Base64 JSON>
# ANSIROVER-END #
```

The converter validates and removes that internal block before writing the human `description` field. It reconstructs an `ansirover` mapping only when policy was explicitly declared. Runtime defaults are not added during export.

Schema version 1 remains valid for policy-only metadata. Schema version 2 adds `envVarTemplates`, a mapping containing only environment-variable values that used AnsiRover template expansion. Resolved values and secrets are never stored in this metadata.

During export, a key present in `envVarTemplates` is written back to `<app-name>.env-vars.env` using its original template instead of the resolved value stored in CapRover. If metadata references an environment key that no longer exists in live CapRover state, conversion fails rather than fabricating a sidecar.

Duplicate blocks, invalid Base64 or JSON, unsupported schema versions, incompatible policy fields, and invalid template metadata abort the conversion.

## Sidecars

When present in the input JSON, these fields are written as sidecars in the same directory as the YAML:

```text
<app-name>.env-vars.env
<app-name>.pre-deploy-function.js
<app-name>.service-update-override.yaml
<app-name>.custom-nginx-config.conf
```

The extensions are intentional to improve editor highlighting in VS Code.

## deployedImageName

`deployedImageName` is not copied from the app definition root.

It is derived by:

```text
Find versions item where:
  versions[].version == deployedVersion

Then use:
  versions[].deployedImageName
```

## tags

Tags are kept as:

```yaml
tags:
  - tagName: tag-a
```

Only `tagName` is managed by AnsiRover.

## httpAuth

HTTP basic auth is kept as:

```yaml
httpAuth:
  user: admin
  passwordHashed: "$apr1$cTzqC0j6$Up3JGQHPTKz3me4DuiPnk1"
```

AnsiRover stores and restores the already-hashed password value. It does not need the plaintext password.

## Options

```bash
--force
```

Without `--force`, the command refuses to overwrite existing YAML or sidecar files.

With `--force`, stale sidecars are removed when the corresponding field is empty in the input JSON.
