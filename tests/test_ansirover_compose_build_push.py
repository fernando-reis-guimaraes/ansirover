import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "ansirover-compose-build-push.py"
SPEC = importlib.util.spec_from_file_location("ansirover_compose_build_push", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnsiRoverComposeBuildPushTests(unittest.TestCase):
    def test_no_arguments_prints_usage_without_running_commands(self):
        stdout = io.StringIO()

        with mock.patch.object(MODULE, "run_command") as run_command:
            with contextlib.redirect_stdout(stdout):
                exit_code = MODULE.main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Usage:", stdout.getvalue())
        run_command.assert_not_called()

    def test_compose_must_define_exactly_one_service(self):
        compose_model = {
            "services": {
                "frontend": {"build": {"context": ".."}},
                "backend": {"build": {"context": ".."}},
            }
        }

        with self.assertRaises(SystemExit):
            MODULE.get_single_build_service(compose_model)

    def test_ansirover_directory_must_be_flat(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ansirover_dir = Path(temporary_directory)
            (ansirover_dir / "nested-app").mkdir()

            with self.assertRaises(SystemExit):
                MODULE.ensure_ansirover_is_flat(ansirover_dir)

    def test_build_context_resolves_relative_to_ansirover_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_root = Path(temporary_directory)
            ansirover_dir = application_root / "ansirover"
            source_dir = application_root / "source"
            ansirover_dir.mkdir()
            source_dir.mkdir()

            service_definition = {
                "build": {
                    "context": "../source",
                }
            }

            build_context = MODULE.resolve_build_context(
                ansirover_dir,
                "example",
                service_definition,
            )

            self.assertEqual(build_context, source_dir.resolve())

    def test_image_tag_is_taken_from_git_repo_owning_build_context(self):
        application_root = Path("/tmp/application")
        application_git_root = application_root
        ansirover_dir = application_root / "ansirover"
        compose_path = ansirover_dir / "compose.yaml"
        build_context = application_root / "source-submodule"
        source_git_root = build_context

        discovery_model = {
            "services": {
                "example": {
                    "build": {"context": str(build_context)},
                    "image": (
                        "cr.umapps.net/umapps/example:"
                        + MODULE.DISCOVERY_IMAGE_TAG
                    ),
                }
            }
        }
        resolved_model = {
            "services": {
                "example": {
                    "build": {"context": str(build_context)},
                    "image": "cr.umapps.net/umapps/example:abc1234",
                }
            }
        }

        with mock.patch.object(
            MODULE,
            "resolve_application_layout",
            return_value=(
                application_root,
                application_git_root,
                ansirover_dir,
                compose_path,
            ),
        ):
            with mock.patch.object(MODULE, "ensure_ansirover_is_flat"):
                with mock.patch.object(
                    MODULE,
                    "load_compose_model",
                    side_effect=[discovery_model, resolved_model],
                ):
                    with mock.patch.object(
                        MODULE,
                        "resolve_build_context",
                        side_effect=[build_context, build_context],
                    ):
                        with mock.patch.object(
                            MODULE,
                            "resolve_git_root",
                            return_value=source_git_root,
                        ) as resolve_git_root:
                            with mock.patch.object(
                                MODULE,
                                "get_image_tag",
                                return_value="abc1234",
                            ) as get_image_tag:
                                with mock.patch.object(
                                    MODULE,
                                    "warn_if_git_worktree_is_dirty",
                                ):
                                    with mock.patch.object(MODULE, "print_plan"):
                                        stdout = io.StringIO()

                                        with contextlib.redirect_stdout(stdout):
                                            exit_code = MODULE.main(
                                                ["/tmp/application", "--dry-run"]
                                            )

        self.assertEqual(exit_code, 0)
        resolve_git_root.assert_called_once_with(
            build_context,
            "build.context source",
        )
        get_image_tag.assert_called_once_with(source_git_root)
        self.assertIn("IMAGE_TAG=abc1234", stdout.getvalue())
        self.assertIn(
            "SOURCE_GIT_ROOT=/tmp/application/source-submodule",
            stdout.getvalue(),
        )

    def test_application_layout_accepts_ansirover_submodule_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_root = Path(temporary_directory) / "application"
            ansirover_dir = application_root / "ansirover"
            ansirover_dir.mkdir(parents=True)
            compose_path = ansirover_dir / "compose.yaml"
            compose_path.write_text("services: {}\n", encoding="utf-8")

            with mock.patch.object(
                MODULE,
                "resolve_git_root",
                return_value=application_root.resolve(),
            ) as resolve_git_root:
                layout = MODULE.resolve_application_layout(ansirover_dir)

            self.assertEqual(layout[0], application_root.resolve())
            self.assertEqual(layout[1], application_root.resolve())
            self.assertEqual(layout[2], ansirover_dir.resolve())
            self.assertEqual(layout[3], compose_path.resolve())
            resolve_git_root.assert_called_once_with(
                application_root.resolve(),
                "application",
            )

    def test_remote_build_context_is_rejected(self):
        service_definition = {
            "build": {
                "context": "https://github.com/example/example.git",
            }
        }

        with self.assertRaises(SystemExit):
            MODULE.resolve_build_context(
                Path("/tmp/ansirover"),
                "example",
                service_definition,
            )


if __name__ == "__main__":
    unittest.main()
