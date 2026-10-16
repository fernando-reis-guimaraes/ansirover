#!/usr/bin/env python3

import importlib.util
import io
import os
import subprocess
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORE_SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-restore-upload.py"

import caprover_ansirover_policy as policy


def load_core_module(name="ansirover_amun_fallback_core_test"):
    specification = importlib.util.spec_from_file_location(name, CORE_SCRIPT_PATH)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class AmunPolicyHelperTests(unittest.TestCase):
    def setUp(self):
        policy.clear_amun_cache()

    def tearDown(self):
        policy.clear_amun_cache()

    def enabled_env(self):
        return mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": ""}, clear=False)

    def test_valid_secret_names(self):
        self.assertTrue(policy.is_valid_amun_secret_name("SERVER_A_CAPTAIN_PASSWORD"))
        self.assertTrue(policy.is_valid_amun_secret_name("DB_PASS_VAR"))
        self.assertTrue(policy.is_valid_amun_secret_name("a"))
        self.assertFalse(policy.is_valid_amun_secret_name("_LEADING_UNDERSCORE"))
        self.assertFalse(policy.is_valid_amun_secret_name(""))
        self.assertFalse(policy.is_valid_amun_secret_name("has space"))
        self.assertFalse(policy.is_valid_amun_secret_name("has$var"))

    def test_lookup_disabled_via_env(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=False):
            self.assertTrue(policy.is_amun_lookup_disabled())
            self.assertIsNone(policy.list_amun_secret_names())
            self.assertFalse(policy.amun_secret_exists("ANYTHING"))
            value, _error = policy.fetch_amun_secret("ANYTHING")
            self.assertIsNone(value)

    def test_list_returns_none_when_binary_missing(self):
        with mock.patch.object(policy.shutil, "which", return_value=None):
            self.assertIsNone(policy.list_amun_secret_names())

    def test_exists_uses_cached_list(self):
        completed = subprocess.CompletedProcess(
            args=["amun-keychain-secrets", "list"],
            returncode=0,
            stdout="ALPHA\nBETA\n",
            stderr="",
        )
        with self.enabled_env():
            with mock.patch.object(policy.shutil, "which", return_value="/usr/bin/amun-keychain-secrets"):
                with mock.patch.object(policy.subprocess, "run", return_value=completed) as run:
                    self.assertTrue(policy.amun_secret_exists("ALPHA"))
                    self.assertFalse(policy.amun_secret_exists("MISSING"))
                    # Second call must reuse the cached list, not rerun the binary.
                    self.assertTrue(policy.amun_secret_exists("BETA"))
                    run.assert_called_once()

    def test_fetch_caches_successful_value(self):
        completed = subprocess.CompletedProcess(
            args=["amun-keychain-secrets", "get", "MYSECRET"],
            returncode=0,
            stdout="secret-value",
            stderr="",
        )
        with self.enabled_env():
            with mock.patch.object(policy.shutil, "which", return_value="/usr/bin/amun-keychain-secrets"):
                with mock.patch.object(policy.subprocess, "run", return_value=completed) as run:
                    value, error = policy.fetch_amun_secret("MYSECRET")
                    self.assertEqual(value, "secret-value")
                    self.assertIsNone(error)
                    value_again, _ = policy.fetch_amun_secret("MYSECRET")
                    self.assertEqual(value_again, "secret-value")
                    run.assert_called_once()

    def test_fetch_failure_returns_stderr_without_value(self):
        completed = subprocess.CompletedProcess(
            args=["amun-keychain-secrets", "get", "MYSECRET"],
            returncode=1,
            stdout="",
            stderr="ERROR: secret not found: MYSECRET",
        )
        with self.enabled_env():
            with mock.patch.object(policy.shutil, "which", return_value="/usr/bin/amun-keychain-secrets"):
                with mock.patch.object(policy.subprocess, "run", return_value=completed):
                    value, error = policy.fetch_amun_secret("MYSECRET")
                    self.assertIsNone(value)
                    self.assertIn("secret not found", error)


class CaptainPasswordAmunFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_core_module()

    def setUp(self):
        policy.clear_amun_cache()

    def tearDown(self):
        policy.clear_amun_cache()

    def test_env_var_still_wins_over_amun(self):
        with mock.patch.dict(
            os.environ,
            {"SERVER_A_CAPTAIN_PASSWORD": "from-env", "ANSIROVER_DISABLE_AMUN": ""},
            clear=True,
        ):
            with mock.patch.object(
                self.module, "fetch_amun_secret", side_effect=AssertionError("must not check amun")
            ):
                self.assertEqual(self.module.require_captain_password("server-a"), "from-env")

    def test_missing_password_falls_back_to_amun_with_warning(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                self.module, "fetch_amun_secret", return_value=("amun-secret", None)
            ):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    value = self.module.require_captain_password("server-a")
                self.assertEqual(value, "amun-secret")
                # The fetched secret is cached into the process environment.
                self.assertEqual(os.environ.get("SERVER_A_CAPTAIN_PASSWORD"), "amun-secret")
                output = stderr.getvalue()
                self.assertIn("SERVER_A_CAPTAIN_PASSWORD", output)
                self.assertIn("Secure Enclave", output)
                self.assertIn("Touch ID", output)
                self.assertNotIn("amun-secret", output)

    def test_missing_password_still_fails_when_amun_has_no_secret(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.module.require_captain_password("server-a")

    def test_url_mode_falls_back_to_amun(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                self.module, "fetch_amun_secret", return_value=("url-secret", None)
            ):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    value = self.module.require_captain_password("https://captain.example.com")
                self.assertEqual(value, "url-secret")
                output = stderr.getvalue()
                self.assertIn("CAPROVER_PASSWORD", output)
                self.assertIn("Secure Enclave", output)


class EnvVarTemplateAmunFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_core_module("ansirover_amun_fallback_templates_test")

    def setUp(self):
        policy.clear_amun_cache()

    def tearDown(self):
        policy.clear_amun_cache()

    def parse(self, text, environment):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "example.env-vars.env"
            path.write_text(text, encoding="utf-8")
            return self.module.parse_env_vars_file_with_templates(
                path,
                environment=dict(environment),
            )

    def test_missing_template_variable_falls_back_to_amun(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                self.module, "fetch_amun_secret", return_value=("amun-value", None)
            ):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    env_vars, templates = self.parse("VALUE=${MYSECRET}\n", {})
                self.assertEqual(env_vars, [{"key": "VALUE", "value": "amun-value"}])
                self.assertEqual(templates, {"VALUE": "${MYSECRET}"})
                output = stderr.getvalue()
                self.assertIn("MYSECRET", output)
                self.assertIn("Secure Enclave", output)
                self.assertIn("Touch ID", output)
                self.assertNotIn("amun-value", output)

    def test_present_empty_variable_does_not_touch_amun(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                self.module, "fetch_amun_secret", side_effect=AssertionError("must not check amun")
            ):
                env_vars, _ = self.parse("VALUE=prefix-${EMPTY}-suffix\n", {"EMPTY": ""})
                self.assertEqual(env_vars, [{"key": "VALUE", "value": "prefix--suffix"}])

    def test_missing_variable_without_amun_still_fails(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.parse("VALUE=${MISSING}\n", {})

    def test_empty_amun_value_is_valid_for_templates(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(self.module, "fetch_amun_secret", return_value=("", None)):
                env_vars, _ = self.parse("VALUE=prefix-${EMPTY}-suffix\n", {})
                self.assertEqual(env_vars, [{"key": "VALUE", "value": "prefix--suffix"}])


if __name__ == "__main__":
    unittest.main()
