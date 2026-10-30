import os
import shutil
import atexit
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

_workspace = Path.cwd() / ".api-test-workspace"
if _workspace.exists(): raise RuntimeError("Test workspace already exists; inspect it before removing")
_workspace.mkdir()
atexit.register(lambda: shutil.rmtree(_workspace))
class Workspace:
    name = str(_workspace)
_tmp = Workspace()
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["STORAGE_PROVIDER"] = "minio"
os.environ["MINIO_ENDPOINT"] = "minio.test:9000"
os.environ["MINIO_ACCESS_KEY"] = "test-access-key"
os.environ["MINIO_SECRET_KEY"] = "test-secret-key"
os.environ["MINIO_BUCKET"] = "software-portal-test"
os.environ["DATA_DIR"] = os.path.join(_tmp.name, "data")
os.environ["UPLOAD_STAGING_DIR"] = os.path.join(_tmp.name, ".upload-staging")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp.name, "data", "test.sqlite")
os.environ["SECRET_KEY"] = "test-only-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
from fastapi.testclient import TestClient
with patch('dotenv.load_dotenv'):
    from backend.main import app, row, write, hash_password, verify_password, init_db, client_ip
from backend import storage
from starlette.requests import Request


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.remote = MagicMock()
        self.remote.bucket_exists.return_value = True
        from minio.error import S3Error
        self.remote.get_bucket_policy.side_effect = S3Error("NoSuchBucketPolicy", "none", "/", "r", "h", None)
        self.remote.remove_object = MagicMock()
        self.remote.fput_object = MagicMock()
        self.remote.presigned_get_object.return_value = "https://storage.example/private?signature=test"
        self.client_patch = patch.object(storage, "_client", return_value=self.remote)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        write("DELETE FROM settings WHERE key LIKE 'rate:%'")
        self.client = TestClient(app)
        self.client.post("/api/admin/login", json={"password":"test-admin-password"})
        self.headers={"X-CSRF-Token":self.client.cookies.get("portal_csrf")}
        created=self.client.post("/api/admin/products",headers=self.headers,json={"name":"Test product","published":True})
        self.assertEqual(created.status_code,200,created.text)
        self.pid=created.json()["id"]
        self.client.cookies.clear()

    def test_solution_status_transitions(self):
        result = self.client.post('/api/solutions', data={'title': 'Status transition guide', 'body': 'Meaningful solution content'})
        self.assertEqual(result.status_code, 200, result.text)
        sid = result.json()['id']
        endpoint = f'/api/admin/solutions/{sid}/status'
        self.assertEqual(self.client.post(endpoint, data={'status': 'approved'}).status_code, 401)
        headers = self.login()
        self.assertEqual(self.client.post(endpoint, headers=headers, data={'status': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/solutions/999999/status', headers=headers, data={'status': 'approved'}).status_code, 404)
        for status in ('approved', 'rejected', 'pending', 'approved'):
            with self.subTest(status=status):
                write('UPDATE solutions SET featured_slot=1,featured_at=? WHERE id=?', ('2026-01-01', sid))
                response = self.client.post(endpoint, headers=headers, data={'status': status, 'reason': '审核备注'})
                self.assertEqual(response.status_code, 200, response.text)
                saved = row('SELECT status,reject_reason,featured_slot,featured_at FROM solutions WHERE id=?', (sid,))
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['reject_reason'], '审核备注')
                self.assertEqual(saved['featured_slot'], 1 if status == 'approved' else 0)
                self.assertEqual(saved['featured_at'], '2026-01-01' if status == 'approved' else None)
                self.assertEqual(self.client.get(f'/api/solutions/{sid}').status_code, 200 if status == 'approved' else 404)

    def test_admin_edit_solution_preserves_status(self):
        result = self.client.post('/api/solutions', data={'title': 'Original solution', 'body': 'Original meaningful body'})
        sid = result.json()['id']
        payload = {'title': 'Updated solution', 'body': '<!--portal-rich--><h2>Updated rich content</h2>', 'tags': [' custom ', 'custom'], 'product_ids': [self.pid], 'author': 'Editor'}
        self.assertEqual(self.client.post(f'/api/admin/solutions/{sid}', data=payload).status_code, 401)
        headers = self.login()
        for status in ('pending', 'approved', 'rejected'):
            self.client.post(f'/api/admin/solutions/{sid}/status', headers=headers, data={'status': status})
            response = self.client.post(f'/api/admin/solutions/{sid}', headers=headers, data=payload)
            self.assertEqual(response.status_code, 200, response.text)
            saved = row('SELECT title,body,tags,status FROM solutions WHERE id=?', (sid,))
            self.assertEqual(saved['title'], 'Updated solution')
            self.assertIn('<h2>', saved['body'])
            self.assertEqual(saved['tags'], '["custom"]')
            self.assertEqual(saved['status'], status)
        self.assertEqual(self.client.post(f'/api/admin/solutions/{sid}', headers=headers, data={**payload, 'product_ids': [999999]}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/solutions/999999', headers=headers, data=payload).status_code, 404)

    def test_admin_solution_preview_includes_private_body_and_attachments(self):
        result = self.client.post('/api/solutions', data={'title': 'Preview solution', 'body': '## Full preview\n\n**Complete content**', 'tags': ['preview'], 'product_ids': [self.pid]}, files={'files': ('guide.txt', b'attachment preview', 'text/plain')})
        self.assertEqual(result.status_code, 200, result.text)
        sid = result.json()['id']
        self.assertEqual(self.client.get(f'/api/solutions/{sid}').status_code, 404)
        self.assertEqual(self.client.get('/api/admin/data').status_code, 401)
        self.login()
        item = next(s for s in self.client.get('/api/admin/data').json()['solutions'] if s['id'] == sid)
        self.assertEqual(item['status'], 'pending')
        self.assertIn('**Complete content**', item['body'])
        self.assertEqual(item['tags'], ['preview'])
        self.assertEqual(item['product_ids'], [self.pid])
        self.assertEqual(item['files'][0]['filename'], 'guide.txt')
        self.assertEqual(item['files'][0]['content_type'], 'text/plain')
        url = f"/api/files/{item['files'][0]['id']}"
        self.assertEqual(self.client.get(url, follow_redirects=False).status_code, 307)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_solution_rich_tags_and_legacy_migration(self):
        from backend.main import db
        from backend.richtext import RICH_PREFIX
        def submit(body, tags=None):
            write("DELETE FROM settings WHERE key LIKE 'rate:%'")
            return self.client.post('/api/solutions', data={'title': 'Rich solution', 'body': body, **({'tags': tags} if tags is not None else {})})
        markdown = '## Markdown\n\n**Preserved text**'
        result = submit(markdown, [' alpha ', 'beta', 'alpha'])
        self.assertEqual(result.status_code, 200, result.text)
        sid = result.json()['id']
        self.assertEqual(row('SELECT body,tags FROM solutions WHERE id=?', (sid,)), {'body': markdown, 'tags': '["alpha", "beta"]'})
        h = self.login()
        admin = next(s for s in self.client.get('/api/admin/data').json()['solutions'] if s['id'] == sid)
        self.assertEqual(admin['tags'], ['alpha', 'beta'])
        self.client.post(f'/api/admin/solutions/{sid}/status', headers=h, data={'status': 'approved'})
        self.assertEqual(self.client.get(f'/api/solutions/{sid}').json()['tags'], ['alpha', 'beta'])
        self.assertEqual(next(s for s in self.client.get('/api/bootstrap').json()['solutions'] if s['id'] == sid)['tags'], ['alpha', 'beta'])
        for tags in ([' '], ['x' * 31], [str(i) for i in range(21)], ['bad\x00tag']):
            self.assertEqual(submit(markdown, tags).status_code, 400)
        self.assertEqual(submit(RICH_PREFIX + '<p><br></p><script>hidden text</script>').status_code, 400)
        result = submit(RICH_PREFIX + '<p onclick="bad()">Meaningful visible content</p><script>evil()</script><img src="javascript:bad()">')
        self.assertEqual(result.status_code, 200, result.text)
        saved = row('SELECT body FROM solutions WHERE id=?', (result.json()['id'],))['body']
        for forbidden in ('onclick', '<script', 'javascript:'):
            self.assertNotIn(forbidden, saved)
        legacy = submit(markdown).json()['id']
        with db() as c:
            c.execute('ALTER TABLE solutions DROP COLUMN tags')
        init_db()
        init_db()
        self.assertEqual(row('SELECT tags FROM solutions WHERE id=?', (legacy,))['tags'], '[]')

    def test_solution_image_ownership_approval_and_expiry(self):
        import uuid
        from backend.main import cleanup_expired_draft_media
        from backend.richtext import RICH_PREFIX
        token, other = str(uuid.uuid4()), str(uuid.uuid4())
        from io import BytesIO
        from PIL import Image
        buffer = BytesIO()
        Image.new('RGB', (1, 1)).save(buffer, format='PNG')
        image = buffer.getvalue()
        def upload(token=token, content=image, name='image.png'):
            return self.client.post('/api/solution-media', data={'draft_token': token}, files={'file': (name, content, 'image/png')})
        self.assertEqual(upload(token='-' * 36).status_code, 400)
        self.assertEqual(upload(content=b'fake').status_code, 415)
        broken = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
        self.assertEqual(upload(content=broken).status_code, 415)
        oversized = MagicMock()
        oversized.__enter__.return_value = oversized
        oversized.format, oversized.width, oversized.height = 'PNG', 5001, 5000
        with patch('backend.main.Image.open', return_value=oversized):
            self.assertEqual(upload().status_code, 415)
        self.assertEqual(upload(name='image.svg').status_code, 415)
        self.assertEqual(upload(content=b'x' * (5 * 1024 * 1024 + 1)).status_code, 413)
        first, unused = upload(), upload()
        self.assertEqual(first.status_code, 200, first.text)
        first, unused = first.json(), unused.json()
        url, mid = first['url'], first['id']
        self.assertEqual(self.client.get(url, follow_redirects=False).status_code, 307)
        self.assertEqual(self.client.get(url.replace(token, other)).status_code, 404)
        def submit(owner):
            write("DELETE FROM settings WHERE key LIKE 'rate:%'")
            return self.client.post('/api/solutions', data={'title': 'Image solution', 'body': RICH_PREFIX + f'<p>Image solution text</p><img src="{url}">', 'draft_token': owner})
        self.assertEqual(submit(other).status_code, 400)
        self.assertEqual(submit('').status_code, 400)
        result = submit(token)
        self.assertEqual(result.status_code, 200, result.text)
        sid = result.json()['id']
        final = f'/api/solution-media/{mid}'
        self.assertIn(final, row('SELECT body FROM solutions WHERE id=?', (sid,))['body'])
        self.assertIsNone(row('SELECT solution_id FROM solution_media WHERE id=?', (unused['id'],))['solution_id'])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(final).status_code, 404)
        self.assertEqual(submit(token).status_code, 400)
        h = self.login()
        self.assertEqual(self.client.get(final, follow_redirects=False).status_code, 307)
        self.client.post(f'/api/admin/solutions/{sid}/status', headers=h, data={'status': 'approved'})
        self.client.cookies.clear()
        self.assertEqual(self.client.get(final, follow_redirects=False).status_code, 307)
        h = self.login()
        self.client.post(f'/api/admin/solutions/{sid}/status', headers=h, data={'status': 'rejected'})
        self.client.cookies.clear()
        self.assertEqual(self.client.get(final).status_code, 404)
        write("UPDATE solution_media SET expires_at='2000-01-01' WHERE id=?", (unused['id'],))
        self.assertEqual(self.client.get(unused['url']).status_code, 404)
        url = unused['url']
        self.assertEqual(submit(token).status_code, 400)
        cleanup_expired_draft_media()
        self.assertIsNone(row('SELECT id FROM solution_media WHERE id=?', (unused['id'],)))
        self.assertEqual(self.client.post('/api/admin/upload', data={'kind': 'rich-image', 'draft_token': token}, files={'file': ('image.png', image, 'image/png')}).status_code, 401)
        with patch('backend.main.rate', side_effect=__import__('fastapi').HTTPException(429, 'limited')):
            self.assertEqual(upload().status_code, 429)

    def test_deliverable_metadata_roundtrip_and_validation(self):
        h = self.login()
        uploaded = self.client.post('/api/admin/upload', headers=h, files={'file': ('opaque.zip', b'package', 'application/zip')}).json()
        metadata = {'platform': 'Linux', 'size': '12 MB', 'updated_at': '2026-10-20T09:10:00Z'}
        def release():
            return {'version': '3.1', 'platform': 'Windows', 'size': 'old size', 'deliverables': [
                {'kind': 'file', **uploaded, **metadata},
                {'kind': 'url', 'url': 'https://example.com/download', **metadata},
            ]}
        payload = {'id': self.pid, 'name': 'Hierarchy', 'published': True, 'platforms': ['Windows', 'Linux'], 'releases': [release()], 'subapps': [{'name': 'Child', 'releases': [release()]}]}
        def save():
            return self.client.post('/api/admin/products', headers=h, json=payload)
        self.assertEqual(save().status_code, 200)
        product = next(p for p in self.client.get('/api/admin/data').json()['products'] if p['id'] == self.pid)
        payload['releases'], payload['subapps'] = product['releases'], product['subapps']
        ids = [[d['id'] for d in group['releases'][0]['deliverables']] for group in (product, product['subapps'][0])]
        self.assertEqual(save().status_code, 200)
        # Legacy clients may resend URLs without their database ID.
        payload['releases'][0]['deliverables'][1].pop('id')
        self.assertEqual(save().status_code, 200)
        self.client.cookies.clear()
        public = self.client.get(f'/api/products/{self.pid}').json()
        listing = next(p for p in self.client.get('/api/bootstrap').json()['products'] if p['id'] == self.pid)
        for result in (public, listing):
            for index, group in enumerate((result, result['subapps'][0])):
                r = group['releases'][0]
                self.assertEqual(r['version'], '3.1')
                self.assertEqual(r['platform'], 'Windows')
                self.assertEqual([d['id'] for d in r['deliverables']], ids[index])
                for d in r['deliverables']:
                    self.assertEqual({key: d[key] for key in metadata}, metadata)
                    self.assertNotIn('object_key', d)
                self.assertEqual(r['deliverables'][0]['filename'], 'opaque.zip')
        h = self.login()
        target = payload['subapps'][0]['releases'][0]['deliverables'][0]
        for field, value in [('platform', 'Android'), ('size', 123), ('updated_at', 'yesterday')]:
            previous = target[field]
            target[field] = value
            self.assertEqual(save().status_code, 400)
            target[field] = previous
        url = payload['releases'][0]['deliverables'][1]
        url['url'] = 'javascript:alert(1)'
        self.assertEqual(save().status_code, 400)
        url['url'] = 'https://example.com/download'
        target['id'] = ids[0][0]
        self.assertEqual(save().status_code, 400)
        target['id'] = ids[1][0]
        payload['releases'][0]['deliverables'].append(dict(url))
        self.assertEqual(save().status_code, 400)
        payload['releases'][0]['deliverables'].pop()
        for field in metadata:
            target.pop(field)
        target.pop('object_key')
        self.assertEqual(save().status_code, 200)
        retained = row('SELECT platform,size,updated_at,filename FROM deliverables WHERE id=?', (target['id'],))
        self.assertEqual(retained, {**metadata, 'filename': 'opaque.zip'})
        payload['releases'][0].pop('deliverables')
        self.assertEqual(save().status_code, 200)
        payload['releases'][0]['deliverables'] = []
        self.assertEqual(save().status_code, 200)
        self.assertEqual(row('SELECT count(*) n FROM deliverables WHERE release_id=?', (payload['releases'][0]['id'],))['n'], 0)

    def test_deliverable_legacy_backfill_is_additive_and_idempotent(self):
        import sqlite3
        path = _workspace / 'legacy.sqlite'
        self.addCleanup(lambda: [p.unlink() for p in _workspace.glob('legacy.sqlite*')])
        with sqlite3.connect(path) as c:
            c.executescript("""
                CREATE TABLE releases(id INTEGER PRIMARY KEY,product_id INTEGER,subapp_id INTEGER,version TEXT,notes TEXT,created_at TEXT,platform TEXT,size TEXT);
                CREATE TABLE deliverables(id INTEGER PRIMARY KEY,release_id INTEGER,label TEXT,kind TEXT,url TEXT DEFAULT '',object_key TEXT DEFAULT '',filename TEXT DEFAULT '');
                INSERT INTO releases VALUES(1,1,NULL,'1','notes','2025-01-02T03:04:05+00:00','Windows','45 MB');
                INSERT INTO releases VALUES(2,1,7,'2','child','2025-02-03T04:05:06+00:00','Linux','6 MB');
                INSERT INTO deliverables VALUES(10,1,'old','file','','opaque-key','unrelated.bin');
                INSERT INTO deliverables VALUES(11,2,'site','url','https://example.com','','');
            """)
        with patch('backend.main.DB_PATH', path):
            init_db()
            for did, platform, size, updated in [(10, 'Windows', '45 MB', '2025-01-02T03:04:05+00:00'), (11, 'Linux', '6 MB', '2025-02-03T04:05:06+00:00')]:
                d = row('SELECT * FROM deliverables WHERE id=?', (did,))
                self.assertEqual((d['platform'], d['size'], d['updated_at']), (platform, size, updated))
            self.assertEqual(row('SELECT filename,object_key FROM deliverables WHERE id=10'), {'filename': 'unrelated.bin', 'object_key': 'opaque-key'})
            write("UPDATE deliverables SET platform='',size='custom',updated_at='' WHERE id=10")
            init_db()
            self.assertEqual(row('SELECT platform,size,updated_at FROM deliverables WHERE id=10'), {'platform': '', 'size': 'custom', 'updated_at': ''})
            self.assertEqual(row('SELECT count(*) n FROM deliverables')['n'], 2)

    def test_product_and_subapp_platform_releases_are_separate(self):
        self.client.post('/api/admin/login', json={'password': 'test-admin-password'})
        headers = {'X-CSRF-Token': self.client.cookies.get('portal_csrf')}
        releases = lambda: [
            {'version': '1.0', 'platform': 'Windows', 'notes': 'Windows history'},
            {'version': '2.0', 'platform': 'Windows', 'notes': 'Windows latest'},
            {'version': '1.5', 'platform': 'iPadOS', 'notes': 'iPad latest'},
        ]
        result = self.client.post('/api/admin/products', headers=headers, json={
            'id': self.pid, 'name': 'Multi-platform suite', 'published': True,
            'platforms': ['Windows', 'iPadOS'],
            'releases': releases(),
            'subapps': [{'name': name, 'releases': releases()} for name in ('Editor', 'Viewer')],
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.client.cookies.clear()
        detail = self.client.get(f'/api/products/{self.pid}').json()
        listing = next(p for p in self.client.get('/api/bootstrap').json()['products'] if p['id'] == self.pid)
        for product in (detail, listing):
            self.assertEqual(len(product['releases']), 3)
            self.assertEqual(len(product['subapps']), 2)
            parent_ids = {r['id'] for r in product['releases']}
            seen_ids = set(parent_ids)
            for subapp in product['subapps']:
                self.assertEqual(len(subapp['releases']), 3)
                ids = {r['id'] for r in subapp['releases']}
                self.assertTrue(seen_ids.isdisjoint(ids))
                seen_ids.update(ids)
            for group in [product, *product['subapps']]:
                windows = [r['version'] for r in group['releases'] if r['platform'] == 'Windows']
                self.assertEqual(windows, ['2.0', '1.0'])
                self.assertEqual([r['version'] for r in group['releases'] if r['platform'] == 'iPadOS'], ['1.5'])

    def test_admin_solution_product_associations_and_validation(self):
        h = self.login()
        submitted = self.client.post('/api/solutions', data={'title': 'Useful guide', 'body': 'A useful guide about this product', 'product_ids': str(self.pid)})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        sid = row('SELECT id FROM solutions ORDER BY id DESC LIMIT 1')['id']
        endpoint = f'/api/admin/solutions/{sid}/products'
        self.assertEqual(self.client.post(endpoint, headers=h, json=[self.pid, 999999]).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/data').json()['solutions'][0]['product_ids'], [self.pid])
        self.assertEqual(self.client.post(endpoint, headers=h, json=[]).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/data').json()['solutions'][0]['product_ids'], [])
        self.assertIsNone(row('SELECT product_id FROM solutions WHERE id=?', (sid,))['product_id'])
        self.assertEqual(self.client.post(endpoint, headers=h, json=[self.pid, self.pid]).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/data').json()['solutions'][0]['product_ids'], [self.pid])

    def test_release_platform_and_uploaded_object_validation(self):
        h = self.login()
        payload = {'id': self.pid, 'name': 'Package', 'platforms': ['Windows'], 'releases': [{'version': '1', 'platform': 'Linux'}]}
        self.assertEqual(self.client.post('/api/admin/products', headers=h, json=payload).status_code, 400)
        payload['releases'][0]['platform'] = 'Windows'
        payload['releases'][0]['deliverables'] = [{'kind': 'file', 'object_key': 'not-uploaded'}]
        self.assertEqual(self.client.post('/api/admin/products', headers=h, json=payload).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/upload', headers=h, data={'kind':'screenshot'}, files={'file':('shot.png',b'image','image/png')}).status_code, 400)

    def test_legacy_screenshot_cleanup_retries_and_preserves_shared_objects(self):
        from backend.main import cleanup_product_screenshots
        write('INSERT INTO screenshots(product_id,object_key,filename) VALUES(?,?,?)', (self.pid,'legacy-shot','shot.png'))
        with patch.object(storage, 'delete', side_effect=RuntimeError('storage unavailable')), patch('backend.main.logging.exception'):
            cleanup_product_screenshots()
        self.assertIsNotNone(row('SELECT id FROM screenshots WHERE object_key=?', ('legacy-shot',)))
        with patch.object(storage, 'delete') as remove:
            cleanup_product_screenshots()
            remove.assert_called_once_with('legacy-shot')
        self.assertIsNone(row('SELECT id FROM screenshots WHERE object_key=?', ('legacy-shot',)))

    def test_subapp_package_upload_and_removal(self):
        h = self.login()
        upload = self.client.post('/api/admin/upload', headers=h, files={'file': ('subapp.zip', b'package', 'application/zip')})
        self.assertEqual(upload.status_code, 200, upload.text)
        payload = {'id': self.pid, 'name': 'Suite', 'platforms': ['Windows'], 'subapps': [{'name': 'Editor', 'releases': [{'version': '1', 'platform': 'Windows', 'deliverables': [{'kind': 'file', **upload.json()}]}]}]}
        self.assertEqual(self.client.post('/api/admin/products', headers=h, json=payload).status_code, 200)
        product = next(p for p in self.client.get('/api/admin/data').json()['products'] if p['id'] == self.pid)
        release = product['subapps'][0]['releases'][0]
        self.assertEqual(release['deliverables'][0]['filename'], 'subapp.zip')
        self.assertEqual(release['deliverables'][0]['platform'], 'Windows')
        self.assertEqual(release['deliverables'][0]['size'], release['size'])
        self.assertEqual(release['deliverables'][0]['updated_at'], release['created_at'])
        payload['subapps'] = product['subapps']
        payload['subapps'][0]['releases'][0]['deliverables'] = []
        self.assertEqual(self.client.post('/api/admin/products', headers=h, json=payload).status_code, 200)
        product = next(p for p in self.client.get('/api/admin/data').json()['products'] if p['id'] == self.pid)
        self.assertEqual(product['subapps'][0]['releases'][0]['deliverables'], [])

    def test_feedback_comment_images_and_public_ip(self):
        result = self.client.post('/api/feedback', data={'product_id': self.pid, 'body': 'Feedback with public IP', 'nickname': 'Example'})
        self.assertEqual(result.status_code, 200, result.text)
        fid = row('SELECT id FROM feedback WHERE product_id=? ORDER BY id DESC LIMIT 1', (self.pid,))['id']
        write("UPDATE feedback SET status='approved' WHERE id=?", (fid,))
        result = self.client.post(f'/api/feedback/{fid}/comments', data={'body': 'Image comment', 'nickname': 'Reader'}, files=[('files', ('example.png', b'example-image', 'image/png'))])
        self.assertEqual(result.status_code, 200, result.text)
        cid = result.json()['id']
        image_id = row('SELECT id FROM feedback_comment_files WHERE comment_id=?', (cid,))['id']
        public_feedback = lambda: next(f for f in self.client.get(f'/api/products/{self.pid}').json()['feedback'] if f['id'] == fid)
        self.assertEqual(public_feedback()['ip'], 'testclient')
        self.assertEqual(public_feedback()['comments'], [])
        self.assertEqual(self.client.get(f'/api/feedback-comment-files/{image_id}').status_code, 404)
        self.client.post('/api/admin/login', json={'password': 'test-admin-password'})
        headers = {'X-CSRF-Token': self.client.cookies.get('portal_csrf')}
        self.assertEqual(self.client.get(f'/api/admin/feedback-comment-files/{image_id}', follow_redirects=False).status_code, 307)
        self.assertEqual(self.client.post(f'/api/admin/feedback-comments/{cid}/status', headers=headers, data={'status': 'approved'}).status_code, 200)
        comment = public_feedback()['comments'][0]
        self.assertEqual(comment['ip'], 'testclient')
        self.assertEqual(comment['files'][0]['id'], image_id)
        self.assertEqual(self.client.get(f'/api/feedback-comment-files/{image_id}', follow_redirects=False).status_code, 307)
        write("UPDATE feedback SET status='pending' WHERE id=?", (fid,))
        self.assertEqual(self.client.get(f'/api/feedback-comment-files/{image_id}').status_code, 404)

    def test_solution_featured_slots(self):
        from backend.main import now
        ids = [write("INSERT INTO solutions(title,body,status,created_at) VALUES(?,?,'approved',?)", (f"Featured {i}", "Test body", now())) for i in range(2)]
        endpoint = lambda sid: f"/api/admin/solutions/{sid}/featured"
        self.assertEqual(self.client.post(endpoint(ids[0]), data={"featured_slot":1}).status_code, 401)
        self.client.post("/api/admin/login", json={"password":"test-admin-password"})
        headers = {"X-CSRF-Token":self.client.cookies.get("portal_csrf")}
        for sid in ids:
            self.assertEqual(self.client.post(endpoint(sid), headers=headers, data={"featured_slot":1}).status_code, 200)
        self.assertEqual(row("SELECT featured_slot FROM solutions WHERE id=?", (ids[0],))["featured_slot"], 0)
        self.assertEqual(self.client.post(endpoint(ids[1]), headers=headers, data={"featured_slot":4}).status_code, 400)
        public = self.client.get("/api/bootstrap").json()["solutions"]
        self.assertEqual(next(s for s in public if s["id"] == ids[1])["featured_slot"], 1)
        write("UPDATE solutions SET status='pending' WHERE id=?", (ids[1],))
        self.assertNotIn(ids[1], [s["id"] for s in self.client.get("/api/bootstrap").json()["solutions"]])
        self.assertEqual(self.client.post(endpoint(ids[1]), headers=headers, data={"featured_slot":2}).status_code, 400)
        self.assertEqual(self.client.post(endpoint(ids[0]), headers=headers, data={"featured_slot":0}).status_code, 200)

    def test_health_and_public_bootstrap(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertTrue(self.client.get("/api/bootstrap").json()["products"])

    def test_login_csrf_and_admin_data(self):
        self.assertEqual(self.client.get("/api/admin/data").status_code, 401)
        self.assertEqual(self.client.post("/api/admin/login", json={"password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/admin/login", json={"password": "test-admin-password"}).status_code, 200)
        self.assertTrue(self.client.get("/api/admin/data").json()["products"])
        self.assertEqual(self.client.post("/api/admin/categories", data={"name": "新分类"}).status_code, 403)
        token = self.client.cookies.get("portal_csrf")
        self.assertEqual(self.client.post("/api/admin/categories", data={"name": "新分类"}, headers={"X-CSRF-Token": token}).status_code, 200)

    def test_submission_is_private_until_approved_and_comments_work(self):
        payload = {"title": "团队文件整理方案", "body": "第一步整理目录，第二步规范命名，再自动归档。", "category": "效率工具"}
        result = self.client.post("/api/solutions", data=payload)
        self.assertEqual(result.status_code, 200)
        sid = result.json()["id"]
        self.assertNotIn(sid, [s["id"] for s in self.client.get("/api/bootstrap").json()["solutions"]])
        self.client.post("/api/admin/login", json={"password": "test-admin-password"})
        token = self.client.cookies.get("portal_csrf")
        self.client.post(f"/api/admin/solutions/{sid}/status", data={"status": "approved"}, headers={"X-CSRF-Token": token})
        self.assertIn(sid, [s["id"] for s in self.client.get("/api/bootstrap").json()["solutions"]])
        submitted = self.client.post(f"/api/solutions/{sid}/comments", data={"body": "很实用"})
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["status"], "pending")
        self.assertEqual(self.client.get(f"/api/solutions/{sid}").json()["comments"], [])
        self.assertEqual(self.client.post(f"/api/admin/comments/{submitted.json()['id']}/status", data={"status": "approved"}, headers={"X-CSRF-Token": token}).status_code, 200)
        comments=self.client.get(f"/api/solutions/{sid}").json()["comments"]
        self.assertEqual(len(comments),1)
        self.assertNotIn("ip",comments[0])

    def test_feedback_is_admin_only_and_resource_clicks_counted(self):
        p = self.client.get("/api/bootstrap").json()["products"][0]
        self.assertEqual(self.client.post("/api/feedback", data={"product_id": p["id"], "body": "希望增加批量处理。"}).status_code, 200)
        self.assertTrue(self.client.get("/api/bootstrap").json()["products"])
        self.assertEqual(self.client.get(f"/api/products/{self.pid}").json()["feedback"], [])
        self.assertEqual(self.client.get(f"/api/download/999999", follow_redirects=False).status_code, 404)
        self.client.post("/api/admin/login", json={"password": "test-admin-password"})
        admin = self.client.get("/api/admin/data").json()
        self.assertEqual(admin["stats"]["downloads"], 0)
        self.assertEqual(admin["feedback"][0]["body"], "希望增加批量处理。")
 
    def login(self):
        self.client.post("/api/admin/login",json={"password":"test-admin-password"})
        return {"X-CSRF-Token":self.client.cookies.get("portal_csrf")}

    def test_no_demo_and_salted_hashing(self):
        self.assertFalse(row("SELECT id FROM products WHERE name='文件小助手'"))
        encoded=hash_password("same-password")
        self.assertNotEqual(encoded,hash_password("same-password"))
        self.assertTrue(verify_password("same-password",encoded))
        self.assertFalse(verify_password("wrong",encoded))
        import hashlib
        write("UPDATE settings SET value=? WHERE key='admin_hash'",(hashlib.sha256(b"test-admin-password").hexdigest(),))
        self.login()
        self.assertTrue(row("SELECT value FROM settings WHERE key='admin_hash'")["value"].startswith("pbkdf2_sha256$"))
        self.assertFalse(row("SELECT id FROM products WHERE name='文件小助手'"))

    def test_articles_taxonomies_feedback_and_csrf_delete(self):
        h=self.login()
        for kind in ("platform","type"):
            self.assertEqual(self.client.post("/api/admin/taxonomies",data={"kind":kind,"name":"Web"},headers=h).status_code,200)
        self.assertEqual(self.client.post("/api/admin/taxonomies",data={"kind":"form","name":"Web"},headers=h).status_code,400)
        aid=self.client.post("/api/admin/articles",headers=h,data={"title":"Article","body":"Body","published":"true"}).json()["id"]
        self.assertIn(aid,[a["id"] for a in self.client.get("/api/bootstrap").json()["articles"]])
        self.client.post("/api/admin/articles",headers=h,data={"article_id":aid,"title":"Edited","body":"New body","published":"false"})
        self.assertNotIn(aid,[a["id"] for a in self.client.get("/api/bootstrap").json()["articles"]])
        self.assertEqual(self.client.delete(f"/api/admin/articles/{aid}").status_code,403)
        result=self.client.post("/api/feedback",data={"product_id":self.pid,"body":"Screenshot feedback"},files={"files":("screen.png",b"image","image/png")})
        self.assertEqual(result.status_code,200,result.text)
        feedback=self.client.get("/api/admin/data").json()["feedback"][0]
        fid=feedback["files"][0]["id"]
        self.assertEqual(self.client.get(f"/api/admin/feedback-files/{fid}", follow_redirects=False).status_code,307)
        self.assertEqual(self.client.post(f"/api/admin/feedback/{feedback['id']}/handled",headers=h,data={"handled":"true"}).status_code,200)
        self.assertEqual(self.client.get("/api/admin/data").json()["feedback"][0]["handled"],1)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(f"/api/admin/feedback-files/{fid}").status_code,401)

    def test_minio_upload_file_edit_and_authorized_signature(self):
        h=self.login()
        remote=MagicMock()
        remote.bucket_exists.return_value=True
        from minio.error import S3Error
        remote.get_bucket_policy.side_effect=S3Error("NoSuchBucketPolicy","none","/","r","h",None)
        remote.presigned_get_object.return_value="https://storage.example/private?signature=test"
        with patch.object(storage,"_client",return_value=remote):
            uploaded=self.client.post("/api/admin/upload",headers=h,files={"file":("package.zip",b"archive","application/zip")})
            self.assertEqual(uploaded.status_code,200,uploaded.text)
            obj=uploaded.json()
            self.assertFalse((Path(os.environ["UPLOAD_STAGING_DIR"])/obj["object_key"]).exists())
            payload={"id":self.pid,"name":"Remote product","published":True,"platform":"Windows","platforms":["Windows"],"releases":[{"version":"1.0.0","platform":"Windows","deliverables":[{"kind":"file",**obj},{"kind":"url","url":"https://example.com"}]}]}
            self.assertEqual(self.client.post("/api/admin/products",headers=h,json=payload).status_code,200)
            product=next(p for p in self.client.get("/api/admin/data").json()["products"] if p["id"]==self.pid)
            payload["releases"]=product["releases"]
            self.client.post("/api/admin/products",headers=h,json=payload)
            public=next(p for p in self.client.get("/api/bootstrap").json()["products"] if p["id"]==self.pid)
            self.assertEqual(len(public["releases"]),1)
            self.assertEqual(len(public["releases"][0]["deliverables"]),2)
            self.assertEqual(public["releases"][0]["deliverables"][0]["kind"],"file")
            self.assertNotIn("object_key",public["releases"][0]["deliverables"][0])
            did=public["releases"][0]["deliverables"][0]["id"]
            self.assertEqual(self.client.get(f"/api/download/{did}",follow_redirects=False).status_code,307)
            self.assertEqual(remote.presigned_get_object.call_args.kwargs["expires"].total_seconds(),300)
            payload["published"]=False
            self.client.post("/api/admin/products",headers=h,json=payload)
            remote.presigned_get_object.reset_mock()
            self.assertEqual(self.client.get(f"/api/download/{did}",follow_redirects=False).status_code,404)
            remote.presigned_get_object.assert_not_called()
            remote.fput_object.assert_called_once()

    def test_icon_upload_association_and_public_access(self):
        h = self.login()
        image = b"\x89PNG\r\n\x1a\n" + b"sample"
        self.assertEqual(self.client.post("/api/admin/upload", data={"kind":"icon"}, files={"file":("bad.svg",b"<svg/>","image/svg+xml")}, headers=h).status_code,415)
        self.assertEqual(self.client.post("/api/admin/upload", data={"kind":"icon"}, files={"file":("bad.png",b"not an image","image/png")}, headers=h).status_code,415)
        uploaded = self.client.post("/api/admin/upload", data={"kind":"icon"}, files={"file":("logo.png",image,"image/png")}, headers=h)
        self.assertEqual(uploaded.status_code,200,uploaded.text)
        key = uploaded.json()["object_key"]
        self.assertEqual(self.client.post("/api/admin/products",headers=h,json={"name":"invalid","icon_object_key":"unknown"}).status_code,400)
        self.assertEqual(self.client.post("/api/admin/products",headers=h,json={"name":"With icon","icon":"✧","icon_object_key":key,"published":True}).status_code,200)
        icon_product = next(p for p in self.client.get("/api/bootstrap").json()["products"] if p["name"]=="With icon")
        self.assertEqual(icon_product["icon"],"✧")
        self.assertEqual(icon_product["icon_image_url"],f"/api/product-icons/{icon_product['id']}")
        self.assertNotIn("icon_object_key",icon_product)
        self.assertEqual(self.client.get(icon_product["icon_image_url"],follow_redirects=False).status_code,307)
        self.assertEqual(self.remote.presigned_get_object.call_args.kwargs["response_headers"]["response-content-type"],"image/png")
        pid=icon_product["id"]
        self.client.post("/api/admin/products",headers=h,json={"id":pid,"name":"Updated","published":True})
        self.assertEqual(row("SELECT icon_object_key FROM products WHERE id=?",(pid,))["icon_object_key"],key)
        self.client.post("/api/admin/products",headers=h,json={"id":pid,"name":"Hidden","published":False})
        self.assertEqual(self.client.get(f"/api/product-icons/{pid}").status_code,404)
        self.client.post("/api/admin/products",headers=h,json={"id":pid,"name":"No icon","published":True,"icon_object_key":""})
        self.assertEqual(self.client.get(f"/api/product-icons/{pid}").status_code,404)

    def test_featured_slots_order_replacement_and_publish_filter(self):
        h=self.login()
        ids=[]
        for slot in (1,2,3):
            response=self.client.post("/api/admin/products",headers=h,json={"name":f"Featured {slot}","featured_slot":slot,"published":True})
            self.assertEqual(response.status_code,200,response.text)
            ids.append(response.json()["id"])
        self.assertEqual(self.client.post("/api/admin/products",headers=h,json={"name":"Invalid","featured_slot":4}).status_code,422)
        public=self.client.get("/api/bootstrap").json()["products"]
        self.assertEqual([p["id"] for p in public[:3]],ids)
        replacement=self.client.post("/api/admin/products",headers=h,json={"name":"Replacement","featured_slot":2,"published":True}).json()["id"]
        self.assertEqual(row("SELECT featured_slot FROM products WHERE id=?",(ids[1],))["featured_slot"],0)
        public=self.client.get("/api/bootstrap").json()["products"]
        self.assertEqual([p["id"] for p in public[:3]],[ids[0],replacement,ids[2]])
        self.client.post("/api/admin/products",headers=h,json={"id":ids[0],"name":"Draft","featured_slot":1,"published":False})
        public=self.client.get("/api/bootstrap").json()["products"]
        self.assertEqual([p["id"] for p in public if p["featured"]],[replacement,ids[2]])
        self.assertEqual(row("SELECT featured_slot FROM products WHERE id=?",(ids[0],))["featured_slot"],1)

    def test_draft_rich_images_bind_cancel_and_expire(self):
        from backend.main import cleanup_expired_draft_media
        import uuid
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        h = self.login()
        token = str(uuid.uuid4())
        uploaded = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'draft_token': token}, files={'file': ('pixel.png', png, 'image/png')})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        mid, url = uploaded.json()['id'], uploaded.json()['url']
        self.assertEqual(url, f'/api/draft-media/{token}/{mid}')
        media = row('SELECT product_id,object_key FROM product_media WHERE id=?', (mid,))
        self.assertIsNone(media['product_id'])
        self.assertEqual(self.client.get(url, follow_redirects=False).status_code, 307)
        body = f'<!--portal-rich--><img src="{url}">'
        saved = self.client.post('/api/admin/products', headers=h, json={'name': 'Draft-bound', 'published': True, 'description': body, 'draft_token': token})
        self.assertEqual(saved.status_code, 200, saved.text)
        pid = saved.json()['id']
        self.assertEqual(row('SELECT product_id FROM product_media WHERE id=?', (mid,))['product_id'], pid)
        self.assertEqual(row('SELECT description FROM products WHERE id=?', (pid,))['description'], f'<!--portal-rich--><img src="/api/product-media/{mid}">')
        self.assertEqual(self.client.get(url).status_code, 404)
        canceled_token = str(uuid.uuid4())
        canceled = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'draft_token': canceled_token}, files={'file': ('pixel.png', png, 'image/png')}).json()
        canceled_key = row('SELECT object_key FROM product_media WHERE id=?', (canceled['id'],))['object_key']
        self.assertEqual(self.client.delete(f'/api/admin/draft-media/{canceled_token}', headers=h).status_code, 200)
        self.assertIsNone(row('SELECT id FROM product_media WHERE id=?', (canceled['id'],)))
        self.remote.remove_object.assert_called_with(os.environ['MINIO_BUCKET'], canceled_key)
        expired_token = str(uuid.uuid4())
        expired = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'draft_token': expired_token}, files={'file': ('pixel.png', png, 'image/png')}).json()
        write("UPDATE product_media SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (expired['id'],))
        cleanup_expired_draft_media()
        self.assertIsNone(row('SELECT id FROM product_media WHERE id=?', (expired['id'],)))
        self.assertEqual(self.client.get(expired['url']).status_code, 404)
        unauthorized = self.client.delete(f'/api/admin/draft-media/{expired_token}')
        self.assertEqual(unauthorized.status_code, 403)

    def test_rich_table_and_media_layout_sanitization(self):
        from backend.richtext import clean_rich
        html = '<!--portal-rich--><p style="text-align: center">Centered</p><figure style="text-align: right"><img src="https://example.com/a.png" width="640" style="width: 640px"></figure><video src="https://example.com/a.mp4" width="800" style="text-align: center"></video><table><thead><tr><th>Head</th></tr></thead><tbody><tr><td colspan="2" colwidth="640,320">Cell</td></tr></tbody></table><p style="text-align: center;position:fixed">Unsafe</p>'
        cleaned = clean_rich(html)
        self.assertIn('text-align: center', cleaned)
        self.assertIn('width="640"', cleaned)
        self.assertIn('width: 640px', cleaned)
        self.assertIn('<table>', cleaned)
        self.assertIn('<th>Head</th>', cleaned)
        self.assertIn('colspan="2"', cleaned)
        self.assertIn('colwidth="640,320"', cleaned)
        self.assertNotIn('position:fixed', cleaned)
        self.assertNotIn('autoplay', cleaned)
        combined = clean_rich('<!--portal-rich--><img src="https://example.com/a.png" width="640" style="width: 640px; text-align: center">')
        self.assertIn('width: 640px', combined)
        self.assertIn('text-align: center', combined)

    def test_rich_image_upload_sanitization_binding_and_access(self):
        rich = '<!--portal-rich--><p onclick="bad()">Safe<script>alert(1)</script></p><figure><video controls autoplay width="640" src="https://example.com/demo.mp4"></video><figcaption>Video caption</figcaption></figure><a href="https://example.com/path">link</a>'
        h = self.login()
        saved = self.client.post('/api/admin/products', headers=h, json={'id': self.pid, 'name': 'Rich product', 'published': False, 'description': rich})
        self.assertEqual(saved.status_code, 200, saved.text)
        cleaned = row('SELECT description FROM products WHERE id=?', (self.pid,))['description']
        self.assertTrue(cleaned.startswith('<!--portal-rich-->'))
        self.assertNotIn('script', cleaned)
        self.assertNotIn('onclick', cleaned)
        self.assertNotIn('javascript:', cleaned)
        self.assertNotIn('autoplay', cleaned)
        self.assertIn('<figure>', cleaned)
        self.assertIn('<figcaption>Video caption</figcaption>', cleaned)
        self.assertIn('width="640"', cleaned)
        self.assertIn('https://example.com/demo.mp4', cleaned)
        self.assertIn('https://example.com/path', cleaned)

        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        self.client.cookies.clear()
        unauthorized = self.client.post('/api/admin/upload', data={'kind': 'rich-image', 'product_id': self.pid}, files={'file': ('pixel.png', png, 'image/png')})
        self.assertEqual(unauthorized.status_code, 401)
        h = self.login()
        no_csrf = self.client.post('/api/admin/upload', data={'kind': 'rich-image', 'product_id': self.pid}, files={'file': ('pixel.png', png, 'image/png')})
        self.assertEqual(no_csrf.status_code, 403)
        invalid = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'product_id': self.pid}, files={'file': ('bad.svg', b'<svg/>', 'image/svg+xml')})
        self.assertEqual(invalid.status_code, 415)
        oversized = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'product_id': self.pid}, files={'file': ('large.png', b'x' * (5 * 1024 * 1024 + 1), 'image/png')})
        self.assertEqual(oversized.status_code, 413)
        uploaded = self.client.post('/api/admin/upload', headers=h, data={'kind': 'rich-image', 'product_id': self.pid}, files={'file': ('pixel.png', png, 'image/png')})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        mid, url = uploaded.json()['id'], uploaded.json()['url']
        self.assertEqual(url, f'/api/product-media/{mid}')
        media = row('SELECT product_id,content_type,object_key FROM product_media WHERE id=?', (mid,))
        self.assertEqual((media['product_id'], media['content_type']), (self.pid, 'image/png'))
        self.assertIsNone(row('SELECT object_key FROM uploaded_objects WHERE object_key=?', (media['object_key'],)))

        self.client.cookies.clear()
        self.assertEqual(self.client.get(url).status_code, 404)
        h = self.login()
        draft = self.client.get(url, follow_redirects=False)
        self.assertEqual(draft.status_code, 307)
        self.assertEqual(draft.headers['cache-control'], 'private, no-store')
        body = f'<!--portal-rich--><p>Image</p><img src="{url}"><img src="https://example.com/a.svg">'
        saved = self.client.post('/api/admin/products', headers=h, json={'id': self.pid, 'name': 'Rich product', 'published': True, 'description': body})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn('a.svg', row('SELECT description FROM products WHERE id=?', (self.pid,))['description'])
        self.client.cookies.clear()
        public = self.client.get(url, follow_redirects=False)
        self.assertEqual(public.status_code, 307)
        self.assertEqual(public.headers['cache-control'], 'private, no-store')
        self.assertGreaterEqual(self.remote.presigned_get_object.call_count, 2)

        h = self.login()
        other = self.client.post('/api/admin/products', headers=h, json={'name': 'Other product'}).json()['id']
        cross = self.client.post('/api/admin/products', headers=h, json={'id': other, 'name': 'Other product', 'description': f'<!--portal-rich--><img src="{url}">'})
        self.assertEqual(cross.status_code, 400)
        too_long = self.client.post('/api/admin/products', headers=h, json={'id': self.pid, 'name': 'Rich product', 'description': '<!--portal-rich--><p>' + ('😀' * 30000) + '</p>'})
        self.assertEqual(too_long.status_code, 422)

    def test_media_private_until_approved(self):
        created=self.client.post("/api/solutions",data={"title":"Media example","body":"Long enough solution content"},files={"files":("video.mp4",b"video data","video/mp4")}).json()
        sid=created["id"]
        fid=row("SELECT id FROM solution_files WHERE solution_id=?",(sid,))["id"]
        self.assertEqual(self.client.get(f"/api/files/{fid}").status_code,404)
        h=self.login()
        self.client.post(f"/api/admin/solutions/{sid}/status",headers=h,data={"status":"approved"})
        self.client.cookies.clear()
        result=self.client.get(f"/api/files/{fid}",follow_redirects=False)
        self.assertEqual(result.status_code,307)
        self.assertEqual(result.headers["location"],"https://storage.example/private?signature=test")


if __name__ == "__main__":
    unittest.main()
