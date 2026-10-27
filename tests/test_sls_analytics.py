import unittest
from datetime import datetime, timezone

from sls_analytics import summarize_logs


class SLSAnalyticsTestCase(unittest.TestCase):
    def test_summarizes_history_and_week_by_application(self):
        logs = [
            self.log("zipvid", "device-a", "transcode_started", "2026-08-30T10:00:00Z"),
            self.log("zipvid", "device-a", "transcode_started", "2026-09-01T10:00:00Z"),
            self.log("zipvid", "device-a", "transcode_started", "2026-09-02T10:00:00Z"),
            self.log("zipvid", "device-a", "transcode_finished", "2026-09-02T10:02:00Z", "success", 100),
            self.log("zipvid", "device-b", "transcode_finished", "2026-09-02T11:00:00Z", "failed", 20),
            self.log("other", "device-a", "transcode_started", "2026-09-03T10:00:00Z"),
        ]

        result = summarize_logs(logs, now=datetime(2026, 9, 3, 12, tzinfo=timezone.utc))
        zipvid = next(item for item in result["applications"] if item["app_id"] == "zipvid")

        self.assertEqual(result["week_start"], "2026-08-31")
        self.assertEqual(zipvid["history"]["user_count"], 1)
        self.assertEqual(zipvid["history"]["task_count"], 3)
        self.assertEqual(zipvid["week"]["usage_count"], 2)
        self.assertEqual(zipvid["week"]["success_count"], 1)
        self.assertEqual(zipvid["week"]["failure_count"], 1)
        self.assertEqual(zipvid["week"]["success_rate"], 50.0)
        self.assertEqual(zipvid["week"]["saved_seconds"], 50)
        self.assertEqual(result["totals"]["week"]["user_count"], 1)

    @staticmethod
    def log(app_id, install_id, event_type, created_at, status=None, duration=None):
        payload = {}
        if status is not None:
            payload["status"] = status
        if duration is not None:
            payload["transcode_duration_sec"] = duration
        return {
            "app_id": app_id,
            "install_id": install_id,
            "event_type": event_type,
            "created_at": created_at,
            "payload_json": payload,
        }


if __name__ == "__main__":
    unittest.main()