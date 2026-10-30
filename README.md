# 软件驿站

面向内网团队的软件分发与需求收集平台。公开用户可以搜索软件、查看介绍与嵌入视频、下载安装包、留言和提交软件愿望；管理员可以维护软件，并查看阿里云数据服务提供的使用指标。

## 功能

- 软件目录、分类筛选与全文搜索
- 软件详情按主软件、子软件分别展示版本与下载；每个平台独立显示最新发布版本及历史版本，最新顺序按创建时间（相同时按记录 ID）排列。
- 软件详情、安装包下载及下载次数统计
- URL 形式的视频内嵌与新窗口播放
- 软件留言与公开许愿墙
- 密码保护的软件管理、安装包上传和删除
- 阿里云 SLS 软件使用与效率看板；未配置凭据时明确显示配置状态
- SQLite WAL 持久化、上传容量限制与健康检查

## 管理控制台

- 点击右上角管理入口登录后，进入独立的 `/admin` 页面；支持刷新和浏览器前进、后退。
- 在“软件管理”中新增或编辑软件时，使用独立编辑视图，不再在弹层中输入。点击“返回软件列表”或“取消”放弃未保存的表单修改，点击“保存软件”提交。
- 表单按基本信息、发布设置、图标和版本分组。版本和子软件版本的平台只能选择产品已选平台；两类版本均可上传软件包（最大 100 MB），上传后保存软件才会关联下载附件。产品介绍与安全提示使用 BlockNote 富文本编辑器，并兼容显示和继续编辑既有 Markdown；支持表格、正文任意位置插入多个图片/视频/音频块、拖动排序，以及媒体标题、尺寸和对齐设置。支持安全的 HTTP/HTTPS 媒体 URL（视频/音频需为可直接播放的媒体地址）。图片也可上传 PNG/JPEG/GIF/WebP（最大 5 MB、最多 2500 万像素，不支持 SVG）；新增软件在保存之前即可插入图片，保存时将正文引用的草稿图片关联到新产品并转换为正式地址。
- 新建软件时放弃编辑会清理该草稿的上传图片；页面意外关闭或网络中断留下的草稿图片会在 24 小时后失效，并在服务启动时清理。
- 正文上传图片保存为稳定的 `/api/product-media/{id}` 地址，而非短期对象存储签名。公开产品可访问，未发布产品仅管理员可访问；每次访问才生成 5 分钟私有存储签名，取消发布后已签发的地址可能在剩余有效期内继续有效。正文图片与版本附件独立管理。
- 方案社区的投稿页沿用产品介绍的 BlockNote 富文本编辑器，支持表格、媒体尺寸和对齐设置、全屏编辑和图片上传，兼容原有 Markdown。方案图片支持 PNG/JPEG/GIF/WebP，最大 5 MB、2500 万像素；草稿图片 24 小时过期，提交时仅绑定正文实际引用的图片，正式图片随方案审核状态控制访问。
- 投稿支持多个自定义标签：输入后按回车确认，最多 20 个，每个最多 30 字；提交时自动去除首尾空白和重复标签。方案列表与详情展示标签，富文本正文在列表中显示纯文本摘要。提交成功后刷新后台审核数据；待审核方案不在公开社区列表展示，可在管理后台“方案审核 → 待审核”查看。后台“方案社区”默认列出全部状态的方案，可直接修改状态或编辑标题、正文、标签、分类、署名及关联软件。编辑保存保留原状态，审核状态由管理员单独修改。“预览完整方案”在当前页面展开，与公开详情共用展示组件；关联软件采用紧凑布局。未通过内容及附件仍仅管理员可见。
- 图标与软件包上传操作会立即上传文件；退出编辑不会撤销已上传的文件。产品截图已停用，旧截图记录及其专属存储对象在服务启动时清理（存储删除失败则保留记录并在下次启动重试）。在方案审核中可以为任意状态的方案选择关联软件并保存。

## 详情页与互动规则

- 管理员可选填写安全提示；留空时详情页不显示提示。
- 详情页在标题下以浅色卡片展示软件最新版本及历史版本；产品与可选子软件的平台版本各自独立排序。安全提示与左栏对齐，平台和分类支持标签多选（工具类型仅保留后台数据，不在详情侧栏展示），产品信息侧栏展示标签、浏览次数、累计成功按钮点击数及赞踩；反馈列表、审核后公开的图文反馈和提交入口位于侧栏，反馈正文与图片在同一编辑区。
- 下载按钮仅适用于管理员上传的实体文件；公开详情使用文件名识别可下载附件，不依赖仅管理员可见的存储对象键。网页工具可配置网址并以“打开网址”呈现，不作为安装包下载；相关使用方案按点赞数降序展示。
- 软件反馈及其图片、评论需经管理员审核后公开；反馈和评论展示发帖 IP。评论支持可选昵称、同框文字与图片输入（最多 5 张，每张 50 MB），提交后显示待审核提示。后台可预览评论图片并审核；仅父反馈和评论均审核通过时评论图片可公开访问。公开图片支持点击放大及上一张/下一张切换。用户可点赞反馈，并以匿名浏览器标识切换软件赞/踩。
- 浏览次数与成功响应的按钮点击数分别统计。


需要 Python 3.12（3.9 及以上也可用于本地开发）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
npm ci
npm run build
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8950
```

打开 `http://127.0.0.1:8950`。FastAPI 会直接托管 `dist/` 中的 React 页面；开发时也可使用 VS Code 任务启动同一后端。部署前必须设置 `ADMIN_PASSWORD` 与 `SECRET_KEY`。

运行测试：

```bash
python -m unittest discover -s tests -v
```

## 下载附件后端契约

- `POST /api/admin/products` 的主软件及子软件 `releases[].deliverables[]` 接收 `{id?, kind, label?, platform?, size?, updated_at?, object_key?, filename?, url?}`。版本号仍为 `release.version`；版本的 `platform/size/created_at` 保留兼容。
- `kind=file` 使用上传接口返回的 `object_key`；文件名以服务端上传记录为准，不解析文件名推断平台。既有附件可仅带 `id` 保留文件。`kind=url` 必须为无凭据的 HTTP/HTTPS URL；携带 ID 更新原记录，不带 ID 时复用同版本、同平台、同 URL 的记录，重复引用返回 400。
- `platform` 为产品 `platforms` 中的值或空字符串；`size` 为展示字符串；`updated_at` 为 ISO 8601 日期/时间或空字符串，三者均限 80 字符。新附件省略字段时取版本 `platform/size/created_at`；更新附件省略字段时保留原值。显式空字符串会持久化。无效平台、类型、日期、URL、跨版本 ID 或未上传文件返回 400，事务回滚。
- 管理与公开产品详情、bootstrap 均返回附件 `platform/size/updated_at`，仅管理响应返回 `object_key`。附件 ID 必须属于对应版本且类型一致；省略 `deliverables` 保留附件，显式列表为完整替换（`[]` 删除全部，含 URL）。
- 启动时以可重复执行的加列迁移，从版本元数据一次性回填既有文件和 URL 附件；保留 ID、文件名、对象键和版本字段，后续启动不覆盖附件元数据。

相关 API 回归测试（屏蔽 dotenv，避免读取本地 `.env`）：

```bash
.venv/bin/python -c 'from unittest.mock import patch; import unittest; p = patch("dotenv.load_dotenv", return_value=False); p.start(); unittest.main(module="tests_test_api", verbosity=2)'
```

## AI 客户端接入规划（2026-10-26，待实施）

实施状态：只读查询层和 MCP 服务骨架已实现；正式部署前仍需安装依赖、配置认证与完成客户端真实调用验收。

目标：让 Codex、WorkBuddy 在回答问题时，查询杜武库的软件与使用方案，并引用原文及下载入口。网站保留现有用途，无需重新打包为独立 Agent。

- 技术路线：MCP 优先，Skill／客户端使用指令作为辅助，CLI 暂缓；MCP 与网站复用业务查询层及权限规则。
- 首期只读工具：`search_software`（按用途、关键词、平台、分类检索）、`get_software`（介绍、注意事项、版本与下载入口）、`search_solutions`（按问题或关联软件检索）、`get_solution`（方案正文与关联软件）。查询实现位于 `backend/mcp_server.py`，支持 stdio 和 Streamable HTTP；网站部署时 MCP 使用同一个 Web 端口的 `/mcp` 路径。
- MCP 内网访问：网站部署时 MCP 使用同一个 Web 端口的 `/mcp` 路径；MCP 不单独校验 Bearer Token，访问控制由部署平台的内网网络策略负责。请勿将 `/mcp` 暴露到不受信任的网络。
- 搜索结果提供精简摘要、稳定 ID 和原文链接，详情按需读取；首期优先关键词检索，暂不要求向量数据库。
- 仅暴露已发布软件、已审核公开方案；不返回后台配置、存储密钥或未公开内容，不开放后台修改、自动下载、安装或执行。查询下载入口不得增加下载次数。
- 已确认使用场景为团队共享，采用集中部署的远程 MCP 服务，优先采用 Streamable HTTP；可与网站同机部署，结合认证、权限与限流。客户端必须能访问对应网络，远程服务可部署于可达内网，不要求暴露公网。本地 stdio 仅作为客户端兼容性不足时的适配备选，不作为主要交付方式。
- 待核实：Codex 与 WorkBuddy 的具体版本支持的 MCP 传输方式、认证方式与配置格式；未验证前不承诺配置通用。视兼容性决定是否提供本地 stdio 适配。
- 实施顺序：核实客户端兼容性 → 完善只读查询能力 → 实现 MCP 与权限控制 → Codex、WorkBuddy 真实调用验收 → 补充 Skill／使用指令。
- 验收场景：用户描述需求，客户端完成“搜索软件 → 查询关联方案 → 回答推荐理由、操作步骤、注意事项 → 引用原文及下载入口”。是否自动调用由客户端、模型及用户权限决定。
- 当前已安装客户端：Codex CLI 0.135.0、WorkBuddy 5.8.3。已验证 Codex CLI 可调用本地 stdio MCP，WorkBuddy 已通过信任确认并成功调用本地 `ping` MCP；正式只读服务的四个查询工具仍需分别完成客户端真实调用验收。

## 内网部署

项目符合 [部署要求](docs/部署要求.md)：使用 Python 3.12、监听 `0.0.0.0`、端口读取 `PORT`，后端直接托管静态页面，并提供 `GET /api/health`。

部署配置：

1. 将项目部署到 `/data/deploys` 下的平台分配目录。
2. 安装 `requirements.txt` 中的固定版本依赖。
3. 至少设置 `SECRET_KEY` 和 `ADMIN_PASSWORD`；可从 `.env.example` 复制其他配置。
4. 使用 `Procfile` 启动 FastAPI；平台会传入 `PORT`，应用监听 `0.0.0.0`。
5. 保证 `data/` 与 `uploads/` 在版本更新时被保留并可写；两者已加入 `.gitignore`。
6. 反向代理若限制请求体大小，应同步调大，否则大安装包会在到达 FastAPI 前被拒绝。

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
