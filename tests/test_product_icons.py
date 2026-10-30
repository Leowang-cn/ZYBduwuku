import hashlib
import hmac
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi.responses import Response


class ProductIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "APP_ENV": "test", "SECRET_KEY": "icon-test-secret",
            "DATABASE_URL": f"sqlite:///{cls.temp.name}/app.sqlite",
            "DATA_DIR": cls.temp.name, "UPLOAD_STAGING_DIR": f"{cls.temp.name}/staging",
        })
        cls.env.start()
        from unittest.mock import MagicMock
        from fastapi import FastAPI
        server = MagicMock()
        server.streamable_http_app.return_value = FastAPI()
        with patch("backend.mcp_server.create_server", return_value=server):
            from backend import main
        cls.main = main
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.env.stop()
        cls.temp.cleanup()

    def test_icon_visibility_and_formats(self):
        token = hmac.new(self.main.SECRET.encode(), b"portal-admin", hashlib.sha256).hexdigest()
        for published, admin, filename, key, status in [
            (1, False, "icon.png", "icons/test", 200),
            (0, False, "icon.png", "icons/test", 404),
            (0, True, "icon.png", "icons/test", 200),
            (0, True, "icon.png", "", 404),
            (1, False, "icon.svg", "icons/test", 404),
        ]:
            with self.subTest(published=published, admin=admin, filename=filename, key=key):
                product = {"published": published, "icon_filename": filename, "icon_object_key": key}
                with patch.object(self.main, "row", return_value=product), patch.object(self.main.storage, "serve", return_value=Response(b"png", media_type="image/png")) as serve:
                    response = self.client.get("/api/product-icons/1", headers={"cookie": f"portal_admin={token}"} if admin else {})
                    self.assertEqual(response.status_code, status)
                    if status == 200:
                        self.assertEqual(response.headers["content-type"], "image/png")
                        serve.assert_called_once_with(key, filename, "image/png", attachment=False)
                    else:
                        serve.assert_not_called()
        with patch.object(self.main, "row", return_value=None):
            self.assertEqual(self.client.get("/api/product-icons/999").status_code, 404)
