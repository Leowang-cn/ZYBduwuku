import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from sls_analytics import SLSAnalyticsError, load_dashboard


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
DATABASE_PATH = DATA_DIR / "app.sqlite"
ALLOWED_EXTENSIONS = {
    ".apk", ".deb", ".dmg", ".exe", ".gz", ".msi", ".pkg", ".rar", ".rpm", ".tar", ".zip"
}

PRODUCT_PRESENTATION = {
    "Zipvid": {
        "mark": "ZV",
        "tone": "lime",
        "windows_version": "2.6.0",
        "macos_version": "2.6.0",
        "announcement": "新版将批量转码、压缩和字幕处理收进了同一条工作流。",
        "features": [
            ("批量转码", "一次导入多个视频，统一设置后连续处理。"),
            ("智能压缩", "在清晰度与文件体积之间快速取得平衡。"),
            ("本地处理", "素材无需上传，在设备本地完成转换。"),
        ],
        "releases": [
            ("2026-09-02", "2.6.0", "Windows · macOS", "新增字幕烧录预设，优化大批量任务的暂停与恢复。"),
            ("2026-07-18", "2.5.1", "Windows · macOS", "修复部分竖屏素材方向识别异常。"),
            ("2026-05-09", "2.5.0", "Windows · macOS", "加入体积优先压缩模式与任务结果摘要。"),
        ],
    },
    "Xiami": {
        "mark": "XI",
        "tone": "coral",
        "windows_version": "1.4.2",
        "macos_version": "1.4.2",
        "announcement": "新增失败行定位与结果摘要，处理大表时更容易回看。",
        "features": [
            ("表格工作流", "把重复的数据整理步骤保存为可复用流程。"),
            ("逐行校验", "问题数据会被准确标记，不打断其余任务。"),
            ("结果导出", "完成后生成清晰的成功、失败与耗时摘要。"),
        ],
        "releases": [
            ("2026-08-29", "1.4.2", "Windows · macOS", "新增失败行定位与处理结果摘要。"),
            ("2026-06-11", "1.4.1", "Windows · macOS", "提升大型工作簿的读取稳定性。"),
            ("2026-03-22", "1.4.0", "Windows", "工作流现在可以保存并重复运行。"),
        ],
    },
}

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-this-secret"),
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_db():
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    database = g.pop("db", None)
    if database is not None:
        database.close()


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    database = get_db()
    database.executescript(
        """
        CREATE TABLE IF NOT EXISTS software (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '效率工具',
            summary TEXT NOT NULL,
            description TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT 'box',
            video_url TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            stored_name TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            downloads INTEGER NOT NULL DEFAULT 0,
            is_featured INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS comment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            software_id INTEGER NOT NULL REFERENCES software(id) ON DELETE CASCADE,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wish (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            department TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '待评估',
            created_at TEXT NOT NULL
        );
        """
    )
    count = database.execute("SELECT COUNT(*) FROM software").fetchone()[0]
    if count == 0:
        timestamp = now_iso()
        database.executemany(
            """INSERT INTO software
               (name, version, category, summary, description, icon, video_url,
                is_featured, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("Zipvid", "2.6.0", "视频工具", "把繁琐的视频处理压缩成几次点击", "Zipvid 是面向团队日常素材处理的桌面工具。它将转码、压缩与字幕处理放在同一条清晰的工作流里，适合批量、稳定地处理本地视频。", "zipvid", "", 1, timestamp, timestamp),
                ("Xiami", "1.4.2", "数据工具", "让重复的表格整理变成一条可靠流程", "Xiami 用来组合、运行和检查表格处理流程。它保留每一步的结果，准确定位失败行，适合需要反复执行的数据整理任务。", "xiami", "", 0, timestamp, timestamp),
            ],
        )
    legacy_names = [
        row["name"]
        for row in database.execute("SELECT name FROM software ORDER BY id").fetchall()
    ]
    if legacy_names == ["桌面效率助手", "数据检查器", "安全传输客户端"]:
        timestamp = now_iso()
        database.execute(
            """UPDATE software SET name=?, version=?, category=?, summary=?, description=?, icon=?,
               is_featured=?, updated_at=? WHERE id=(SELECT MIN(id) FROM software)""",
            ("Zipvid", "2.6.0", "视频工具", "把繁琐的视频处理压缩成几次点击", "Zipvid 是面向团队日常素材处理的桌面工具。它将转码、压缩与字幕处理放在同一条清晰的工作流里，适合批量、稳定地处理本地视频。", "zipvid", 1, timestamp),
        )
        database.execute(
            """UPDATE software SET name=?, version=?, category=?, summary=?, description=?, icon=?,
               is_featured=?, updated_at=? WHERE id=(SELECT id FROM software ORDER BY id LIMIT 1 OFFSET 1)""",
            ("Xiami", "1.4.2", "数据工具", "让重复的表格整理变成一条可靠流程", "Xiami 用来组合、运行和检查表格处理流程。它保留每一步的结果，准确定位失败行，适合需要反复执行的数据整理任务。", "xiami", 0, timestamp),
        )
        database.execute(
            "DELETE FROM software WHERE id=(SELECT id FROM software ORDER BY id DESC LIMIT 1)"
        )
    database.commit()


@app.before_request
def ensure_database():
    init_db()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def valid_video_url(value):
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def save_package(upload):
    if not upload or not upload.filename:
        return None
    original = secure_filename(upload.filename)
    suffix = Path(original).suffix.lower()
    if not original or suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("安装包格式不支持")
    stored = f"{uuid.uuid4().hex}{suffix}"
    destination = UPLOAD_DIR / stored
    upload.save(destination)
    return original, stored, destination.stat().st_size


@app.get("/")
def index():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sql = "SELECT * FROM software WHERE 1=1"
    params = []
    if query:
        sql += " AND (name LIKE ? OR summary LIKE ? OR description LIKE ?)"
        term = f"%{query}%"
        params.extend([term, term, term])
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY is_featured DESC, updated_at DESC"
    software = get_db().execute(sql, params).fetchall()
    categories = get_db().execute("SELECT DISTINCT category FROM software ORDER BY category").fetchall()
    stats = get_db().execute(
        """SELECT
               (SELECT COUNT(*) FROM software) AS software_count,
               (SELECT COALESCE(SUM(downloads), 0) FROM software) AS downloads,
               (SELECT COUNT(*) FROM comment) AS comment_count,
               (SELECT COUNT(*) FROM wish) AS wish_count"""
    ).fetchone()
    return render_template(
        "index.html",
        software=software,
        presentations=PRODUCT_PRESENTATION,
        categories=categories,
        stats=stats,
        query=query,
        active_category=category,
    )


@app.route("/software/<int:software_id>", methods=["GET", "POST"])
def software_detail(software_id):
    database = get_db()
    item = database.execute("SELECT * FROM software WHERE id = ?", (software_id,)).fetchone()
    if item is None:
        abort(404)
    if request.method == "POST":
        author = request.form.get("author", "").strip()[:40]
        content = request.form.get("content", "").strip()[:1000]
        if not author or not content:
            flash("请填写称呼和留言内容", "error")
        else:
            database.execute(
                "INSERT INTO comment (software_id, author, content, created_at) VALUES (?, ?, ?, ?)",
                (software_id, author, content, now_iso()),
            )
            database.commit()
            flash("留言已发布", "success")
            return redirect(url_for("software_detail", software_id=software_id) + "#comments")
    comments = database.execute(
        "SELECT * FROM comment WHERE software_id = ? ORDER BY created_at DESC", (software_id,)
    ).fetchall()
    return render_template(
        "detail.html",
        item=item,
        comments=comments,
        presentation=PRODUCT_PRESENTATION.get(item["name"], {}),
    )


@app.get("/software/<int:software_id>/download")
def download(software_id):
    database = get_db()
    item = database.execute("SELECT * FROM software WHERE id = ?", (software_id,)).fetchone()
    if item is None or not item["stored_name"]:
        abort(404)
    package_path = UPLOAD_DIR / item["stored_name"]
    if not package_path.is_file():
        abort(404)
    database.execute("UPDATE software SET downloads = downloads + 1 WHERE id = ?", (software_id,))
    database.commit()
    return send_from_directory(UPLOAD_DIR, item["stored_name"], as_attachment=True, download_name=item["file_name"])


@app.route("/wishes", methods=["GET", "POST"])
def wishes():
    database = get_db()
    if request.method == "POST":
        author = request.form.get("author", "").strip()[:40]
        department = request.form.get("department", "").strip()[:60]
        content = request.form.get("content", "").strip()[:1000]
        if not author or not content:
            flash("请填写称呼和需求描述", "error")
        else:
            database.execute(
                "INSERT INTO wish (author, department, content, created_at) VALUES (?, ?, ?, ?)",
                (author, department, content, now_iso()),
            )
            database.commit()
            flash("愿望已收到", "success")
            return redirect(url_for("wishes"))
    items = database.execute("SELECT * FROM wish ORDER BY created_at DESC").fetchall()
    return render_template("wishes.html", wishes=items)


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        configured = os.getenv("ADMIN_PASSWORD", "admin123")
        password = request.form.get("password", "")
        valid = check_password_hash(configured, password) if configured.startswith(("scrypt:", "pbkdf2:")) else secrets.compare_digest(configured, password)
        if valid:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin_software"))
        flash("密码不正确", "error")
    return render_template("login.html")


@app.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/admin/software")
@admin_required
def admin_software():
    software = get_db().execute("SELECT * FROM software ORDER BY updated_at DESC").fetchall()
    return render_template("admin/software.html", software=software)


@app.route("/admin/software/new", methods=["GET", "POST"])
@app.route("/admin/software/<int:software_id>/edit", methods=["GET", "POST"])
@admin_required
def software_form(software_id=None):
    database = get_db()
    item = database.execute("SELECT * FROM software WHERE id = ?", (software_id,)).fetchone() if software_id else None
    if software_id and item is None:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        summary = request.form.get("summary", "").strip()[:200]
        description = request.form.get("description", "").strip()[:5000]
        video_url = request.form.get("video_url", "").strip()[:500]
        if not name or not summary or not description:
            flash("名称、简介和详细说明不能为空", "error")
        elif not valid_video_url(video_url):
            flash("视频地址必须是有效的 HTTP(S) URL", "error")
        else:
            package = None
            try:
                package = save_package(request.files.get("package"))
            except ValueError as error:
                flash(str(error), "error")
                return render_template("admin/software_form.html", item=item)
            values = (
                name,
                request.form.get("version", "").strip()[:40],
                request.form.get("category", "效率工具").strip()[:60],
                summary,
                description,
                request.form.get("icon", "box").strip()[:40],
                video_url,
                1 if request.form.get("is_featured") else 0,
                now_iso(),
            )
            if item:
                file_values = package or (item["file_name"], item["stored_name"], item["file_size"])
                database.execute(
                    """UPDATE software SET name=?, version=?, category=?, summary=?, description=?, icon=?,
                       video_url=?, is_featured=?, updated_at=?, file_name=?, stored_name=?, file_size=? WHERE id=?""",
                    values + file_values + (software_id,),
                )
                if package and item["stored_name"] and item["stored_name"] != package[1]:
                    (UPLOAD_DIR / item["stored_name"]).unlink(missing_ok=True)
            else:
                file_values = package or ("", "", 0)
                database.execute(
                    """INSERT INTO software (name, version, category, summary, description, icon, video_url,
                       is_featured, updated_at, file_name, stored_name, file_size, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values + file_values + (now_iso(),),
                )
            database.commit()
            flash("软件信息已保存", "success")
            return redirect(url_for("admin_software"))
    return render_template("admin/software_form.html", item=item)


@app.post("/admin/software/<int:software_id>/delete")
@admin_required
def delete_software(software_id):
    database = get_db()
    item = database.execute("SELECT stored_name FROM software WHERE id = ?", (software_id,)).fetchone()
    if item:
        database.execute("DELETE FROM software WHERE id = ?", (software_id,))
        database.commit()
        if item["stored_name"]:
            (UPLOAD_DIR / item["stored_name"]).unlink(missing_ok=True)
    flash("软件已删除", "success")
    return redirect(url_for("admin_software"))


@app.get("/admin/dashboard")
@admin_required
def dashboard():
    error = None
    try:
        metrics = load_dashboard()
    except SLSAnalyticsError as exc:
        metrics = {
            "configured": False,
            "week_start": "",
            "totals": {
                "history": {"user_count": 0, "usage_count": 0, "task_count": 0, "saved_seconds": 0},
                "week": {"user_count": 0, "usage_count": 0, "task_count": 0, "saved_seconds": 0},
            },
            "applications": [],
        }
        error = str(exc)
    return render_template("admin/dashboard.html", metrics=metrics, error=error)


@app.get("/api/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return jsonify(status="ok", database="ok")


@app.template_filter("filesize")
def filesize(value):
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@app.template_filter("duration")
def format_duration(value):
    minutes = round(float(value or 0) / 60)
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {remaining_minutes} 分钟" if remaining_minutes else f"{hours} 小时"
    days, remaining_hours = divmod(hours, 24)
    return f"{days} 天 {remaining_hours} 小时" if remaining_hours else f"{days} 天"


@app.template_filter("date")
def format_date(value):
    return value[:10] if value else ""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8950")), debug=os.getenv("FLASK_DEBUG") == "1")