# 软件驿站

面向内网团队的软件分发与需求收集平台。公开用户可以搜索软件、查看介绍与嵌入视频、下载安装包、留言和提交软件愿望；管理员可以维护软件，并查看阿里云数据服务提供的使用指标。

## 功能

- 软件目录、分类筛选与全文搜索
- 软件详情、安装包下载及下载次数统计
- URL 形式的视频内嵌与新窗口播放
- 软件留言与公开许愿墙
- 密码保护的软件管理、安装包上传和删除
- 阿里云 SLS 软件使用与效率看板；未配置凭据时明确显示配置状态
- SQLite WAL 持久化、上传容量限制与健康检查

## 本地运行

需要 Python 3.12（3.9 及以上也可用于本地开发）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
python app.py
```

打开 `http://127.0.0.1:8950`。未配置时管理员初始密码为 `admin123`，仅用于本地预览，部署前必须设置 `ADMIN_PASSWORD` 与 `SECRET_KEY`。

运行测试：

```bash
python -m unittest discover -s tests -v
```

## 内网部署

项目符合 [部署要求](docs/部署要求.md)：使用 Python 3.12、监听 `0.0.0.0`、端口读取 `PORT`，后端直接托管静态页面，并提供 `GET /api/health`。

部署配置：

1. 将项目部署到 `/data/deploys` 下的平台分配目录。
2. 安装 `requirements.txt` 中的固定版本依赖。
3. 至少设置 `SECRET_KEY` 和 `ADMIN_PASSWORD`；可从 `.env.example` 复制其他配置。
4. 使用 `Procfile` 启动。平台会传入 `PORT`，Gunicorn 默认使用 2 个 worker、每个 4 个线程。
5. 保证 `data/` 与 `uploads/` 在版本更新时被保留并可写；两者已加入 `.gitignore`。
6. 反向代理若限制请求体大小，应同步调大，否则大安装包会在到达 Flask 前被拒绝。

SQLite 适合当前单机内网部署。若以后运行多个应用实例，应申请独立 PostgreSQL，并将安装包迁移到文档提供的 MinIO，避免实例间数据不一致。

## 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8950` | 服务端口 |
| `SECRET_KEY` | 开发值 | 会话签名密钥，生产必须修改 |
| `ADMIN_PASSWORD` | `admin123` | 管理密码，生产必须修改；也支持 Werkzeug 哈希值 |
| `DATA_DIR` | `./data` | SQLite 数据目录 |
| `UPLOAD_DIR` | `./uploads` | 安装包目录 |
| `MAX_UPLOAD_MB` | `2048` | 单文件上传上限（MB） |
| `WEB_CONCURRENCY` | `2` | Gunicorn worker 数量 |
| `WEB_THREADS` | `4` | 每个 worker 的线程数 |
| `ALIYUN_SLS_REGION` | `cn-hangzhou` | SLS 地域 |
| `ALIYUN_SLS_ENDPOINT` | `cn-hangzhou.log.aliyuncs.com` | SLS 服务端点 |
| `ALIYUN_SLS_PROJECT` | `zyb-telemetry` | SLS Project |
| `ALIYUN_SLS_LOGSTORE` | `events` | SLS Logstore |
| `ALIYUN_SLS_HISTORY_DAYS` | `3650` | “历史累计”向前读取的最大天数，实际仍受 SLS 保存周期限制 |
| `SOFTWARE_SAVINGS_RATES_JSON` | `{"zipvid":0.5}` | 各软件成功任务的节约时间系数；任务耗时乘以该系数 |
| `ALIYUN_ACCESS_KEY_ID` | 空 | 只读 RAM 用户的 AccessKey ID |
| `ALIYUN_ACCESS_KEY_SECRET` | 空 | 只读 RAM 用户的 AccessKey Secret |

## 阿里云 SLS 数据看板

管理员看板通过阿里云 SLS Python SDK 读取 `zyb-telemetry/events` 中所有包含任务事件的应用日志，并按 `app_id` 自动分组。页面同时展示历史累计与按 UTC+8 计算的本周数据。当前统计：

- 使用人数：有任务开始事件的 `install_id` 匿名去重数量，仅作为近似人数
- 使用次数与任务量：`transcode_started` 事件数量
- 成功和失败任务量及比例：根据 `transcode_finished` 的 `payload_json.status` 统计
- 节约时间：仅累计成功任务，用 `transcode_duration_sec` 乘以该软件配置的节约系数

默认配置 `{"zipvid":0.5}` 表示 zipvid 每个成功任务节约其实际任务耗时的一半。增加其他软件时，在 `SOFTWARE_SAVINGS_RATES_JSON` 中加入对应 `app_id`，例如 `{"zipvid":0.5,"other_app":0.25}`。未配置系数的软件仍统计人数、次数、任务量和成功/失败，但节约时间为 0。

应用仅在单次聚合过程中使用 `install_id` 去重，不保存或展示该值，也不在页面展示文件名等日志明细。建议创建仅有目标 Project/Logstore 查询权限的 RAM 用户，将密钥作为部署环境变量注入，不要写入 `.env.example` 或提交到仓库。

看板默认自动翻页读取时间窗内的原始日志，确保超过单页限制时仍能完整聚合。数据量显著增长后，可将 [sls_analytics.py](sls_analytics.py) 中的相同口径迁移为 SLS SQL 或定时 SQL，以降低读取量。

## 离线统计

从 SLS 控制台将完整查询结果导出为 CSV gzip 后，把原始文件放入 `data/exports/`，运行：

```bash
python offline_analytics.py data/exports/<导出文件>.csv.gz
```

脚本在 `data/offline/` 生成 `offline_summary.json`、`offline_summary.csv` 和 `data_quality.json`。输出只包含聚合计数、比例和时间范围，不包含 `install_id`、任务 ID、文件名或其他日志明细。`data/` 已被 Git 忽略；原始导出文件仍应只保存在访问受控的本地目录，并在统计完成后按数据管理要求归档或删除。

## 数据与备份

- 结构化数据：`data/app.sqlite`
- 安装包：`uploads/`
- 建议定期同时备份这两个目录。
- 更新软件安装包时，旧文件会在数据库更新成功后自动清理。