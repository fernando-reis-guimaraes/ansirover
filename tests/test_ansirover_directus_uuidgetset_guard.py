import importlib.util
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-restore-upload.py"


def load_script_module():
    specification = importlib.util.spec_from_file_location(
        "ansirover_restore_upload_directus_guard_test",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class DirectusUuidgetsetRestoreGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()
        cls.canonical_guard = (
            REPOSITORY_ROOT / "templates" / "nginx-uuidgetset-snippet.conf"
        ).read_text(encoding="utf-8")

    def test_directus_family_image_markers_are_case_insensitive(self):
        image_references = (
            "directus/directus:11.17.4",
            "cr.example.test/team/Directito:11.17.4",
            "cr.example.test/team/INPTAH:latest",
            "cr.example.test/team/inpath:latest",
            "ghcr.io/lawebcapsule/d9:latest",
        )

        for image_reference in image_references:
            with self.subTest(image_reference=image_reference):
                self.assertTrue(
                    self.module.image_requires_uuidgetset_guard(image_reference)
                )

    def test_non_directus_image_does_not_require_guard(self):
        self.assertFalse(
            self.module.image_requires_uuidgetset_guard(
                "cr.example.test/team/ordinary-app:latest"
            )
        )
        self.module.require_directus_family_uuidgetset_guard(
            "cr.example.test/team/ordinary-app:latest",
            "",
        )

    def test_directus_family_image_rejects_missing_custom_nginx(self):
        with self.assertRaises(SystemExit):
            self.module.require_directus_family_uuidgetset_guard(
                "cr.example.test/team/directito:11.17.4",
                "",
            )

    def test_directus_family_image_rejects_word_only_or_incomplete_guard(self):
        incomplete_configs = (
            "# _uuidgetset is mentioned but no guard exists\n",
            "location / { proxy_pass $upstream; }\n# _uuidgetset\n",
            'location ~ "^/items/[A-Za-z0-9_]+_uuidgetset$" { return 404; }\n',
        )

        for custom_nginx_config in incomplete_configs:
            with self.subTest(custom_nginx_config=custom_nginx_config):
                with self.assertRaises(SystemExit):
                    self.module.require_directus_family_uuidgetset_guard(
                        "cr.example.test/team/programacao-backend-directus:latest",
                        custom_nginx_config,
                    )

    def test_directus_family_image_accepts_complete_canonical_guard_with_indentation(self):
        indented_guard = "\n".join(
            "    " + line if line else ""
            for line in self.canonical_guard.splitlines()
        )
        custom_nginx_config = (
            "server {\n"
            "    set $upstream http://example:8055;\n"
            + indented_guard
            + "\n}\n"
        )

        self.module.require_directus_family_uuidgetset_guard(
            "cr.example.test/team/programacao-backend-directus:latest",
            custom_nginx_config,
        )


if __name__ == "__main__":
    unittest.main()
