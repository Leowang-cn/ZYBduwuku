from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ipaddress
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend import storage
from backend.richtext import clean_rich, meaningful_rich
from PIL import Image, UnidentifiedImageError
from io import BytesIO

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
STAGING = Path(os.getenv("UPLOAD_STAGING_DIR", str(ROOT / ".upload-staging"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
STAGING.mkdir(parents=True, exist_ok=True)
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.sqlite")
DB_PATH = Path(DB_URL.removeprefix("sqlite:///"))
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT / DB_PATH).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
SECRET = os.getenv("SECRET_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

if not SECRET:
    raise RuntimeError("SECRET_KEY is required for administrator sessions")

if os.getenv("APP_ENV", "development") != "test" and not (os.getenv("APP_ENV", "development") == "development" and os.getenv("ALLOW_UNCONFIGURED_STORAGE") == "true"):
    storage.require_configuration()
if os.getenv("APP_ENV", "development") == "production":
    if len(SECRET)<32 or len(ADMIN_PASSWORD)<12: raise RuntimeError("Production requires strong initial credentials")
    if os.getenv("SEED_DEMO_DATA", "false").lower()=="true": raise RuntimeError("Demo seeding is forbidden in production")

from contextlib import asynccontextmanager


from backend.mcp_server import create_server

MCP_SERVER = create_server(DB_PATH)
MCP_APP = MCP_SERVER.streamable_http_app()


@asynccontextmanager
async def app_lifespan(application):
    async with MCP_SERVER.session_manager.run():
        cleanup_expired_draft_media()
        cleanup_product_screenshots()
        yield


app = FastAPI(title="Internal Software Portal", docs_url=None, redoc_url=None, lifespan=app_lifespan)
app.state.mcp_enabled = True
app.mount("/mcp", MCP_APP, name="mcp")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT '',vendor TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT 'self',icon TEXT NOT NULL DEFAULT '✦',featured INTEGER NOT NULL DEFAULT 0,published INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subapps(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS releases(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,subapp_id INTEGER REFERENCES subapps(id) ON DELETE CASCADE,version TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliverables(id INTEGER PRIMARY KEY AUTOINCREMENT,release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,label TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',object_key TEXT NOT NULL DEFAULT '',filename TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS screenshots(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,object_key TEXT NOT NULL,filename TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,body TEXT NOT NULL,handled INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback_votes(feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,voter TEXT NOT NULL,PRIMARY KEY(feedback_id,voter));
        CREATE TABLE IF NOT EXISTS feedback_comments(id INTEGER PRIMARY KEY AUTOINCREMENT,feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,parent_id INTEGER REFERENCES feedback_comments(id) ON DELETE CASCADE,body TEXT NOT NULL,nickname TEXT NOT NULL DEFAULT '匿名',status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,ip TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS feedback_comment_files(id INTEGER PRIMARY KEY AUTOINCREMENT,comment_id INTEGER NOT NULL REFERENCES feedback_comments(id) ON DELETE CASCADE,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS solutions(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,body TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,author TEXT NOT NULL DEFAULT '匿名',status TEXT NOT NULL DEFAULT 'pending',reject_reason TEXT NOT NULL DEFAULT '',ip TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS solution_files(id INTEGER PRIMARY KEY AUTOINCREMENT,solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT,solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,body TEXT NOT NULL,author TEXT NOT NULL DEFAULT '匿名',ip TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'approved',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS product_votes(product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,voter TEXT NOT NULL,value INTEGER NOT NULL CHECK(value IN (-1,1)),PRIMARY KEY(product_id,voter));
        CREATE TABLE IF NOT EXISTS solution_likes(solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,voter TEXT NOT NULL,PRIMARY KEY(solution_id,voter));
        CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,body TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',published INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_solutions_status ON solutions(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_events_date ON events(created_at,kind);
        """)
        if "platform" not in {item["name"] for item in c.execute("PRAGMA table_info(releases)")}: c.execute("ALTER TABLE releases ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
        if "size" not in {item["name"] for item in c.execute("PRAGMA table_info(releases)")}: c.execute("ALTER TABLE releases ADD COLUMN size TEXT NOT NULL DEFAULT ''")
        deliverable_columns = {item["name"] for item in c.execute("PRAGMA table_info(deliverables)")}
        for field, source in (("platform", "platform"), ("size", "size"), ("updated_at", "created_at")):
            if field not in deliverable_columns:
                c.execute(f"ALTER TABLE deliverables ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
                c.execute(f"UPDATE deliverables SET {field}=COALESCE((SELECT {source} FROM releases WHERE releases.id=deliverables.release_id),'')")
        comment_columns = {item["name"] for item in c.execute("PRAGMA table_info(comments)")}
        if "ip" not in comment_columns:
            c.execute("ALTER TABLE comments ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
        if "parent_id" not in comment_columns:
            c.execute("ALTER TABLE comments ADD COLUMN parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE")
        if "status" not in comment_columns:
            c.execute("ALTER TABLE comments ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'")
        feedback_columns = {item["name"] for item in c.execute("PRAGMA table_info(feedback)")}
        if "handled" not in feedback_columns:
            c.execute("ALTER TABLE feedback ADD COLUMN handled INTEGER NOT NULL DEFAULT 0")
        if "nickname" not in feedback_columns: c.execute("ALTER TABLE feedback ADD COLUMN nickname TEXT NOT NULL DEFAULT '匿名'")
        if "status" not in feedback_columns: c.execute("ALTER TABLE feedback ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "ip" not in feedback_columns: c.execute("ALTER TABLE feedback ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
        comment_columns = {item["name"] for item in c.execute("PRAGMA table_info(feedback_comments)")}
        if "ip" not in comment_columns: c.execute("ALTER TABLE feedback_comments ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
        c.execute("CREATE TABLE IF NOT EXISTS feedback_comment_files(id INTEGER PRIMARY KEY AUTOINCREMENT,comment_id INTEGER NOT NULL REFERENCES feedback_comments(id) ON DELETE CASCADE,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL)")
        if "safety_notice" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN safety_notice TEXT NOT NULL DEFAULT ''")
        if "views" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN views INTEGER NOT NULL DEFAULT 0")
        if "platforms" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN platforms TEXT NOT NULL DEFAULT ''")
        if "categories" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN categories TEXT NOT NULL DEFAULT ''")
        if "parent_id" not in {item["name"] for item in c.execute("PRAGMA table_info(feedback)")}: c.execute("ALTER TABLE feedback ADD COLUMN parent_id INTEGER REFERENCES feedback(id) ON DELETE CASCADE")
        if "types" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN types TEXT NOT NULL DEFAULT '[]'")
        if "website_url" not in {item["name"] for item in c.execute("PRAGMA table_info(products)")}: c.execute("ALTER TABLE products ADD COLUMN website_url TEXT NOT NULL DEFAULT ''")
        c.execute("CREATE TABLE IF NOT EXISTS taxonomies(kind TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(kind,name))")
        # Legacy form taxonomy entries become the new multi-select tool types.
        c.execute("UPDATE taxonomies SET kind='type' WHERE kind='form'")
        for table, column, definition in (("solutions", "featured_slot", "INTEGER NOT NULL DEFAULT 0"), ("products", "platform", "TEXT NOT NULL DEFAULT ''"), ("products", "form", "TEXT NOT NULL DEFAULT ''"), ("products", "icon_object_key", "TEXT NOT NULL DEFAULT ''"), ("products", "icon_filename", "TEXT NOT NULL DEFAULT ''"), ("products", "featured_slot", "INTEGER NOT NULL DEFAULT 0")):
            if column not in {item["name"] for item in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Convert legacy featured flags once, preserving the existing newest-first ordering.
        if not c.execute("SELECT 1 FROM settings WHERE key='featured_slots_migrated'").fetchone():
            for slot, item in enumerate(c.execute("SELECT id FROM products WHERE featured=1 ORDER BY id DESC LIMIT 3").fetchall(), 1):
                c.execute("UPDATE products SET featured_slot=? WHERE id=?", (slot, item["id"]))
            c.execute("UPDATE products SET featured=CASE WHEN featured_slot>0 THEN 1 ELSE 0 END")
            c.execute("INSERT INTO settings(key,value) VALUES('featured_slots_migrated','1')")
        c.execute("CREATE TABLE IF NOT EXISTS solution_products(solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,PRIMARY KEY(solution_id,product_id))")
        c.execute("INSERT OR IGNORE INTO solution_products(solution_id,product_id) SELECT id,product_id FROM solutions WHERE product_id IS NOT NULL")
        c.execute("CREATE INDEX IF NOT EXISTS idx_solution_products_product ON solution_products(product_id,solution_id)")
        c.execute("CREATE TABLE IF NOT EXISTS feedback_files(id INTEGER PRIMARY KEY AUTOINCREMENT,feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS solution_media(id INTEGER PRIMARY KEY AUTOINCREMENT,solution_id INTEGER REFERENCES solutions(id) ON DELETE CASCADE,draft_token_hash TEXT,expires_at TEXT,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_solution_media_expiry ON solution_media(solution_id,expires_at)")
        if "tags" not in {item["name"] for item in c.execute("PRAGMA table_info(solutions)")}:
            c.execute("ALTER TABLE solutions ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        c.execute("CREATE TABLE IF NOT EXISTS expired_media_cleanup(object_key TEXT PRIMARY KEY)")
        c.execute("CREATE TABLE IF NOT EXISTS product_media(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,draft_token_hash TEXT,expires_at TEXT,object_key TEXT NOT NULL,filename TEXT NOT NULL,content_type TEXT NOT NULL)")
        media_columns = {item["name"]: item for item in c.execute("PRAGMA table_info(product_media)")}
        if "draft_token_hash" not in media_columns:
            c.execute("ALTER TABLE product_media ADD COLUMN draft_token_hash TEXT")
        if "expires_at" not in media_columns:
            c.execute("ALTER TABLE product_media ADD COLUMN expires_at TEXT")
        if media_columns["product_id"]["notnull"]:
            c.execute("ALTER TABLE product_media RENAME TO product_media_legacy")
            c.execute("""CREATE TABLE product_media(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                draft_token_hash TEXT,
                expires_at TEXT,
                object_key TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL
            )""")
            c.execute("""INSERT INTO product_media(id,product_id,draft_token_hash,expires_at,object_key,filename,content_type)
                SELECT id,product_id,draft_token_hash,expires_at,object_key,filename,content_type
                FROM product_media_legacy""")
            c.execute("DROP TABLE product_media_legacy")
        c.execute("CREATE TABLE IF NOT EXISTS uploaded_objects(object_key TEXT PRIMARY KEY,filename TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS icon_objects(object_key TEXT PRIMARY KEY,filename TEXT NOT NULL)")
        for name in ("效率工具", "开发工具", "设计创作", "系统工具"):
            c.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name,))
        existing = c.execute("SELECT value FROM settings WHERE key='admin_hash'").fetchone()
        if not existing and ADMIN_PASSWORD:
            c.execute("INSERT INTO settings VALUES('admin_hash',?)", (hash_password(ADMIN_PASSWORD),))
        if os.getenv("SEED_DEMO_DATA", "false").lower() == "true" and not c.execute("SELECT 1 FROM products LIMIT 1").fetchone():
            now = datetime.now(timezone.utc).isoformat()
            for row in [("文件小助手", "批量处理文件，让日常工作更轻松。", "将重复的文件整理任务化繁为简。", "效率工具", "自研", "▦", 1), ("开发者工具箱", "为团队开发者准备的效率工具集。", "常用开发工具与使用说明。", "开发工具", "自研", "⌘", 1), ("灵感画板", "记录灵感，快速整理创意。", "轻量便捷的创意整理工具。", "设计创作", "第三方", "✧", 0)]:
                cur = c.execute("INSERT INTO products(name,summary,description,category,kind,vendor,icon,featured,featured_slot,published,created_at) VALUES(?,?,?,?,?,?,?,?,?,1,?)", (row[0], row[1], row[2], row[3], row[4], "本地团队" if row[4] == "自研" else "社区精选", row[5], row[6], len(c.execute("SELECT id FROM products WHERE featured_slot>0").fetchall()) + 1 if row[6] else 0, now))
                p = cur.lastrowid
                r = c.execute("INSERT INTO releases(product_id,version,notes,created_at) VALUES(?,?,?,?)", (p, "1.0.0", "首次发布", now)).lastrowid
                c.execute("INSERT INTO deliverables(release_id,label,kind,url) VALUES(?,?,?,?)", (r, "访问官方网站", "url", "https://example.com"))


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    return "pbkdf2_sha256$%s$%s" % (salt.hex(), hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000).hex())


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), encoded)


def now():
    return datetime.now(timezone.utc).isoformat()


def rows(query, args=()):
    with db() as c:
        return [dict(x) for x in c.execute(query, args).fetchall()]


def row(query, args=()):
    with db() as c:
        item = c.execute(query, args).fetchone()
        return dict(item) if item else None


def write(query, args=()):
    with db() as c:
        cur = c.execute(query, args)
        return cur.lastrowid


init_db()


def is_admin(request: Request):
    token = request.cookies.get("portal_admin", "")
    if not token or not SECRET:
        return False
    expected = hmac.new(SECRET.encode(), b"portal-admin", hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "请先登录管理后台")


def client_ip(request: Request):
    peer=request.client.host if request.client else ""
    try:
        trusted=[ipaddress.ip_network(c.strip()) for c in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",") if c.strip()]
        if not any(ipaddress.ip_address(peer) in network for network in trusted): return peer
        chain=request.headers.get("x-forwarded-for", "").split(",")
        for address in reversed(chain):
            address=str(ipaddress.ip_address(address.strip()))
            if not any(ipaddress.ip_address(address) in network for network in trusted): return address
        return address
    except ValueError:
        return peer


def rate(request: Request, name: str, limit: int = 12, period: int = 60):
    key = "rate:" + name + ":" + client_ip(request)
    current = int(time.time())
    with db() as c:
        previous = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        try:
            start, count = map(int, previous[0].split(":")) if previous else (current, 0)
        except Exception:
            start, count = current, 0
        if current - start >= period:
            start, count = current, 0
        if count >= limit:
            raise HTTPException(429, "操作过于频繁，请稍后重试")
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, f"{start}:{count+1}"))


def public_product(p, admin=False):
    p["safety_notice"] = p.get("safety_notice", "")
    p["categories"] = json.loads(p.get("categories") or "[]")
    p["platforms"] = json.loads(p.get("platforms") or "[]")
    p["types"] = json.loads(p.get("types") or "[]")
    p["website_url"] = p.get("website_url", "")
    p["type_tags"] = [x["name"] for x in rows("SELECT name FROM taxonomies WHERE kind='type' ORDER BY name")]
    p["platform_tags"] = [x["name"] for x in rows("SELECT name FROM taxonomies WHERE kind='platform' ORDER BY name")]
    p["views"] = p.get("views", 0)
    p["votes"] = {"up": row("SELECT count(*) n FROM product_votes WHERE product_id=? AND value=1", (p["id"],))["n"], "down": row("SELECT count(*) n FROM product_votes WHERE product_id=? AND value=-1", (p["id"],))["n"]}
    p["clicks"] = {"download":row("SELECT count(*) n FROM events WHERE product_id=? AND kind='download'",(p["id"],))["n"],"external":row("SELECT count(*) n FROM events WHERE product_id=? AND kind='external'",(p["id"],))["n"]}
    p["click_count"] = p["clicks"]["download"] + p["clicks"]["external"]
    releases = rows("SELECT id,version,notes,created_at,platform,size FROM releases WHERE product_id=? AND subapp_id IS NULL ORDER BY created_at DESC,id DESC", (p["id"],))
    for release in releases:
        release["deliverables"] = rows("SELECT id,label,kind,url,filename,platform,size,updated_at" + (",object_key" if admin else "") + " FROM deliverables WHERE release_id=?", (release["id"],))
    p["releases"] = releases
    p["subapps"] = rows("SELECT id,name FROM subapps WHERE product_id=? ORDER BY id", (p["id"],))
    for subapp in p["subapps"]:
        subapp["releases"] = rows("SELECT id,version,notes,created_at,platform,size FROM releases WHERE subapp_id=? ORDER BY created_at DESC,id DESC", (subapp["id"],))
        for release in subapp["releases"]:
            release["deliverables"] = rows("SELECT id,label,kind,url,filename,platform,size,updated_at" + (",object_key" if admin else "") + " FROM deliverables WHERE release_id=?", (release["id"],))
    p["icon_image_url"] = f"/api/product-icons/{p['id']}" if p["icon_object_key"] else ""
    if not admin:
        p.pop("icon_object_key", None)
        p.pop("icon_filename", None)
        p["featured"] = int(bool(p["featured_slot"]))
    return p


@app.get("/api/health")
def health():
    with db() as c:
        c.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/bootstrap")
def bootstrap():
    products = rows("SELECT * FROM products WHERE published=1 ORDER BY CASE WHEN featured_slot>0 THEN 0 ELSE 1 END,featured_slot,id DESC")
    for product in products:
        product["solutions"] = rows("SELECT s.id,s.title,s.tags,s.category FROM solutions s JOIN solution_products sp ON sp.solution_id=s.id WHERE sp.product_id=? AND s.status='approved' ORDER BY (SELECT count(*) FROM solution_likes l WHERE l.solution_id=s.id) DESC,s.created_at DESC", (product["id"],))
        for solution in product["solutions"]:
            solution["tags"] = json.loads(solution["tags"])
        public_product(product)
    solution_links = rows("SELECT sp.solution_id,p.name FROM solution_products sp JOIN products p ON p.id=sp.product_id JOIN solutions s ON s.id=sp.solution_id WHERE s.status='approved' AND p.published=1")
    links_by_solution = {}
    for link in solution_links:
        links_by_solution.setdefault(link["solution_id"], []).append(link["name"])
    solutions = rows("SELECT s.id,s.title,s.body,s.tags,s.category,s.product_id,s.author,s.created_at,s.featured_slot,p.name AS product_name,(SELECT count(*) FROM comments c WHERE c.solution_id=s.id AND c.status='approved') AS comments,(SELECT count(*) FROM solution_likes l WHERE l.solution_id=s.id) AS likes FROM solutions s LEFT JOIN products p ON p.id=s.product_id WHERE s.status='approved' ORDER BY likes DESC,s.created_at DESC LIMIT 100")
    for solution in solutions:
        solution["tags"] = json.loads(solution["tags"])
        solution["likes"] = row("SELECT count(*) n FROM solution_likes WHERE solution_id=?", (solution["id"],))["n"]
        solution["product_names"] = links_by_solution.get(solution["id"], [])
    feedback = [{**item, "files": rows("SELECT id,filename FROM feedback_files WHERE feedback_id=?", (item["id"],)), "likes": row("SELECT count(*) n FROM feedback_votes WHERE feedback_id=?", (item["id"],))["n"], "comments": rows("SELECT id,parent_id,body,nickname,created_at FROM feedback_comments WHERE feedback_id=? AND status='approved' ORDER BY id", (item["id"],))} for item in rows("SELECT f.id,f.product_id,f.body,f.nickname,f.created_at,p.name product_name FROM feedback f JOIN products p ON p.id=f.product_id WHERE f.status='approved' ORDER BY f.id DESC LIMIT 100")]
    return {"products": products, "categories": [x["name"] for x in rows("SELECT name FROM categories ORDER BY name")], "taxonomies": rows("SELECT kind,name FROM taxonomies WHERE kind IN ('platform','type') ORDER BY kind,name"), "solutions": solutions, "feedback": feedback, "articles": rows("SELECT id,title,body,category,created_at FROM articles WHERE published=1 ORDER BY id DESC LIMIT 30"), "admin": False}


@app.get("/api/solutions/{sid}")
def solution_detail(sid: int):
    item = row("SELECT s.id,s.title,s.body,s.tags,s.category,s.author,s.created_at FROM solutions s WHERE s.id=? AND s.status='approved'", (sid,))
    if not item: raise HTTPException(404, "内容不存在")
    item["tags"] = json.loads(item["tags"])
    item["products"] = rows("SELECT p.id,p.name,p.summary,p.category,p.icon,p.icon_object_key,p.icon_filename FROM products p JOIN solution_products sp ON sp.product_id=p.id WHERE sp.solution_id=? AND p.published=1 ORDER BY p.name", (sid,))
    for product in item["products"]:
        product["icon_image_url"] = f"/api/product-icons/{product['id']}" if product["icon_object_key"] else ""
        product.pop("icon_object_key", None)
        product.pop("icon_filename", None)
    item["product_name"] = item["products"][0]["name"] if item["products"] else ""
    item["files"] = rows("SELECT id,filename,content_type FROM solution_files WHERE solution_id=?", (sid,))
    item["comments"] = rows("SELECT id,body,author,parent_id,created_at FROM comments WHERE solution_id=? AND status='approved' ORDER BY id", (sid,))
    item["likes"] = row("SELECT count(*) n FROM solution_likes WHERE solution_id=?",(sid,))["n"]
    return item


@app.get("/api/files/{fid}")
def get_file(fid: int, request: Request):
    f = row("SELECT sf.object_key,sf.filename,sf.content_type,s.status FROM solution_files sf JOIN solutions s ON s.id=sf.solution_id WHERE sf.id=?", (fid,))
    if not f or (f["status"] != "approved" and not is_admin(request)):
        raise HTTPException(404, "文件不存在")
    return storage.serve(f["object_key"], f["filename"], f["content_type"], attachment=not f["content_type"].startswith(("image/", "video/")))


@app.get("/api/screenshots/{sid}")
def get_screenshot(sid: int):
    f = row("SELECT s.object_key,s.filename,p.published FROM screenshots s JOIN products p ON p.id=s.product_id WHERE s.id=?", (sid,))
    if not f or not f["published"]: raise HTTPException(404, "图片不存在")
    return storage.serve(f["object_key"], f["filename"], "image/" + Path(f["filename"]).suffix.lstrip(".").replace("jpg", "jpeg"), attachment=False)


@app.get("/api/product-icons/{pid}")
def get_product_icon(pid: int):
    product = row("SELECT icon_object_key,icon_filename,published FROM products WHERE id=?", (pid,))
    if not product or not product["published"] or not product["icon_object_key"]:
        raise HTTPException(404, "图片不存在")
    ext = Path(product["icon_filename"]).suffix.lower()
    content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(ext)
    if not content_type:
        raise HTTPException(404, "图片不存在")
    return storage.serve(product["icon_object_key"], product["icon_filename"], content_type, attachment=False)


@app.get("/api/draft-media/{token}/{mid}")
def get_draft_media(token: str, mid: int):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", token):
        raise HTTPException(404, "图片不存在")
    media = row("SELECT object_key,filename,content_type,expires_at FROM product_media WHERE id=? AND product_id IS NULL AND draft_token_hash=?", (mid, hashlib.sha256(token.encode()).hexdigest()))
    if not media or not media["expires_at"] or media["expires_at"] <= now():
        raise HTTPException(404, "图片不存在")
    response = storage.serve(media["object_key"], media["filename"], media["content_type"], attachment=False)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/product-media/{mid}")
def get_product_media(mid: int, request: Request):
    media = row("SELECT m.object_key,m.filename,m.content_type,p.published FROM product_media m JOIN products p ON p.id=m.product_id WHERE m.id=?", (mid,))
    if not media or (not media["published"] and not is_admin(request)):
        raise HTTPException(404, "图片不存在")
    response = storage.serve(media["object_key"], media["filename"], media["content_type"], attachment=False)
    response.headers["Cache-Control"] = "private, no-store"
    return response


class Login(BaseModel):
    password: str


@app.post("/api/admin/login")
def login(data: Login, request: Request):
    rate(request, "login", 8, 300)
    saved = row("SELECT value FROM settings WHERE key='admin_hash'")
    if not saved or not verify_password(data.password, saved["value"]):
        raise HTTPException(401, "密码错误")
    token = hmac.new(SECRET.encode(), b"portal-admin", hashlib.sha256).hexdigest()
    if "$" not in saved["value"]:
        write("UPDATE settings SET value=? WHERE key='admin_hash'",(hash_password(data.password),))
    response = JSONResponse({"ok": True})
    response.set_cookie("portal_admin", token, httponly=True, secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", samesite="strict", max_age=8*3600, path="/")
    response.set_cookie("portal_csrf", secrets.token_urlsafe(24), httponly=False, secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", samesite="strict", max_age=8*3600, path="/")
    return response


@app.post("/api/admin/logout")
def logout(request: Request):
    require_admin(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie("portal_admin", path="/"); response.delete_cookie("portal_csrf", path="/")
    return response


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.url.path.startswith("/api/admin/") and request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/api/admin/login" and is_admin(request):
        csrf = request.cookies.get("portal_csrf", "")
        if not csrf or not hmac.compare_digest(csrf, request.headers.get("X-CSRF-Token", "")):
            return JSONResponse({"detail": "CSRF 验证失败"}, status_code=403)
    return await call_next(request)


@app.get("/api/admin/me")
def admin_me(request: Request):
    return {"admin": is_admin(request)}


@app.get("/api/admin/data")
def admin_data(request: Request):
    require_admin(request)
    products = [public_product(p, admin=True) for p in rows("SELECT * FROM products ORDER BY id DESC")]
    feedback_items = [{**f,"files":rows("SELECT id,filename FROM feedback_files WHERE feedback_id=?",(f["id"],)),"comments":[{**comment,"files":rows("SELECT id,filename FROM feedback_comment_files WHERE comment_id=?",(comment["id"],))} for comment in rows("SELECT id,parent_id,body,nickname,status,created_at,ip FROM feedback_comments WHERE feedback_id=? ORDER BY id",(f["id"],))]} for f in rows("SELECT f.*,p.name product_name FROM feedback f JOIN products p ON p.id=f.product_id ORDER BY f.id DESC")]
    return {"products": products, "solutions": [{**s, "files": rows("SELECT id,filename,content_type FROM solution_files WHERE solution_id=?", (s["id"],)), "tags": json.loads(s["tags"]), "product_ids": [link["product_id"] for link in rows("SELECT product_id FROM solution_products WHERE solution_id=? ORDER BY product_id", (s["id"],))]} for s in rows("SELECT s.*,p.name product_name FROM solutions s LEFT JOIN products p ON p.id=s.product_id ORDER BY s.id DESC")], "feedback": feedback_items, "comments": rows("SELECT c.*,s.title solution_title FROM comments c JOIN solutions s ON s.id=c.solution_id ORDER BY c.id DESC"), "articles": rows("SELECT * FROM articles ORDER BY id DESC"), "categories": [x["name"] for x in rows("SELECT name FROM categories ORDER BY name")], "taxonomies": rows("SELECT kind,name FROM taxonomies WHERE kind IN ('platform','type') ORDER BY kind,name"), "stats": {"products": row("SELECT count(*) n FROM products")["n"], "published": row("SELECT count(*) n FROM products WHERE published=1")["n"], "pending": row("SELECT count(*) n FROM solutions WHERE status='pending'")["n"], "feedback": row("SELECT count(*) n FROM feedback")["n"], "downloads": row("SELECT count(*) n FROM events WHERE kind='download'")["n"], "external_clicks": row("SELECT count(*) n FROM events WHERE kind='external'")["n"], "trend": rows("SELECT substr(created_at,1,10) day,kind,count(*) count FROM events WHERE created_at>=date('now','-13 days') GROUP BY day,kind ORDER BY day")}}


class ProductInput(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1,max_length=120)
    summary: str = Field(default="",max_length=500)
    description: str = Field(default="",max_length=60000)
    category: str = Field(default="",max_length=80)
    kind: str = "self"
    vendor: str = Field(default="",max_length=120)
    icon: str = Field(default="✦",max_length=8)
    featured: bool = False
    featured_slot: Optional[int] = Field(default=None, ge=0, le=3)
    icon_object_key: Optional[str] = None
    published: bool = False
    platform: str = Field(default="", max_length=80)
    form: str = Field(default="", max_length=80)
    platforms: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    website_url: str = Field(default="", max_length=2048)
    safety_notice: str = Field(default="",max_length=8000)
    draft_token: Optional[str] = None
    subapps: list[dict] = Field(default_factory=list)
    releases: list[dict] = Field(default_factory=list)

    @staticmethod
    def _save_release_files(c, rid, release):
        if "deliverables" not in release:
            return
        if not isinstance(release["deliverables"], list):
            raise HTTPException(400, "版本附件必须为列表")
        parent = c.execute("SELECT r.*,p.platforms FROM releases r JOIN products p ON p.id=r.product_id WHERE r.id=?", (rid,)).fetchone()
        allowed_platforms = set(json.loads(parent["platforms"] or "[]"))
        kept_ids = set()
        for d in release["deliverables"]:
            if not isinstance(d, dict) or d.get("kind") not in {"file", "url"}:
                raise HTTPException(400, "附件类型必须为 file 或 url")
            kind = d["kind"]
            did = d.get("id")
            existing = c.execute("SELECT * FROM deliverables WHERE id=? AND release_id=? AND kind=?", (did, rid, kind)).fetchone() if did else None
            if did and not existing:
                raise HTTPException(400, "附件不属于该版本或类型不匹配")
            metadata = {}
            for field, fallback, limit in (("platform", parent["platform"], 80), ("size", parent["size"], 80), ("updated_at", parent["created_at"], 80)):
                value = d.get(field, existing[field] if existing else fallback)
                if not isinstance(value, str) or len(value) > limit:
                    raise HTTPException(400, "附件元数据必须为不超过 80 字符的字符串")
                metadata[field] = value
            if metadata["platform"] and metadata["platform"] not in allowed_platforms:
                raise HTTPException(400, "附件平台必须属于软件适用平台")
            if metadata["updated_at"]:
                try:
                    datetime.fromisoformat(metadata["updated_at"].replace("Z", "+00:00"))
                except ValueError:
                    raise HTTPException(400, "附件更新时间必须为 ISO 8601 日期或时间")
            label = str(d.get("label") or ("下载" if kind == "file" else "访问网站"))[:120]
            key, filename, url = "", "", ""
            if kind == "file":
                key = str(d.get("object_key") or (existing["object_key"] if existing else ""))
                uploaded = c.execute("SELECT filename FROM uploaded_objects WHERE object_key=?", (key,)).fetchone() if key else None
                if uploaded:
                    filename = uploaded["filename"]
                elif existing and key == existing["object_key"]:
                    filename = existing["filename"]
                else:
                    raise HTTPException(400, "附件文件未上传")
            else:
                url = d.get("url", existing["url"] if existing else "")
                if not isinstance(url, str) or len(url) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in url):
                    raise HTTPException(400, "附件网址无效")
                try:
                    parsed = urlparse(url)
                    valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
                    parsed.port
                except ValueError:
                    valid = False
                if not valid:
                    raise HTTPException(400, "附件网址必须为 HTTP/HTTPS 地址")
                if not existing:
                    existing = c.execute("SELECT * FROM deliverables WHERE release_id=? AND kind='url' AND url=? AND platform=? ORDER BY id LIMIT 1", (rid, url, metadata["platform"])).fetchone()
            values = (label, url, key, filename, metadata["platform"], metadata["size"], metadata["updated_at"])
            if existing:
                did = existing["id"]
                if did in kept_ids:
                    raise HTTPException(400, "版本附件重复")
                c.execute("UPDATE deliverables SET label=?,url=?,object_key=?,filename=?,platform=?,size=?,updated_at=? WHERE id=?", (*values, did))
            else:
                did = c.execute("INSERT INTO deliverables(label,url,object_key,filename,platform,size,updated_at,release_id,kind) VALUES(?,?,?,?,?,?,?,?,?)", (*values, rid, kind)).lastrowid
            kept_ids.add(did)
        for existing in c.execute("SELECT id FROM deliverables WHERE release_id=?", (rid,)).fetchall():
            if existing["id"] not in kept_ids:
                c.execute("DELETE FROM deliverables WHERE id=?", (existing["id"],))


@app.post("/api/admin/products")
def save_product(data: ProductInput, request: Request):
    require_admin(request)
    data.description = clean_rich(data.description)
    data.safety_notice = clean_rich(data.safety_notice)
    for text, limit in ((data.description, 60000), (data.safety_notice, 8000)):
        if len(text.encode('utf-16-le')) // 2 > limit:
            raise HTTPException(422, "富文本超过长度限制")
        for match in re.finditer(r'/api/(product-media/([0-9]+)|draft-media/([0-9a-fA-F-]{36})/([0-9]+))', text):
            if match.group(2):
                media = row("SELECT product_id FROM product_media WHERE id=?", (int(match.group(2)),))
                if not media or media['product_id'] != data.id:
                    raise HTTPException(400, "图片不属于该产品")
            elif not data.draft_token or not hmac.compare_digest(match.group(3), data.draft_token):
                raise HTTPException(400, "草稿图片令牌无效")
    values=(data.name,data.summary,data.description,data.category,"第三方" if data.kind in {"third","第三方"} else "自研",data.vendor,data.icon,int(data.published))
    with db() as c:
        existing = c.execute("SELECT id,icon_object_key,featured_slot FROM products WHERE id=?", (data.id,)).fetchone() if data.id else None
        if data.id and not existing:
            raise HTTPException(404, "产品不存在")
        allowed_platforms = set(data.platforms)
        for release in [*data.releases, *(r for sub in data.subapps if isinstance(sub, dict) for r in sub.get("releases", []))]:
            if not isinstance(release, dict) or (release.get("platform") and release["platform"] not in allowed_platforms):
                raise HTTPException(400, "版本平台必须属于软件适用平台")
        icon_key = existing["icon_object_key"] if existing and data.icon_object_key is None else (data.icon_object_key or "")
        icon_file = c.execute("SELECT filename FROM icon_objects WHERE object_key=?", (icon_key,)).fetchone() if icon_key else None
        if icon_key and not icon_file:
            if not existing or icon_key != existing["icon_object_key"]:
                raise HTTPException(400, "图标上传记录不存在")
            icon_file = c.execute("SELECT icon_filename AS filename FROM products WHERE id=?", (data.id,)).fetchone()
        if data.featured_slot is not None:
            slot = data.featured_slot
        elif data.featured:
            slot = existing["featured_slot"] if existing and existing["featured_slot"] else next((s for s in (1, 2, 3) if not c.execute("SELECT 1 FROM products WHERE featured_slot=?", (s,)).fetchone()), 0)
            if not slot: raise HTTPException(409, "精选展示位已满")
        else:
            slot = 0
        if slot:
            c.execute("UPDATE products SET featured=0,featured_slot=0 WHERE featured_slot=? AND id!=?", (slot, data.id or 0))
        if data.id:
            c.execute("UPDATE products SET name=?,summary=?,description=?,category=?,kind=?,vendor=?,icon=?,published=?,icon_object_key=?,icon_filename=?,featured=?,featured_slot=?,safety_notice=?,platform=?,form=?,categories=?,platforms=?,types=?,website_url=? WHERE id=?", (*values,icon_key,icon_file["filename"] if icon_file else "",int(bool(slot)),slot,data.safety_notice,data.platform,data.form,json.dumps(data.categories,ensure_ascii=False),json.dumps(data.platforms,ensure_ascii=False),json.dumps(data.types,ensure_ascii=False),data.website_url,data.id))
            pid=data.id
        else:
            pid=c.execute("INSERT INTO products(name,summary,description,category,kind,vendor,icon,published,icon_object_key,icon_filename,featured,featured_slot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (*values,icon_key,icon_file["filename"] if icon_file else "",int(bool(slot)),slot,now())).lastrowid
            c.execute("UPDATE products SET platform=?,form=?,safety_notice=?,categories=?,platforms=?,types=?,website_url=? WHERE id=?",(data.platform,data.form,data.safety_notice,json.dumps(data.categories,ensure_ascii=False),json.dumps(data.platforms,ensure_ascii=False),json.dumps(data.types,ensure_ascii=False),data.website_url,pid))
        token_media = {}
        for text in (data.description, data.safety_notice):
            for token, media_id in re.findall(r'/api/draft-media/([0-9a-fA-F-]{36})/([0-9]+)', text):
                if not data.draft_token or not hmac.compare_digest(token, data.draft_token):
                    raise HTTPException(400, "草稿图片令牌无效")
                token_media[int(media_id)] = token
        for mid, token in token_media.items():
            media = c.execute("SELECT expires_at FROM product_media WHERE id=? AND product_id IS NULL AND draft_token_hash=?", (mid, hashlib.sha256(token.encode()).hexdigest())).fetchone()
            if not media or not media["expires_at"] or media["expires_at"] <= now():
                raise HTTPException(400, "草稿图片不存在或已过期")
        if token_media:
            draft_ids = set(token_media)
            updated = c.execute("UPDATE product_media SET product_id=?,draft_token_hash=NULL,expires_at=NULL WHERE id IN (%s) AND product_id IS NULL" % ",".join("?" for _ in draft_ids), (pid, *sorted(draft_ids)))
            if updated.rowcount != len(draft_ids):
                raise HTTPException(400, "草稿图片已被使用")
            for text_field in ("description", "safety_notice"):
                value = getattr(data, text_field)
                for mid, token in token_media.items():
                    value = value.replace(f'/api/draft-media/{token}/{mid}', f'/api/product-media/{mid}')
                if text_field == "description": c.execute("UPDATE products SET description=? WHERE id=?", (value, pid))
                else: c.execute("UPDATE products SET safety_notice=? WHERE id=?", (value, pid))
        for item in data.subapps:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:120]
            if not name:
                continue
            sub_id = item.get("id")
            if sub_id:
                owned = c.execute("SELECT id FROM subapps WHERE id=? AND product_id=?", (sub_id, pid)).fetchone()
                if not owned:
                    raise HTTPException(400, "子软件不属于该软件")
                c.execute("UPDATE subapps SET name=? WHERE id=?", (name, sub_id))
            else:
                sub_id = c.execute("INSERT INTO subapps(product_id,name) VALUES(?,?)", (pid,name)).lastrowid
            for release in item.get("releases", []):
                version = str(release.get("version", "")).strip()[:80]
                if not version:
                    continue
                rid = release.get("id")
                if rid:
                    owned = c.execute("SELECT id FROM releases WHERE id=? AND subapp_id=?", (rid, sub_id)).fetchone()
                    if not owned:
                        raise HTTPException(400, "版本不属于该子软件")
                    c.execute("UPDATE releases SET version=?,notes=?,platform=?,size=? WHERE id=?", (version,str(release.get("notes", ""))[:10000],str(release.get("platform", ""))[:80],str(release.get("size", ""))[:80],rid))
                else:
                    rid = c.execute("INSERT INTO releases(product_id,subapp_id,version,notes,created_at,platform,size) VALUES(?,?,?,?,?,?,?)", (pid,sub_id,version,str(release.get("notes", ""))[:10000],now(),str(release.get("platform", ""))[:80],str(release.get("size", ""))[:80])).lastrowid
                ProductInput._save_release_files(c, rid, release)
        for release in data.releases:
            version = str(release.get("version", "")).strip()[:80]
            if not version:
                continue
            rid = release.get("id")
            if rid:
                owned = c.execute("SELECT id FROM releases WHERE id=? AND product_id=? AND subapp_id IS NULL", (rid,pid)).fetchone()
                if not owned:
                    raise HTTPException(400, "版本不属于该软件")
                c.execute("UPDATE releases SET version=?,notes=?,platform=?,size=? WHERE id=?", (version,str(release.get("notes", ""))[:10000],str(release.get("platform", ""))[:80],str(release.get("size", ""))[:80],rid))
            else:
                rid = c.execute("INSERT INTO releases(product_id,version,notes,created_at,platform,size) VALUES(?,?,?,?,?,?)", (pid,version,str(release.get("notes", ""))[:10000],now(),str(release.get("platform", ""))[:80],str(release.get("size", ""))[:80])).lastrowid
            ProductInput._save_release_files(c, rid, release)
    return {"id":pid}


@app.delete("/api/admin/products/{pid}")
def delete_product(pid: int,request: Request):
    require_admin(request); write("DELETE FROM products WHERE id=?",(pid,)); return {"ok":True}


async def save_upload(file: UploadFile, allowed: set[str], max_mb: int = 50, content_type: Optional[str] = None):
    name=Path(file.filename or "file").name
    ext=Path(name).suffix.lower()
    if ext not in allowed: raise HTTPException(415,"不支持的文件类型")
    key=uuid.uuid4().hex+ext
    path=STAGING/key
    size=0
    with path.open("wb") as out:
        while chunk:=await file.read(1024*1024):
            size+=len(chunk)
            if size>min(MAX_BYTES,max_mb*1024*1024):
                path.unlink(missing_ok=True); raise HTTPException(413,"文件超过大小限制")
            out.write(chunk)
    try:
        storage.upload(key,path,content_type or file.content_type or "application/octet-stream")
    finally:
        path.unlink(missing_ok=True)
    return key,name


@app.delete("/api/admin/draft-media/{token}")
def cancel_draft_media(token: str, request: Request):
    require_admin(request)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", token):
        raise HTTPException(400, "无效草稿令牌")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        objects = c.execute("SELECT object_key FROM product_media WHERE product_id IS NULL AND draft_token_hash=?", (digest,)).fetchall()
        c.execute("DELETE FROM product_media WHERE product_id IS NULL AND draft_token_hash=?", (digest,))
    for item in objects:
        try:
            storage.delete(item["object_key"])
        except Exception:
            with db() as c:
                c.execute("INSERT OR IGNORE INTO expired_media_cleanup(object_key) VALUES(?)", (item["object_key"],))
    return {"ok": True, "deleted": len(objects)}


def cleanup_expired_draft_media():
    with db() as c:
        objects = c.execute("SELECT object_key FROM product_media WHERE product_id IS NULL AND expires_at<=?", (now(),)).fetchall()
        c.execute("DELETE FROM product_media WHERE product_id IS NULL AND expires_at<=?", (now(),))
        drafts = c.execute("SELECT id,object_key FROM solution_media WHERE solution_id IS NULL AND expires_at<=? LIMIT 100", (now(),)).fetchall()
        for draft in drafts:
            c.execute("INSERT OR IGNORE INTO expired_media_cleanup(object_key) VALUES(?)", (draft["object_key"],))
            c.execute("DELETE FROM solution_media WHERE id=?", (draft["id"],))
        retry = c.execute("SELECT object_key FROM expired_media_cleanup LIMIT 100").fetchall()
    for item in objects:
        with db() as c:
            c.execute("INSERT OR IGNORE INTO expired_media_cleanup(object_key) VALUES(?)", (item["object_key"],))
    for item in [*objects, *retry]:
        try:
            storage.delete(item["object_key"])
            with db() as c:
                c.execute("DELETE FROM expired_media_cleanup WHERE object_key=?", (item["object_key"],))
        except Exception:
            pass
    return len(objects)




def cleanup_draft_media_on_startup():
    cleanup_expired_draft_media()
    cleanup_product_screenshots()


def cleanup_product_screenshots():
    """Retire legacy screenshots, retrying failed storage deletions on next startup."""
    with db() as c:
        screenshots = c.execute("SELECT id,object_key FROM screenshots").fetchall()
        for item in screenshots:
            key = item["object_key"]
            shared = any(c.execute(f"SELECT 1 FROM {table} WHERE object_key=? LIMIT 1", (key,)).fetchone()
                         for table in ("deliverables", "product_media", "icon_objects", "uploaded_objects", "solution_files", "feedback_files", "feedback_comment_files"))
            if not shared and not c.execute("SELECT 1 FROM screenshots WHERE id!=? AND object_key=?", (item["id"], key)).fetchone():
                try:
                    storage.delete(key)
                except Exception:
                    logging.exception("Could not delete legacy screenshot %s", item["id"])
                    continue
            c.execute("DELETE FROM screenshots WHERE id=?", (item["id"],))


@app.post("/api/admin/upload")
async def admin_upload(request: Request,file: UploadFile=File(...), kind: str=Form("release"), product_id: int=Form(0), draft_token: str=Form("")):
    require_admin(request)
    if kind == 'rich-image':
        if product_id and not row("SELECT id FROM products WHERE id=?", (product_id,)):
            raise HTTPException(400, "产品不存在")
        if not product_id and not re.fullmatch(r"[0-9a-fA-F-]{36}", draft_token):
            raise HTTPException(400, "无效草稿令牌")
        ext = Path(file.filename or '').suffix.lower()
        formats = {'.png': ('PNG', 'image/png'), '.jpg': ('JPEG', 'image/jpeg'), '.jpeg': ('JPEG', 'image/jpeg'), '.gif': ('GIF', 'image/gif'), '.webp': ('WEBP', 'image/webp')}
        if ext not in formats:
            raise HTTPException(415, "仅支持 PNG/JPEG/GIF/WebP")
        content = await file.read(min(MAX_BYTES, 5 * 1024 * 1024) + 1)
        if len(content) > min(MAX_BYTES, 5 * 1024 * 1024):
            raise HTTPException(413, "图片最大 5 MB")
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format != formats[ext][0] or image.width * image.height > 25000000:
                    raise ValueError('Invalid image')
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(415, "无效图片或图片尺寸过大")
        await file.seek(0)
        key, name = await save_upload(file, set(formats), 5, formats[ext][1])
        if product_id:
            mid = write("INSERT INTO product_media(product_id,object_key,filename,content_type) VALUES(?,?,?,?)", (product_id, key, name, formats[ext][1]))
            return {'id': mid, 'url': f'/api/product-media/{mid}'}
        expires = datetime.fromtimestamp(time.time() + 86400, timezone.utc).isoformat()
        mid = write("INSERT INTO product_media(product_id,draft_token_hash,expires_at,object_key,filename,content_type) VALUES(NULL,?,?,?,?,?)", (hashlib.sha256(draft_token.encode()).hexdigest(), expires, key, name, formats[ext][1]))
        return {'id': mid, 'url': f'/api/draft-media/{draft_token}/{mid}', 'expires_at': expires}
    if kind not in {'icon', 'release'}:
        raise HTTPException(400, "无效上传类型")
    if kind=="icon":
        image_types={".png":b"\x89PNG\r\n\x1a\n", ".jpg":b"\xff\xd8\xff", ".jpeg":b"\xff\xd8\xff", ".gif":b"GIF8", ".webp":b"RIFF"}
        ext=Path(file.filename or "").suffix.lower()
        header=await file.read(16)
        await file.seek(0)
        if ext not in image_types or not header.startswith(image_types[ext]) or (ext==".webp" and header[8:12]!=b"WEBP"):
            raise HTTPException(415,"不支持的图片格式")
        key,name=await save_upload(file,set(image_types),5)
        write("INSERT INTO icon_objects(object_key,filename) VALUES(?,?)", (key,name))
        return {"object_key":key,"filename":name}
    allowed={".zip",".exe",".msi",".dmg",".pkg",".appimage",".deb",".rpm",".7z",".tar",".gz",".pdf",".png",".jpg",".jpeg",".webp",".gif",".mp4",".webm",".mov",".txt",".md",".docx",".xlsx",".pptx",".json",".crx"}
    key,name=await save_upload(file,allowed,100)
    write("INSERT INTO uploaded_objects VALUES(?,?)",(key,name))
    return {"object_key":key,"filename":name}

@app.post("/api/admin/solutions/{sid}")
def edit_solution(sid: int, request: Request, title: str = Form(...), body: str = Form(...), category: str = Form(""), author: str = Form(""), tags: list[str] = Form([]), product_ids: list[int] = Form([]), draft_token: str = Form("")):
    require_admin(request)
    if not 4 <= len(title.strip()) <= 200 or len(body) > 30000:
        raise HTTPException(400, "标题须为 4–200 字，正文不超过 30000 字")
    body = clean_rich(body)
    if not meaningful_rich(body):
        raise HTTPException(400, "正文内容不足")
    normalized = list(dict.fromkeys(tag.strip() for tag in tags))
    if len(normalized) > 20 or any(not tag or len(tag) > 30 or any(ord(c) < 32 or ord(c) == 127 for c in tag) for tag in normalized):
        raise HTTPException(400, "最多 20 个标签，每个须为 1 至 30 字")
    digest = solution_token_hash(draft_token) if draft_token else None
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT id FROM solutions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "方案不存在")
        ids = sorted(set(product_ids))
        if any(not c.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone() for pid in ids):
            raise HTTPException(400, "关联产品不存在")
        image_ids = set()
        def link_image(match):
            token, mid = match.group(1), int(match.group(2))
            if not digest or solution_token_hash(token) != digest or not c.execute("SELECT id FROM solution_media WHERE id=? AND solution_id IS NULL AND draft_token_hash=? AND expires_at>?", (mid, digest, now())).fetchone():
                raise HTTPException(400, "草稿图片不存在或不属于此草稿")
            image_ids.add(mid)
            return f'/api/solution-media/{mid}'
        body = re.sub(r'/api/solution-draft-media/([0-9a-f-]{36})/([1-9][0-9]*)', link_image, body)
        for mid in set(re.findall(r'/api/solution-media/([1-9][0-9]*)', body)):
            if int(mid) not in image_ids and not c.execute("SELECT id FROM solution_media WHERE id=? AND solution_id=?", (int(mid), sid)).fetchone():
                raise HTTPException(400, "图片不属于此方案")
        c.execute("UPDATE solutions SET title=?,body=?,category=?,author=?,tags=?,product_id=? WHERE id=?", (title.strip(), body, category[:80], author.strip()[:60] or "匿名", json.dumps(normalized, ensure_ascii=False), ids[0] if ids else None, sid))
        c.execute("DELETE FROM solution_products WHERE solution_id=?", (sid,))
        c.executemany("INSERT INTO solution_products(solution_id,product_id) VALUES(?,?)", [(sid, pid) for pid in ids])
        for mid in image_ids:
            c.execute("UPDATE solution_media SET solution_id=?,draft_token_hash=NULL,expires_at=NULL WHERE id=?", (sid, mid))
    return {"ok": True}


@app.post("/api/admin/solutions/{sid}/products")
def update_solution_products(sid: int, request: Request, product_ids: list[int]):
    require_admin(request)
    with db() as c:
        if not c.execute("SELECT id FROM solutions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "方案不存在")
        ids = sorted(set(product_ids))
        if any(not c.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone() for pid in ids):
            raise HTTPException(400, "关联产品不存在")
        c.execute("DELETE FROM solution_products WHERE solution_id=?", (sid,))
        c.executemany("INSERT INTO solution_products(solution_id,product_id) VALUES(?,?)", [(sid,pid) for pid in ids])
        c.execute("UPDATE solutions SET product_id=? WHERE id=?", (ids[0] if ids else None, sid))
    return {"ok": True}


@app.post("/api/admin/solutions/{sid}/status")
def solution_status(sid:int,request:Request,status:str=Form(...),reason:str=Form("")):
    require_admin(request)
    if status not in {"approved","rejected","pending"}: raise HTTPException(400,"无效状态")
    if not row("SELECT id FROM solutions WHERE id=?", (sid,)):
        raise HTTPException(404, "方案不存在")
    write("UPDATE solutions SET status=?,reject_reason=?,featured_slot=CASE WHEN ?='approved' THEN featured_slot ELSE 0 END WHERE id=?",(status,reason[:1000],status,sid)); return {"ok":True}


@app.post("/api/admin/solutions/{sid}/featured")
def feature_solution(sid:int,request:Request,featured_slot:int=Form(...)):
    require_admin(request)
    if featured_slot not in (0,1,2,3): raise HTTPException(400,"无效推荐位置")
    with db() as c:
        item=c.execute("SELECT status FROM solutions WHERE id=?",(sid,)).fetchone()
        if not item: raise HTTPException(404,"方案不存在")
        if featured_slot and item["status"] != "approved": raise HTTPException(400,"仅已通过的方案可推荐")
        if featured_slot:
            c.execute("UPDATE solutions SET featured_slot=0 WHERE featured_slot=?",(featured_slot,))
        c.execute("UPDATE solutions SET featured_slot=? WHERE id=?",(featured_slot,sid))
    return {"ok":True}


@app.delete("/api/admin/solutions/{sid}")
def delete_solution(sid:int,request:Request): require_admin(request); write("DELETE FROM solutions WHERE id=?",(sid,)); return {"ok":True}


@app.get("/api/feedback-files/{fid}")
def public_feedback_file(fid:int):
    f=row("SELECT ff.* FROM feedback_files ff JOIN feedback f ON f.id=ff.feedback_id WHERE ff.id=? AND f.status='approved'",(fid,))
    if not f: raise HTTPException(404,"文件不存在")
    return storage.serve(f["object_key"],f["filename"],f["content_type"],attachment=False)


@app.get("/api/admin/feedback-files/{fid}")
def feedback_file(fid:int,request:Request):
    require_admin(request)
    f=row("SELECT * FROM feedback_files WHERE id=?",(fid,))
    if not f: raise HTTPException(404,"文件不存在")
    return storage.serve(f["object_key"],f["filename"],f["content_type"],attachment=False)


@app.post("/api/admin/taxonomies")
def taxonomy(request:Request,kind:str=Form(...),name:str=Form(...)):
    require_admin(request)
    if kind not in {"platform","type"} or not name.strip() or len(name)>80: raise HTTPException(400,"无效分类")
    write("INSERT OR IGNORE INTO taxonomies VALUES(?,?)",(kind,name.strip()))
    return {"ok":True}


@app.delete("/api/admin/taxonomies/{kind}/{name}")
def delete_taxonomy(kind:str,name:str,request:Request):
    require_admin(request)
    write("DELETE FROM taxonomies WHERE kind=? AND name=?",(kind,name))
    return {"ok":True}


@app.post("/api/feedback/{fid}/like")
def like_feedback(fid:int,request:Request):
    if not row("SELECT id FROM feedback WHERE id=? AND status='approved'",(fid,)): raise HTTPException(404,"反馈不存在")
    voter=request.cookies.get("portal_voter") or secrets.token_urlsafe(24)
    with db() as c:
        exists=c.execute("SELECT 1 FROM feedback_votes WHERE feedback_id=? AND voter=?",(fid,voter)).fetchone()
        if exists: c.execute("DELETE FROM feedback_votes WHERE feedback_id=? AND voter=?",(fid,voter)); liked=False
        else: c.execute("INSERT INTO feedback_votes VALUES(?,?)",(fid,voter)); liked=True
        count=c.execute("SELECT count(*) FROM feedback_votes WHERE feedback_id=?",(fid,)).fetchone()[0]
    response=JSONResponse({"liked":liked,"likes":count}); response.set_cookie("portal_voter",voter,httponly=True,samesite="lax",max_age=31536000); return response


@app.get("/api/feedback-comment-files/{file_id}")
def public_feedback_comment_file(file_id:int):
    f=row("SELECT cf.* FROM feedback_comment_files cf JOIN feedback_comments c ON c.id=cf.comment_id WHERE cf.id=? AND c.status='approved' AND EXISTS(SELECT 1 FROM feedback f WHERE f.id=c.feedback_id AND f.status='approved')",(file_id,))
    if not f: raise HTTPException(404,"文件不存在")
    return storage.serve(f["object_key"],f["filename"],f["content_type"],attachment=False)


@app.get("/api/admin/feedback-comment-files/{file_id}")
def admin_feedback_comment_file(file_id:int,request:Request):
    require_admin(request)
    f=row("SELECT * FROM feedback_comment_files WHERE id=?",(file_id,))
    if not f: raise HTTPException(404,"文件不存在")
    return storage.serve(f["object_key"],f["filename"],f["content_type"],attachment=False)


@app.post("/api/feedback/{fid}/comments")
async def feedback_comment(fid:int,request:Request,body:str=Form(...),nickname:str=Form(""),parent_id:int=Form(0),files:list[UploadFile]=File(default=[])):
    rate(request,"feedback-comment",8)
    if not row("SELECT id FROM feedback WHERE id=? AND status='approved'",(fid,)): raise HTTPException(404,"反馈不存在")
    if len(body.strip())<1 or len(body)>2000: raise HTTPException(400,"评论长度需为 1–2000 字")
    if len(files)>5: raise HTTPException(400,"最多上传 5 张图片")
    if parent_id and not row("SELECT id FROM feedback_comments WHERE id=? AND feedback_id=? AND status='approved'",(parent_id,fid)): raise HTTPException(400,"回复目标不存在")
    cid=write("INSERT INTO feedback_comments(feedback_id,parent_id,body,nickname,status,created_at,ip) VALUES(?,?,?,?,'pending',?,?)",(fid,parent_id or None,body.strip(),nickname.strip()[:60] or "匿名",now(),client_ip(request)))
    for upload in files:
        if not upload.filename: continue
        suffix=Path(upload.filename).suffix.lower()
        if suffix not in {".png",".jpg",".jpeg",".webp",".gif"}: raise HTTPException(400,"仅支持 PNG/JPG/WEBP/GIF 图片")
        key,name=await save_upload(upload,{".png",".jpg",".jpeg",".webp",".gif"},50)
        write("INSERT INTO feedback_comment_files(comment_id,object_key,filename,content_type) VALUES(?,?,?,?)",(cid,key,name,upload.content_type or "image/jpeg"))
    return {"id":cid,"status":"pending"}


@app.post("/api/admin/feedback/{fid}/handled")
def handle_feedback(fid:int,request:Request,handled:bool=Form(True)):
    require_admin(request)
    write("UPDATE feedback SET handled=? WHERE id=?",(int(handled),fid))
    return {"ok":True}


@app.delete("/api/admin/feedback/{fid}")
def delete_feedback(fid:int,request:Request): require_admin(request); write("DELETE FROM feedback WHERE id=?",(fid,)); return {"ok":True}


@app.post("/api/admin/categories")
def add_category(request:Request,name:str=Form(...)):
    require_admin(request)
    if len(name.strip())>80: raise HTTPException(400,"分类名称过长")
    try: write("INSERT INTO categories(name) VALUES(?)",(name.strip(),))
    except sqlite3.IntegrityError: raise HTTPException(409,"分类已存在")
    return {"ok":True}


@app.delete("/api/admin/categories/{name}")
def del_category(name:str,request:Request): require_admin(request); write("DELETE FROM categories WHERE name=?",(name,)); return {"ok":True}


@app.post("/api/admin/articles")
def save_article(request:Request,title:str=Form(...),body:str=Form(...),category:str=Form(""),article_id:int=Form(0),published:bool=Form(False)):
    require_admin(request)
    if article_id: write("UPDATE articles SET title=?,body=?,category=?,published=? WHERE id=?",(title[:200],body[:30000],category[:80],int(published),article_id)); aid=article_id
    else: aid=write("INSERT INTO articles(title,body,category,published,created_at) VALUES(?,?,?,?,?)",(title[:200],body[:30000],category[:80],int(published),now()))
    return {"id":aid}


@app.delete("/api/admin/articles/{aid}")
def delete_article(aid:int,request:Request): require_admin(request); write("DELETE FROM articles WHERE id=?",(aid,)); return {"ok":True}


class PasswordInput(BaseModel):
    current: str
    new: str = Field(min_length=12,max_length=256)


@app.post("/api/admin/password")
def change_password(data:PasswordInput,request:Request):
    require_admin(request)
    saved=row("SELECT value FROM settings WHERE key='admin_hash'")
    if not verify_password(data.current, saved["value"]): raise HTTPException(400,"当前密码不正确")
    write("INSERT OR REPLACE INTO settings VALUES('admin_hash',?)",(hash_password(data.new),))
    return {"ok":True}


def event(kind: str, pid: int):
    write("INSERT INTO events(kind,product_id,created_at) VALUES(?,?,?)",(kind,pid,now()))


@app.get("/api/download/{did}")
def download(did:int):
    d=row("SELECT d.*,p.id pid,p.published FROM deliverables d JOIN releases r ON r.id=d.release_id JOIN products p ON p.id=r.product_id WHERE d.id=? AND d.kind='file' AND d.object_key!=''",(did,))
    if not d or not d["published"]: raise HTTPException(404,"文件资源不可用")
    response=storage.serve(d["object_key"],d["filename"],"application/octet-stream")
    event("download",d["pid"])
    return response


@app.post("/api/feedback")
async def feedback(request:Request,product_id:int=Form(...),body:str=Form(...),nickname:str=Form(""),files:list[UploadFile]=File(default=[])):
    rate(request,"feedback",6)
    if len(body.strip())<2 or len(body)>3000: raise HTTPException(400,"反馈长度需为 2–3000 字")
    if not row("SELECT id FROM products WHERE id=? AND published=1",(product_id,)): raise HTTPException(404,"产品不存在")
    if len(files)>5: raise HTTPException(400,"最多上传 5 张截图")
    fid=write("INSERT INTO feedback(product_id,body,nickname,status,created_at,ip) VALUES(?,?,?,'pending',?,?)",(product_id,body.strip(),nickname.strip()[:60] or "匿名",now(),client_ip(request)))
    for f in files:
        if not f.filename: continue
        key,name=await save_upload(f,{".png",".jpg",".jpeg",".webp",".gif"},50)
        write("INSERT INTO feedback_files(feedback_id,object_key,filename,content_type) VALUES(?,?,?,?)",(fid,key,name,mimetypes.guess_type(name)[0] or "application/octet-stream"))
    return {"ok":True}


def solution_token_hash(token: str):
    try:
        parsed = uuid.UUID(token)
        if str(parsed) != token.lower():
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "无效草稿令牌")
    return hashlib.sha256(str(parsed).encode()).hexdigest()


@app.post("/api/solution-media")
async def upload_solution_media(request: Request, file: UploadFile = File(...), draft_token: str = Form(...)):
    """Public capability-token image drafts expire after 24 hours."""
    rate(request, "solution-image", 20)
    digest = solution_token_hash(draft_token)
    cleanup_expired_draft_media()
    formats = {'.png': ('PNG', 'image/png'), '.jpg': ('JPEG', 'image/jpeg'), '.jpeg': ('JPEG', 'image/jpeg'), '.gif': ('GIF', 'image/gif'), '.webp': ('WEBP', 'image/webp')}
    ext = Path(file.filename or '').suffix.lower()
    if ext not in formats:
        raise HTTPException(415, "仅支持 PNG/JPEG/GIF/WebP")
    content = await file.read(min(MAX_BYTES, 5 * 1024 * 1024) + 1)
    if len(content) > min(MAX_BYTES, 5 * 1024 * 1024):
        raise HTTPException(413, "图片最大 5 MB")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != formats[ext][0] or image.width * image.height > 25000000:
                raise ValueError('Invalid image')
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(415, "无效图片或图片尺寸过大")
    await file.seek(0)
    key, name = await save_upload(file, set(formats), 5, formats[ext][1])
    expires = datetime.fromtimestamp(time.time() + 86400, timezone.utc).isoformat()
    try:
        mid = write("INSERT INTO solution_media(draft_token_hash,expires_at,object_key,filename,content_type) VALUES(?,?,?,?,?)", (digest, expires, key, name, formats[ext][1]))
    except Exception:
        with db() as c:
            c.execute("INSERT OR IGNORE INTO expired_media_cleanup(object_key) VALUES(?)", (key,))
        raise
    return {'id': mid, 'url': f'/api/solution-draft-media/{draft_token.lower()}/{mid}', 'expires_at': expires}


@app.get("/api/solution-draft-media/{token}/{mid}")
def get_solution_draft_media(token: str, mid: int):
    try:
        digest = solution_token_hash(token)
    except HTTPException:
        raise HTTPException(404, "图片不存在")
    media = row("SELECT * FROM solution_media WHERE id=? AND solution_id IS NULL AND draft_token_hash=? AND expires_at>?", (mid, digest, now()))
    if not media:
        raise HTTPException(404, "图片不存在")
    response = storage.serve(media['object_key'], media['filename'], media['content_type'], attachment=False)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.get("/api/solution-media/{mid}")
def get_solution_media(mid: int, request: Request):
    media = row("SELECT m.*,s.status FROM solution_media m JOIN solutions s ON s.id=m.solution_id WHERE m.id=?", (mid,))
    if not media or (media['status'] != 'approved' and not is_admin(request)):
        raise HTTPException(404, "图片不存在")
    response = storage.serve(media['object_key'], media['filename'], media['content_type'], attachment=False)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.post("/api/solutions")
async def submit_solution(request:Request,title:str=Form(...),body:str=Form(...),category:str=Form(""),product_id:int=Form(0),product_ids:list[int]=Form([]),author:str=Form(""),files:list[UploadFile]=File(default=[]),tags:list[str]=Form([]),draft_token:str=Form("")):
    rate(request,"solution",4)
    if len(title.strip())<4 or len(title)>200 or len(body)>30000: raise HTTPException(400,"标题至少 4 字，正文至少 10 字且不超过 30000 字")
    body = clean_rich(body)
    if not meaningful_rich(body): raise HTTPException(400, "正文内容不足")
    normalized_tags = []
    for tag in tags:
        tag = tag.strip()
        if not tag or len(tag) > 30 or any(ord(c) < 32 or ord(c) == 127 for c in tag):
            raise HTTPException(400, "标签须为 1 至 30 字")
        if tag not in normalized_tags:
            normalized_tags.append(tag)
    if len(normalized_tags) > 20:
        raise HTTPException(400, "最多 20 个标签")
    digest = solution_token_hash(draft_token) if draft_token else None
    if len(files)>5: raise HTTPException(400,"最多上传 5 个附件")
    pids=sorted(set(product_ids + ([product_id] if product_id else [])))
    if any(not row("SELECT id FROM products WHERE id=? AND published=1", (pid,)) for pid in pids): raise HTTPException(400,"关联产品不存在")
    with db() as c:
        def link_image(match):
            token, mid = match.group(1), int(match.group(2))
            if not digest or solution_token_hash(token) != digest:
                raise HTTPException(400, "图片不属于此草稿")
            media = c.execute("SELECT id FROM solution_media WHERE id=? AND solution_id IS NULL AND draft_token_hash=? AND expires_at>?", (mid, digest, now())).fetchone()
            if not media:
                raise HTTPException(400, "草稿图片不存在或已过期")
            image_ids.add(mid)
            return f'/api/solution-media/{mid}'
        image_ids = set()
        c.execute("BEGIN IMMEDIATE")
        body = re.sub(r'/api/solution-draft-media/([0-9a-f-]{36})/([1-9][0-9]*)', link_image, body)
        sid = c.execute("INSERT INTO solutions(title,body,tags,category,product_id,author,status,ip,created_at) VALUES(?,?,?,?,?,?,'pending',?,?)", (title.strip(),body.strip(),json.dumps(normalized_tags, ensure_ascii=False),category[:80],pids[0] if pids else None,author.strip()[:60] or "匿名",client_ip(request),now())).lastrowid
        c.executemany("INSERT INTO solution_products(solution_id,product_id) VALUES(?,?)", [(sid, pid) for pid in pids])
        for mid in image_ids:
            c.execute("UPDATE solution_media SET solution_id=?,draft_token_hash=NULL,expires_at=NULL WHERE id=?", (sid, mid))
    allowed={".png",".jpg",".jpeg",".webp",".gif",".mp4",".webm",".mov",".pdf",".zip",".txt",".md"}
    for f in files:
        if not f.filename: continue
        key,name=await save_upload(f,allowed,50)
        write("INSERT INTO solution_files(solution_id,object_key,filename,content_type) VALUES(?,?,?,?)",(sid,key,name,f.content_type or "application/octet-stream"))
    return {"id":sid,"status":"pending"}


@app.post("/api/solutions/{sid}/comments")
def comment(sid:int,request:Request,body:str=Form(...),author:str=Form(""),parent_id:int=Form(0)):
    rate(request,"comment",8)
    if not row("SELECT id FROM solutions WHERE id=? AND status='approved'",(sid,)): raise HTTPException(404,"内容不存在")
    if parent_id and not row("SELECT id FROM comments WHERE id=? AND solution_id=? AND status='approved'",(parent_id,sid)): raise HTTPException(400,"回复目标不存在")
    if len(body.strip())<1 or len(body)>3000: raise HTTPException(400,"回复长度需为 1–3000 字")
    cid=write("INSERT INTO comments(solution_id,parent_id,body,author,ip,status,created_at) VALUES(?,?,?,?,?,'pending',?)",(sid,parent_id or None,body.strip(),author.strip()[:60] or "匿名",client_ip(request),now())); return {"id":cid,"status":"pending"}


@app.get("/api/products/{pid}")
def product_detail(pid:int):
    if not row("SELECT id FROM products WHERE id=? AND published=1",(pid,)): raise HTTPException(404,"产品不存在")
    write("UPDATE products SET views=views+1 WHERE id=?",(pid,))
    with db() as c:
        p=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    product=public_product(dict(p))
    product["feedback"] = [{**f,"files":rows("SELECT id,filename FROM feedback_files WHERE feedback_id=?",(f["id"],)),"likes":row("SELECT count(*) n FROM feedback_votes WHERE feedback_id=?",(f["id"],))["n"],"comments":[{**comment,"files":rows("SELECT id,filename FROM feedback_comment_files WHERE comment_id=?",(comment["id"],))} for comment in rows("SELECT id,parent_id,body,nickname,created_at,ip FROM feedback_comments WHERE feedback_id=? AND status='approved' ORDER BY id",(f["id"],))]} for f in rows("SELECT id,product_id,body,nickname,created_at,ip FROM feedback WHERE product_id=? AND status='approved' ORDER BY id DESC LIMIT 100",(pid,))]
    product["solutions"]=rows("SELECT s.id,s.title,s.tags,s.category,(SELECT count(*) FROM solution_likes l WHERE l.solution_id=s.id) likes FROM solutions s JOIN solution_products sp ON sp.solution_id=s.id WHERE sp.product_id=? AND s.status='approved' ORDER BY likes DESC,s.created_at DESC",(pid,))
    for solution in product["solutions"]:
        solution["tags"] = json.loads(solution["tags"])
    return product


@app.post("/api/products/{pid}/vote")
def vote_product(pid:int,request:Request,value:int=Form(...)):
    rate(request,"vote",30,60)
    if value not in (-1,0,1): raise HTTPException(400,"投票值无效")
    if not row("SELECT id FROM products WHERE id=? AND published=1",(pid,)): raise HTTPException(404,"产品不存在")
    voter=request.cookies.get("portal_voter") or secrets.token_urlsafe(24)
    with db() as c:
        if value: c.execute("INSERT INTO product_votes VALUES(?,?,?) ON CONFLICT(product_id,voter) DO UPDATE SET value=excluded.value",(pid,voter,value))
        else: c.execute("DELETE FROM product_votes WHERE product_id=? AND voter=?",(pid,voter))
        counts={v:c.execute("SELECT count(*) FROM product_votes WHERE product_id=? AND value=?",(pid,v)).fetchone()[0] for v in (-1,1)}
    response=JSONResponse({"up":counts[1],"down":counts[-1],"value":value})
    response.set_cookie("portal_voter",voter,httponly=True,samesite="lax",max_age=31536000)
    return response


@app.post("/api/solutions/{sid}/like")
def like_solution(sid:int,request:Request):
    if not row("SELECT id FROM solutions WHERE id=? AND status='approved'",(sid,)): raise HTTPException(404,"内容不存在")
    voter=request.cookies.get("portal_voter") or secrets.token_urlsafe(24)
    with db() as c:
        exists=c.execute("SELECT 1 FROM solution_likes WHERE solution_id=? AND voter=?",(sid,voter)).fetchone()
        if exists: c.execute("DELETE FROM solution_likes WHERE solution_id=? AND voter=?",(sid,voter)); liked=False
        else: c.execute("INSERT INTO solution_likes VALUES(?,?)",(sid,voter)); liked=True
        count=c.execute("SELECT count(*) FROM solution_likes WHERE solution_id=?",(sid,)).fetchone()[0]
    response=JSONResponse({"liked":liked,"likes":count}); response.set_cookie("portal_voter",voter,httponly=True,samesite="lax",max_age=31536000); return response


@app.post("/api/admin/comments/{cid}/status")
def comment_status(cid:int,request:Request,status:str=Form(...)):
    require_admin(request)
    if status not in {"approved","rejected","pending"}: raise HTTPException(400,"无效状态")
    if not row("SELECT id FROM comments WHERE id=?",(cid,)): raise HTTPException(404,"评论不存在")
    write("UPDATE comments SET status=? WHERE id=?",(status,cid)); return {"ok":True}


@app.post("/api/admin/feedback/{fid}/status")
def feedback_status(fid:int,request:Request,status:str=Form(...)):
    require_admin(request)
    if status not in {"approved","rejected","pending"}: raise HTTPException(400,"无效状态")
    if not row("SELECT id FROM feedback WHERE id=?",(fid,)): raise HTTPException(404,"反馈不存在")
    write("UPDATE feedback SET status=? WHERE id=?",(status,fid)); return {"ok":True}


@app.post("/api/admin/feedback-comments/{cid}/status")
def feedback_comment_status(cid:int,request:Request,status:str=Form(...)):
    require_admin(request)
    if status not in {"approved","rejected","pending"}: raise HTTPException(400,"无效状态")
    if not row("SELECT id FROM feedback_comments WHERE id=?",(cid,)): raise HTTPException(404,"评论不存在")
    write("UPDATE feedback_comments SET status=? WHERE id=?",(status,cid)); return {"ok":True}


if (ROOT/"dist").is_dir(): app.mount("/assets",StaticFiles(directory=ROOT/"dist"/"assets"),name="assets")

@app.get("/{path:path}")
def frontend(path:str):
    target=ROOT/"dist"/path
    if target.is_file() and ROOT/"dist" in target.resolve().parents: return FileResponse(target)
    if (ROOT/"dist"/"index.html").is_file(): return FileResponse(ROOT/"dist"/"index.html")
    return JSONResponse({"message":"前端尚未构建，请执行 npm run build"},status_code=503)
