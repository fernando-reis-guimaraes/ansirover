import importlib.util
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "ansirover-machine-target-exec.py"
SPEC = importlib.util.spec_from_file_location("ansirover_machine_target_exec", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnsiRoverMachineTargetExecTests(unittest.TestCase):
    def test_bare_machine_name_is_allowed(self):
        MODULE.validate_caprover_target_argument(["srv1", "example"])

    def test_direct_captain_url_is_allowed(self):
        MODULE.validate_caprover_target_argument(
            ["https://captain.srv1.umapps.net", "example"]
        )

    def test_at_sign_is_rejected_in_caprover_target(self):
        with self.assertRaises(SystemExit) as context:
            MODULE.validate_caprover_target_argument(["root@srv1", "example"])

        self.assertEqual(context.exception.code, 2)

    def test_help_bypasses_target_validation(self):
        MODULE.validate_caprover_target_argument(["--help"])

    def test_runner_preserves_arguments_when_execing_implementation(self):
        implementation_name = "ansirover-download-backup.py"

        with mock.patch.object(Path, "is_file", return_value=True):
            with mock.patch.object(MODULE.os, "execv") as execv:
                MODULE.main([implementation_name, "srv1", "example"])

        execv.assert_called_once()
        exec_arguments = execv.call_args.args[1]
        self.assertEqual(exec_arguments[-2:], ["srv1", "example"])

    def test_every_server_target_cli_is_covered(self):
        self.assertEqual(
            MODULE.ALLOWED_IMPLEMENTATIONS,
            {
                "ansirover-delete-app-safely.py",
                "ansirover-delete-app-without-local-backup.py",
                "ansirover-download-backup.py",
                "ansirover-health-check.py",
                "ansirover-logs-print.py",
                "ansirover-restore-upload.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
