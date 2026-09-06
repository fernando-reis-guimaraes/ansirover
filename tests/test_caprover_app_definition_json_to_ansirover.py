import importlib.util
from pathlib import Path
import unittest

from caprover_ansirover_policy import BEGIN_MARKER, merge_description


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "caprover-app-definition-json-to-ansirover.py"


def load_script_module():
    specification = importlib.util.spec_from_file_location(
        "caprover_app_definition_json_to_ansirover",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def make_app_definition(description):
    return {
        "appName": "example",
        "deployedVersion": 7,
        "versions": [
            {
                "version": 7,
                "deployedImageName": "example/app:latest",
            }
        ],
        "description": description,
        "hasPersistentData": False,
        "instanceCount": 1,
        "containerHttpPort": 80,
        "notExposeAsWebApp": False,
        "websocketSupport": False,
    }


class AnsiRoverExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_human_description_is_exported_without_policy_section(self):
        exported = self.module.build_ansirover_object(
            make_app_definition("Useful human description.")
        )
        yaml_text = self.module.emit_ansirover_yaml(exported, "example")

        self.assertEqual(exported["description"], "Useful human description.")
        self.assertNotIn("ansirover", exported)
        self.assertNotIn("ansirover:", yaml_text)

    def test_explicit_policy_round_trip_does_not_materialize_defaults(self):
        description = merge_description(
            "Useful human description.",
            {"restartDelay": "30m"},
        )

        exported = self.module.build_ansirover_object(
            make_app_definition(description)
        )
        yaml_text = self.module.emit_ansirover_yaml(exported, "example")

        self.assertEqual(exported["description"], "Useful human description.")
        self.assertEqual(exported["ansirover"], {"restartDelay": "30m"})
        self.assertIn("ansirover:\n  restartDelay: 30m\n", yaml_text)
        self.assertNotIn("gracePeriod", yaml_text)
        self.assertNotIn("postgresReconnectThreshold", yaml_text)
        self.assertNotIn("postgresPollInterval", yaml_text)
        self.assertNotIn(BEGIN_MARKER, yaml_text)

    def test_invalid_metadata_aborts_export(self):
        description = "\n".join(
            (
                "Useful human description.",
                "",
                BEGIN_MARKER,
                "not-valid-base64",
                "# ANSIROVER-END #",
            )
        )

        with self.assertRaises(SystemExit):
            self.module.build_ansirover_object(make_app_definition(description))


if __name__ == "__main__":
    unittest.main()
