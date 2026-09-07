#!/usr/bin/env python3

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path.home() / ".config" / "configstore" / "caprover.json"

SSH_OPTIONS = [
    "-n",
    "-o",
    "ConnectTimeout=30",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=4",
]

LOG_TAIL_LINES = 200
LOG_TAIL_LINES_FOLLOW = 100


def log_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail(message, exit_code=2):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def info(message):
    print(f"[{log_timestamp()}] {message}", file=sys.stderr, flush=True)


def validate_simple_name(label, value):
    if not value:
        fail(f"{label} must not be empty")

    if "\x00" in value:
        fail(f"{label} must not contain NUL byte: {value!r}")

    if "/" in value:
        fail(f"{label} must not contain slash: {value}")

    if value in {".", ".."}:
        fail(f"{label} must not be . or ..")

    if any(character.isspace() for character in value):
        fail(f"{label} must not contain whitespace: {value}")


def validate_caprover_machine_name(machine_name):
    validate_simple_name("machine name", machine_name)

    if "@" in machine_name:
        fail(
            "CapRover machine name must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )


def load_caprover_config():
    if not CONFIG_PATH.exists():
        fail(f"CapRover CLI config not found: {CONFIG_PATH}")

    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"invalid CapRover CLI config JSON: {CONFIG_PATH}: {error}")


def find_captain_url_by_machine(config, machine_name):
    cap_machines = config.get("CapMachines", [])

    if not isinstance(cap_machines, list):
        fail("CapRover CLI config does not contain CapMachines list")

    for machine in cap_machines:
        if not isinstance(machine, dict):
            continue

        if machine.get("name") != machine_name:
            continue

        base_url = str(machine.get("baseUrl", ""))

        if not base_url:
            fail(f"CapRover machine has empty baseUrl: {machine_name}")

        return base_url

    fail(f"CapRover machine not found in config: {machine_name}")


def hostname_from_captain_url(captain_url):
    host = urlparse(captain_url).hostname

    if not host:
        host = re.sub(r"^https?://", "", captain_url)
        host = host.split("/", 1)[0]
        host = host.split(":", 1)[0]

    if host.startswith("captain."):
        host = host[len("captain."):]

    if not host:
        fail(f"could not derive SSH host from CapRover URL: {captain_url}")

    return host


def derive_ssh_target_from_captain_url(captain_url):
    return f"root@{hostname_from_captain_url(captain_url)}"


def resolve_ssh_target(machine_name):
    captain_url = find_captain_url_by_machine(load_caprover_config(), machine_name)
    info(f"[config] CapRover URL for {machine_name}: {captain_url}")
    return derive_ssh_target_from_captain_url(captain_url)


def normalize_service_name(app_name):
    if app_name.startswith("srv-captain--"):
        return app_name

    if app_name.startswith("captain--"):
        return "srv-" + app_name

    return "srv-captain--" + app_name


def build_remote_script(service_name, follow):
    follow_value = "yes" if follow else "no"
    docker_name_format = "{{.Name}}"

    return f"""set -euo pipefail

service_name={shlex.quote(service_name)}
follow={shlex.quote(follow_value)}

echo "== checking docker =="
command -v docker >/dev/null

echo "== checking service =="
if ! docker service inspect "$service_name" >/dev/null 2>&1; then
  echo "ERROR: service not found: $service_name" >&2
  echo >&2
  echo "== available CapRover-like services ==" >&2
  docker service ls --format '{docker_name_format}' | grep -E '^srv-captain--' >&2 || true
  exit 1
fi

echo "OK: service found: $service_name"
echo

echo "== service tasks =="
docker service ps --no-trunc "$service_name"
echo

echo "== service logs =="
if [ "$follow" = "yes" ]; then
  exec docker service logs --tail {LOG_TAIL_LINES_FOLLOW} --timestamps -f "$service_name"
fi

docker service logs --tail {LOG_TAIL_LINES} --timestamps "$service_name"
"""


def run_remote(ssh_target, remote_script):
    if shutil.which("ssh") is None:
        fail("ssh executable not found in PATH")

    command = ["ssh", *SSH_OPTIONS, ssh_target, "bash -lc " + shlex.quote(remote_script)]

    try:
        completed_process = subprocess.run(command, check=False)
    except KeyboardInterrupt:
        raise SystemExit(130)

    raise SystemExit(completed_process.returncode)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="ansirover-logs-print",
        description=(
            "Print CapRover app logs from a remote Docker Swarm host over SSH. "
            "The SSH target is derived from the CapRover CLI config machine name."
        )
    )

    parser.add_argument("machine_name", help="CapRover CLI server/machine name, e.g. server-a; @ is not allowed")
    parser.add_argument("app_name", help="CapRover app name")
    parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="follow new log output instead of printing the recent tail once",
    )

    return parser.parse_args(argv)


def main():
    args = parse_args(sys.argv[1:])

    validate_caprover_machine_name(args.machine_name)
    validate_simple_name("app name", args.app_name)

    service_name = normalize_service_name(args.app_name)
    ssh_target = resolve_ssh_target(args.machine_name)

    info("[plan] Action: print CapRover app logs via SSH")
    info("[plan] Machine: " + args.machine_name)
    info("[plan] SSH target: " + ssh_target)
    info("[plan] CapRover app: " + args.app_name)
    info("[plan] Docker service: " + service_name)
    info("[plan] Follow: " + ("yes" if args.follow else "no"))

    run_remote(ssh_target, build_remote_script(service_name, args.follow))


if __name__ == "__main__":
    main()
