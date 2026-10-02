# 开发与测试

## 工具

Python 3.10+、Node 22+、PostgreSQL 17（需 `pg_trgm`）。生产镜像使用 Python 3.12 和 Node 22 构建。Windows 中文路径下，使用 `python -X utf8` 或设置 `PYTHONUTF8=1`。

```powershell
uv sync --frozen
cd frontend
npm ci
npm run build
cd ..
```

设置 `GMV_DATABASE_URL` 为开发 PostgreSQL 地址，`GMV_DATA_DIR` 为开发媒体目录，`GMV_IMPORT_ROOTS` 为允许索引的目录 JSON 数组。然后执行：

```powershell
.venv\Scripts\python -X utf8 -m alembic upgrade head
# 首次需设置至少 12 位的 GMV_ADMIN_PASSWORD
.venv\Scripts\python -X utf8 -m genmedia.cli bootstrap
.venv\Scripts\python -X utf8 -m uvicorn genmedia.main:app --port 8080
```

另一终端执行 `.venv\Scripts\python -X utf8 -m genmedia.cli worker`。前端热更新在 `frontend` 执行 `npm run dev`，通过 Vite 代理 `/api`。

## 自动化测试

v0.2 备份集成测试需要 PostgreSQL 17 的 `pg_dump` 和 `pg_restore`；用 `GMV_PG_DUMP_BINARY` 指定路径。缺少客户端时该项会明确跳过，不等于备份功能已验证。当前完整记录见 [v0.2 验证记录](VALIDATION-V0.2.md)。

`scripts/check_browser.py` 固定使用隔离测试站点 `127.0.0.1:18083`，从本地 `.env` 读取测试管理员初始密码，不打印密码。先以 `genmedia-v02-test` Compose 项目启动独立数据卷，再运行该脚本；不要在正式库运行会新增测试数据的浏览器用例。

```powershell
.venv\Scripts\python -X utf8 -m pytest -q
.venv\Scripts\ruff check backend scripts
cd frontend
npm test
npm run build
npm run test:e2e
```

没有 `GMV_TEST_DATABASE_URL` 时，数据库测试明确跳过。真实集成验收必须把该变量设置到**独立的、名字以 `_test` 结尾的 PostgreSQL 数据库**；测试会清空该库内的应用表，禁止连接生产库。

Playwright 默认使用本机 Chrome；设置 `GMV_BROWSER_CHANNEL` 可切换浏览器。端到端测试使用独立开发服务与 `data/test-environment.json` 中的临时账号配置；测试 Compose 时，通过 `GMV_E2E_PASSWORD` 传入 `.env` 中的管理员密码，通过 `GMV_E2E_URL` 指定地址。会验证上传、解析、刷新会话、搜索、评分、导出、退出登录和手机导航。

### 本机 Docker 不可用时的隔离测试环境

仓库提供 Windows 专用辅助脚本，仅用于开发验收：

```powershell
npm install --prefix data/test-runtime --no-save --ignore-scripts @embedded-postgres/windows-x64@17.10.0-beta.17
.venv\Scripts\python -X utf8 scripts/dev_database.py start
.venv\Scripts\python -X utf8 scripts/check.py -q
.venv\Scripts\python -X utf8 scripts/create_fixtures.py
.venv\Scripts\python -X utf8 scripts/dev_server.py app
# 在另一终端：
.venv\Scripts\python -X utf8 scripts/dev_server.py worker
```

该环境仅监听 `127.0.0.1:55432`，创建隔离的 dev/test/bench/restore_test 库。随机临时凭据写入忽略的 `data/test-environment.json`，不会写入测试报告。PostgreSQL Windows 初始化不兼容某些中文程序路径，因此中文项目路径下会使用任务专属的 ASCII 临时目录；具体位置保存在配置的 `runtime` 字段。

停止开发 app / worker 后，可执行 `python -X utf8 scripts/dev_database.py stop`。不用此辅助环境时，不需要安装此 npm 包。

## 基准与恢复验证

```powershell
.venv\Scripts\python -X utf8 scripts/benchmark.py --assets 100000 --users 5 --repeats 10
.venv\Scripts\python -X utf8 scripts/verify_restore.py
```

基准只写入独立的 `genmedia_bench`，恢复验证只重置 `genmedia_restore_test`。前者通过真实 PostgreSQL＋FastAPI TestClient 测量鉴权、校验、查询、分面和序列化，排除浏览器与网络开销。它不是导入吞吐量测试，也不是在限定 4 核 / 16 GB 容器中运行的认证结果。

Windows 的独立恢复验证需要 PostgreSQL 17 客户端工具。将 `GMV_PG_CLIENT_BIN` 设置为 `pg_dump.exe` 和 `pg_restore.exe` 所在目录（包含其 DLL）。可从 [EDB 官方二进制包](https://www.enterprisedb.com/download-postgresql-binaries) 获取。脚本验证所有表的内容及媒体 tar 恢复后的原件哈希；Compose 备份仍使用 `scripts/backup.py`。

### 限定资源的 Compose 基准

`compose.benchmark.yaml` 必须配合独立的项目名 `genmedia-bench` 使用。三个容器绑定到相同的 CPU 0–3，内存上限分别为 PostgreSQL 10 GiB、app 4 GiB、worker 1 GiB。Docker 至少需要这些可用资源。

```powershell
$env:GMV_PORT = '18082'
docker compose -p genmedia-bench -f compose.yaml -f compose.benchmark.yaml up -d --build
docker cp scripts/benchmark.py genmedia-bench-app-1:/tmp/benchmark.py
docker cp scripts/dev_database.py genmedia-bench-app-1:/tmp/dev_database.py
docker exec genmedia-bench-app-1 python /tmp/benchmark.py --external-env --seed-only --assets 100000 --users 5
.venv\Scripts\python -X utf8 scripts/benchmark.py --external-env --skip-seed --http-url http://127.0.0.1:18082 --assets 100000 --users 5 --repeats 20 --hardware-note 'Compose services share CPU 0-3; total memory ceilings 15 GiB' --report docs/benchmark-compose-results.json
docker compose -p genmedia-bench -f compose.yaml -f compose.benchmark.yaml stop
Remove-Item Env:GMV_PORT
```

Linux 使用 `export GMV_PORT=18082`、`.venv/bin/python` 和 `unset GMV_PORT` 替换对应 PowerShell 命令。合成基准会重置独立 `_bench` 数据库，禁止对日常工作库运行。HTTP 模式包含本机 TCP 开销；负载客户端运行在容器之外。

### 容器恢复与目录闭环复测

`scripts/check_compose_restore.py` 对比默认主项目与 `genmedia-restore-test` 的 19 张表，并检查恢复服务的媒体、导出和个人状态。先按运维文档备份主项目，将 `COMPOSE_PROJECT_NAME=genmedia-restore-test`、`GMV_PORT=18081` 设置到当前进程，构建后仅启动 postgres，运行 `backup.py restore`，最后启动 app / worker。仅在刚完成恢复、尚未修改恢复库时执行核对脚本；可通过脚本参数更改项目容器名和地址。

目录验收可在该恢复测试项目叠加 `compose.watch-test.yaml`，把 worker 补扫间隔缩短到 10 秒，再执行 `scripts/check_directory_watch.py --timeout 50`。脚本仅允许默认 `data/imports` 挂载，用新建子目录内的合成图片验证自动发现、分类、缺失和重新出现，不修改已有素材。测试结束关闭自动监控，并保留测试资料供检查。生产配置不使用此覆盖文件。

`scripts/create_fixtures.py` 生成可重复的合成图像；不包含个人素材。生成器兼容性样本包括 A1111、NovelAI、NovelAI 隐写和 ComfyUI；测试另外覆盖冲突、损坏、空元数据和超大 Seed。

## 接口与迁移维护

修改 API 后执行 `python -X utf8 -m genmedia.cli openapi`，再在 `frontend` 运行 `npm run types`。将 `docs/openapi.json` 与 `src/api.generated.ts` 同时更新。

初始 SQL 已冻结。`scripts/freeze_schema.py` 只用于首次发布前建立基线，发布后禁止重跑覆盖；新增结构使用 Alembic 新迁移。依赖调整后更新两个锁文件，并运行后端测试、前端类型检查和相关浏览器测试。
