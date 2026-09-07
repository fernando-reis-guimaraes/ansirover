import importlib.util
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "ansirover-template-app-create.py"
SPEC = importlib.util.spec_from_file_location("ansirover_template_app_create", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnsiRoverTemplateAppCreateTests(unittest.TestCase):
    def test_ansirover_directory_is_used_directly(self):
        current_dir = Path("/tmp/example/ansirover")
        self.assertEqual(MODULE.resolve_app_dir(current_dir, "example"), current_dir)

    def test_named_ansirover_environment_is_used_directly(self):
        current_dir = Path("/tmp/example/ansirover.production")
        self.assertEqual(MODULE.resolve_app_dir(current_dir, "example"), current_dir)

    def test_directory_matching_app_name_is_used_directly(self):
        current_dir = Path("/tmp/example")
        self.assertEqual(MODULE.resolve_app_dir(current_dir, "example"), current_dir)

    def test_other_directory_gets_app_subdirectory(self):
        current_dir = Path("/tmp/project")
        self.assertEqual(
            MODULE.resolve_app_dir(current_dir, "example"),
            current_dir / "example",
        )

    def test_readme_is_short_human_index(self):
        readme = MODULE.render_readme("example")
        self.assertIn("| File | Responsibility |", readme)
        self.assertIn("`DEPLOY.md`", readme)
        self.assertIn("`compose.yaml`", readme)
        self.assertLess(len(readme.splitlines()), 30)

    def test_deploy_template_describes_aggregate_runtime_and_external_services(self):
        deploy = MODULE.render_deploy("example")
        self.assertIn("production build packages them into one image/service", deploy)
        self.assertIn("Docker-in-Docker", deploy)
        self.assertIn("External services are deployment prerequisites", deploy)
        self.assertIn("ansirover.<name>/", deploy)
        self.assertIn("do not rewrite it automatically", deploy)

    def test_existing_deploy_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "DEPLOY.md"
            path.write_text("user-owned\n", encoding="utf-8")
            created = MODULE.write_user_owned_file_if_missing(path, "replacement\n")
            self.assertFalse(created)
            self.assertEqual(path.read_text(encoding="utf-8"), "user-owned\n")


if __name__ == "__main__":
    unittest.main()
