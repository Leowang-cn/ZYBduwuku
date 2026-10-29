"""Read-only MCP query service for the public software catalogue.

The query functions are intentionally independent of the MCP SDK so they can be
used by tests and by other read-only integrations.  When ``mcp`` is installed,
this module also exposes a FastMCP server over stdio or Streamable HTTP.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

MAX_QUERY_LENGTH = 200
MAX_TEXT_LENGTH = 4000
MAX_LIMIT = 50
DEFAULT_LIMIT = 10


def _bounded_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    return str(value or "")[:limit]


def _args(query: str = "", limit: int = DEFAULT_LIMIT) -> tuple[str, int]:
    query = _bounded_text(query, MAX_QUERY_LENGTH).strip()
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return query, limit


def connect_readonly(database: str | Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only URI mode with row mappings."""
    path = Path(database).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _product_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": _bounded_text(row["name"], 200),
        "summary": _bounded_text(row["summary"], 1000),
        "category": _bounded_text(row["category"], 200),
        "vendor": _bounded_text(row["vendor"], 200),
        "url": f"/api/products/{row['id']}",
    }


def search_software(database: str | Path, query: str = "", limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    query, limit = _args(query, limit)
    pattern = f"%{query}%"
    with connect_readonly(database) as conn:
        rows = conn.execute(
            """SELECT id,name,summary,category,vendor FROM products
               WHERE published=1 AND (?='' OR name LIKE ? OR summary LIKE ?
                 OR description LIKE ? OR category LIKE ? OR vendor LIKE ?)
               ORDER BY featured_slot > 0 DESC, name COLLATE NOCASE, id DESC LIMIT ?""",
            (query, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    return [_product_summary(row) for row in rows]


def get_software(database: str | Path, software_id: int) -> Optional[dict[str, Any]]:
    if not isinstance(software_id, int) or isinstance(software_id, bool) or software_id < 1:
        raise ValueError("software_id must be a positive integer")
    with connect_readonly(database) as conn:
        product = conn.execute(
            "SELECT id,name,summary,description,category,vendor,kind FROM products WHERE id=? AND published=1",
            (software_id,),
        ).fetchone()
        if not product:
            return None
        releases = conn.execute(
            """SELECT r.id,r.version,r.notes,r.platform,r.size,r.created_at,sa.name AS subapp_name
               FROM releases r LEFT JOIN subapps sa ON sa.id=r.subapp_id
               WHERE r.product_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 100""",
            (software_id,),
        ).fetchall()
        release_data = []
        for release in releases:
            deliverables = conn.execute(
                """SELECT d.id,d.label,d.kind,d.url,d.object_key,d.filename,d.platform,d.size,d.updated_at
                   FROM deliverables d WHERE d.release_id=? ORDER BY d.id""",
                (release["id"],),
            ).fetchall()
            release_data.append({
                "id": release["id"], "version": _bounded_text(release["version"], 200),
                "notes": _bounded_text(release["notes"]), "platform": _bounded_text(release["platform"], 80),
                "size": _bounded_text(release["size"], 80), "created_at": release["created_at"],
                "subapp": _bounded_text(release["subapp_name"], 200),
                "deliverables": [{
                    "id": d["id"], "label": _bounded_text(d["label"], 200), "kind": d["kind"],
                    "url": _bounded_text(d["url"], 1000) if d["kind"] == "url" else "",
                    "filename": _bounded_text(d["filename"], 255),
                    "platform": _bounded_text(d["platform"], 80), "size": _bounded_text(d["size"], 80),
                    "updated_at": d["updated_at"],
                    "download_url": f"/api/download/{d['id']}" if d["kind"] == "file" and d["object_key"] else "",
                } for d in deliverables],
            })
    return {
        "id": product["id"], "name": _bounded_text(product["name"], 200),
        "summary": _bounded_text(product["summary"], 1000), "description": _bounded_text(product["description"]),
        "category": _bounded_text(product["category"], 200), "vendor": _bounded_text(product["vendor"], 200),
        "kind": product["kind"], "url": f"/api/products/{product['id']}", "releases": release_data,
    }


def _solution_summary(row: sqlite3.Row) -> dict[str, Any]:
    try:
        tags = json.loads(row["tags"] or "[]")
    except (TypeError, json.JSONDecodeError):
        tags = []
    return {"id": row["id"], "title": _bounded_text(row["title"], 300),
            "summary": _bounded_text(row["body"], 1000), "category": _bounded_text(row["category"], 200),
            "tags": tags[:20] if isinstance(tags, list) else [], "author": _bounded_text(row["author"], 100),
            "created_at": row["created_at"], "url": f"/api/solutions/{row['id']}"}


def search_solutions(database: str | Path, query: str = "", limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    query, limit = _args(query, limit)
    pattern = f"%{query}%"
    with connect_readonly(database) as conn:
        rows = conn.execute(
            """SELECT id,title,body,category,tags,author,created_at FROM solutions
               WHERE status='approved' AND (?='' OR title LIKE ? OR body LIKE ? OR category LIKE ? OR tags LIKE ?)
               ORDER BY created_at DESC,id DESC LIMIT ?""",
            (query, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    return [_solution_summary(row) for row in rows]


def get_solution(database: str | Path, solution_id: int) -> dict[str, Any] | None:
    if not isinstance(solution_id, int) or isinstance(solution_id, bool) or solution_id < 1:
        raise ValueError("solution_id must be a positive integer")
    with connect_readonly(database) as conn:
        solution = conn.execute(
            "SELECT id,title,body,category,tags,author,created_at FROM solutions WHERE id=? AND status='approved'",
            (solution_id,),
        ).fetchone()
        if not solution:
            return None
        products = conn.execute(
            """SELECT p.id,p.name,p.summary,p.category,p.vendor FROM products p
               JOIN solution_products sp ON sp.product_id=p.id
               WHERE sp.solution_id=? AND p.published=1 ORDER BY p.name COLLATE NOCASE,p.id""",
            (solution_id,),
        ).fetchall()
    result = _solution_summary(solution)
    result["body"] = _bounded_text(solution["body"])
    result["products"] = [_product_summary(p) for p in products]
    return result


def create_server(database: str | Path):
    """Create the optional FastMCP server, if the SDK is installed."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the optional 'mcp' dependency to run the MCP server") from exc
    from mcp.server.transport_security import TransportSecuritySettings

    server = FastMCP(
        "software-portal-readonly",
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @server.tool()
    def search_software(query: str = "", limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        return globals()["search_software"](database, query, limit)

    @server.tool()
    def get_software(software_id: int) -> Optional[dict[str, Any]]:
        return globals()["get_software"](database, software_id)

    @server.tool()
    def search_solutions(query: str = "", limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        return globals()["search_solutions"](database, query, limit)

    @server.tool()
    def get_solution(solution_id: int) -> dict[str, Any] | None:
        return globals()["get_solution"](database, solution_id)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/app.sqlite"))
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    args = parser.parse_args()
    server = create_server(args.database)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
