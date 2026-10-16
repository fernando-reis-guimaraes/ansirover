import importlib.util
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "ansirover-health-check.py"
SPEC = importlib.util.spec_from_file_location("ansirover_health_check", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnsiRoverHealthCheckTargetGrammarTests(unittest.TestCase):
    def test_machine_name_accepts_bare_name(self):
        MODULE.validate_caprover_machine_name("srv1")

    def test_machine_name_rejects_at(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_caprover_machine_name("root@srv1")

    def test_ssh_target_accepts_user_at_host(self):
        MODULE.validate_ssh_target("root@srv1")
        MODULE.validate_ssh_target("ubuntu@10.0.0.5")

    def test_ssh_target_rejects_non_explicit_or_malformed_targets(self):
        invalid_targets = [
            "srv1",
            "10.0.0.5",
            "storage.example.com",
            "@storage",
            "root@",
            "root@@storage",
            "https://storage.example.com",
        ]

        for ssh_target in invalid_targets:
            with self.subTest(ssh_target=ssh_target):
                with self.assertRaises(SystemExit):
                    MODULE.validate_ssh_target(ssh_target)

    def test_bare_machine_name_resolves_through_caprover_config(self):
        config = {
            "CapMachines": [
                {
                    "name": "srv1",
                    "baseUrl": "https://captain.srv1.example.com",
                }
            ]
        }

        with mock.patch.object(MODULE, "load_caprover_config", return_value=config):
            ssh_target = MODULE.resolve_ssh_target("srv1", None)

        self.assertEqual(ssh_target, "root@srv1.example.com")

    def test_explicit_ssh_target_skips_caprover_lookup_for_ssh_target(self):
        with mock.patch.object(MODULE, "load_caprover_config") as load_caprover_config:
            ssh_target = MODULE.resolve_ssh_target("srv1", "root@storage")

        self.assertEqual(ssh_target, "root@storage")
        load_caprover_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
