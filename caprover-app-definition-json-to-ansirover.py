#!/usr/bin/env python3

import argparse
import json
import re
import sys
from pathlib import Path

from caprover_ansirover_policy import (
    AnsiRoverPolicyError,
    POLICY_KEYS,
    split_description_metadata,
)

ANSI_ROVER_FIELD_ORDER = [
    "appName",
    "deployedImageName",
    "description",
    "ansirover",
    "hasPersistentData",
    "instanceCount",
    "containerHttpPort",
    "notExposeAsWebApp",
    "websocketSupport",
    "customDomain",
    "tags",
    "httpAuth",
    "volumes",
    "ports",
]

SIDECAR_FIELDS = [
    "envVars",
    "preDeployFunction",
    "serviceUpdateOverride",
    "customNginxConfig",
]


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message):
    print(message, file=sys.stderr, flush=True)


def result(message):
    print(message, flush=True)


def read_json_file(path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"input JSON file not found: {path}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        fail(f"invalid JSON file: {path}: {error}")

    if not isinstance(data, dict):
        fail("input JSON must be one app definition object")

    return data


def validate_app_name(app_name):
    if not isinstance(app_name, str):
        fail("appName must be a string")

    if not app_name.strip():
        fail("appName is required")

    if "/" in app_name:
        fail(f"appName must not contain slash: {app_name}")

    if app_name in {".", ".."}:
        fail(f"appName must not be . or ..: {app_name}")

    if any(character.isspace() for character in app_name):
        fail(f"appName must not contain whitespace: {app_name}")

    return app_name


def to_boolean(value, field_name, default):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"true", "yes", "1"}:
            return True

        if normalized in {"false", "no", "0"}:
            return False

    fail(f"{field_name} must be boolean-compatible")


def to_integer(value, field_name, default):
    if value is None:
        return default

    if isinstance(value, bool):
        fail(f"{field_name} must be an integer, not boolean")

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        normalized = value.strip()

        if re.fullmatch(r"-?[0-9]+", normalized):
            return int(normalized)

    fail(f"{field_name} must be integer-compatible")


def to_string(value, field_name, default):
    if value is None:
        return default

    if isinstance(value, str):
        return value

    if isinstance(value, int) or isinstance(value, bool) or isinstance(value, float):
        return str(value)

    fail(f"{field_name} must be string-compatible")


def get_deployed_image_name(app_definition):
    deployed_version = app_definition.get("deployedVersion")
    versions = app_definition.get("versions")

    if deployed_version is None or deployed_version == "":
        info("[warn] deployedVersion is empty; deployedImageName will be empty")
        return ""

    if not isinstance(versions, list):
        info("[warn] versions is not a list; deployedImageName will be empty")
        return ""

    for version_item in versions:
        if not isinstance(version_item, dict):
            continue

        version_value = version_item.get("version")

        if str(version_value) != str(deployed_version):
            continue

        deployed_image_name = version_item.get("deployedImageName")

        if deployed_image_name is None or deployed_image_name == "":
            fail(
                "matched deployedVersion but version item has no deployedImageName: "
                + str(deployed_version)
            )

        if not isinstance(deployed_image_name, str):
            fail("deployedImageName inside versions must be a string")

        return deployed_image_name

    fail(
        "could not find deployedImageName: no item in versions has version == deployedVersion "
        + str(deployed_version)
    )


def normalize_volumes(value):
    if value is None:
        return []

    if not isinstance(value, list):
        fail("volumes must be a list")

    normalized_volumes = []

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"volumes[{index}] must be an object")

        normalized_item = {}

        for key in ["containerPath", "volumeName", "hostPath"]:
            if key not in item:
                continue

            field_value = item.get(key)

            if field_value is None or field_value == "":
                continue

            normalized_item[key] = to_string(field_value, f"volumes[{index}].{key}", "")

        if "containerPath" not in normalized_item:
            fail(f"volumes[{index}] is missing containerPath")

        if "volumeName" not in normalized_item and "hostPath" not in normalized_item:
            fail(f"volumes[{index}] must have volumeName or hostPath")

        if "volumeName" in normalized_item and "hostPath" in normalized_item:
            fail(f"volumes[{index}] must not have both volumeName and hostPath")

        normalized_volumes.append(normalized_item)

    return normalized_volumes


def normalize_ports(value):
    if value is None:
        return []

    if not isinstance(value, list):
        fail("ports must be a list")

    normalized_ports = []

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"ports[{index}] must be an object")

        normalized_item = {}

        if "hostPort" not in item:
            fail(f"ports[{index}] is missing hostPort")

        if "containerPort" not in item:
            fail(f"ports[{index}] is missing containerPort")

        normalized_item["hostPort"] = to_integer(item.get("hostPort"), f"ports[{index}].hostPort", 0)
        normalized_item["containerPort"] = to_integer(item.get("containerPort"), f"ports[{index}].containerPort", 0)

        normalized_ports.append(normalized_item)

    return normalized_ports


def normalize_tags(value):
    if value is None:
        return []

    if not isinstance(value, list):
        fail("tags must be a list")

    normalized_tags = []

    for index, item in enumerate(value):
        if isinstance(item, str):
            tag_name = item
        elif isinstance(item, dict):
            tag_name = item.get("tagName")
        else:
            fail(f"tags[{index}] must be an object or string")

        if tag_name is None:
            continue

        tag_name = str(tag_name).strip()

        if not tag_name:
            continue

        normalized_tags.append({"tagName": tag_name})

    return normalized_tags


def normalize_http_auth(value):
    if value is None:
        return {}

    if value == "":
        return {}

    if not isinstance(value, dict):
        fail("httpAuth must be an object")

    normalized_http_auth = {}

    for key in ["user", "passwordHashed"]:
        if key not in value:
            continue

        item_value = value.get(key)

        if item_value is None:
            continue

        item_value = str(item_value)

        if item_value == "":
            continue

        normalized_http_auth[key] = item_value

    return normalized_http_auth


def normalize_custom_domains(value):
    if value is None:
        return []

    if not isinstance(value, list):
        fail("customDomain must be a list")

    normalized_domains = []
    seen_domains = set()

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"customDomain[{index}] must be an object")

        public_domain = item.get("publicDomain")

        if not isinstance(public_domain, str) or not public_domain.strip():
            fail(f"customDomain[{index}].publicDomain must be a non-empty string")

        public_domain = public_domain.strip()
        normalized_domain = public_domain.lower()

        if normalized_domain in seen_domains:
            fail(f"customDomain contains duplicate publicDomain: {public_domain}")

        seen_domains.add(normalized_domain)
        normalized_domains.append(
            {
                "publicDomain": public_domain,
                "hasSsl": to_boolean(
                    item.get("hasSsl"),
                    f"customDomain[{index}].hasSsl",
                    False,
                ),
            }
        )

    return normalized_domains


def build_ansirover_object(app_definition):
    app_name = validate_app_name(app_definition.get("appName"))

    try:
        (
            human_description,
            explicit_policy,
            _env_var_templates,
        ) = split_description_metadata(app_definition.get("description"))
    except AnsiRoverPolicyError as error:
        fail("invalid AnsiRover metadata in CapRover description: " + str(error))

    ansirover_object = {
        "appName": app_name,
        "deployedImageName": get_deployed_image_name(app_definition),
        "description": human_description,
        "hasPersistentData": to_boolean(app_definition.get("hasPersistentData"), "hasPersistentData", False),
        "instanceCount": to_integer(app_definition.get("instanceCount"), "instanceCount", 1),
        "containerHttpPort": to_integer(app_definition.get("containerHttpPort"), "containerHttpPort", 80),
        "notExposeAsWebApp": to_boolean(app_definition.get("notExposeAsWebApp"), "notExposeAsWebApp", False),
        "websocketSupport": to_boolean(app_definition.get("websocketSupport"), "websocketSupport", False),
        "customDomain": normalize_custom_domains(app_definition.get("customDomain")),
        "tags": normalize_tags(app_definition.get("tags")),
        "httpAuth": normalize_http_auth(app_definition.get("httpAuth")),
        "volumes": normalize_volumes(app_definition.get("volumes")),
        "ports": normalize_ports(app_definition.get("ports")),
    }

    if explicit_policy:
        ansirover_object["ansirover"] = explicit_policy

    return ansirover_object


def yaml_scalar(value):
    if isinstance(value, bool):
        if value:
            return "true"

        return "false"

    if isinstance(value, int):
        return str(value)

    if value is None:
        return "null"

    if not isinstance(value, str):
        fail(f"unsupported YAML scalar type: {type(value).__name__}")

    if value == "":
        return '""'

    if value.lower() in {"true", "false", "null", "yes", "no"}:
        return json.dumps(value, ensure_ascii=False)

    if re.fullmatch(r"[A-Za-z0-9_./:@+=,\-]+", value):
        return value

    return json.dumps(value, ensure_ascii=False)


def emit_list_of_objects(key, items, key_order):
    lines = []

    if not items:
        lines.append(f"{key}: []")
        return lines

    lines.append(f"{key}:")

    for item in items:
        first_key = True

        for item_key in key_order:
            if item_key not in item:
                continue

            if first_key:
                lines.append(f"  - {item_key}: {yaml_scalar(item[item_key])}")
                first_key = False
            else:
                lines.append(f"    {item_key}: {yaml_scalar(item[item_key])}")

    return lines


def emit_object(key, item, key_order):
    lines = []

    if not item:
        lines.append(f"{key}: {{}}")
        return lines

    lines.append(f"{key}:")

    for item_key in key_order:
        if item_key not in item:
            continue

        lines.append(f"  {item_key}: {yaml_scalar(item[item_key])}")

    return lines


def emit_ansirover_yaml(ansirover_object, app_name):
    lines = []

    for field_name in ANSI_ROVER_FIELD_ORDER:
        if field_name == "ansirover":
            if field_name not in ansirover_object:
                continue

            lines.extend(
                emit_object(
                    "ansirover",
                    ansirover_object["ansirover"],
                    POLICY_KEYS,
                )
            )
            continue

        if field_name == "tags":
            lines.extend(
                emit_list_of_objects(
                    "tags",
                    ansirover_object["tags"],
                    ["tagName"],
                )
            )
            continue

        if field_name == "customDomain":
            lines.extend(
                emit_list_of_objects(
                    "customDomain",
                    ansirover_object["customDomain"],
                    ["publicDomain", "hasSsl"],
                )
            )
            continue

        if field_name == "httpAuth":
            lines.extend(
                emit_object(
                    "httpAuth",
                    ansirover_object["httpAuth"],
                    ["user", "passwordHashed"],
                )
            )
            continue

        if field_name == "volumes":
            lines.extend(
                emit_list_of_objects(
                    "volumes",
                    ansirover_object["volumes"],
                    ["containerPath", "volumeName", "hostPath"],
                )
            )
            continue

        if field_name == "ports":
            lines.extend(
                emit_list_of_objects(
                    "ports",
                    ansirover_object["ports"],
                    ["hostPort", "containerPort"],
                )
            )
            continue

        lines.append(f"{field_name}: {yaml_scalar(ansirover_object[field_name])}")

    return "\n".join(lines) + "\n"


def has_meaningful_content(value):
    if value is None:
        return False

    if isinstance(value, str):
        return value.strip() != ""

    if isinstance(value, list):
        return len(value) > 0

    if isinstance(value, dict):
        return len(value) > 0

    return True


def normalize_text_sidecar(value, field_name):
    if value is None:
        return ""

    if isinstance(value, str):
        if value == "":
            return ""

        return value.rstrip("\n") + "\n"

    if isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, indent=2, ensure_ascii=False) + "\n"

    if isinstance(value, int) or isinstance(value, bool) or isinstance(value, float):
        return str(value) + "\n"

    fail(f"{field_name} has unsupported sidecar type: {type(value).__name__}")


def normalize_env_vars_sidecar(value, env_var_templates=None):
    templates = dict(env_var_templates or {})

    if value is None:
        if templates:
            fail(
                "AnsiRover envVarTemplates references environment variables "
                "but CapRover envVars is empty"
            )
        return ""

    seen_keys = set()
    lines = []

    def add_env_var(key, item_value):
        normalized_key = str(key)

        if not normalized_key:
            fail("envVars contains an empty key")

        normalized_value = "" if item_value is None else str(item_value)

        if normalized_key in templates:
            normalized_value = templates[normalized_key]

        lines.append(normalized_key + "=" + normalized_value)
        seen_keys.add(normalized_key)

    if isinstance(value, str):
        if value == "":
            if templates:
                fail(
                    "AnsiRover envVarTemplates references environment variables "
                    "but CapRover envVars is empty"
                )
            return ""

        if not templates:
            return value.rstrip("\n") + "\n"

        for index, raw_line in enumerate(value.splitlines(), start=1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                lines.append(raw_line)
                continue

            if line.startswith("export "):
                line = line[len("export "):].strip()

            if "=" not in line:
                fail(
                    f"envVars line {index} cannot be reconciled with AnsiRover envVarTemplates"
                )

            key, item_value = line.split("=", 1)
            add_env_var(key.strip(), item_value)

    elif isinstance(value, dict):
        for key, item_value in value.items():
            add_env_var(key, item_value)

    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                line = item.strip()

                if not line:
                    continue

                if line.startswith("export "):
                    line = line[len("export "):].strip()

                if "=" not in line:
                    fail(f"envVars[{index}] string must be KEY=VALUE")

                key, item_value = line.split("=", 1)
                add_env_var(key.strip(), item_value)
                continue

            if not isinstance(item, dict):
                fail(f"envVars[{index}] must be object or string")

            key = item.get("key")

            if key is None:
                key = item.get("name")

            if key is None or str(key) == "":
                fail(f"envVars[{index}] is missing key")

            add_env_var(key, item.get("value"))

    else:
        fail(f"envVars has unsupported sidecar type: {type(value).__name__}")

    missing_template_keys = sorted(set(templates) - seen_keys)

    if missing_template_keys:
        fail(
            "AnsiRover envVarTemplates references keys missing from live CapRover envVars: "
            + ", ".join(missing_template_keys)
        )

    if not lines:
        return ""

    return "\n".join(lines) + "\n"

def build_sidecar_texts(app_definition):
    sidecar_texts = {}

    try:
        _human_description, _explicit_policy, env_var_templates = (
            split_description_metadata(app_definition.get("description"))
        )
    except AnsiRoverPolicyError as error:
        fail("invalid AnsiRover metadata in CapRover description: " + str(error))

    env_vars_text = normalize_env_vars_sidecar(
        app_definition.get("envVars"),
        env_var_templates,
    )

    if env_vars_text:
        sidecar_texts["env-vars.env"] = env_vars_text

    pre_deploy_function_text = normalize_text_sidecar(
        app_definition.get("preDeployFunction"),
        "preDeployFunction",
    )

    if pre_deploy_function_text:
        sidecar_texts["pre-deploy-function.js"] = pre_deploy_function_text

    service_update_override_text = normalize_text_sidecar(
        app_definition.get("serviceUpdateOverride"),
        "serviceUpdateOverride",
    )

    if service_update_override_text:
        sidecar_texts["service-update-override.yaml"] = service_update_override_text

    custom_nginx_config_text = normalize_text_sidecar(
        app_definition.get("customNginxConfig"),
        "customNginxConfig",
    )

    if custom_nginx_config_text:
        sidecar_texts["custom-nginx-config.conf"] = custom_nginx_config_text

    return sidecar_texts


def check_output_paths(yaml_path, sidecar_paths, force):
    existing_paths = []

    if yaml_path.exists():
        existing_paths.append(yaml_path)

    for sidecar_path in sidecar_paths:
        if sidecar_path.exists():
            existing_paths.append(sidecar_path)

    if existing_paths and not force:
        for existing_path in existing_paths:
            print(f"existing output: {existing_path}", file=sys.stderr)

        fail("output file already exists; use --force to overwrite/remove stale sidecars")


def write_outputs(yaml_path, yaml_text, app_name, sidecar_texts, force):
    yaml_path.parent.mkdir(parents=True, exist_ok=True)

    known_sidecar_paths = [
        yaml_path.parent / f"{app_name}.env-vars.env",
        yaml_path.parent / f"{app_name}.pre-deploy-function.js",
        yaml_path.parent / f"{app_name}.service-update-override.yaml",
        yaml_path.parent / f"{app_name}.custom-nginx-config.conf",
        yaml_path.parent / f"{app_name}.env-vars.txt",
        yaml_path.parent / f"{app_name}.pre-deploy-function.txt",
        yaml_path.parent / f"{app_name}.service-update-override.txt",
        yaml_path.parent / f"{app_name}.custom-nginx-config.txt",
    ]

    wanted_sidecar_paths = [
        yaml_path.parent / f"{app_name}.{sidecar_suffix}"
        for sidecar_suffix in sidecar_texts.keys()
    ]

    check_output_paths(yaml_path, wanted_sidecar_paths, force)

    yaml_path.write_text(yaml_text, encoding="utf-8")

    for sidecar_suffix, sidecar_text in sidecar_texts.items():
        sidecar_path = yaml_path.parent / f"{app_name}.{sidecar_suffix}"
        sidecar_path.write_text(sidecar_text, encoding="utf-8")
        result("SIDECAR_FILE=" + str(sidecar_path))

    if force:
        for known_sidecar_path in known_sidecar_paths:
            if known_sidecar_path in wanted_sidecar_paths:
                continue

            if known_sidecar_path.exists():
                known_sidecar_path.unlink()
                info("[cleanup] removed stale sidecar: " + str(known_sidecar_path))


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Convert one CapRover app definition JSON object to AnsiRover YAML. "
            "This is not a generic JSON-to-YAML converter."
        )
    )

    parser.add_argument("app_json_file", help="input app definition JSON file")
    parser.add_argument("app_yaml_file", help="output AnsiRover YAML file")
    parser.add_argument("--force", action="store_true", help="overwrite output YAML and sidecars")

    return parser.parse_args()


def main():
    args = parse_args()

    app_json_path = Path(args.app_json_file).expanduser().resolve()
    app_yaml_path = Path(args.app_yaml_file).expanduser().resolve()

    app_definition = read_json_file(app_json_path)
    ansirover_object = build_ansirover_object(app_definition)
    app_name = ansirover_object["appName"]

    if app_yaml_path.stem.lower() != app_name.lower():
        info(
            "[warn] output YAML basename does not match appName: "
            + app_yaml_path.name
            + " vs "
            + app_name
        )

    yaml_text = emit_ansirover_yaml(ansirover_object, app_name)
    sidecar_texts = build_sidecar_texts(app_definition)

    info("[plan] Action: app definition JSON to AnsiRover YAML")
    info("[plan] Input JSON: " + str(app_json_path))
    info("[plan] Output YAML: " + str(app_yaml_path))
    info("[plan] App: " + app_name)
    info("[plan] Sidecars to write: " + str(len(sidecar_texts)))

    write_outputs(
        yaml_path=app_yaml_path,
        yaml_text=yaml_text,
        app_name=app_name,
        sidecar_texts=sidecar_texts,
        force=args.force,
    )

    result("OUTPUT_FILE=" + str(app_yaml_path))
    result("APP_NAME=" + app_name)
    result("DEPLOYED_IMAGE_NAME=" + ansirover_object["deployedImageName"])
    result("SIDECAR_COUNT=" + str(len(sidecar_texts)))
    info("[done] Converted app definition JSON to AnsiRover YAML.")


if __name__ == "__main__":
    main()
