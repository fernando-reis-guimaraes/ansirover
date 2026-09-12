import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ansirover-compose-build-push.py"
SPEC = importlib.util.spec_from_file_location("ansirover_compose_build_push", SCRIPT)
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


class BuildPushTests(unittest.TestCase):
    def test_no_arguments_only_prints_usage(self):
        stdout = io.StringIO()
        with mock.patch.object(MODULE, "run") as run_command:
            with contextlib.redirect_stdout(stdout):
                exit_code = MODULE.main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("Usage:", stdout.getvalue())
        run_command.assert_not_called()

    def test_input_path_must_be_exact_git_top_level(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            nested = app / "backend"
            ansirover = app / "ansirover"
            nested.mkdir(parents=True)
            ansirover.mkdir()
            (ansirover / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            init_repo(app)

            with self.assertRaises(SystemExit):
                MODULE.resolve_layout(nested)

            resolved = MODULE.resolve_layout(app)
            self.assertEqual(resolved[0], app.resolve())
            self.assertEqual(resolved[2], ansirover.resolve())

    def test_named_environment_is_selected_from_git_root(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            ansirover = app / "ansirover.production"
            ansirover.mkdir(parents=True)
            (ansirover / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            init_repo(app)

            resolved = MODULE.resolve_layout(app, "production")
            self.assertEqual(resolved[2], ansirover.resolve())

    def test_default_ansirover_may_be_symlink_to_named_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            app = (Path(directory) / "app").resolve()
            target = app / "ansirover.staging"
            target.mkdir(parents=True)
            (target / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (app / "ansirover").symlink_to("ansirover.staging", target_is_directory=True)
            init_repo(app)

            resolved = MODULE.resolve_layout(app)
            self.assertEqual(resolved[2], app / "ansirover")
            self.assertEqual(resolved[2].resolve(), target.resolve())

    def test_environment_name_cannot_escape_application_root(self):
        for invalid_name in ["../production", "production/test", "production test", ".."]:
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(SystemExit):
                    MODULE.validate_environment_name(invalid_name)

    def test_one_compose_means_one_service(self):
        with self.assertRaises(SystemExit):
            MODULE.single_service({"services": {"a": {"build": "."}, "b": {"build": "."}}})

    def test_ansirover_must_be_flat(self):
        with tempfile.TemporaryDirectory() as directory:
            ansirover = Path(directory)
            (ansirover / "nested").mkdir()
            with self.assertRaises(SystemExit):
                MODULE.require_flat_ansirover(ansirover)

    def test_dockerignore_requires_every_ansirover_environment_after_negations(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            (app / "ansirover").mkdir()
            (app / "ansirover.production").mkdir()
            init_repo(app)
            definition = {"build": {"context": ".."}}

            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, app, "app", definition)

            ignore = app / ".dockerignore"
            ignore.write_text(
                "ansirover/\nansirover.production/\n!**/*.md\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, app, "app", definition)

            ignore.write_text(
                "!**/*.md\nansirover/\nansirover.production/\n",
                encoding="utf-8",
            )
            self.assertEqual(
                MODULE.validate_dockerignore(app, app, "app", definition),
                ignore,
            )

    def test_missing_dockerignore_rule_prints_safe_fix_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            app = (Path(directory) / "app").resolve()
            (app / "ansirover").mkdir(parents=True)
            (app / "ansirover.production").mkdir()
            init_repo(app)
            ignore = app / ".dockerignore"
            ignore.write_text("node_modules", encoding="utf-8")
            definition = {"build": {"context": ".."}}
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    MODULE.validate_dockerignore(
                        app,
                        app,
                        "app",
                        definition,
                        "production",
                    )

            output = stderr.getvalue()
            self.assertIn("Fix and continue with:", output)
            self.assertIn("ansirover/", output)
            self.assertIn("ansirover.production/", output)
            self.assertIn(f"git -C {app} add -- .dockerignore", output)
            self.assertIn(
                "commit --only -m 'chore: exclude ansirover from Docker context' -- .dockerignore",
                output,
            )
            self.assertIn(f"git -C {app} push origin HEAD", output)
            self.assertIn(
                f"ansirover-compose-build-push {app} --environment production",
                output,
            )

    def test_dockerfile_specific_ignore_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory).resolve()
            (app / "ansirover").mkdir()
            docker_dir = app / "docker"
            docker_dir.mkdir()
            init_repo(app)
            (app / ".dockerignore").write_text("ansirover/\n", encoding="utf-8")
            dockerfile = docker_dir / "build.Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            specific = docker_dir / "build.Dockerfile.dockerignore"
            specific.write_text("node_modules/\n", encoding="utf-8")
            definition = {"build": {"context": "..", "dockerfile": "docker/build.Dockerfile"}}
            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, app, "app", definition)
            specific.write_text("ansirover/\n", encoding="utf-8")
            self.assertEqual(MODULE.validate_dockerignore(app, app, "app", definition), specific)

    def test_source_clean_check_ignores_all_ansirover_environment_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            ansirover = app / "ansirover"
            production = app / "ansirover.production"
            ansirover.mkdir(parents=True)
            production.mkdir()
            (app / "app.txt").write_text("clean\n", encoding="utf-8")
            (ansirover / "compose.yaml").write_text("clean\n", encoding="utf-8")
            (production / "compose.yaml").write_text("clean\n", encoding="utf-8")
            init_repo(app)
            git(app, "add", ".")
            git(app, "commit", "-qm", "initial")
            (ansirover / "compose.yaml").write_text("dirty\n", encoding="utf-8")
            (production / "compose.yaml").write_text("dirty\n", encoding="utf-8")
            MODULE.require_clean_source(app, app)
            (app / "app.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.require_clean_source(app, app)

    def test_inventory_update(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "app.yaml"
            inventory.write_text("appName: app\ndeployedImageName: old:image\n", encoding="utf-8")
            updated = MODULE.inventory_with_image(inventory, "cr.umapps.net/umapps/app:abc1234")
            self.assertIn("deployedImageName: cr.umapps.net/umapps/app:abc1234", updated)

    def test_allowlist_includes_readme_and_deploy_but_leaves_unknown_file_out(self):
        with tempfile.TemporaryDirectory() as directory:
            ansirover = Path(directory) / "ansirover"
            ansirover.mkdir()
            compose = ansirover / "compose.yaml"
            inventory = ansirover / "app.yaml"
            readme = ansirover / "README.md"
            deploy = ansirover / "DEPLOY.md"
            sidecar = ansirover / "app.env-vars.env"
            dockerfile = ansirover / "Build.Dockerfile"
            specific_ignore = ansirover / "Build.Dockerfile.dockerignore"
            unknown = ansirover / "notes.tmp"
            for path in [compose, inventory, readme, deploy, sidecar, dockerfile, specific_ignore, unknown]:
                path.write_text("x\n", encoding="utf-8")
            paths = MODULE.allowlisted_paths(ansirover, "app", dockerfile, specific_ignore)
            self.assertIn(compose, paths)
            self.assertIn(inventory, paths)
            self.assertIn(readme, paths)
            self.assertIn(deploy, paths)
            self.assertIn(sidecar, paths)
            self.assertIn(dockerfile, paths)
            self.assertIn(specific_ignore, paths)
            self.assertNotIn(unknown, paths)

    def test_commit_paths_excludes_even_staged_unknown_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = (Path(directory) / "repo").resolve()
            repo.mkdir()
            init_repo(repo)
            allowed = repo / "compose.yaml"
            unknown = repo / "notes.tmp"
            allowed.write_text("one\n", encoding="utf-8")
            unknown.write_text("one\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "initial")
            allowed.write_text("two\n", encoding="utf-8")
            unknown.write_text("two\n", encoding="utf-8")
            git(repo, "add", "notes.tmp")
            self.assertTrue(MODULE.commit_paths(repo, [allowed], "test"))
            changed = git(repo, "show", "--name-only", "--pretty=format:", "HEAD").splitlines()
            self.assertEqual(changed, ["compose.yaml"])
            self.assertIn("notes.tmp", git(repo, "status", "--porcelain"))

    def test_git_environment_is_noninteractive(self):
        environment = MODULE.noninteractive_git_environment()
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GCM_INTERACTIVE"], "Never")
        self.assertIn("BatchMode=yes", environment["GIT_SSH_COMMAND"])

    def test_remote_build_context_is_rejected(self):
        definition = {"build": {"context": "https://github.com/example/example.git"}}
        with self.assertRaises(SystemExit):
            MODULE.build_context(Path("/tmp/ansirover"), "app", definition)


if __name__ == "__main__":
    unittest.main()
