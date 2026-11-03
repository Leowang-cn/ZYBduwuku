import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

INDEX = '<!doctype html><html><head><meta charset="UTF-8"/><title>杜武库｜发现好工具，分享好用法</title></head><body><div id="root"></div></body></html>'


class ShareMetaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "APP_ENV": "test", "SECRET_KEY": "share-test-secret",
            "DATABASE_URL": f"sqlite:///{cls.temp.name}/app.sqlite",
            "DATA_DIR": cls.temp.name, "UPLOAD_STAGING_DIR": f"{cls.temp.name}/staging",
            "DUWUKU_PUBLIC_BASE_URL": "https://duwuku.example.com/",
        })
        cls.env.start()
        server = MagicMock()
        server.streamable_http_app.return_value = FastAPI()
        with patch("backend.mcp_server.create_server", return_value=server):
            from backend import main
        cls.main = main
        # backend.main may already be imported by another test with a different database.
        cls.db_patch = patch.object(main, "DB_PATH", Path(cls.temp.name) / "share.sqlite")
        cls.db_patch.start()
        main.init_db()
        cls.index = Path(cls.temp.name) / "index.html"
        cls.index.write_text(INDEX, encoding="utf-8")
        cls.client = TestClient(main.app)
        with main.db() as c:
            c.execute("INSERT INTO products(id,name,summary,published,icon_object_key,created_at) VALUES(901,'Zip<vid>','压缩 \"视频\" 工具',1,'icons/a','2026-01-01')")
            c.execute("INSERT INTO products(id,name,summary,published,created_at) VALUES(902,'Hidden','未发布',0,'2026-01-01')")
            c.execute("INSERT INTO solutions(id,title,body,status,created_at) VALUES(901,'方案标题','<!--portal-rich--><p>第一步&amp;打开</p><script>x</script><p>' || printf('%.200c','长') || '</p>','approved','2026-01-01')")
            c.execute("INSERT INTO solutions(id,title,body,status,created_at) VALUES(902,'待审核','正文','pending','2026-01-01')")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db_patch.stop()
        cls.env.stop()
        cls.temp.cleanup()

    def render(self, params):
        return self.main.render_index(self.index, params).body.decode()

    def views(self, table, item_id):
        return self.main.row(f"SELECT views FROM {table} WHERE id=?", (item_id,))["views"]

    def test_product_meta_is_escaped_and_absolute(self):
        before = self.views("products", 901)
        page = self.render({"software": "901"})
        self.assertIn("<title>Zip&lt;vid&gt;｜杜武库</title>", page)
        self.assertIn('<meta property="og:title" content="Zip&lt;vid&gt;"/>', page)
        self.assertIn('content="压缩 &quot;视频&quot; 工具"', page)
        self.assertIn('<meta property="og:url" content="https://duwuku.example.com/?software=901"/>', page)
        self.assertIn('<meta property="og:image" content="https://duwuku.example.com/api/product-icons/901"/>', page)
        self.assertEqual(before, self.views("products", 901))

    def test_solution_meta_uses_plain_truncated_body(self):
        page = self.render({"solution": "901"})
        self.assertIn("<title>方案标题｜杜武库</title>", page)
        description = page.split('<meta name="description" content="', 1)[1].split('"', 1)[0]
        self.assertTrue(description.startswith("第一步&amp;打开 长"))
        self.assertNotIn("<", description)
        self.assertNotIn("x 长", description)
        self.assertEqual(120, len(description.replace("&amp;", "&")))
        self.assertNotIn("og:image", page)

    def test_unpublished_missing_or_invalid_ids_return_plain_index(self):
        for params in ({"software": "902"}, {"solution": "902"}, {"software": "999"}, {"solution": "abc"}, {"software": "-1"}, {}):
            with self.subTest(params=params):
                self.assertEqual(INDEX, self.render(params))

    def test_missing_base_url_omits_absolute_links(self):
        with patch.dict(os.environ, {"DUWUKU_PUBLIC_BASE_URL": ""}):
            page = self.render({"software": "901"})
        self.assertIn("og:title", page)
        self.assertNotIn("og:url", page)
        self.assertNotIn("og:image", page)

    def test_frontend_route_serves_meta(self):
        dist = self.main.ROOT / "dist" / "index.html"
        if not dist.is_file():
            self.skipTest("frontend not built")
        response = self.client.get("/?solution=901")
        self.assertEqual(200, response.status_code)
        self.assertIn("og:title", response.text)
        self.assertEqual("no-cache", response.headers["cache-control"])


if __name__ == "__main__":
    unittest.main()
