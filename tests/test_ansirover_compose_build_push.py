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

    def test_one_compose_means_one_service(self):
        with self.assertRaises(SystemExit):
            MODULE.single_service({"services": {"a": {"build": "."}, "b": {"build": "."}}})

    def test_ansirover_must_be_flat(self):
        with tempfile.TemporaryDirectory() as directory:
            ansirover = Path(directory)
            (ansirover / "nested").mkdir()
            with self.assertRaises(SystemExit):
                MODULE.require_flat_ansirover(ansirover)

    def test_dockerignore_is_required_and_must_follow_negations(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            ansirover = app / "ansirover"
            ansirover.mkdir()
            definition = {"build": {"context": ".."}}
            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, ansirover, "app", definition)
            ignore = app / ".dockerignore"
            ignore.write_text("ansirover/\n!**/*.md\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, ansirover, "app", definition)
            ignore.write_text("!**/*.md\nansirover/\n", encoding="utf-8")
            self.assertEqual(
                MODULE.validate_dockerignore(app, ansirover, "app", definition),
                ignore,
            )

    def test_dockerfile_specific_ignore_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            ansirover = app / "ansirover"
            docker_dir = app / "docker"
            ansirover.mkdir()
            docker_dir.mkdir()
            (app / ".dockerignore").write_text("ansirover/\n", encoding="utf-8")
            dockerfile = docker_dir / "build.Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            specific = docker_dir / "build.Dockerfile.dockerignore"
            specific.write_text("node_modules/\n", encoding="utf-8")
            definition = {"build": {"context": "..", "dockerfile": "docker/build.Dockerfile"}}
            with self.assertRaises(SystemExit):
                MODULE.validate_dockerignore(app, ansirover, "app", definition)
            specific.write_text("ansirover/\n", encoding="utf-8")
            self.assertEqual(MODULE.validate_dockerignore(app, ansirover, "app", definition), specific)

    def test_source_clean_check_ignores_ansirover_only(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            ansirover = app / "ansirover"
            ansirover.mkdir(parents=True)
            (app / "app.txt").write_text("clean\n", encoding="utf-8")
            (ansirover / "compose.yaml").write_text("clean\n", encoding="utf-8")
            init_repo(app)
            git(app, "add", ".")
            git(app, "commit", "-qm", "initial")
            (ansirover / "compose.yaml").write_text("dirty\n", encoding="utf-8")
            MODULE.require_clean_source(app, ansirover)
            (app / "app.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                MODULE.require_clean_source(app, ansirover)

    def test_inventory_update(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "app.yaml"
            inventory.write_text("appName: app\ndeployedImageName: old:image\n", encoding="utf-8")
            updated = MODULE.inventory_with_image(inventory, "cr.umapps.net/umapps/app:abc1234")
            self.assertIn("deployedImageName: cr.umapps.net/umapps/app:abc1234", updated)

    def test_allowlist_leaves_unknown_file_out(self):
        with tempfile.TemporaryDirectory() as directory:
            ansirover = Path(directory) / "ansirover"
            ansirover.mkdir()
            compose = ansirover / "compose.yaml"
            inventory = ansirover / "app.yaml"
            sidecar = ansirover / "app.env-vars.env"
            dockerfile = ansirover / "Build.Dockerfile"
            specific_ignore = ansirover / "Build.Dockerfile.dockerignore"
            unknown = ansirover / "notes.tmp"
            for path in [compose, inventory, sidecar, dockerfile, specific_ignore, unknown]:
                path.write_text("x\n", encoding="utf-8")
            paths = MODULE.allowlisted_paths(ansirover, "app", dockerfile, specific_ignore)
            self.assertIn(compose, paths)
            self.assertIn(inventory, paths)
            self.assertIn(sidecar, paths)
            self.assertIn(dockerfile, paths)
            self.assertIn(specific_ignore, paths)
            self.assertNotIn(unknown, paths)

    def test_commit_paths_excludes_even_staged_unknown_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
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
