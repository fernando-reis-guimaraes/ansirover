import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-restore-upload.py"


def load_script_module():
    specification = importlib.util.spec_from_file_location(
        "ansirover_restore_upload",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CliAndApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def complete_api_call(self, command, **_kwargs):
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(
            '{"status":100,"data":{"ok":true}}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_wait_deploy_is_enabled_by_default(self):
        arguments = self.module.parse_args(["storage", "example"])

        self.assertTrue(arguments.wait_deploy)

    def test_wait_deploy_remains_accepted_and_enabled(self):
        arguments = self.module.parse_args(
            ["storage", "example", "--wait-deploy"]
        )

        self.assertTrue(arguments.wait_deploy)

    def test_no_wait_uses_detached_deploy(self):
        arguments = self.module.parse_args(["storage", "example", "--no-wait"])

        self.assertFalse(arguments.wait_deploy)
        self.assertEqual(
            self.module.get_deploy_api_path("example", arguments.wait_deploy),
            "/user/apps/appData/example?detached=1",
        )

    def test_default_wait_uses_synchronous_deploy(self):
        arguments = self.module.parse_args(["storage", "example"])

        self.assertEqual(
            self.module.get_deploy_api_path("example", arguments.wait_deploy),
            "/user/apps/appData/example",
        )

    def test_url_target_is_detected_and_validated(self):
        target = "https://captain.example.test"

        self.assertTrue(self.module.is_caprover_url(target))
        self.module.validate_server_target(target)

    def test_url_target_skips_ssh_image_preflight(self):
        self.assertFalse(
            self.module.should_preflight_deploy_image(
                "https://captain.example.test",
                with_image=False,
            )
        )
        self.assertTrue(
            self.module.should_preflight_deploy_image(
                "storage",
                with_image=False,
            )
        )

    def test_url_target_rejects_with_image(self):
        with self.assertRaises(SystemExit):
            self.module.validate_server_mode_options(
                "https://captain.example.test",
                with_image=True,
                with_volumes=False,
            )

    def test_url_target_rejects_with_volumes(self):
        with self.assertRaises(SystemExit):
            self.module.validate_server_mode_options(
                "https://captain.example.test",
                with_image=False,
                with_volumes=True,
            )

    @mock.patch("subprocess.run")
    def test_url_api_uses_caprover_url_and_password(self, run):
        run.side_effect = self.complete_api_call

        with mock.patch.dict(
            os.environ,
            {"CAPROVER_PASSWORD": "url-secret"},
            clear=True,
        ):
            response = self.module.run_caprover_api(
                "https://captain.example.test",
                "/user/system/info",
                "GET",
                {},
            )

        self.assertEqual(response, {"ok": True})
        command = run.call_args.args[0]
        self.assertIn("--caproverUrl", command)
        self.assertEqual(
            command[command.index("--caproverUrl") + 1],
            "https://captain.example.test",
        )
        self.assertNotIn("--caproverName", command)
        self.assertEqual(command[command.index("-p") + 1], "url-secret")

    @mock.patch("subprocess.run")
    def test_name_api_keeps_caprover_name_mode(self, run):
        run.side_effect = self.complete_api_call

        with mock.patch.dict(
            os.environ,
            {"STORAGE_CAPTAIN_PASSWORD": "name-secret"},
            clear=True,
        ):
            response = self.module.run_caprover_api(
                "storage",
                "/user/system/info",
                "GET",
                {},
            )

        self.assertEqual(response, {"ok": True})
        command = run.call_args.args[0]
        self.assertIn("--caproverName", command)
        self.assertEqual(command[command.index("--caproverName") + 1], "storage")
        self.assertNotIn("--caproverUrl", command)
        self.assertEqual(command[command.index("-p") + 1], "name-secret")

    @mock.patch("subprocess.run")
    def test_url_api_requires_caprover_password(self, run):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.module.run_caprover_api(
                    "https://captain.example.test",
                    "/user/system/info",
                    "GET",
                    {},
                )

        run.assert_not_called()

    @mock.patch("subprocess.run")
    def test_name_api_requires_captain_password(self, run):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.module.run_caprover_api(
                    "storage",
                    "/user/system/info",
                    "GET",
                    {},
                )

        run.assert_not_called()

    def test_require_captain_password_fails_fast_for_machine_name(self):
        with mock.patch.dict(os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True):
            with self.assertRaises(SystemExit):
                self.module.require_captain_password("server-a")

    def test_restore_one_app_fails_before_any_work_without_password(self):
        args = mock.Mock(
            server_name="server-a",
            app_name="example",
            path="/tmp/example",
        )

        with mock.patch.dict(
            os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
        ), mock.patch.object(
            self.module,
            "load_ansirover_app",
        ) as load_app, mock.patch.object(
            self.module,
            "preflight_postgres",
        ) as preflight:
            with self.assertRaises(SystemExit):
                self.module.restore_one_app(args)

        load_app.assert_not_called()
        preflight.assert_not_called()

    @mock.patch("subprocess.run")
    def test_image_preflight_pulls_image_on_destination_server(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        with mock.patch.object(
            self.module,
            "get_caprover_root_domain",
            return_value="storage.example.test",
        ):
            self.module.require_deploy_image_available(
                "storage",
                "registry.example.test/team/app:123",
            )

        run.assert_called_once_with(
            [
                "ssh",
                "-n",
                "root@storage.example.test",
                "docker pull registry.example.test/team/app:123",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @mock.patch("subprocess.run")
    def test_image_preflight_fails_when_destination_cannot_pull_image(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="manifest unknown",
        )

        with mock.patch.object(
            self.module,
            "get_caprover_root_domain",
            return_value="storage.example.test",
        ):
            with self.assertRaises(SystemExit):
                self.module.require_deploy_image_available(
                    "storage",
                    "registry.example.test/team/app:missing",
                )

    def test_inventory_parser_accepts_optional_ansirover_policy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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
                        "ansirover:",
                        "  gracePeriod: 5m",
                        "  postgresReconnectThreshold: 5",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            inventory = self.module.load_ansirover_app(app_directory, "example")

        self.assertEqual(
            inventory["ansirover"],
            {"gracePeriod": "5m", "postgresReconnectThreshold": 5},
        )

    def test_inventory_parser_rejects_removed_restart_delay(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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
                        "ansirover:",
                        "  restartDelay: 30m",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                self.module.load_ansirover_app(app_directory, "example")

    def test_legacy_inventory_does_not_materialize_ansirover_policy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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

            inventory = self.module.load_ansirover_app(app_directory, "example")

        self.assertEqual(inventory["ansirover"], {})
        self.assertEqual(self.module.build_persisted_description(inventory), "")

    def test_inventory_parser_rejects_unknown_ansirover_field(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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
                        "ansirover:",
                        "  restartDaley: 30m",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                self.module.load_ansirover_app(app_directory, "example")

    def test_inventory_parser_rejects_null_ansirover_mapping(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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
                        "ansirover: null",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                self.module.load_ansirover_app(app_directory, "example")

    def test_inventory_parser_normalizes_custom_domains(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_directory = Path(temporary_directory)
            (app_directory / "example.yaml").write_text(
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
                        "customDomain:",
                        "  - publicDomain: App.Example.COM",
                        "    hasSsl: true",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            inventory = self.module.load_ansirover_app(app_directory, "example")

        self.assertEqual(
            inventory["customDomain"],
            [
                {
                    "publicDomain": "app.example.com",
                    "hasSsl": True,
                }
            ],
        )

    def test_configure_custom_domains_adds_domain_then_enables_ssl(self):
        with mock.patch.object(self.module, "run_caprover_api") as run_api:
            self.module.configure_custom_domains(
                server_name="storage",
                app_name="example",
                app_definition={"customDomain": []},
                desired_custom_domains=[
                    {
                        "publicDomain": "app.example.com",
                        "hasSsl": True,
                    }
                ],
            )

        self.assertEqual(
            run_api.call_args_list,
            [
                mock.call(
                    server_name="storage",
                    api_path="/user/apps/appDefinitions/customdomain",
                    method="POST",
                    data={
                        "appName": "example",
                        "customDomain": "app.example.com",
                    },
                ),
                mock.call(
                    server_name="storage",
                    api_path="/user/apps/appDefinitions/enablecustomdomainssl",
                    method="POST",
                    data={
                        "appName": "example",
                        "customDomain": "app.example.com",
                    },
                ),
            ],
        )

    def test_configure_custom_domains_only_enables_missing_ssl(self):
        with mock.patch.object(self.module, "run_caprover_api") as run_api:
            self.module.configure_custom_domains(
                server_name="storage",
                app_name="example",
                app_definition={
                    "customDomain": [
                        {
                            "publicDomain": "app.example.com",
                            "hasSsl": False,
                        }
                    ]
                },
                desired_custom_domains=[
                    {
                        "publicDomain": "app.example.com",
                        "hasSsl": True,
                    }
                ],
            )

        run_api.assert_called_once_with(
            server_name="storage",
            api_path="/user/apps/appDefinitions/enablecustomdomainssl",
            method="POST",
            data={
                "appName": "example",
                "customDomain": "app.example.com",
            },
        )

    def test_configure_custom_domains_is_idempotent(self):
        custom_domains = [
            {
                "publicDomain": "app.example.com",
                "hasSsl": True,
            }
        ]

        with mock.patch.object(self.module, "run_caprover_api") as run_api:
            self.module.configure_custom_domains(
                server_name="storage",
                app_name="example",
                app_definition={"customDomain": custom_domains},
                desired_custom_domains=custom_domains,
            )

        run_api.assert_not_called()


class DeployMonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_build_log_entries_keep_caprover_line_numbers(self):
        entries = self.module.get_build_log_entries(
            {
                "logs": {
                    "firstLineNumber": -2,
                    "lines": ["", "first", "second"],
                }
            }
        )

        self.assertEqual(entries, [(-1, "first"), (0, "second")])

    def test_build_log_failure_uses_caprover_failure_marker(self):
        self.assertTrue(
            self.module.build_log_has_failed(
                [(10, "----------------------"), (11, "Deploy failed!")]
            )
        )
        self.assertFalse(
            self.module.build_log_has_failed([(10, "deployment warning")])
        )

    def test_waiting_deploy_submits_detached_and_polls_build_status(self):
        build_statuses = [
            {
                "isAppBuilding": True,
                "logs": {
                    "firstLineNumber": -1,
                    "lines": ["", "Pulling image"],
                },
            },
            {
                "isAppBuilding": False,
                "logs": {
                    "firstLineNumber": 0,
                    "lines": ["Pulling image", "Deployed successfully"],
                },
            },
        ]

        with mock.patch.object(
            self.module,
            "run_caprover_api",
        ) as run_api, mock.patch.object(
            self.module,
            "get_build_status",
            side_effect=build_statuses,
        ), mock.patch.object(
            self.module,
            "get_deploy_poll_seconds",
            return_value=0,
        ):
            self.module.deploy_image(
                "storage",
                "example",
                "registry.example.test/app:123",
                True,
            )

        deploy_call = run_api.call_args_list[0]
        self.assertEqual(
            deploy_call.kwargs["api_path"],
            "/user/apps/appData/example?detached=1",
        )


class PostgresPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_postgres_connection_discovery_is_not_limited_to_db_connection_string(self):
        connections = self.module.get_postgres_connection_envs(
            {
                "PGWEB_DATABASE_URL": "postgresql://user:secret@db.example.test/appdb?application_name=pgweb",
                "OIDC_DATABASE_URL": "postgres://user:secret@db.example.test/oidc?application_name=idp",
                "NOT_A_DATABASE_URL": "https://example.test",
            }
        )

        self.assertEqual(
            [connection[0] for connection in connections],
            ["PGWEB_DATABASE_URL", "OIDC_DATABASE_URL"],
        )

    def test_application_name_is_required_and_must_match_yaml_app_name(self):
        env_map = {
            "PGWEB_DATABASE_URL": "postgresql://user:secret@db.example.test/appdb?sslmode=require",
        }
        parsed = self.module.urlparse(env_map["PGWEB_DATABASE_URL"])

        with mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ) as fail:
            with self.assertRaises(RuntimeError):
                self.module.validate_postgres_application_name(
                    env_map,
                    "PGWEB_DATABASE_URL",
                    parsed,
                    "pgweb",
                )

        message = fail.call_args.args[0]
        self.assertIn("application_name=pgweb", message)
        self.assertIn(
            "PGWEB_DATABASE_URL_ALLOW_MISSING_APPLICATION_NAME=true",
            message,
        )
        self.assertNotIn("secret", message)

    def test_application_name_bypass_allows_intentional_missing_parameter(self):
        env_map = {
            "LEGACY_DATABASE_URL": "postgresql://user:secret@db.example.test/appdb",
            "LEGACY_DATABASE_URL_ALLOW_MISSING_APPLICATION_NAME": "true",
        }
        parsed = self.module.urlparse(env_map["LEGACY_DATABASE_URL"])

        result = self.module.validate_postgres_application_name(
            env_map,
            "LEGACY_DATABASE_URL",
            parsed,
            "legacy-app",
        )

        self.assertFalse(result)

    def test_application_name_bypass_does_not_allow_wrong_identity(self):
        env_map = {
            "DATABASE_URL": "postgresql://user:secret@db.example.test/appdb?application_name=wrong-app",
            "DATABASE_URL_ALLOW_MISSING_APPLICATION_NAME": "true",
        }
        parsed = self.module.urlparse(env_map["DATABASE_URL"])

        with mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ) as fail:
            with self.assertRaises(RuntimeError):
                self.module.validate_postgres_application_name(
                    env_map,
                    "DATABASE_URL",
                    parsed,
                    "expected-app",
                )

        self.assertIn("expected-app", fail.call_args.args[0])
        self.assertNotIn("secret", fail.call_args.args[0])

    def test_psql_preflight_keeps_password_out_of_command_arguments(self):
        args = mock.Mock(app_name="example", path="/tmp/example")

        with mock.patch.object(
            self.module,
            "get_expected_application_name",
            return_value="example",
        ), mock.patch.object(
            self.module,
            "get_inventory_env_map",
            return_value={
                "DB_CLIENT": "pg",
                "DB_CONNECTION_STRING": "postgresql://user:super-secret@db.example.test:5432/appdb?sslmode=verify-full&sslrootcert=system&application_name=example",
            },
        ), mock.patch.object(
            self.module.shutil,
            "which",
            return_value="/usr/bin/psql",
        ), mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="appdb\n",
                stderr="",
            ),
        ) as run:
            self.module.preflight_postgres(args)

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("super-secret", " ".join(command))
        self.assertEqual(environment["PGPASSWORD"], "super-secret")
        self.assertEqual(environment["PGDATABASE"], "appdb")
        self.assertEqual(environment["PGSSLMODE"], "verify-full")
        self.assertEqual(environment["PGSSLROOTCERT"], "system")
        self.assertEqual(environment["PGAPPNAME"], "example")
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_psql_preflight_uses_system_roots_for_portable_verify_full_uri(self):
        connection_string = (
            "postgresql://user:secret@db.example.test:5432/appdb"
            "?sslmode=verify-full&application_name=example"
        )

        with mock.patch.dict(self.module.os.environ, {}, clear=True):
            environment, database_name = self.module.build_psql_environment(
                connection_string
            )

        self.assertEqual(database_name, "appdb")
        self.assertEqual(environment["PGSSLMODE"], "verify-full")
        self.assertEqual(environment["PGSSLROOTCERT"], "system")

    def test_psql_preflight_reports_sanitized_connection_error(self):
        connection_string = (
            "postgresql://user:super-secret@db.example.test/appdb"
            "?application_name=example"
        )

        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="certificate verification failed for password super-secret",
            ),
        ), mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ) as fail:
            with self.assertRaises(RuntimeError):
                self.module.run_postgres_connection_preflight(
                    "DATABASE_URL",
                    connection_string,
                )

        message = fail.call_args.args[0]
        self.assertIn("certificate verification failed", message)
        self.assertIn("<redacted>", message)
        self.assertNotIn("super-secret", message)

    def test_psql_preflight_timeout_does_not_report_credentials(self):
        connection_string = (
            "postgresql://user:super-secret@db.example.test/appdb"
            "?application_name=example"
        )

        with mock.patch.object(
            self.module.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["psql"], 15),
        ), mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ) as fail:
            with self.assertRaises(RuntimeError):
                self.module.run_postgres_connection_preflight(
                    "DATABASE_URL",
                    connection_string,
                )

        self.assertIn("timed out", fail.call_args.args[0])
        self.assertNotIn("super-secret", fail.call_args.args[0])


class PostDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_live_env_drift_reports_key_names_not_values(self):
        args = mock.Mock(server_name="storage", app_name="example")

        with mock.patch.object(
            self.module,
            "get_inventory_env_map",
            return_value={"SECRET": "expected-secret", "UNCHANGED": "same"},
        ), mock.patch.object(
            self.module,
            "require_app_definition",
            return_value={
                "envVars": [
                    {"key": "SECRET", "value": "wrong-secret"},
                    {"key": "UNCHANGED", "value": "same"},
                ]
            },
        ), mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ) as fail:
            with self.assertRaises(RuntimeError):
                self.module.verify_live_env_drift(args)

        message = fail.call_args.args[0]
        self.assertIn("SECRET", message)
        self.assertNotIn("expected-secret", message)
        self.assertNotIn("wrong-secret", message)

    def test_live_env_drift_reports_postgres_target_without_credentials(self):
        details = self.module.build_live_env_drift_details(
            {
                "PGWEB_DATABASE_URL": (
                    "postgresql://inventory-user:inventory-password@db.example.test:5432/crud"
                    "?sslmode=require&application_name=pgweb"
                )
            },
            {
                "PGWEB_DATABASE_URL": (
                    "postgresql://live-user:live-password@db.example.test:5432/classlog"
                    "?sslmode=require&application_name=pgweb"
                )
            },
        )
        message = "\n".join(details)

        self.assertIn("host=db.example.test port=5432 database=crud", message)
        self.assertIn("host=db.example.test port=5432 database=classlog", message)
        self.assertNotIn("inventory-password", message)
        self.assertNotIn("live-password", message)
        self.assertNotIn("sslmode", message)

    def test_live_env_drift_ignores_extra_live_keys(self):
        details = self.module.build_live_env_drift_details(
            {"DECLARED": "same"},
            {"DECLARED": "same", "CAPROVER_INTERNAL": "extra"},
        )

        self.assertEqual(details, [])

    def test_service_name_prefers_bare_service(self):
        self.module._SERVICE_NAME_CACHE.clear()

        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "run_remote_command",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="abc123\n",
                stderr="",
            ),
        ) as run_remote_command:
            resolved = self.module.resolve_service_name("storage", "prefixed-app")
            cached = self.module.resolve_service_name("storage", "prefixed-app")

        self.assertEqual(resolved, "prefixed-app")
        self.assertEqual(cached, "prefixed-app")
        self.assertEqual(run_remote_command.call_count, 1)
        probe_command = run_remote_command.call_args_list[0].args[1]
        self.assertIn("prefixed-app", probe_command)

    def test_service_name_falls_back_to_prefixed_name(self):
        self.module._SERVICE_NAME_CACHE.clear()

        def fake_run_remote_command(server_name, remote_command):
            if "srv-captain--fallback-app" in remote_command:
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="def456\n",
                    stderr="",
                )

            return subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Error: No such service: fallback-app",
            )

        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "run_remote_command",
            side_effect=fake_run_remote_command,
        ) as run_remote_command:
            resolved = self.module.resolve_service_name("storage", "fallback-app")

        self.assertEqual(resolved, "srv-captain--fallback-app")
        self.assertEqual(run_remote_command.call_count, 2)

    def test_service_name_defaults_to_bare_when_neither_exists(self):
        self.module._SERVICE_NAME_CACHE.clear()

        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "run_remote_command",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Error: No such service",
            ),
        ):
            resolved = self.module.resolve_service_name("storage", "bare-app")

        self.assertEqual(resolved, "bare-app")

    def test_service_name_skips_probe_for_url_targets(self):
        self.module._SERVICE_NAME_CACHE.clear()

        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=True,
        ), mock.patch.object(
            self.module,
            "run_remote_command",
        ) as run_remote_command:
            resolved = self.module.resolve_service_name(
                "https://captain.example.test",
                "url-app",
            )

        self.assertEqual(resolved, "url-app")
        run_remote_command.assert_not_called()

    def test_wait_for_swarm_convergence_fails_on_new_failed_task(self):
        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "resolve_service_name",
            return_value="srv-captain--example",
        ), mock.patch.object(
            self.module,
            "get_service_replica_counts",
            return_value=(1, 0),
        ), mock.patch.object(
            self.module,
            "get_new_failed_tasks",
            return_value=["new-task"],
        ), mock.patch.object(
            self.module,
            "print_service_tasks",
        ), mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ):
            with self.assertRaises(RuntimeError):
                self.module.wait_for_swarm_convergence(
                    "storage",
                    "example",
                    {"old-task"},
                )

    def test_postgres_session_tracker_ignores_pool_expansion(self):
        state = self.module.create_postgres_session_generation_state(
            {"100|start-a"}
        )

        first = self.module.observe_postgres_session_generation(
            state,
            {"100|start-a", "101|start-b", "102|start-c"},
        )
        second = self.module.observe_postgres_session_generation(
            state,
            {"100|start-a", "101|start-b", "102|start-c", "103|start-d"},
        )

        self.assertFalse(first["reconnect_wave"])
        self.assertEqual(first["event"], "pool_expansion")
        self.assertFalse(second["reconnect_wave"])
        self.assertEqual(second["event"], "pool_expansion")

    def test_postgres_session_tracker_uses_first_connection_as_baseline(self):
        state = self.module.create_postgres_session_generation_state(set())

        observation = self.module.observe_postgres_session_generation(
            state,
            {"100|start-a"},
        )

        self.assertFalse(observation["reconnect_wave"])
        self.assertEqual(observation["event"], "initial_connection_baseline")

    def test_postgres_session_tracker_counts_reconnect_after_full_disconnect(self):
        state = self.module.create_postgres_session_generation_state(
            {"100|start-a"}
        )

        disconnected = self.module.observe_postgres_session_generation(
            state,
            set(),
        )
        reconnected = self.module.observe_postgres_session_generation(
            state,
            {"101|start-b"},
        )

        self.assertFalse(disconnected["reconnect_wave"])
        self.assertEqual(disconnected["event"], "full_disconnect")
        self.assertTrue(reconnected["reconnect_wave"])
        self.assertEqual(reconnected["event"], "reconnect_after_full_disconnect")

    def test_postgres_session_tracker_ignores_partial_pool_churn(self):
        state = self.module.create_postgres_session_generation_state(
            {"100|start-a", "101|start-b"}
        )

        observation = self.module.observe_postgres_session_generation(
            state,
            {"101|start-b", "102|start-c"},
        )

        self.assertFalse(observation["reconnect_wave"])
        self.assertEqual(observation["event"], "partial_pool_churn")

    def test_post_deploy_reconnect_threshold_scales_service_to_zero(self):
        args = mock.Mock(
            server_name="storage",
            app_name="example",
            path="/tmp/example",
        )
        session_snapshots = [
            {"100|start-a"},
            {"101|start-b"},
            {"102|start-c"},
        ]

        with mock.patch.object(
            self.module.shutil,
            "which",
            return_value="/usr/bin/psql",
        ), mock.patch.object(
            self.module,
            "get_monitorable_postgres_connections",
            return_value=[("DATABASE_URL", "postgresql://redacted")],
        ), mock.patch.object(
            self.module,
            "get_expected_application_name",
            return_value="example",
        ), mock.patch.object(
            self.module,
            "get_postgres_session_ids",
            side_effect=session_snapshots,
        ), mock.patch.object(
            self.module,
            "scale_service_to_zero",
        ) as scale_to_zero, mock.patch.object(
            self.module,
            "fail",
            side_effect=RuntimeError,
        ):
            with self.assertRaises(RuntimeError):
                self.module.monitor_post_deploy_postgres_reconnects(
                    args,
                    {
                        "gracePeriod": "10s",
                        "postgresReconnectThreshold": 2,
                        "postgresPollInterval": "1ms",
                    },
                )

        scale_to_zero.assert_called_once_with("storage", "example")

    def test_restore_skips_post_deploy_checks_for_no_wait(self):
        args = mock.Mock(
            server_name="storage",
            app_name="example",
            path="/tmp/example",
            wait_deploy=False,
        )

        with mock.patch.dict(
            os.environ,
            {"STORAGE_CAPTAIN_PASSWORD": "test-secret"},
            clear=False,
        ), mock.patch.object(
            self.module,
            "load_ansirover_app",
            return_value={"ansirover": {}},
        ), mock.patch.object(
            self.module,
            "preflight_postgres",
        ), mock.patch.object(
            self.module,
            "get_task_state_snapshot",
            return_value=set(),
        ), mock.patch.object(
            self.module,
            "restore_one_app_steps",
        ) as restore, mock.patch.object(
            self.module,
            "verify_live_env_drift",
        ) as verify_drift, mock.patch.object(
            self.module,
            "verify_ansirover_metadata",
        ) as verify_metadata, mock.patch.object(
            self.module,
            "wait_for_swarm_convergence",
        ) as wait_for_convergence, mock.patch.object(
            self.module,
            "monitor_post_deploy_postgres_reconnects",
        ) as monitor_reconnects:
            self.module.restore_one_app(args)

        restore.assert_called_once_with(args)
        verify_drift.assert_called_once_with(args)
        verify_metadata.assert_called_once_with(args, {})
        wait_for_convergence.assert_not_called()
        monitor_reconnects.assert_not_called()

    def test_fast_all_restore_still_verifies_explicit_policy(self):
        args = mock.Mock(
            server_name="storage",
            app_name="example",
            path="/tmp/example",
            wait_deploy=True,
        )

        with mock.patch.dict(
            os.environ,
            {"STORAGE_CAPTAIN_PASSWORD": "test-secret"},
            clear=False,
        ), mock.patch.object(
            self.module,
            "load_ansirover_app",
            return_value={"ansirover": {"gracePeriod": "5m"}},
        ), mock.patch.object(
            self.module,
            "preflight_postgres",
        ), mock.patch.object(
            self.module,
            "restore_one_app_steps",
        ), mock.patch.object(
            self.module,
            "verify_ansirover_metadata",
        ) as verify_metadata, mock.patch.object(
            self.module,
            "verify_live_env_drift",
        ) as verify_drift:
            self.module.restore_one_app(args, run_post_deploy_checks=False)

        verify_metadata.assert_called_once_with(args, {"gracePeriod": "5m"})
        verify_drift.assert_called_once_with(args)

    def test_post_deploy_baseline_is_captured_after_final_config(self):
        args = mock.Mock(
            server_name="storage",
            app_name="example",
            path="/tmp/example",
            wait_deploy=True,
        )
        call_order = []

        def record(name):
            def side_effect(*_args, **_kwargs):
                call_order.append(name)
                if name == "snapshot":
                    return {"old-task"}
                return None
            return side_effect

        with mock.patch.dict(
            os.environ,
            {"STORAGE_CAPTAIN_PASSWORD": "test-secret"},
            clear=False,
        ), mock.patch.object(
            self.module,
            "load_ansirover_app",
            return_value={"ansirover": {}},
        ), mock.patch.object(
            self.module,
            "preflight_postgres",
        ), mock.patch.object(
            self.module,
            "get_task_state_snapshot",
            side_effect=record("snapshot"),
        ) as snapshot, mock.patch.object(
            self.module,
            "restore_one_app_steps",
            side_effect=record("steps"),
        ), mock.patch.object(
            self.module,
            "verify_ansirover_metadata",
            side_effect=record("metadata"),
        ), mock.patch.object(
            self.module,
            "verify_live_env_drift",
            side_effect=record("drift"),
        ), mock.patch.object(
            self.module,
            "wait_for_swarm_convergence",
            side_effect=record("convergence"),
        ) as wait_for_convergence, mock.patch.object(
            self.module,
            "monitor_post_deploy_postgres_reconnects",
        ):
            self.module.restore_one_app(args)

        snapshot.assert_called_once_with("storage", "example")
        wait_for_convergence.assert_called_once_with(
            "storage", "example", {"old-task"}
        )
        self.assertEqual(
            call_order,
            ["steps", "metadata", "drift", "snapshot", "convergence"],
        )

    def test_all_mode_post_deploy_checks_are_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.module.should_run_all_post_deploy_checks())

    def test_all_mode_post_deploy_checks_can_be_enabled(self):
        with mock.patch.dict(
            os.environ,
            {"CAPROVER_ANSIROVER_ALL_POST_DEPLOY_CHECKS": "true"},
            clear=True,
        ):
            self.assertTrue(self.module.should_run_all_post_deploy_checks())


if __name__ == "__main__":
    unittest.main()
