import importlib.util
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

        self.assertEqual(
            MODULE.resolve_app_dir(current_dir, "example"),
            current_dir,
        )

    def test_directory_matching_app_name_is_used_directly(self):
        current_dir = Path("/tmp/example")

        self.assertEqual(
            MODULE.resolve_app_dir(current_dir, "example"),
            current_dir,
        )

    def test_other_directory_gets_app_subdirectory(self):
        current_dir = Path("/tmp/project")

        self.assertEqual(
            MODULE.resolve_app_dir(current_dir, "example"),
            current_dir / "example",
        )


if __name__ == "__main__":
    unittest.main()
