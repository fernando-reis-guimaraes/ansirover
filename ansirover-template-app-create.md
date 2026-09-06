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

## Default output

```text
<app-name>/
├── <app-name>.yaml
├── <app-name>.env-vars.env
├── <app-name>.custom-nginx-config.conf
├── <app-name>.pre-deploy-function.js
├── <app-name>.service-update-override.yaml
└── README.md
```

## Minimal output

```bash
ansirover-template-app-create <app-name> --minimal
```

```text
<app-name>/
├── <app-name>.yaml
└── README.md
```

## Restore/deploy

```bash
ansirover-restore-upload <server-name> <app-name> <path-to-app-folder> --with-image --with-volumes --wait-deploy
```

## Download/refresh backup

```bash
ansirover-download-backup <server-name> <app-name> <path-to-app-folder> --with-image --with-volumes --force
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
