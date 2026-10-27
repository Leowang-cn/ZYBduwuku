import csv
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from offline_analytics import analyze_export, read_export


class OfflineAnalyticsTestCase(unittest.TestCase):
    def test_reads_gzip_export_and_writes_sanitized_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "export.csv.gz"
            rows = [
                {
                    "app_id": "zipvid",
                    "event_type": "transcode_started",
                    "install_id": "private-device-id",
                    "created_at": "2026-09-02T10:00:00Z",
                    "payload_json": json.dumps({"file_name": "private.mp4"}),
                    "": "",
                },
                {
                    "app_id": "zipvid",
                    "event_type": "transcode_finished",
                    "install_id": "private-device-id",
                    "created_at": "2026-09-02T10:02:00Z",
                    "payload_json": json.dumps({"status": "success", "transcode_duration_sec": 100}),
                    "": json.dumps({"task_id": "private-task-id"}),
                },
            ]
            with gzip.open(source, "wt", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            parsed = list(read_export(source))
            self.assertEqual(parsed[1]["task_id"], "private-task-id")

            output = root / "output"
            paths = analyze_export(
                source,
                output,
                now=datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
            )

            summary = json.loads(paths[0].read_text(encoding="utf-8"))
            quality = json.loads(paths[2].read_text(encoding="utf-8"))
            self.assertEqual(summary["totals"]["history"]["task_count"], 1)
            self.assertEqual(summary["totals"]["history"]["saved_seconds"], 50)
            self.assertEqual(quality["task_id"]["present_count"], 1)
            for path in paths:
                content = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("private-device-id", content)
                self.assertNotIn("private.mp4", content)
                self.assertNotIn("private-task-id", content)


if __name__ == "__main__":
    unittest.main()