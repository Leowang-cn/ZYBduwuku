import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from backend import storage


class CosStorageTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "STORAGE_PROVIDER": "cos", "COS_REGION": "ap-guangzhou",
            "COS_BUCKET": "test-1250000000", "COS_PREFIX": "/duwuku/test/",
            "COS_SECRET_ID": "fake-id", "COS_SECRET_KEY": "fake-key",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_required_settings(self):
        storage.require_configuration()
        for name in storage._SETTINGS["cos"]:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.assertRaises(RuntimeError):
                    storage.require_configuration()
                self.assertFalse(storage.configured())

    def test_prefix_and_keys(self):
        self.assertEqual(storage._object_key("files/id.zip"), "duwuku/test/files/id.zip")
        for key in ("", "/file", "../file", "x/../y", "x//y", "x\\y"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                storage._object_key(key)
        for prefix in ("/", "../x", "x//y", "x\\y"):
            with patch.dict(os.environ, {"COS_PREFIX": prefix}), self.assertRaises(RuntimeError):
                storage.require_configuration()

    def test_private_upload(self):
        client = MagicMock()
        with patch.object(storage, "_cos_client", return_value=client):
            storage.upload("files/id.zip", Path("archive.zip"), "application/zip")
        client.upload_file.assert_called_once_with(
            Bucket="test-1250000000", Key="duwuku/test/files/id.zip",
            LocalFilePath="archive.zip", ContentType="application/zip", ACL="private")

    def test_signed_download_real_sdk_without_network(self):
        with patch("qcloud_cos.cos_auth.time.time", return_value=1800000000):
            response = storage.serve("files/id.zip", "中文 文件.zip", "application/zip")
        parsed = urlparse(response.headers["location"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.path, "/duwuku/test/files/id.zip")
        params = parse_qs(parsed.query)
        self.assertIn("q-signature", params)
        start, end = map(int, params["q-sign-time"][0].split(";"))
        self.assertEqual(end, 1800000300)
        self.assertEqual(start, 1799999940)
        self.assertTrue(params["response-content-disposition"][0].startswith("attachment; filename*=UTF-8''"))
        inline = storage.serve("images/a.png", "a.png", "image/png", attachment=False)
        self.assertEqual(parse_qs(urlparse(inline.headers["location"]).query)["response-content-disposition"], ["inline"])

    def test_invalid_provider(self):
        with patch.dict(os.environ, {"STORAGE_PROVIDER": "other"}), self.assertRaises(RuntimeError):
            storage.require_configuration()


if __name__ == "__main__":
    unittest.main()
