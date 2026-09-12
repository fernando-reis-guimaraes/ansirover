#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import unittest

from caprover_ansirover_policy import (
    AnsiRoverPolicyError,
    BEGIN_MARKER,
    END_MARKER,
    decode_metadata,
    duration_to_seconds,
    encode_metadata,
    get_effective_policy,
    merge_description,
    split_description,
    validate_explicit_policy,
)


class AnsiRoverPolicyTest(unittest.TestCase):
    def test_duration_conversion_uses_one_canonical_parser(self):
        self.assertEqual(duration_to_seconds("gracePeriod", "3m"), 180.0)
        self.assertEqual(duration_to_seconds("gracePeriod", "1h30m"), 5400.0)
        self.assertEqual(duration_to_seconds("postgresPollInterval", "500ms"), 0.5)
        self.assertEqual(duration_to_seconds("swarmStabilityPeriod", "15s"), 15.0)

    def test_effective_policy_uses_defaults_without_materializing_them(self):
        explicit = {"restartDelay": "30m"}

        self.assertEqual(validate_explicit_policy(explicit), explicit)
        self.assertEqual(
            get_effective_policy(explicit),
            {
                "restartDelay": "30m",
                "swarmStabilityPeriod": "15s",
                "gracePeriod": "3m",
                "postgresReconnectThreshold": 3,
                "postgresPollInterval": "1s",
            },
        )

    def test_swarm_stability_period_is_valid_explicit_policy(self):
        explicit = {"swarmStabilityPeriod": "30s"}

        self.assertEqual(validate_explicit_policy(explicit), explicit)
        self.assertEqual(get_effective_policy(explicit)["swarmStabilityPeriod"], "30s")

    def test_metadata_round_trip_preserves_only_explicit_values(self):
        explicit = {
            "restartDelay": "30m",
            "postgresReconnectThreshold": 5,
        }

        payload = encode_metadata(explicit)

        self.assertIsNotNone(payload)
        self.assertEqual(decode_metadata(payload), explicit)

    def test_metadata_is_compact_deterministic_json_in_canonical_base64(self):
        explicit = {
            "postgresPollInterval": "2s",
            "restartDelay": "1h",
        }

        payload = encode_metadata(explicit)
        decoded = base64.b64decode(payload).decode("utf-8")

        self.assertEqual(
            decoded,
            '{"schemaVersion":1,"ansirover":{"restartDelay":"1h","postgresPollInterval":"2s"}}',
        )
        self.assertEqual(json.loads(decoded)["schemaVersion"], 1)

    def test_description_round_trip_preserves_human_text(self):
        explicit = {
            "restartDelay": "1h",
            "gracePeriod": "3m",
        }
        description = merge_description("Human description.", explicit)

        self.assertIn(BEGIN_MARKER, description)
        self.assertIn(END_MARKER, description)
        human_description, decoded_policy = split_description(description)
        self.assertEqual(human_description, "Human description.")
        self.assertEqual(decoded_policy, explicit)

    def test_human_description_without_metadata_is_unchanged(self):
        self.assertEqual(split_description("Human description."), ("Human description.", {}))

    def test_empty_description_without_metadata_is_supported(self):
        self.assertEqual(split_description(""), ("", {}))

    def test_replacing_metadata_does_not_duplicate_markers(self):
        first = merge_description("Human description.", {"restartDelay": "1h"})
        second = merge_description(first, {"restartDelay": "30m"})

        self.assertEqual(second.count(BEGIN_MARKER), 1)
        self.assertEqual(second.count(END_MARKER), 1)
        self.assertEqual(split_description(second)[1], {"restartDelay": "30m"})

    def test_empty_policy_removes_metadata_block(self):
        description = merge_description("Human description.", {"restartDelay": "1h"})

        self.assertEqual(merge_description(description, {}), "Human description.")

    def test_duplicate_markers_are_rejected(self):
        payload = encode_metadata({"restartDelay": "1h"})
        description = "\n".join(
            (
                BEGIN_MARKER,
                payload,
                END_MARKER,
                BEGIN_MARKER,
                payload,
                END_MARKER,
            )
        )

        with self.assertRaises(AnsiRoverPolicyError):
            split_description(description)

    def test_corrupt_base64_is_rejected(self):
        with self.assertRaises(AnsiRoverPolicyError):
            decode_metadata("THIS-IS-NOT-BASE64!!!")

    def test_invalid_json_is_rejected(self):
        payload = base64.b64encode(b"not-json").decode("ascii")

        with self.assertRaises(AnsiRoverPolicyError):
            decode_metadata(payload)

    def test_unknown_schema_version_is_rejected(self):
        payload = base64.b64encode(
            b'{"schemaVersion":2,"ansirover":{"restartDelay":"1h"}}'
        ).decode("ascii")

        with self.assertRaises(AnsiRoverPolicyError):
            decode_metadata(payload)

    def test_non_integer_schema_version_is_rejected(self):
        for schema_version in (True, 1.0, "1"):
            with self.subTest(schema_version=schema_version):
                document = {
                    "schemaVersion": schema_version,
                    "ansirover": {"restartDelay": "1h"},
                }
                payload = base64.b64encode(
                    json.dumps(document).encode("utf-8")
                ).decode("ascii")

                with self.assertRaises(AnsiRoverPolicyError):
                    decode_metadata(payload)

    def test_missing_ansirover_document_key_is_rejected(self):
        payload = base64.b64encode(b'{"schemaVersion":1}').decode("ascii")

        with self.assertRaises(AnsiRoverPolicyError):
            decode_metadata(payload)

    def test_null_ansirover_metadata_object_is_rejected(self):
        payload = base64.b64encode(
            b'{"schemaVersion":1,"ansirover":null}'
        ).decode("ascii")

        with self.assertRaises(AnsiRoverPolicyError):
            decode_metadata(payload)

    def test_unknown_policy_key_is_rejected(self):
        with self.assertRaises(AnsiRoverPolicyError):
            validate_explicit_policy({"somethingElse": "1h"})

    def test_invalid_duration_is_rejected(self):
        with self.assertRaises(AnsiRoverPolicyError):
            validate_explicit_policy({"gracePeriod": "three minutes"})

    def test_zero_duration_is_rejected(self):
        with self.assertRaises(AnsiRoverPolicyError):
            validate_explicit_policy({"postgresPollInterval": "0s"})

    def test_reconnect_threshold_must_be_positive_integer(self):
        for value in (0, -1, True, "3"):
            with self.subTest(value=value):
                with self.assertRaises(AnsiRoverPolicyError):
                    validate_explicit_policy({"postgresReconnectThreshold": value})


if __name__ == "__main__":
    unittest.main()
