# ansirover-logs-print

Print CapRover app logs from a remote Docker Swarm host over SSH, resolving the SSH host from the CapRover CLI config machine name.

## Usage

```bash
ansirover-logs-print <machine-name> <app-name> [-f]
```

## Examples

```bash
ansirover-logs-print srv1 example-app
ansirover-logs-print srv1 example-app -f
ansirover-logs-print srv1 example-app --follow
```

## Machine name resolution

The `<machine-name>` is looked up in the CapRover CLI config at:

```text
~/.config/configstore/caprover.json
```

The matching machine's `baseUrl` provides the SSH host, and a leading `captain.` subdomain is stripped. The SSH user is always `root`.

```text
srv1 -> https://captain.srv1.example.com -> root@srv1.example.com
```

## App normalization

The `<app-name>` is normalized to the Docker Swarm service name exactly like `caprover-logs-print`:

```text
example-app -> srv-captain--example-app
srv-captain--example-app -> srv-captain--example-app
captain--example-app -> srv-captain--example-app
```

## Behavior

Without `-f`, the command prints the service task list and the last 200 log lines. With `-f`, it prints the service task list and then follows the log output live from the last 100 lines.

The remote host must allow SSH as `root` and must have Docker Swarm access.

## Related

- `caprover-logs-print` prints the same logs but takes the SSH host directly instead of a CapRover CLI machine name.
