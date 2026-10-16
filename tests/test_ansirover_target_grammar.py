import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

SERVER_TARGET_CLIS = [
    "ansirover-delete-app-safely",
    "ansirover-delete-app-without-local-backup",
    "ansirover-download-backup",
    "ansirover-health-check",
    "ansirover-logs-print",
    "ansirover-restore-upload",
]


def load_module(cli_name):
    module_path = REPOSITORY_ROOT / f"{cli_name}.py"
    module_name = cli_name.replace("-", "_") + "_target_grammar_test"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnsiRoverTargetGrammarTests(unittest.TestCase):
    def test_server_target_shims_are_canonical_direct_exec(self):
        for cli_name in SERVER_TARGET_CLIS:
            with self.subTest(cli_name=cli_name):
                shim_path = REPOSITORY_ROOT / cli_name
                expected = (
                    "#!/bin/sh\n"
                    f'exec "$HOME/bin/.packages/ansirover/{cli_name}.py" "$@"\n'
                )
                self.assertEqual(shim_path.read_text(encoding="utf-8"), expected)

    def test_machine_name_validators_reject_at(self):
        cli_names = [
            "ansirover-delete-app-safely",
            "ansirover-delete-app-without-local-backup",
            "ansirover-download-backup",
            "ansirover-health-check",
            "ansirover-logs-print",
        ]

        for cli_name in cli_names:
            with self.subTest(cli_name=cli_name):
                module = load_module(cli_name)

                with self.assertRaises(SystemExit):
                    module.validate_caprover_machine_name("root@srv1")

    def test_restore_target_rejects_at_and_preserves_url_mode(self):
        module = load_module("ansirover-restore-upload")

        with self.assertRaises(SystemExit):
            module.validate_server_target("root@srv1")

        with self.assertRaises(SystemExit):
            module.validate_server_target("https://user@captain.example.com")

        module.validate_server_target("https://captain.example.com")

    def test_option_before_positionals_does_not_bypass_logs_validation(self):
        module = load_module("ansirover-logs-print")
        args = module.parse_args(["--follow", "root@srv1", "example"])

        self.assertEqual(args.machine_name, "root@srv1")

        with self.assertRaises(SystemExit):
            module.validate_caprover_machine_name(args.machine_name)

    def test_option_before_positionals_does_not_bypass_delete_validation(self):
        module = load_module("ansirover-delete-app-without-local-backup")

        with mock.patch.object(
            sys,
            "argv",
            [
                "ansirover-delete-app-without-local-backup",
                "--force",
                "root@srv1",
                "example",
            ],
        ):
            with mock.patch.object(module, "delete_app") as delete_app:
                with self.assertRaises(SystemExit):
                    module.main()

        delete_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
