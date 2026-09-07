# ansirover-template-app-create

Create a local AnsiRover app template.

## Usage

```bash
ansirover-template-app-create <app-name> [options]
```

Options:

```text
--image <image>      Docker image for deployedImageName. Default: nginx:alpine
--port <port>        containerHttpPort. Default: 80
--minimal            Create only minimal YAML + README and suppress post-create guide.
--with-examples      Default behavior. Create YAML plus example sidecars.
--force              Overwrite existing files.
```

When called without parameters, it prints usage plus a short guide for AI/tools.

## Directory behavior

The current directory controls where the generated files are placed:

- if the current directory is named `ansirover`, create the files directly in that directory;
- if the current directory name already matches `<app-name>`, create the files directly in that directory;
- otherwise create a new `<app-name>/` directory under the current directory.

For the canonical application layout, create or enter the top-level `ansirover/` directory before running the command:

```text
<application-git-root>/
└── ansirover/
    ├── <app-name>.yaml
    ├── <app-name>.env-vars.env
    ├── <app-name>.custom-nginx-config.conf
    ├── <app-name>.pre-deploy-function.js
    ├── <app-name>.service-update-override.yaml
    └── README.md
```

The command prints `APP_TEMPLATE_DIR` and `APP_TEMPLATE_YAML` so callers do not need to guess the generated path.

## Default output

The default template contains:

```text
<app-name>.yaml
<app-name>.env-vars.env
<app-name>.custom-nginx-config.conf
<app-name>.pre-deploy-function.js
<app-name>.service-update-override.yaml
README.md
```

## Minimal output

```bash
ansirover-template-app-create <app-name> --minimal
```

```text
<app-name>.yaml
README.md
```

## Restore/deploy

From the generated template directory:

```bash
ansirover-restore-upload <server-name> <app-name> . --with-image --with-volumes --wait-deploy
```

## Download/refresh backup

From the generated template directory:

```bash
ansirover-download-backup <server-name> <app-name> . --with-image --with-volumes --force
```

## Format summary

The main file is `<app-name>.yaml`.

The example template includes the optional `ansirover` policy mapping with all current runtime fields. The minimal template omits it to demonstrate that legacy inventories remain valid. Review the example `restartDelay` before deploying a critical app.

Optional sidecars use the same prefix:

```text
<app-name>.env-vars.env
<app-name>.custom-nginx-config.conf
<app-name>.pre-deploy-function.js
<app-name>.service-update-override.yaml
```

Named volumes use `volumeName + containerPath` and are handled by `--with-volumes`.

Bind mounts use `hostPath + containerPath` and must be backed up separately.
