import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx
import uvicorn
import threading


class McpNoAuthTests(unittest.TestCase):
    def test_mcp_is_available_without_token_and_site_still_responds(self):
        temp = tempfile.TemporaryDirectory()
        database = Path(temp.name) / "app.sqlite"
        os.environ.update({"APP_ENV": "test", "SECRET_KEY": "test-secret", "DATABASE_URL": f"sqlite:///{database}"})
        os.environ.pop("MCP_BEARER_TOKEN", None)
        import backend.main as main
        main = importlib.reload(main)
        server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            while not server.started:
                pass
            port = server.servers[0].sockets[0].getsockname()[1]
            response = httpx.get(f"http://127.0.0.1:{port}/mcp/")
            self.assertNotEqual(503, response.status_code)
            self.assertNotEqual(401, response.status_code)
            health = httpx.get(f"http://127.0.0.1:{port}/api/health")
            self.assertEqual(200, health.status_code)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            temp.cleanup()
            for key in ("APP_ENV", "SECRET_KEY", "DATABASE_URL", "MCP_BEARER_TOKEN"):
                os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
