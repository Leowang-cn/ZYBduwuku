import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class McpHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "app.sqlite"
        conn = sqlite3.connect(self.database)
        conn.executescript("""
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, summary TEXT, description TEXT, category TEXT, vendor TEXT, kind TEXT, published INTEGER, featured INTEGER DEFAULT 0, featured_slot INTEGER DEFAULT 0, website_url TEXT DEFAULT '');
            CREATE TABLE releases (id INTEGER PRIMARY KEY, product_id INTEGER, subapp_id INTEGER, version TEXT, notes TEXT, platform TEXT, size TEXT, created_at TEXT);
            CREATE TABLE subapps (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT);
            CREATE TABLE deliverables (id INTEGER PRIMARY KEY, release_id INTEGER, label TEXT, kind TEXT, url TEXT, object_key TEXT, filename TEXT, platform TEXT, size TEXT, updated_at TEXT);
            CREATE TABLE solutions (id INTEGER PRIMARY KEY, title TEXT, body TEXT, category TEXT, product_id INTEGER, tags TEXT, author TEXT, status TEXT, created_at TEXT);
            CREATE TABLE solution_products (solution_id INTEGER, product_id INTEGER);
            INSERT INTO products VALUES (1, 'Public', 'Summary', 'Description', 'Category', 'Vendor', 'self', 1, 0, 0, '');
        """)
        conn.commit()
        conn.close()
        os.environ.update({
            "APP_ENV": "test",
            "SECRET_KEY": "integration-test-secret",
            "DATABASE_URL": f"sqlite:///{self.database}",
        })
        import importlib
        main = importlib.import_module("backend.main")
        main = importlib.reload(main)
        self.app = main.app
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            pass
        self.port = self.server.servers[0].sockets[0].getsockname()[1]

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.temp.cleanup()
        for key in ("APP_ENV", "SECRET_KEY", "DATABASE_URL", "MCP_BEARER_TOKEN"):
            os.environ.pop(key, None)

    def test_streamable_http_initialize_tools_and_query_without_auth(self):
        async def exercise():
            async with httpx.AsyncClient() as http:
                async with streamable_http_client(f"http://127.0.0.1:{self.port}/mcp/", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        self.assertIn("2025-", initialized.protocolVersion)
                        tools = await session.list_tools()
                        self.assertEqual({"search_software", "get_software", "search_solutions", "get_solution"}, {t.name for t in tools.tools})
                        result = await session.call_tool("search_software", {"query": "Public"})
                        self.assertIn("Public", str(result.content))
                        await session.call_tool("get_software", {"software_id": 1})
                        await session.call_tool("get_software", {"software_id": 99})
        asyncio.run(exercise())
        conn = sqlite3.connect(self.database)
        self.assertEqual([("software", 1, "get_software")], conn.execute("SELECT kind,target_id,tool FROM mcp_citations").fetchall())
        conn.close()

    def test_mcp_download_link_redirects_and_records_source(self):
        conn = sqlite3.connect(self.database)
        conn.execute("INSERT INTO releases(id,product_id,version,created_at) VALUES(1,1,'1.0','2026-01-01')")
        conn.execute("INSERT INTO deliverables(id,release_id,label,kind,url,object_key,filename) VALUES(1,1,'site','url','https://vendor.example/app','','')")
        conn.commit(); conn.close()
        response = httpx.get(f"http://127.0.0.1:{self.port}/api/download/1?src=mcp")
        self.assertEqual(302, response.status_code)
        self.assertEqual("https://vendor.example/app", response.headers["location"])
        httpx.get(f"http://127.0.0.1:{self.port}/api/download/1")
        conn = sqlite3.connect(self.database)
        self.assertEqual([("external", "mcp"), ("external", "web")], conn.execute("SELECT kind,source FROM events ORDER BY id").fetchall())
        conn.close()



class DetailStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ.update({"APP_ENV": "test", "SECRET_KEY": "integration-test-secret", "DATABASE_URL": f"sqlite:///{Path(self.temp.name) / 'full.sqlite'}"})
        import importlib
        from fastapi.testclient import TestClient
        self.main = importlib.reload(importlib.import_module("backend.main"))
        self.client = TestClient(self.main.app)

    def tearDown(self):
        self.temp.cleanup()
        for key in ("APP_ENV", "SECRET_KEY", "DATABASE_URL"):
            os.environ.pop(key, None)

    def test_solution_and_product_details_include_views_and_ai_citations(self):
        from backend.mcp_server import record_citations
        main = self.main
        product_id = main.write("INSERT INTO products(name,published,created_at) VALUES('Full',1,'2026-01-01')")
        solution_id = main.write("INSERT INTO solutions(title,body,category,tags,author,status,created_at) VALUES('Guide','body','','[]','a','approved','2026-01-01')")
        record_citations(main.DB_PATH, "solution", [solution_id], "get_solution")
        record_citations(main.DB_PATH, "software", [product_id], "get_software")
        self.client.get(f"/api/solutions/{solution_id}")
        solution = self.client.get(f"/api/solutions/{solution_id}").json()
        self.assertEqual((2, 1), (solution["views"], solution["ai_citations"]))
        product = self.client.get(f"/api/products/{product_id}").json()
        self.assertEqual((1, 1), (product["views"], product["ai_citations"]))


if __name__ == "__main__":
    unittest.main()
