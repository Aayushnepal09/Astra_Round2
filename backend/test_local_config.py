"""Local key loading uses fixture values and never makes provider requests."""

import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from local_config import groq_api_key


class LocalConfigurationChecks(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="astra-config-check-", dir=settings.BASE_DIR / ".local")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"

    def test_missing_file_and_blank_key_leave_assistant_unconfigured(self):
        self.assertEqual(groq_api_key(self.path, {}), "")
        self.path.write_text("# Local configuration\nGROQ_API_KEY=\n", encoding="utf-8")
        self.assertEqual(groq_api_key(self.path, {}), "")

    def test_plain_quoted_and_bom_values(self):
        for value in ["fixture-key", '"fixture-key"', "'fixture-key'"]:
            with self.subTest(value=value):
                self.path.write_text("# Comment\n GROQ_API_KEY = " + value + "\n", encoding="utf-8-sig")
                self.assertEqual(groq_api_key(self.path, {}), "fixture-key")

    def test_environment_takes_precedence_including_explicit_disable(self):
        self.path.write_text("GROQ_API_KEY=file-fixture\n", encoding="utf-8")
        self.assertEqual(groq_api_key(self.path, {"GROQ_API_KEY": "shell-fixture"}), "shell-fixture")
        self.assertEqual(groq_api_key(self.path, {"GROQ_API_KEY": ""}), "")

    def test_only_groq_key_is_read_and_expressions_are_not_evaluated(self):
        self.path.write_text("DJANGO_DEBUG=0\nGROQ_API_KEY=$(do-not-execute)\n", encoding="utf-8")
        environment = {}
        self.assertEqual(groq_api_key(self.path, environment), "$(do-not-execute)")
        self.assertEqual(environment, {})

    def test_bad_quotes_fail_without_revealing_key(self):
        self.path.write_text('GROQ_API_KEY="private-fixture\n', encoding="utf-8")
        with self.assertRaises(ImproperlyConfigured) as caught:
            groq_api_key(self.path, {})
        self.assertNotIn("private-fixture", str(caught.exception))
