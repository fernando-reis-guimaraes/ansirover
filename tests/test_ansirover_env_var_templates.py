#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from caprover_ansirover_policy import (
    BEGIN_MARKER,
    decode_metadata_document,
    merge_description,
    split_description_metadata,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-restore-upload.py"
EXPORT_SCRIPT_PATH = REPOSITORY_ROOT / "caprover-app-definition-json-to-ansirover.py"


def load_script_module(path, module_name):
    specification = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class EnvVarTemplateMetadataTests(unittest.TestCase):
    def test_policy_only_metadata_remains_schema_v1(self):
        description = merge_description(
            "Human description.",
            {"gracePeriod": "5m"},
        )
        payload = description.split(BEGIN_MARKER, 1)[1].splitlines()[1]
        metadata = decode_metadata_document(payload)

        self.assertEqual(metadata["schemaVersion"], 1)
        self.assertEqual(metadata["ansirover"], {"gracePeriod": "5m"})
        self.assertEqual(metadata["envVarTemplates"], {})

    def test_env_var_templates_use_schema_v2_and_round_trip(self):
        templates = {
            "DATABASE_URL": "postgresql://${DB_USER}:${DB_PASSWORD}@db/app",
            "LITERAL": "$${NOT_EXPANDED}",
        }
        description = merge_description(
            "Human description.",
            {"gracePeriod": "5m"},
            templates,
        )

        human, policy, decoded_templates = split_description_metadata(description)
        payload = description.split(BEGIN_MARKER, 1)[1].splitlines()[1]
        metadata = decode_metadata_document(payload)

        self.assertEqual(human, "Human description.")
        self.assertEqual(policy, {"gracePeriod": "5m"})
        self.assertEqual(decoded_templates, templates)
        self.assertEqual(metadata["schemaVersion"], 2)

    def test_templates_can_exist_without_explicit_policy(self):
        templates = {"TOKENIZED": "prefix-${TOKEN}"}
        description = merge_description("", {}, templates)

        human, policy, decoded_templates = split_description_metadata(description)

        self.assertEqual(human, "")
        self.assertEqual(policy, {})
        self.assertEqual(decoded_templates, templates)


class EnvVarTemplateExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module(
            RESTORE_SCRIPT_PATH,
            "ansirover_restore_upload_env_templates_test",
        )

    def parse(self, text, environment, app_name=None):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "example.env-vars.env"
            path.write_text(text, encoding="utf-8")
            return self.module.parse_env_vars_file_with_templates(
                path,
                environment=environment,
                app_name=app_name,
            )

    def test_simple_templates_expand_from_process_environment(self):
        env_vars, templates = self.parse(
            "DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@db/app\n",
            {
                "DB_USER": "appuser",
                "DB_PASSWORD": "secret-value",
            },
        )

        self.assertEqual(
            env_vars,
            [
                {
                    "key": "DATABASE_URL",
                    "value": "postgresql://appuser:secret-value@db/app",
                }
            ],
        )
        self.assertEqual(
            templates,
            {
                "DATABASE_URL": "postgresql://${DB_USER}:${DB_PASSWORD}@db/app"
            },
        )

    def test_builtin_app_name_templates_expand_from_restore_app_name(self):
        env_vars, templates = self.parse(
            (
                "LOWER=${appname}\n"
                "CAMEL=${appName}\n"
                "COMPOSED=prefix-${appName}-suffix\n"
            ),
            {
                "appname": "must-not-be-used",
                "appName": "must-not-be-used",
            },
            app_name="inventory-app",
        )

        self.assertEqual(
            env_vars,
            [
                {"key": "LOWER", "value": "inventory-app"},
                {"key": "CAMEL", "value": "inventory-app"},
                {"key": "COMPOSED", "value": "prefix-inventory-app-suffix"},
            ],
        )
        self.assertEqual(
            templates,
            {
                "LOWER": "${appname}",
                "CAMEL": "${appName}",
                "COMPOSED": "prefix-${appName}-suffix",
            },
        )

    def test_escaped_builtin_app_name_template_is_kept_literal(self):
        env_vars, templates = self.parse(
            "VALUE=$${appName}\n",
            {},
            app_name="inventory-app",
        )

        self.assertEqual(env_vars, [{"key": "VALUE", "value": "${appName}"}])
        self.assertEqual(templates, {"VALUE": "$${appName}"})

    def test_escaped_template_is_kept_literal(self):
        env_vars, templates = self.parse(
            "VALUE=$${VAR}\n",
            {},
        )

        self.assertEqual(env_vars, [{"key": "VALUE", "value": "${VAR}"}])
        self.assertEqual(templates, {"VALUE": "$${VAR}"})

    def test_expansion_is_one_pass_and_nonrecursive(self):
        env_vars, templates = self.parse(
            "VALUE=${FIRST}\n",
            {"FIRST": "${SECOND}", "SECOND": "must-not-be-used"},
        )

        self.assertEqual(env_vars, [{"key": "VALUE", "value": "${SECOND}"}])
        self.assertEqual(templates, {"VALUE": "${FIRST}"})

    def test_sidecar_variables_are_not_expansion_sources(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.parse(
                    "FIRST=local-value\nSECOND=${FIRST}\n",
                    {},
                )

    def test_missing_environment_variable_fails(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.parse(
                    "VALUE=${MISSING}\n",
                    {},
                )

    def test_present_empty_environment_variable_is_valid(self):
        env_vars, templates = self.parse(
            "VALUE=prefix-${EMPTY}-suffix\n",
            {"EMPTY": ""},
        )

        self.assertEqual(
            env_vars,
            [{"key": "VALUE", "value": "prefix--suffix"}],
        )
        self.assertEqual(templates, {"VALUE": "prefix-${EMPTY}-suffix"})

    def test_shell_default_syntax_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.parse(
                "VALUE=${NAME:-default}\n",
                {"NAME": "present"},
            )

    def test_update_payload_persists_template_not_resolved_value(self):
        ansirover = {
            "description": "Human description.",
            "ansirover": {},
            "instanceCount": 1,
            "notExposeAsWebApp": False,
            "websocketSupport": False,
            "volumes": [],
            "ports": [],
            "containerHttpPort": 80,
            "tags": [],
            "httpAuth": {},
        }
        sidecars = {
            "envVars": [{"key": "VALUE", "value": "real-secret"}],
            "envVarTemplates": {"VALUE": "${SECRET}"},
            "customNginxConfig": "",
            "preDeployFunction": "",
            "serviceUpdateOverride": "",
        }

        payload = self.module.build_update_payload(
            app_definition={},
            app_name="example",
            ansirover=ansirover,
            sidecars=sidecars,
            force_ssl=False,
        )
        human, policy, templates = split_description_metadata(
            payload["description"]
        )

        self.assertEqual(human, "Human description.")
        self.assertEqual(policy, {})
        self.assertEqual(templates, {"VALUE": "${SECRET}"})
        self.assertNotIn("real-secret", payload["description"])
        self.assertEqual(
            payload["envVars"],
            [{"key": "VALUE", "value": "real-secret"}],
        )

    def test_inventory_env_vars_receive_restore_app_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_dir = Path(temporary_directory)
            (app_dir / "example.env-vars.env").write_text(
                "APP_NAME=${appName}\n",
                encoding="utf-8",
            )
            args = mock.Mock(
                app_name="example",
                path=str(app_dir),
            )

            self.assertEqual(
                self.module.get_inventory_env_vars(args),
                [{"key": "APP_NAME", "value": "example"}],
            )

    def test_missing_template_variable_fails_before_restore_steps(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_dir = Path(temporary_directory)
            (app_dir / "example.yaml").write_text(
                "\n".join(
                    (
                        "appName: example",
                        "deployedImageName: example/app:latest",
                        'description: ""',
                        "hasPersistentData: false",
                        "instanceCount: 1",
                        "containerHttpPort: 80",
                        "notExposeAsWebApp: false",
                        "websocketSupport: false",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            (app_dir / "example.env-vars.env").write_text(
                "VALUE=${MISSING}\n",
                encoding="utf-8",
            )
            args = mock.Mock(
                server_name="storage",
                app_name="example",
                path=str(app_dir),
                wait_deploy=True,
            )

            with mock.patch.dict(
                os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
            ), mock.patch.object(
                self.module,
                "restore_one_app_steps",
            ) as restore_steps:
                with self.assertRaises(SystemExit):
                    self.module.restore_one_app(
                        args,
                        run_post_deploy_checks=False,
                    )

        restore_steps.assert_not_called()


class EnvVarTemplateExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module(
            EXPORT_SCRIPT_PATH,
            "caprover_export_env_templates_test",
        )

    def make_app_definition(self, description, env_vars):
        return {
            "appName": "example",
            "deployedVersion": 1,
            "versions": [
                {
                    "version": 1,
                    "deployedImageName": "example/app:latest",
                }
            ],
            "description": description,
            "hasPersistentData": False,
            "instanceCount": 1,
            "containerHttpPort": 80,
            "notExposeAsWebApp": False,
            "websocketSupport": False,
            "envVars": env_vars,
        }

    def test_export_reconstructs_template_instead_of_resolved_secret(self):
        description = merge_description(
            "Human description.",
            {},
            {
                "DATABASE_URL": (
                    "postgresql://app:${DB_PASSWORD}@db/app"
                )
            },
        )
        app_definition = self.make_app_definition(
            description,
            [
                {
                    "key": "DATABASE_URL",
                    "value": "postgresql://app:real-secret@db/app",
                },
                {
                    "key": "NORMAL_VALUE",
                    "value": "abc",
                },
            ],
        )

        sidecars = self.module.build_sidecar_texts(app_definition)
        env_text = sidecars["env-vars.env"]

        self.assertIn(
            "DATABASE_URL=postgresql://app:${DB_PASSWORD}@db/app",
            env_text,
        )
        self.assertIn("NORMAL_VALUE=abc", env_text)
        self.assertNotIn("real-secret", env_text)

    def test_export_fails_when_template_key_is_missing_live(self):
        description = merge_description(
            "",
            {},
            {"DATABASE_URL": "postgresql://${DB_PASSWORD}@db/app"},
        )
        app_definition = self.make_app_definition(
            description,
            [{"key": "OTHER", "value": "abc"}],
        )

        with self.assertRaises(SystemExit):
            self.module.build_sidecar_texts(app_definition)


if __name__ == "__main__":
    unittest.main()
