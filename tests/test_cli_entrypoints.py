import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI_NAMES = sorted(path.stem for path in REPOSITORY_ROOT.glob("*.py")
                   if (REPOSITORY_ROOT / path.stem).is_file())


class CliEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.bin = self.home / "bin"
        packages = self.bin / ".packages"
        packages.mkdir(parents=True)
        (packages / "ansirover").symlink_to(REPOSITORY_ROOT, target_is_directory=True)
        for name in CLI_NAMES:
            (self.bin / name).symlink_to(REPOSITORY_ROOT / name)
        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_public_shims_execute_directly_without_shell_fallback(self):
        for name in CLI_NAMES:
            with self.subTest(cli=name):
                result = subprocess.run(
                    [name, "--help"], env=self.environment, cwd=self.home,
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage", (result.stdout + result.stderr).lower())

    def test_download_converts_definition_through_public_shim(self):
        specification = importlib.util.spec_from_file_location(
            "download_entrypoint_test", REPOSITORY_ROOT / "ansirover-download-backup.py"
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        definition = {
            "appName": "example",
            "deployedVersion": 1,
            "versions": [{"version": 1, "deployedImageName": "example/app:latest"}],
            "hasPersistentData": False,
            "instanceCount": 1,
            "containerHttpPort": 80,
            "customDomain": [
                {
                    "publicDomain": "app.example.com",
                    "hasSsl": True,
                }
            ],
        }
        for with_json in (False, True):
            with self.subTest(with_json=with_json):
                output = self.home / str(with_json) / "example"
                with mock.patch.dict(os.environ, self.environment):
                    module.download_one_app(
                        "server-a", definition, output, with_json=with_json,
                        with_image=False, include_dockerhub=False,
                        with_volumes=False, force=False,
                        caprover_root_domain="example.com",
                    )
                yaml_text = (output / "example.yaml").read_text()
                self.assertIn("example/app:latest", yaml_text)
                self.assertIn("publicDomain: app.example.com", yaml_text)
                self.assertIn("hasSsl: true", yaml_text)
                self.assertEqual((output / "example.json").exists(), with_json)


if __name__ == "__main__":
    unittest.main()
