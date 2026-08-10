import json
import os
import re
import sys
import unittest
from pathlib import Path


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "scripts" / "runtime_config.py").is_file()
)
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_config import (  # noqa: E402
    load_consumption_runtime_config,
    load_sem_runtime_config,
)


class PublicConfigurationTests(unittest.TestCase):
    def setUp(self):
        os.environ["ADS_AUTOMATION_CONSUMPTION_CONFIG_PATH"] = str(
            ROOT / "config_clientes.example.json"
        )
        os.environ["ADS_AUTOMATION_SEM_CONFIG_PATH"] = str(
            ROOT / "config_fichas_sem.example.json"
        )

    def test_examples_match_runtime_schema(self):
        self.assertTrue(load_consumption_runtime_config()["clientes"])
        sem = load_sem_runtime_config()
        self.assertEqual(
            sem["clients"]["cliente-demo"]["public_key"],
            "client_001",
        )

    def test_apps_script_execution_api_is_owner_only(self):
        for manifest in (ROOT / "apps-script" / "projects").glob(
            "*/appsscript.json"
        ):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["executionApi"]["access"], "MYSELF")

    @unittest.skipUnless(
        (ROOT / ".public-runner").is_file(),
        "Contrato exclusivo del paquete publico.",
    )
    def test_only_ci_is_an_active_workflow(self):
        active = sorted(
            path.name
            for path in (ROOT / ".github" / "workflows").glob("*")
            if path.is_file()
        )
        self.assertEqual(active, ["ci.yml"])

        examples = sorted(
            path.name
            for path in (ROOT / "examples" / "workflows").glob(
                "*.yml.example"
            )
        )
        self.assertEqual(
            examples,
            [
                "actualizar-consumos-heartbeat.yml.example",
                "actualizar-consumos.yml.example",
                "actualizar-ficha-sem.yml.example",
                "verificar-acciones-calendario-sem.yml.example",
            ],
        )

    @unittest.skipUnless(
        (ROOT / ".public-runner").is_file(),
        "Contrato exclusivo del paquete publico.",
    )
    def test_no_real_clasp_file_is_published(self):
        self.assertFalse(any(ROOT.rglob(".clasp.json")))
        self.assertFalse(any(ROOT.rglob(".webapp-deployment.json")))

    @unittest.skipUnless(
        (ROOT / ".public-runner").is_file(),
        "Contrato exclusivo del paquete publico.",
    )
    def test_english_is_primary_and_spanish_is_available(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "README.es.md").is_file())
        self.assertTrue((ROOT / "docs" / "ARCHITECTURE.md").is_file())
        self.assertTrue((ROOT / "docs" / "ARCHITECTURE.es.md").is_file())

        primary = (ROOT / "README.md").read_text(encoding="utf-8")
        spanish = (ROOT / "README.es.md").read_text(encoding="utf-8")
        self.assertIn("## The Problem", primary)
        self.assertIn("## El Problema", spanish)

    @unittest.skipUnless(
        (ROOT / ".public-runner").is_file(),
        "Contrato exclusivo del paquete publico.",
    )
    def test_internal_identity_is_absent(self):
        identity = re.compile(r"vision[\s_.-]*click", re.I)
        text_suffixes = {".gs", ".html", ".js", ".json", ".md", ".py"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            self.assertIsNone(identity.search(str(path.relative_to(ROOT))))
            if path.suffix.lower() not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(identity.search(text), path)


if __name__ == "__main__":
    unittest.main()
