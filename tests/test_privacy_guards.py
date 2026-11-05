"""Synthetic credentials only; never store confidential audit terms here."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_public_repository import audit


class PrivacyGuardsTests(unittest.TestCase):
    def scan(self, text, terms=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.txt').write_text(text, encoding='utf-8')
            return [label for _, label in audit(root, set(terms))]

    def test_oauth_secret_and_refresh_token(self):
        self.assertIn('secreto OAuth de Google', self.scan('GOCSPX-' + 'a' * 30))
        self.assertIn('refresh token de Google', self.scan('1//' + 'a' * 40))

    def test_other_private_keys(self):
        self.assertIn('clave API privada', self.scan('sk-' + 'a' * 30))
        self.assertIn('clave AWS', self.scan('AKIA' + 'A' * 16))

    def test_confidential_names_stay_external(self):
        self.assertIn('dato presente en configuracion privada',
                      self.scan('DemoAgency', ['demo agency']))

    def test_empty_session_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'capture.har').touch()
            self.assertIn('archivo privado prohibido', [label for _, label in audit(root, set())])

    def test_commit_identifiers_are_not_advertising_accounts(self):
        self.assertEqual(self.scan('commit abc1234567890def123'), [])
