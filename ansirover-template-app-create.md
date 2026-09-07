# ansirover-template-app-create

Create a local AnsiRover app inventory template plus human and agent deployment guidance.

## Usage

```bash
ansirover-template-app-create <app-name> [options]
```

Options:

```text
--image <image>      Docker image for deployedImageName. Default: nginx:alpine
--port <port>        containerHttpPort. Default: 80
--minimal            Create minimal YAML + README + DEPLOY and no example sidecars.
--with-examples      Default behavior. Create YAML plus example sidecars.
--force              Overwrite generated files except an existing DEPLOY.md.
```

When called without parameters, it prints usage and performs no writes.

## Directory behavior

The current directory controls where generated files are placed:

- if the current directory is named `ansirover`, write directly there;
- if the current directory is named `ansirover.<environment>`, write directly there;
- if the current directory already matches `<app-name>`, write directly there;
- otherwise create a new `<app-name>/` directory below the current directory.

The default environment directory is `ansirover/`. Named environments use arbitrary suffixes such as:

```text
ansirover.production/
ansirover.staging/
ansirover.local/
```

AnsiRover does not assign special meaning to those names.

## Default output

```text
<app-name>.yaml
<app-name>.env-vars.env
<app-name>.custom-nginx-config.conf
<app-name>.pre-deploy-function.js
<app-name>.service-update-override.yaml
README.md
DEPLOY.md
```

The generator intentionally does **not** create `compose.yaml`. The deployment workflow must inspect the application and create or repair the canonical one-service Compose build definition separately.

## Minimal output

```bash
ansirover-template-app-create <app-name> --minimal
```

```text
<app-name>.yaml
README.md
DEPLOY.md
```

## README.md responsibility

`README.md` is intentionally short and human-oriented so the environment directory renders cleanly on GitHub. It explains the directory purpose and contains a table describing the responsibilities of `compose.yaml`, `DEPLOY.md`, the app inventory, and optional sidecars.

## DEPLOY.md responsibility

`DEPLOY.md` is deployment intent and architectural context for humans and agents. The generated template explains:

- one AnsiRover deployment converges to one image/service;
- source folders, packages, and workspaces are not automatically deployment boundaries;
- frontend and backend may be one deployment when the production build packages the frontend into the backend runtime;
- the Docker build context may differ from the Dockerfile directory;
- external services are prerequisites owned by provider-specific workflows;
- Git superprojects with submodules are a clean option for independent deployment units, not a mandatory architecture;
- Docker-in-Docker/container aggregation is possible but should not be selected automatically and is generally unsuitable for production.

If `DEPLOY.md` already exists, the generator preserves it even with `--force`. This makes an existing file user-owned.

If the generator creates `DEPLOY.md` during a new bootstrap, an agent should inspect the repository and adapt that new template to the actual deployment model before bootstrap is considered complete.

## Canonical deployment definition

When `compose.yaml` exists, it is the canonical build/publication definition. `DEPLOY.md` complements it with rationale, external prerequisites, environment intent, and project-specific constraints; it does not replace Compose.

## Format summary

The main runtime inventory is `<app-name>.yaml`.

The example template includes the optional `ansirover` policy mapping with the current runtime fields. The minimal template omits it. Review explicit runtime policy rather than materializing defaults without a reason.

Optional sidecars use the same prefix:

```text
<app-name>.env-vars.env
<app-name>.custom-nginx-config.conf
<app-name>.pre-deploy-function.js
<app-name>.service-update-override.yaml
```

Named volumes use `volumeName + containerPath` and are handled by `--with-volumes` during archive restore scenarios.

Bind mounts use `hostPath + containerPath` and must be handled separately on the destination host.
