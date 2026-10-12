import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ansirover-compose-bootstrap.py"
SPEC = importlib.util.spec_from_file_location("ansirover_compose_bootstrap", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def git(path, *arguments):
    return subprocess.run(
        ["git", "-C", str(path), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    git(path, "config", "user.name", "AnsiRover Test")
    git(path, "config", "user.email", "ansirover-test@example.invalid")


def make_app(files=None):
    directory = tempfile.TemporaryDirectory()
    app = Path(directory.name) / "app"
    app.mkdir(parents=True)
    init_repo(app)
    (app / "Dockerfile").write_text("FROM scratch\nEXPOSE 3000\n", encoding="utf-8")
    (app / ".dockerignore").write_text("node_modules\n", encoding="utf-8")
    if files:
        for relative, content in files.items():
            target = app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return directory, app


class ComposeBootstrapTests(unittest.TestCase):
    def test_no_arguments_only_prints_usage(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = MODULE.main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("Usage:", stdout.getvalue())

    def test_input_path_must_be_exact_git_top_level(self):
        directory, app = make_app()
        try:
            nested = app / "backend"
            nested.mkdir()
            with self.assertRaises(SystemExit):
                MODULE.resolve_application_root(nested)
            self.assertEqual(MODULE.resolve_application_root(app), app.resolve())
        finally:
            directory.cleanup()

    def test_environment_name_validation(self):
        for invalid_name in ["../production", "production/test", "production test", ".."]:
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(SystemExit):
                    MODULE.validate_environment_name(invalid_name)

    def test_app_name_prefers_package_json(self):
        directory, app = make_app({"package.json": '{"name": "crosspaste"}'})
        try:
            self.assertEqual(MODULE.detect_app_name(app, None), "crosspaste")
            self.assertEqual(MODULE.detect_app_name(app, "custom"), "custom")
        finally:
            directory.cleanup()

    def test_app_name_falls_back_to_directory(self):
        directory = tempfile.TemporaryDirectory()
        try:
            app = Path(directory.name) / "My-Cool_App"
            app.mkdir()
            init_repo(app)
            (app / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            self.assertEqual(MODULE.detect_app_name(app, None), "my-cool_app")
        finally:
            directory.cleanup()

    def test_port_detection_prefers_env_example(self):
        directory, app = make_app(
            {
                ".env.example": "APP_PORT=4001\n",
                "package.json": '{"name": "demo"}',
            }
        )
        try:
            self.assertEqual(MODULE.detect_port(app, None), 4001)
            self.assertEqual(MODULE.detect_port(app, 5000), 5000)
        finally:
            directory.cleanup()

    def test_port_defaults_to_node_or_generic(self):
        directory, app = make_app({"package.json": '{"name": "demo"}'})
        try:
            self.assertEqual(MODULE.detect_port(app, None), 3000)
        finally:
            directory.cleanup()
        plain_dir = tempfile.TemporaryDirectory()
        try:
            plain = Path(plain_dir.name) / "plain"
            plain.mkdir()
            init_repo(plain)
            (plain / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            self.assertEqual(MODULE.detect_port(plain, None), 80)
        finally:
            plain_dir.cleanup()

    def test_compose_render_is_single_service_with_registry(self):
        text = MODULE.render_compose("crosspaste", "cr.umapps.net/umapps/crosspaste:${IMAGE_TAG:-beta}", "Dockerfile")
        self.assertIn("crosspaste:", text)
        self.assertIn("context: ..", text)
        self.assertIn("dockerfile: Dockerfile", text)
        self.assertIn("linux/amd64", text)
        self.assertIn("linux/arm64", text)
        self.assertIn("cr.umapps.net/umapps/crosspaste:${IMAGE_TAG:-beta}", text)
        # Exactly one service key at two-space indent.
        services = [
            line
            for line in text.splitlines()
            if line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
            and line.strip() != "build:"
        ]
        self.assertEqual(len(services), 1)

    def test_image_override_must_use_internal_registry(self):
        with self.assertRaises(SystemExit):
            MODULE.default_image("demo", "docker.io/library/demo:beta")
        self.assertEqual(
            MODULE.default_image("demo", "cr.umapps.net/umapps/demo:${IMAGE_TAG:-beta}"),
            "cr.umapps.net/umapps/demo:${IMAGE_TAG:-beta}",
        )

    def test_dry_run_writes_nothing(self):
        directory, app = make_app({"package.json": '{"name": "demo"}'})
        try:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = MODULE.main([str(app), "--dry-run"])
            self.assertEqual(exit_code, 0)
            self.assertFalse((app / "ansirover").exists())
            self.assertIn("BOOTSTRAP_COMPLETED=false", stdout.getvalue())
        finally:
            directory.cleanup()

    def test_bootstrap_creates_minimal_scaffold_and_dockerignore(self):
        directory, app = make_app({"package.json": '{"name": "crosspaste"}'})
        try:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = MODULE.main([str(app)])
            self.assertEqual(exit_code, 0)
            ansirover = app / "ansirover"
            self.assertTrue((ansirover / "compose.yaml").is_file())
            self.assertTrue((ansirover / "crosspaste.yaml").is_file())
            self.assertFalse((ansirover / "README.md").exists())
            self.assertFalse((ansirover / "DEPLOY.md").exists())
            inventory = (ansirover / "crosspaste.yaml").read_text(encoding="utf-8")
            self.assertIn("containerHttpPort: 3000", inventory)
            self.assertIn("cr.umapps.net/umapps/crosspaste:beta", inventory)
            self.assertIn("gracePeriod: 3m", inventory)
            self.assertIn("customDomain: []", inventory)
            self.assertIn("publicDomain: app.example.com", inventory)
            compose = (ansirover / "compose.yaml").read_text(encoding="utf-8")
            self.assertIn("context: ..", compose)
            dockerignore = (app / ".dockerignore").read_text(encoding="utf-8")
            self.assertIn("ansirover/", dockerignore)
            self.assertIn("BOOTSTRAP_COMPLETED=true", stdout.getvalue())
            self.assertIn("NEEDS_REVIEW=true", stdout.getvalue())
        finally:
            directory.cleanup()

    def test_existing_files_require_force(self):
        directory, app = make_app({"package.json": '{"name": "demo"}'})
        try:
            MODULE.main([str(app)])
            with self.assertRaises(SystemExit):
                MODULE.main([str(app)])
            compose = app / "ansirover" / "compose.yaml"
            compose.write_text("customized\n", encoding="utf-8")
            MODULE.main([str(app), "--force"])
            self.assertIn("services:", compose.read_text(encoding="utf-8"))
        finally:
            directory.cleanup()

    def test_detect_volumes_prefers_buildable_service(self):
        directory, app = make_app(
            {
                "package.json": '{"name": "demo"}',
                "compose.yaml": (
                    "services:\n"
                    "  app:\n"
                    "    build:\n"
                    "      context: .\n"
                    "      dockerfile: Dockerfile\n"
                    "    volumes:\n"
                    "      - ./data:/app/data\n"
                    "  caddy:\n"
                    "    image: caddy:2-alpine\n"
                    "    volumes:\n"
                    "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n"
                ),
            }
        )
        try:
            volumes = MODULE.detect_volumes(app, "demo")
            self.assertEqual(
                volumes,
                [{"volumeName": "demo-data", "containerPath": "/app/data"}],
            )
        finally:
            directory.cleanup()

    def test_render_yaml_prefills_volumes_in_canonical_order(self):
        text = MODULE.render_yaml(
            "demo",
            "cr.umapps.net/umapps/demo:${IMAGE_TAG:-beta}",
            3000,
            True,
            [{"volumeName": "demo-data", "containerPath": "/app/data"}],
        )
        self.assertIn("volumeName: demo-data", text)
        self.assertIn("containerPath: /app/data", text)
        self.assertLess(text.index("ansirover:"), text.index("hasPersistentData:"))
        self.assertLess(text.index("customDomain:"), text.index("tags:"))

    def test_named_environment_is_used(self):
        directory, app = make_app({"package.json": '{"name": "demo"}'})
        try:
            MODULE.main([str(app), "--environment=production"])
            self.assertTrue((app / "ansirover.production" / "compose.yaml").is_file())
            self.assertFalse((app / "ansirover").exists())
        finally:
            directory.cleanup()

    def test_with_examples_creates_sidecars(self):
        directory, app = make_app({"package.json": '{"name": "demo"}'})
        try:
            MODULE.main([str(app), "--with-examples"])
            ansirover = app / "ansirover"
            self.assertTrue((ansirover / "demo.env-vars.env").is_file())
            self.assertTrue((ansirover / "demo.custom-nginx-config.conf").is_file())
            self.assertTrue((ansirover / "demo.pre-deploy-function.js").is_file())
            self.assertTrue((ansirover / "demo.service-update-override.yaml").is_file())
        finally:
            directory.cleanup()

    def test_missing_dockerfile_fails_with_guidance(self):
        directory = tempfile.TemporaryDirectory()
        try:
            app = Path(directory.name) / "app"
            app.mkdir()
            init_repo(app)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    MODULE.main([str(app)])
            self.assertIn("no Dockerfile found", stderr.getvalue())
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
