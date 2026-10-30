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
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, summary TEXT, description TEXT, category TEXT, vendor TEXT, kind TEXT, published INTEGER, featured INTEGER DEFAULT 0, featured_slot INTEGER DEFAULT 0);
            CREATE TABLE releases (id INTEGER PRIMARY KEY, product_id INTEGER, subapp_id INTEGER, version TEXT, notes TEXT, platform TEXT, size TEXT, created_at TEXT);
            CREATE TABLE subapps (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT);
            CREATE TABLE deliverables (id INTEGER PRIMARY KEY, release_id INTEGER, label TEXT, kind TEXT, url TEXT, object_key TEXT, filename TEXT, platform TEXT, size TEXT, updated_at TEXT);
            CREATE TABLE solutions (id INTEGER PRIMARY KEY, title TEXT, body TEXT, category TEXT, product_id INTEGER, tags TEXT, author TEXT, status TEXT, created_at TEXT);
            CREATE TABLE solution_products (solution_id INTEGER, product_id INTEGER);
            INSERT INTO products VALUES (1, 'Public', 'Summary', 'Description', 'Category', 'Vendor', 'self', 1, 0, 0);
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
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
