"""Regressions using fictional configuration and simulated Sheets only."""

import importlib
import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch


ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "scripts/runtime_config.py").is_file())
sys.path.insert(0, str(ROOT / "scripts"))


class ReliabilityTests(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "ADS_AUTOMATION_CONSUMPTION_CONFIG_PATH": str(ROOT / "config_clientes.example.json"),
            "ADS_AUTOMATION_SEM_CONFIG_PATH": str(ROOT / "config_fichas_sem.example.json"),
        }))
        self.enterContext(patch("dotenv.load_dotenv", return_value=False))

    def test_missing_daily_sheet_is_not_an_empty_month(self):
        module = importlib.import_module("actualizar_consumos")
        with patch.object(module, "get_or_create_daily_worksheet",
                          side_effect=module.WorksheetNotFound("missing")):
            with self.assertRaises(RuntimeError):
                module.process_daily_sheet_for_client(
                    None, {"daily_worksheet_name": "Example daily"}, [],
                    target_day=date(2026, 8, 1), spreadsheet=Mock(),
                    create_if_missing=False,
                )

    def test_renewal_has_one_commit_for_values_and_marker(self):
        module = importlib.import_module("actualizar_fichas_sem")
        spreadsheet = Mock()
        worksheet = Mock(id=123, title="Example")
        batch = module.SpreadsheetMutationBatch(spreadsheet)
        module.queue_monthly_renewal(worksheet, {
            "applied": True,
            "value_updates": [
                {"cell": "D15", "value": "520,00", "kind": "monthly_budget"},
                {"cell": "C8", "value": 46265, "kind": "contract_end"},
            ],
            "note_update": {"cell": "C8", "note": "renewed: 2026-08"},
        }, batch)
        batch.flush()
        spreadsheet.values_batch_update.assert_not_called()
        spreadsheet.batch_update.assert_called_once()
        requests = spreadsheet.batch_update.call_args.args[0]["requests"]
        end = requests[1]["updateCells"]["rows"][0]["values"][0]
        self.assertEqual(end["note"], "renewed: 2026-08")
        self.assertIn("numberValue", end["userEnteredValue"])

    def test_rejected_renewal_can_retry_the_identical_batch(self):
        module = importlib.import_module("actualizar_fichas_sem")
        spreadsheet = Mock()
        spreadsheet.batch_update.side_effect = [RuntimeError("rejected"), {}]
        batch = module.SpreadsheetMutationBatch(spreadsheet)
        module.queue_monthly_renewal(Mock(id=123), {
            "applied": True,
            "value_updates": [{"cell": "C8", "value": 46265, "kind": "contract_end"}],
            "note_update": {"cell": "C8", "note": "renewed: 2026-08"},
        }, batch)
        with self.assertRaises(RuntimeError):
            batch.flush()
        batch.flush()
        self.assertEqual(spreadsheet.batch_update.call_args_list[0],
                         spreadsheet.batch_update.call_args_list[1])

    def test_delayed_schedule_selects_the_correct_season(self):
        from automation.smm_schedule import scheduled_context
        for now, cron in [
            ("2026-09-07T12:45:00+02:00", "20 7 * * *"),
            ("2026-12-07T10:45:00+01:00", "20 8 * * *"),
        ]:
            context = scheduled_context("schedule", cron, datetime.fromisoformat(now))
            self.assertEqual(context["candidate"], "true")

    def test_daily_attempt_prevents_automatic_retry(self):
        from automation.smm_schedule import should_run
        self.assertFalse(should_run(True, "schedule", "", True))
        self.assertFalse(should_run(True, "workflow_dispatch", "windows-fallback", True))
        self.assertTrue(should_run(True, "workflow_dispatch", "manual", True))
