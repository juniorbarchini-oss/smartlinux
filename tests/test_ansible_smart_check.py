import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Import ansible_smart_check directly from scripts
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
import ansible_smart_check as asc


class TestAnsibleSmartCheck(unittest.TestCase):

    def setUp(self):
        # Sample ATA JSON with Healthy status
        self.ata_healthy_json = {
            "device": {"name": "/dev/sda", "protocol": "ATA"},
            "model_name": "WDC WD10EZEX-08WN4A0",
            "serial_number": "WD-WCC6Y1234567",
            "user_capacity": {"bytes": 1000204886016},
            "temperature": {"current": 32},
            "power_on_time": {"hours": 15420},
            "smart_status": {"passed": True},
            "ata_smart_attributes": {
                "table": [
                    {"id": 1, "name": "Raw_Read_Error_Rate", "value": 200, "worst": 200, "thresh": 51, "raw": {"value": 0}},
                    {"id": 5, "name": "Reallocated_Sector_Ct", "value": 200, "worst": 200, "thresh": 140, "raw": {"value": 0}},
                    {"id": 9, "name": "Power_On_Hours", "value": 79, "worst": 79, "thresh": 0, "raw": {"value": 15420}},
                    {"id": 194, "name": "Temperature_Celsius", "value": 115, "worst": 101, "thresh": 0, "raw": {"value": 32, "string": "32"}},
                    {"id": 197, "name": "Current_Pending_Sector", "value": 200, "worst": 200, "thresh": 0, "raw": {"value": 0}},
                    {"id": 198, "name": "Offline_Uncorrectable", "value": 100, "worst": 253, "thresh": 0, "raw": {"value": 0}}
                ]
            }
        }

        # Sample ATA JSON with WARNING (pending sectors, matching PBS SATA_3TB_A incident)
        self.ata_warning_json = {
            "device": {"name": "/dev/sdc", "protocol": "SATA"},
            "model_name": "WDC WD30EFRX-68EUZN0",
            "serial_number": "WD-WCC4N7654321",
            "user_capacity": {"bytes": 3000592982016},
            "temperature": {"current": 38},
            "power_on_time": {"hours": 42100},
            "smart_status": {"passed": True},
            "ata_smart_attributes": {
                "table": [
                    {"id": 5, "name": "Reallocated_Sector_Ct", "value": 200, "worst": 200, "thresh": 140, "raw": {"value": 2, "string": "2"}},
                    {"id": 196, "name": "Reallocated_Event_Count", "value": 200, "worst": 200, "thresh": 0, "raw": {"value": 1, "string": "1"}},
                    {"id": 197, "name": "Current_Pending_Sector", "value": 199, "worst": 199, "thresh": 0, "raw": {"value": 8, "string": "8"}},
                    {"id": 198, "name": "Offline_Uncorrectable", "value": 100, "worst": 253, "thresh": 0, "raw": {"value": 0}}
                ]
            }
        }

        # Sample ATA JSON with CRITICAL FAILURE (threshold breached)
        self.ata_failed_json = {
            "device": {"name": "/dev/sdd", "protocol": "SATA"},
            "model_name": "Seagate ST2000DM001",
            "serial_number": "Z4H0ABCD",
            "user_capacity": {"bytes": 2000398934016},
            "smart_status": {"passed": False},
            "ata_smart_attributes": {
                "table": [
                    {"id": 5, "name": "Reallocated_Sector_Ct", "value": 10, "worst": 10, "thresh": 36, "when_failed": "FAILING_NOW", "raw": {"value": 1540}},
                    {"id": 197, "name": "Current_Pending_Sector", "value": 50, "worst": 50, "thresh": 0, "raw": {"value": 88}}
                ]
            }
        }

        # Sample NVMe JSON with Healthy status
        self.nvme_healthy_json = {
            "device": {"name": "/dev/nvme0n1", "protocol": "NVMe"},
            "model_name": "Micron 512GB NVMe",
            "serial_number": "26115721A8B0",
            "user_capacity": {"bytes": 512110190592},
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "critical_warning": 0,
                "temperature": 35,
                "available_spare": 100,
                "available_spare_threshold": 10,
                "percentage_used": 2,
                "media_errors": 0,
                "power_on_hours": 34
            }
        }

        # Sample NVMe JSON with WARNING (high wear / low spare)
        self.nvme_warning_json = {
            "device": {"name": "/dev/nvme1n1", "protocol": "NVMe"},
            "model_name": "Old Samsung 970 EVO",
            "serial_number": "S466NX0M123456",
            "user_capacity": {"bytes": 250059350016},
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "critical_warning": 0,
                "temperature": 48,
                "available_spare": 9,
                "available_spare_threshold": 10,
                "percentage_used": 96,
                "media_errors": 0,
                "power_on_hours": 38000
            }
        }

        # Sample NVMe JSON with FAILED (critical warning bitmask and media errors)
        self.nvme_failed_json = {
            "device": {"name": "/dev/nvme2n1", "protocol": "NVMe"},
            "model_name": "Failing NVMe SSD",
            "serial_number": "FAILNVME123",
            "user_capacity": {"bytes": 1000204886016},
            "smart_status": {"passed": False},
            "nvme_smart_health_information_log": {
                "critical_warning": 4,
                "temperature": 72,
                "available_spare": 0,
                "available_spare_threshold": 10,
                "percentage_used": 100,
                "media_errors": 142,
                "power_on_hours": 45000
            }
        }

    def test_ata_healthy_evaluation(self):
        result = asc.evaluate_smart_data(self.ata_healthy_json, "/dev/sda")
        self.assertEqual(result["health_status"], "HEALTHY")
        self.assertEqual(result["exit_code"], asc.EXIT_HEALTHY)
        self.assertEqual(result["temperature_c"], 32)
        self.assertEqual(result["power_on_hours"], 15420)
        self.assertEqual(len(result["reasons"]), 0)

    def test_ata_warning_evaluation(self):
        result = asc.evaluate_smart_data(self.ata_warning_json, "/dev/sdc")
        self.assertEqual(result["health_status"], "WARNING")
        self.assertEqual(result["exit_code"], asc.EXIT_WARNING)
        self.assertTrue(any("Current_Pending_Sector: 8" in r for r in result["reasons"]))
        self.assertTrue(any("Reallocated_Sector_Ct: 2" in r for r in result["reasons"]))

    def test_ata_failed_evaluation(self):
        result = asc.evaluate_smart_data(self.ata_failed_json, "/dev/sdd")
        self.assertEqual(result["health_status"], "FAILED")
        self.assertEqual(result["exit_code"], asc.EXIT_FAILED)
        self.assertTrue(any("FAILED: Reallocated_Sector_Ct" in r for r in result["reasons"]))

    def test_nvme_healthy_evaluation(self):
        result = asc.evaluate_smart_data(self.nvme_healthy_json, "/dev/nvme0n1")
        self.assertEqual(result["health_status"], "HEALTHY")
        self.assertEqual(result["exit_code"], asc.EXIT_HEALTHY)
        self.assertEqual(len(result["reasons"]), 0)

    def test_nvme_warning_evaluation(self):
        result = asc.evaluate_smart_data(self.nvme_warning_json, "/dev/nvme1n1")
        self.assertEqual(result["health_status"], "WARNING")
        self.assertEqual(result["exit_code"], asc.EXIT_WARNING)
        self.assertTrue(any("High endurance wear" in r for r in result["reasons"]))
        self.assertTrue(any("Available spare" in r for r in result["reasons"]))

    def test_nvme_failed_evaluation(self):
        result = asc.evaluate_smart_data(self.nvme_failed_json, "/dev/nvme2n1")
        self.assertEqual(result["health_status"], "FAILED")
        self.assertEqual(result["exit_code"], asc.EXIT_FAILED)
        self.assertTrue(any("Critical warning bitmask" in r for r in result["reasons"]))
        self.assertTrue(any("media and data integrity errors" in r.lower() for r in result["reasons"]))

    @patch("urllib.request.urlopen")
    def test_ntfy_alert_on_warning(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = asc.evaluate_smart_data(self.ata_warning_json, "/dev/sdc")
        ok, msg = asc.send_ntfy_alert("http://i7server:8880/smart-alerts", result, "i7server")

        self.assertTrue(ok)
        self.assertIn("sent successfully", msg)
        self.assertTrue(mock_urlopen.called)

        # Inspect request headers/body
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Priority"), "high")
        self.assertIn("WARNING", req.get_header("Title"))
        self.assertIn("i7server", req.get_header("Title"))

    @patch("urllib.request.urlopen")
    def test_ntfy_alert_on_failed(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = asc.evaluate_smart_data(self.ata_failed_json, "/dev/sdd")
        ok, msg = asc.send_ntfy_alert("http://i7server:8880/smart-alerts", result, "i7server")

        self.assertTrue(ok)
        self.assertTrue(mock_urlopen.called)

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Priority"), "urgent")
        self.assertIn("CRITICAL FAILURE", req.get_header("Title"))

    def test_ntfy_alert_no_url(self):
        result = asc.evaluate_smart_data(self.ata_warning_json, "/dev/sdc")
        ok, msg = asc.send_ntfy_alert("", result, "i7server")
        self.assertFalse(ok)
        self.assertIn("not configured", msg)

    @patch("ansible_smart_check.run_smartctl")
    def test_cli_execution_healthy(self, mock_run):
        mock_run.return_value = (0, self.ata_healthy_json, "")
        with patch.object(sys, "argv", ["ansible_smart_check.py", "/dev/sda"]):
            code = asc.main()
            self.assertEqual(code, asc.EXIT_HEALTHY)

    @patch("ansible_smart_check.run_smartctl")
    def test_cli_execution_warning(self, mock_run):
        mock_run.return_value = (0, self.ata_warning_json, "")
        with patch.object(sys, "argv", ["ansible_smart_check.py", "/dev/sdc"]):
            code = asc.main()
            self.assertEqual(code, asc.EXIT_WARNING)

    @patch("ansible_smart_check.run_smartctl")
    def test_cli_execution_failed(self, mock_run):
        mock_run.return_value = (0, self.ata_failed_json, "")
        with patch.object(sys, "argv", ["ansible_smart_check.py", "/dev/sdd"]):
            code = asc.main()
            self.assertEqual(code, asc.EXIT_FAILED)


if __name__ == "__main__":
    unittest.main()
