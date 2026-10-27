
依赖版本查询
写 requirements.txt 前，先查一下本机实际能装到哪个版本，按查出来的版本钉。

包名，如 numpy、pymupdf
查询
数据存储连接速查
下面的内容可以直接复制到项目的「程序需要的配置项」或 .env。 小型单机数据用 SQLite；缓存/异步任务用 Redis；视频、图片等大文件用 MinIO。不要把视频文件塞进 SQLite 或 Redis。

SQLite · 单项目本地数据库
运行中
适用：单机项目、少量结构化数据。不用连服务器，不需要账号密码。

连接配置（复制这一行）：
DATABASE_URL=sqlite:///./data/app.sqlite
Python（requirements.txt 加 sqlalchemy）：
from sqlalchemy import create_engine
engine = create_engine(os.environ["DATABASE_URL"])
数据库文件位于当前项目部署目录的 data/app.sqlite。请把 data/ 放进 .gitignore。

Redis · 缓存与任务队列
运行中
适用：缓存、登录会话、Celery 异步任务、进度、限流。不存视频文件。

连接配置（复制这一行）：
REDIS_URL=redis://127.0.0.1:6380/0
Python（requirements.txt 加 redis）：
import os, redis
client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
client.set("job:123", "running", ex=3600)
status = client.get("job:123")
本工具专用 Redis；不要使用机器上其他系统占用的 6379。

MinIO · 视频/图片/音频对象存储
运行中
适用：上传视频、图片、音频、模型文件、生成成品等大文件。数据库里只存对象路径。

连接配置（全部复制到项目配置）：
MINIO_ENDPOINT=http://127.0.0.1:9010
MINIO_ACCESS_KEY=deployment-admin
MINIO_SECRET_KEY=DeployMinio-9010-2026
MINIO_BUCKET_RAW=vf-dev-raw
MINIO_BUCKET_GENERATED=vf-dev-generated
Python（requirements.txt 加 minio）：
import os
from minio import Minio
client = Minio(os.environ["MINIO_ENDPOINT"].removeprefix("http://"),
    access_key=os.environ["MINIO_ACCESS_KEY"],
    secret_key=os.environ["MINIO_SECRET_KEY"], secure=False)
client.fput_object(os.environ["MINIO_BUCKET_RAW"],
    "uploads/input.mp4", "/tmp/input.mp4")
管理控制台：http://<服务器内网IP>:9011。本工具专用 MinIO；9010 是 API，9011 是管理控制台。

PostgreSQL · 共享业务数据库
未提供
适用：多人/多服务共享数据、事务、复杂查询、生产级关系数据。

当前状态：
本部署后台尚未提供 PostgreSQL 连接地址
本工具尚未提供专用 PostgreSQL。机器上虽有其他系统的数据库容器，但不得复用。项目需要 PostgreSQL 时请申请独立实例。

可用的运行时
Python 3.12.14
默认使用
/usr/local/python3.12/bin/python3.12
Python 3.10.12
/usr/local/python3.10/bin/python3.10
Node v16.20.2
/root/.nvm/versions/node/v16.20.2/bin
Node v24.19.0
构建时使用
/usr/local/node24/bin
另有 4 个 Node 安装在本系统无法运行（glibc 过低）， 工具会自动跳过：node-v18.20.4-linux-x64、node18、v18.20.8、v22.14.0

系统与工具链
操作系统	CentOS Linux release 7.9.2009 (Core)
内核 / glibc	3.10.0-1160.119.1.el7.x86_64 / glibc 2.17
系统默认 python3	Python 3.10.12（未改动，工具另选新版）
编译器	gcc (GCC) 4.8.5 20150623 (Red Hat 4.8.5-44)
gcc (GCC) 11.2.1 20220127 (Red Hat 11.2.1-9)（需 source /opt/rh/devtoolset-11/enable）
gcc (GCC) 9.3.1 20200408 (Red Hat 9.3.1-2)（需 source /opt/rh/devtoolset-9/enable）
pip 源	https://pypi.tuna.tsinghua.edu.cn/simple
GitHub 连通性	SSH ✅ 可用 ／ HTTPS ✅ 可用
服务端口池	8900-8999（已占用 8900、8901、8903）
部署根目录	/data/deploys
磁盘可用	3908.3 GB
OCR 引擎（Tesseract）
已安装：tesseract 3.04.00 /usr/bin/tesseract
可用语言：eng、chi_sim、chi_tra
系统是 Tesseract 3，和新版 pytesseract 有版本解析兼容问题。 推荐业务 Python 用标准库 subprocess 直接调用，不需要额外 Python 依赖：

import subprocess

# image_path 是图片文件路径；简体中文+英文混排

result = subprocess.run(
    ["tesseract", image_path, "stdout", "-l", "chi_sim+eng"],
    capture_output=True, text=True, check=True,
)
text = result.stdout
引擎已经在系统 PATH 里，不用安装或编译 Tesseract。 需要繁体中文时将 chi_sim 换成 chi_tra。

给开发者的几条约定
依赖必须有预编译包（wheel）：本机 gcc 较老，需要现场编译的依赖基本会失败。 钉版本前用上面的查询确认一下。
别在代码里写死端口：端口由系统自动分配。 用 --port（uvicorn）或 --bind 0.0.0.0:$PORT（gunicorn）接收， 或读环境变量 PORT。
监听 0.0.0.0，不要写 127.0.0.1，否则别的机器访问不到。
配置走环境变量，并且代码里要给默认值 （如 os.getenv("N", "2")）。工具只会注入你填了值的变量，留空即为不设置。
Flask 等 WSGI 框架请在 Procfile 或 Dockerfile 里 用 gunicorn 声明启动方式；用 uvicorn 跑 WSGI 应用每个请求都会 500。
前端会自动构建：有 package.json 且带 build 脚本时， 工具会自动执行安装与构建，产物请让后端直接托管。
提供健康检查接口（如 /api/health），部署验收会调用它。
要做 OCR：系统已经安装 Tesseract，Python 用标准库 subprocess 直接调用即可，具体代码见上方 OCR 说明。
数据要落在项目目录内，并加进 .gitignore，否则更新代码时会冲突。
仓库地址用 SSH 形式更稳（HTTPS 偶发不通，工具会自动切换，但直接填 SSH 更快）。
