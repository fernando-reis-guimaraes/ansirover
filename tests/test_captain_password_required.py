import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def load_module(cli_name):
    module_path = REPOSITORY_ROOT / f"{cli_name}.py"
    module_name = cli_name.replace("-", "_") + "_password_required_test"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MACHINE_NAME_CLIS = [
    "ansirover-download-backup",
    "ansirover-delete-app-safely",
    "ansirover-health-check",
    "ansirover-delete-app-without-local-backup",
]


class CaptainPasswordRequiredTests(unittest.TestCase):
    def test_machine_clis_require_password_before_api(self):
        for cli_name in MACHINE_NAME_CLIS:
            with self.subTest(cli=cli_name):
                module = load_module(cli_name)

                with mock.patch.dict(
                    os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
                ):
                    with self.assertRaises(SystemExit):
                        module.require_captain_password("server-a")

    def test_download_backup_api_requires_password(self):
        module = load_module("ansirover-download-backup")

        with mock.patch.dict(
            os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
        ), mock.patch.object(
            module.subprocess,
            "run",
        ) as run:
            with self.assertRaises(SystemExit):
                module.run_caprover_api("server-a", "/user/apps/appDefinitions", "GET", {})

        run.assert_not_called()

    def test_delete_app_safely_api_requires_password(self):
        module = load_module("ansirover-delete-app-safely")

        with mock.patch.dict(
            os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
        ), mock.patch.object(
            module.subprocess,
            "run",
        ) as run:
            with self.assertRaises(SystemExit):
                module.run_caprover_api(
                    "server-a",
                    "/user/apps/appDefinitions",
                    "GET",
                    {},
                    allowed_statuses={100},
                )

        run.assert_not_called()

    def test_health_check_api_requires_password(self):
        module = load_module("ansirover-health-check")

        with mock.patch.dict(
            os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
        ), mock.patch.object(
            module.subprocess,
            "run",
        ) as run:
            with self.assertRaises(SystemExit):
                module.run_caprover_api("server-a", "/user/apps/appDefinitions", "GET", {})

        run.assert_not_called()

    def test_delete_without_backup_requires_password(self):
        module = load_module("ansirover-delete-app-without-local-backup")

        with mock.patch.dict(
            os.environ, {"ANSIROVER_DISABLE_AMUN": "1"}, clear=True
        ), mock.patch.object(
            module.subprocess,
            "run",
        ) as run:
            with self.assertRaises(SystemExit):
                module.delete_app("server-a", "example")

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
