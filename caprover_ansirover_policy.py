#!/usr/bin/env python3

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping


SCHEMA_VERSION = 1
BEGIN_MARKER = "# ANSIROVER-BEGIN #"
END_MARKER = "# ANSIROVER-END #"

POLICY_KEYS = (
    "restartDelay",
    "swarmStabilityPeriod",
    "gracePeriod",
    "postgresReconnectThreshold",
    "postgresPollInterval",
)

DEFAULT_POLICY = {
    "restartDelay": "1h",
    "swarmStabilityPeriod": "15s",
    "gracePeriod": "3m",
    "postgresReconnectThreshold": 3,
    "postgresPollInterval": "1s",
}

_DURATION_PATTERN = re.compile(r"^(?:\d+(?:ms|us|µs|ns|s|m|h))+$")
_DURATION_FACTORS = {
    "ns": 0.000000001,
    "us": 0.000001,
    "µs": 0.000001,
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}


class AnsiRoverPolicyError(ValueError):
    pass


def _validate_duration(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise AnsiRoverPolicyError(f"ansirover.{name} must be a duration string")

    normalized = value.strip()

    if not normalized or not _DURATION_PATTERN.fullmatch(normalized):
        raise AnsiRoverPolicyError(
            f"ansirover.{name} must be a positive duration such as 1s, 3m, 1h, or 1h30m"
        )

    components = re.findall(r"(\d+)(ms|us|µs|ns|s|m|h)", normalized)

    if not components or all(int(amount) == 0 for amount, _unit in components):
        raise AnsiRoverPolicyError(f"ansirover.{name} must be greater than zero")

    return normalized


def duration_to_seconds(name: str, value: object) -> float:
    normalized = _validate_duration(name, value)
    total_seconds = 0.0

    for amount, unit in re.findall(r"(\d+)(ms|us|µs|ns|s|m|h)", normalized):
        total_seconds += int(amount) * _DURATION_FACTORS[unit]

    return total_seconds


def validate_explicit_policy(policy: object | None) -> dict[str, object]:
    if policy is None:
        return {}

    if not isinstance(policy, Mapping):
        raise AnsiRoverPolicyError("ansirover must be an object")

    unknown_keys = sorted(set(policy) - set(POLICY_KEYS))

    if unknown_keys:
        raise AnsiRoverPolicyError(
            "unknown ansirover field(s): " + ", ".join(str(key) for key in unknown_keys)
        )

    result: dict[str, object] = {}

    for key in POLICY_KEYS:
        if key not in policy:
            continue

        value = policy[key]

        if key == "postgresReconnectThreshold":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise AnsiRoverPolicyError(
                    "ansirover.postgresReconnectThreshold must be a positive integer"
                )
            result[key] = value
            continue

        result[key] = _validate_duration(key, value)

    return result


def get_effective_policy(policy: object | None) -> dict[str, object]:
    explicit_policy = validate_explicit_policy(policy)
    result = dict(DEFAULT_POLICY)
    result.update(explicit_policy)
    return result


def encode_metadata(policy: object | None) -> str | None:
    explicit_policy = validate_explicit_policy(policy)

    if not explicit_policy:
        return None

    document = {
        "schemaVersion": SCHEMA_VERSION,
        "ansirover": explicit_policy,
    }
    encoded_json = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.b64encode(encoded_json).decode("ascii")


def decode_metadata(payload: str) -> dict[str, object]:
    if not isinstance(payload, str) or not payload.strip():
        raise AnsiRoverPolicyError("AnsiRover metadata payload is empty")

    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise AnsiRoverPolicyError("AnsiRover metadata payload is not valid Base64 UTF-8") from error

    try:
        document = json.loads(decoded)
    except json.JSONDecodeError as error:
        raise AnsiRoverPolicyError("AnsiRover metadata payload is not valid JSON") from error

    if not isinstance(document, dict):
        raise AnsiRoverPolicyError("AnsiRover metadata document must be an object")

    schema_version = document.get("schemaVersion")

    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise AnsiRoverPolicyError(
            "unsupported AnsiRover metadata schemaVersion: " + str(schema_version)
        )

    unknown_document_keys = sorted(set(document) - {"schemaVersion", "ansirover"})

    if unknown_document_keys:
        raise AnsiRoverPolicyError(
            "unknown AnsiRover metadata field(s): " + ", ".join(unknown_document_keys)
        )

    if "ansirover" not in document:
        raise AnsiRoverPolicyError("AnsiRover metadata document is missing ansirover")

    metadata_policy = document["ansirover"]

    if not isinstance(metadata_policy, Mapping):
        raise AnsiRoverPolicyError("AnsiRover metadata ansirover field must be an object")

    return validate_explicit_policy(metadata_policy)


def split_description(description: object | None) -> tuple[str, dict[str, object]]:
    text = "" if description is None else str(description)
    begin_count = text.count(BEGIN_MARKER)
    end_count = text.count(END_MARKER)

    if begin_count == 0 and end_count == 0:
        return text, {}

    if begin_count != 1 or end_count != 1:
        raise AnsiRoverPolicyError(
            "CapRover description must contain exactly one complete AnsiRover metadata block"
        )

    begin_index = text.index(BEGIN_MARKER)
    end_index = text.index(END_MARKER)

    if end_index < begin_index:
        raise AnsiRoverPolicyError("AnsiRover metadata end marker appears before begin marker")

    payload_start = begin_index + len(BEGIN_MARKER)
    payload_text = text[payload_start:end_index].strip()
    payload_lines = [line.strip() for line in payload_text.splitlines() if line.strip()]

    if len(payload_lines) != 1:
        raise AnsiRoverPolicyError("AnsiRover metadata block must contain exactly one Base64 line")

    explicit_policy = decode_metadata(payload_lines[0])
    human_description = (text[:begin_index] + text[end_index + len(END_MARKER):]).strip()
    return human_description, explicit_policy


def merge_description(description: object | None, policy: object | None) -> str:
    human_description, _old_policy = split_description(description)
    payload = encode_metadata(policy)

    if payload is None:
        return human_description

    metadata_block = "\n".join((BEGIN_MARKER, payload, END_MARKER))

    if human_description:
        return human_description.rstrip() + "\n\n" + metadata_block

    return metadata_block
