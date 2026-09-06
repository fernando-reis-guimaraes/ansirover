#!/usr/bin/env python3

import argparse
import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


SCRIPT_DIR = Path(__file__).resolve().parent
CORE_PATH = SCRIPT_DIR / "ansirover-restore-upload.py"
BYPASS_SUFFIX = "_ALLOW_MISSING_APPLICATION_NAME"


def load_core_module():
    specification = importlib.util.spec_from_file_location(
        "ansirover_restore_upload_core_for_migrate",
        CORE_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


core = load_core_module()


def is_true(value):
    return str(value).strip().lower() == "true"


def parse_env_lines(lines):
    env_map = {}

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()

        if key:
            env_map[key] = value.rstrip("\n")

    return env_map


def is_postgres_uri(value):
    lower_value = value.strip().lower()
    return lower_value.startswith("postgres://") or lower_value.startswith("postgresql://")


def get_application_name_values(query):
    values = []

    for component in query.split("&") if query else []:
        key, separator, value = component.partition("=")

        if unquote(key) == "application_name":
            values.append(unquote(value) if separator else "")

    return values


def normalize_application_name(uri, app_name):
    parsed = urlsplit(uri)
    components = []

    for component in parsed.query.split("&") if parsed.query else []:
        key, _separator, _value = component.partition("=")

        if unquote(key) == "application_name":
            continue

        if component:
            components.append(component)

    components.append("application_name=" + quote(app_name, safe=""))
    query = "&".join(components)

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            query,
            parsed.fragment,
        )
    )


def migrate_env_file(env_path, app_name, check_only=False):
    original_lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    env_map = parse_env_lines(original_lines)
    changed_keys = []
    bypassed_keys = []
    output_lines = []

    for line in original_lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in line:
            output_lines.append(line)
            continue

        key_part, value_part = line.split("=", 1)
        key = key_part.strip()
        newline = "\n" if line.endswith("\n") else ""
        value = value_part.rstrip("\n")

        if not is_postgres_uri(value):
            output_lines.append(line)
            continue

        application_names = get_application_name_values(urlsplit(value).query)
        bypass_key = key + BYPASS_SUFFIX
        bypass_enabled = is_true(env_map.get(bypass_key, ""))

        if not application_names and bypass_enabled:
            bypassed_keys.append(key)
            output_lines.append(line)
            continue

        if application_names == [app_name]:
            output_lines.append(line)
            continue

        normalized_value = normalize_application_name(value, app_name)
        output_lines.append(key_part + "=" + normalized_value + newline)
        changed_keys.append(key)

    if changed_keys and not check_only:
        env_path.write_text("".join(output_lines), encoding="utf-8")

    return changed_keys, bypassed_keys


def find_app_dirs(root_path):
    root_path = root_path.resolve()
    app_dirs = []

    for yaml_path in root_path.rglob("*.yaml"):
        app_dir = yaml_path.parent
        app_name = app_dir.name

        if yaml_path.name != app_name + ".yaml":
            continue

        env_path = app_dir / (app_name + ".env-vars.env")

        if not env_path.exists():
            continue

        app_dirs.append((app_name, app_dir, env_path))

    return sorted(app_dirs, key=lambda item: str(item[1]).lower())


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(
        description="Add or normalize PostgreSQL application_name values in AnsiRover inventory env files."
    )
    parser.add_argument("root", nargs="?", default=".", help="inventory root to scan recursively; defaults to current directory")
    parser.add_argument("--check", action="store_true", help="report required changes without modifying files and exit non-zero when drift exists")
    return parser.parse_args(arguments)


def main(arguments=None):
    args = parse_args(arguments)
    root_path = Path(args.root).expanduser()

    if not root_path.exists() or not root_path.is_dir():
        print("ERROR: inventory root is not a directory: " + str(root_path), file=sys.stderr)
        return 2

    files_scanned = 0
    files_changed = 0
    urls_changed = 0
    bypassed = 0

    for app_name, app_dir, env_path in find_app_dirs(root_path):
        ansirover = core.load_ansirover_app(app_dir, app_name)
        canonical_app_name = str(ansirover["appName"])
        changed_keys, bypassed_keys = migrate_env_file(
            env_path,
            canonical_app_name,
            check_only=args.check,
        )
        files_scanned += 1

        if changed_keys:
            files_changed += 1
            urls_changed += len(changed_keys)

            for key in changed_keys:
                action = "NEEDS_UPDATE" if args.check else "UPDATED"
                print(f"{action}: {env_path} key={key} application_name={canonical_app_name}")

        for key in bypassed_keys:
            bypassed += 1
            print(f"BYPASS: {env_path} key={key}")

    print(
        "SUMMARY: files_scanned="
        + str(files_scanned)
        + " files_changed="
        + str(files_changed)
        + " urls_changed="
        + str(urls_changed)
        + " bypassed="
        + str(bypassed)
    )

    if args.check and urls_changed:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
