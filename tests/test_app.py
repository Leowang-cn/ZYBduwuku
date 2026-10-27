import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch


class SoftwareHubTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ.update(
            DATA_DIR=os.path.join(self.temp_dir.name, "data"),
            UPLOAD_DIR=os.path.join(self.temp_dir.name, "uploads"),
            ADMIN_PASSWORD="test-password",
            SECRET_KEY="test-secret",
        )
        import app

        self.module = app
        app.DATA_DIR = app.Path(os.environ["DATA_DIR"])
        app.UPLOAD_DIR = app.Path(os.environ["UPLOAD_DIR"])
        app.DATABASE_PATH = app.DATA_DIR / "app.sqlite"
        app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self):
        return self.client.post("/admin/login", data={"password": "test-password"})

    def test_public_pages_and_health(self):
        homepage = self.client.get("/")
        self.assertEqual(homepage.status_code, 200)
        self.assertIn("第B3906号仓库", homepage.get_data(as_text=True))
        self.assertIn("Zipvid", homepage.get_data(as_text=True))
        self.assertIn("Xiami", homepage.get_data(as_text=True))
        detail = self.client.get("/software/1")
        self.assertEqual(detail.status_code, 200)
        detail_html = detail.get_data(as_text=True)
        self.assertIn("Windows", detail_html)
        self.assertIn("macOS", detail_html)
        self.assertIn("版本记录", detail_html)
        self.assertIn("评价与留言", detail_html)
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"database": "ok", "status": "ok"})

    def test_comment_and_wish_submission(self):
        response = self.client.post(
            "/software/1", data={"author": "测试员", "content": "运行正常"}
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/wishes", data={"author": "测试员", "content": "需要日志分析工具"}
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_upload_and_download_count(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.post(
            "/admin/software/new",
            data={
                "name": "测试软件",
                "version": "1.0",
                "category": "测试",
                "summary": "验证上传",
                "description": "自动化测试安装包",
                "package": (BytesIO(b"package-content"), "package.zip"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            item = self.module.get_db().execute(
                "SELECT id, downloads FROM software WHERE name = ?", ("测试软件",)
            ).fetchone()
        self.assertEqual(item["downloads"], 0)
        response = self.client.get(f"/software/{item['id']}/download")
        self.assertEqual(response.status_code, 200)
        response.close()
        with self.module.app.app_context():
            downloads = self.module.get_db().execute(
                "SELECT downloads FROM software WHERE id = ?", (item["id"],)
            ).fetchone()[0]
        self.assertEqual(downloads, 1)

    def test_admin_routes_require_login(self):
        for route in ("/admin/software", "/admin/dashboard", "/admin/software/new"):
            self.assertEqual(self.client.get(route).status_code, 302)

    def test_dashboard_uses_sls_metrics(self):
        self.login()
        metrics = {
            "configured": True,
            "week_start": "2026-08-31",
            "totals": {
                "history": {"user_count": 3, "usage_count": 4, "task_count": 4, "saved_seconds": 120},
                "week": {"user_count": 2, "usage_count": 3, "task_count": 3, "saved_seconds": 60},
            },
            "applications": [{
                "app_id": "zipvid",
                "history": {"user_count": 3, "usage_count": 4, "task_count": 4, "saved_seconds": 120, "success_count": 3, "failure_count": 1, "success_rate": 75.0, "failure_rate": 25.0},
                "week": {"user_count": 2, "usage_count": 3, "task_count": 3, "saved_seconds": 60, "success_count": 2, "failure_count": 1, "success_rate": 66.7, "failure_rate": 33.3},
            }],
        }
        with patch.object(self.module, "load_dashboard", return_value=metrics) as loader:
            response = self.client.get("/admin/dashboard")
            self.assertEqual(response.status_code, 200)
            loader.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()