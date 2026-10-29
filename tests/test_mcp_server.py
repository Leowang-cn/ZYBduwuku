import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.mcp_server import get_solution, get_software, search_software, search_solutions


class McpQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, summary TEXT, description TEXT, category TEXT, vendor TEXT, kind TEXT, published INTEGER, featured_slot INTEGER DEFAULT 0);
            CREATE TABLE releases (id INTEGER PRIMARY KEY, product_id INTEGER, subapp_id INTEGER, version TEXT, notes TEXT, platform TEXT, size TEXT, created_at TEXT);
            CREATE TABLE subapps (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT);
            CREATE TABLE deliverables (id INTEGER PRIMARY KEY, release_id INTEGER, label TEXT, kind TEXT, url TEXT, object_key TEXT, filename TEXT, platform TEXT, size TEXT, updated_at TEXT);
            CREATE TABLE solutions (id INTEGER PRIMARY KEY, title TEXT, body TEXT, category TEXT, tags TEXT, author TEXT, status TEXT, created_at TEXT);
            CREATE TABLE solution_products (solution_id INTEGER, product_id INTEGER);
            INSERT INTO products VALUES (1,'Public','summary','description','cat','vendor','self',1,0),(2,'Hidden','x','x','x','x','self',0,0);
            INSERT INTO releases VALUES (1,1,NULL,'1.0','notes','','','2026-01-01');
            INSERT INTO deliverables VALUES (1,1,'site','url','https://example.test','','','','',''),(2,1,'installer','file','https://private-storage.test/signed?secret=x','cos/private-key','app.zip','','','');
            INSERT INTO solutions VALUES (1,'Approved','body','cat','["tag"]','author','approved','2026-01-01'),(2,'Pending','secret','cat','[]','author','pending','2026-01-02');
            INSERT INTO solution_products VALUES (1,1),(1,2);
        """)
        conn.commit(); conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_published_filters_and_bounded_search(self):
        self.assertEqual([1], [item['id'] for item in search_software(self.db, 'Public')])
        self.assertEqual([], search_software(self.db, 'Hidden'))
        self.assertEqual([1], [item['id'] for item in search_solutions(self.db)])

    def test_details_hide_unpublished_and_unapproved_links(self):
        self.assertIsNone(get_software(self.db, 2))
        self.assertIsNone(get_solution(self.db, 2))
        result = get_software(self.db, 1)
        deliverables = result['releases'][0]['deliverables']
        self.assertEqual('https://example.test', deliverables[0]['url'])
        self.assertEqual('', deliverables[1]['url'])
        self.assertEqual('/api/download/2', deliverables[1]['download_url'])
        self.assertNotIn('private-storage.test', str(result))
        self.assertNotIn('cos/private-key', str(result))

    def test_queries_are_read_only(self):
        before = self.db.stat().st_size
        get_software(self.db, 1); search_solutions(self.db)
        self.assertEqual(before, self.db.stat().st_size)


if __name__ == '__main__':
    unittest.main()
