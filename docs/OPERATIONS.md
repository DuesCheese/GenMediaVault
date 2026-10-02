# 部署、备份与恢复

## 本地和服务器

同一份 `compose.yaml` 运行 `app`、`worker`、`postgres`。部署前执行 `python scripts/configure.py`；不安装 Python 时，也可以手动复制 `.env.example` 并替换示例密码。

```text
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app worker
```

默认只监听 `127.0.0.1:8080`。局域网使用时将 `GMV_BIND` 设置为相应地址或 `0.0.0.0`，保留登录。公网部署使用同域 HTTPS 反向代理，设置 `GMV_SECURE_COOKIE=true`；代理保留原始 `Host` 和 `Origin`，不要把媒体卷直接发布为静态目录。反向代理的上传总量限制需与每批上传规模相符；应用每文件上限默认 64 MB，Sidecar 8 MB。

配置中数据库密码建议使用 URL-safe 字符（生成脚本已满足），因为 Compose 将其嵌入数据库 URL。数据库不发布宿主机端口。

## 首次登录与账号

管理员为 `.env` 中的 `GMV_ADMIN_USERNAME`，初始密码为 `GMV_ADMIN_PASSWORD`。首次启动自动建号；已有管理员时，修改环境变量不会重设密码。登录后通过“设置”更改自己的密码或创建其他账号。管理员可通过用户 API 重设成员密码；重设密码、停用账号或变更角色会撤销其会话。

所有成员可以读取全部库。普通成员不能创建服务器目录索引、修改规则、回收资产或重新解析。个人收藏、评分、备注不共享；包含个人数据的导出包仅请求人可下载。

## 持久化数据

| 位置 | 内容 |
| --- | --- |
| PostgreSQL 卷 | 用户、资产、原始元数据、标准化信息、标签、个人状态和任务 |
| `/data/originals` | 上传后托管的原件，按库与内容哈希存放 |
| `/data/thumbnails` | 派生缩略图 |
| `/data/staging` | 未完成、失败或取消上传的暂存；成功任务自动清理 |
| `/data/exports` | 当前请求人生成的导出包 |
| `/imports` | 宿主机目录，只读挂载，不属于应用媒体卷 |

首版不自动清空回收站，也不自动清除失败任务暂存和导出包。长期运行时，应按实际磁盘占用安排维护；不要删除仍需重试任务的暂存。

## 创建一致备份

在项目目录运行：

```text
python scripts/backup.py create backups/2026-10-01
```

脚本短暂停止 app 和 worker，使用 PostgreSQL custom-format dump 备份数据库、tar 备份媒体卷，记录 SHA-256 校验值，然后恢复服务。输出二进制流由 Python 直接写文件，避免 Windows PowerShell 5 的管道编码破坏备份。

备份不包含 `.env` 和 `/imports` 源目录。分别保存部署配置，并通过现有磁盘备份方案保护索引库源文件。数据库元数据不能替代原始图片备份。

## 恢复到空环境

使用新的 Compose 项目／数据卷，不在原有资料上恢复：

```text
docker compose build
docker compose up -d postgres
python scripts/backup.py restore backups/2026-10-01
docker compose up -d
```

恢复脚本先验证文件校验值和归档路径，再检查数据库没有应用表且媒体卷为空；非空环境会拒绝恢复。恢复后使用备份中的账号密码登录；首次启动不会覆盖已有管理员。重新挂载同样的 `/imports` 路径，再扫描索引库核对文件状态。

## 升级

1. 创建备份并记录当前版本。
2. 更新代码和锁文件，执行 `docker compose build`。
3. 执行 `docker compose up -d`。app 在启动前执行 Alembic 迁移，worker 等待 app 健康。
4. 检查 `/api/v1/health`、任务页和样本图片详情。解析器更新后，从图库选择资产并“重新解析”。

原始快照不随重新解析覆盖；规则计算保留人工标签。新增文件提取能力需要原件存在。首版不提供破坏性数据库降级，出现不兼容问题时恢复升级前备份和旧镜像。

## 诊断

- **任务一直等待**：检查 worker 容器状态及日志；app 健康只表示 API 与数据库可用。
- **文件缺失**：先检查宿主机挂载和容器内路径，恢复挂载后扫描；不要通过删库解决。
- **解析告警**：在详情中检查 Raw、冲突和文件页。未知 ComfyUI 节点保留原图，不代表生成参数已完整提取。
- **Docker 内部网络错误**：先确认 Docker Desktop / Engine 正常运行。若构建器访问镜像仓库认证服务失败，可先执行 `docker pull node:22-alpine` 和 `docker pull python:3.12-slim` 再重试构建；项目不修改全局网络配置。
