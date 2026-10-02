import importlib.util
from pathlib import Path
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-logs-print.py"


def load_script_module():
    specification = importlib.util.spec_from_file_location(
        "ansirover_logs_print",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ServiceNameNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_bare_app_name_stays_bare_for_new_caprover(self):
        self.assertEqual(
            self.module.normalize_service_name("example-app"),
            "example-app",
        )

    def test_already_prefixed_app_name_is_stripped_to_bare(self):
        self.assertEqual(
            self.module.normalize_service_name("srv-captain--example-app"),
            "example-app",
        )

    def test_captain_prefix_app_name_is_stripped_to_bare(self):
        self.assertEqual(
            self.module.normalize_service_name("captain--example-app"),
            "example-app",
        )

    def test_service_name_candidates_prefer_bare_then_prefixed(self):
        self.assertEqual(
            self.module.get_service_name_candidates("example-app"),
            ["example-app", "srv-captain--example-app"],
        )


class CaptainUrlHostResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_captain_subdomain_is_stripped(self):
        self.assertEqual(
            self.module.hostname_from_captain_url("https://captain.server-a.example.com"),
            "server-a.example.com",
        )

    def test_plain_host_is_kept(self):
        self.assertEqual(
            self.module.hostname_from_captain_url("https://server-a.example.com"),
            "server-a.example.com",
        )

    def test_schemeless_url_is_tolerated(self):
        self.assertEqual(
            self.module.hostname_from_captain_url("captain.server-a.example.com:3000"),
            "server-a.example.com",
        )

    def test_ssh_target_is_root(self):
        self.assertEqual(
            self.module.derive_ssh_target_from_captain_url(
                "https://captain.server-a.example.com"
            ),
            "root@server-a.example.com",
        )


class CaproverConfigLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def _config(self):
        return {
            "CapMachines": [
                {"name": "server-a", "baseUrl": "https://captain.server-a.example.com"},
                {"name": "server-b", "baseUrl": "https://captain.server-b.example.com"},
            ]
        }

    def test_finds_matching_machine(self):
        self.assertEqual(
            self.module.find_captain_url_by_machine(self._config(), "server-b"),
            "https://captain.server-b.example.com",
        )

    def test_missing_machine_fails(self):
        with self.assertRaises(SystemExit) as context:
            self.module.find_captain_url_by_machine(self._config(), "server-x")
        self.assertEqual(context.exception.code, 2)

    def test_empty_base_url_fails(self):
        config = {"CapMachines": [{"name": "server-a", "baseUrl": ""}]}
        with self.assertRaises(SystemExit) as context:
            self.module.find_captain_url_by_machine(config, "server-a")
        self.assertEqual(context.exception.code, 2)


class CliArgumentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_no_arguments_exits_with_usage_error(self):
        with self.assertRaises(SystemExit) as context:
            self.module.parse_args([])
        self.assertEqual(context.exception.code, 2)

    def test_follow_flag(self):
        arguments = self.module.parse_args(["server-a", "example-app", "-f"])
        self.assertTrue(arguments.follow)

    def test_no_follow_by_default(self):
        arguments = self.module.parse_args(["server-a", "example-app"])
        self.assertFalse(arguments.follow)


class RemoteScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def test_tail_count_without_follow(self):
        script = self.module.build_remote_script("example-app", False)
        self.assertIn("follow=no", script)
        self.assertIn("docker service logs --tail 200 --timestamps", script)
        self.assertIn('if [ "$follow" = "yes" ]; then', script)
        # Dual support: bare first, prefixed fallback.
        self.assertIn('fallback_service_name=', script)
        self.assertIn('srv-captain--example-app', script)

    def test_tail_count_with_follow(self):
        script = self.module.build_remote_script("example-app", True)
        self.assertIn("follow=yes", script)
        self.assertIn("exec docker service logs --tail 100 --timestamps -f", script)


if __name__ == "__main__":
    unittest.main()
