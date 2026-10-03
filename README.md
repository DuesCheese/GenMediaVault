# GenMedia Vault

面向 NovelAI、Stable Diffusion / A1111、ComfyUI 的自托管图片资产库。

**导入 → 提取生成元信息 → 自动分类 → 复杂搜索 → 复制参数继续创作。**

首版采用中文 Web 界面、FastAPI、React 和 PostgreSQL。上传默认进入私人空间，可批量公开或取消公开；收藏、评分、筛选状态、私人备注按用户隔离。原始图片保持不变。

当前版本 **v0.6**：新增私人空间、超级管理员、上传者管理、限时只读分享图链和 GitHub 在线更新。详见 [v0.6 使用与升级指南](docs/V0.6.md)。提供 Windows EXE 图形启动器和完整发布 ZIP。下载后解压，双击 `GenMediaVault.exe`，点击“启动服务”；首次需安装 Docker Desktop。详见 [Windows 启动说明](docs/WINDOWS-LAUNCHER.md)。全局分组见 [v0.4](docs/V0.4.md)，翻译见 [v0.3](docs/V0.3.md)。

## 已实现

- PNG / JPEG / WebP 文件与文件夹上传；托管库与只读索引库。
- SHA-256 同库同上传者去重、缩略图、虚拟滚动图库、详情灯箱与批量整理。
- A1111、NovelAI 普通及 alpha 隐写元信息、ComfyUI 执行图与工作流、EXIF、JSON/TXT Sidecar。
- 原始元数据快照、字段冲突记录、版本化标准化、重新解析；Seed 用字符串返回。
- 简单搜索、可视化条件组和 DSL 共用 AST；AND / OR / NOT、范围、日期、前缀、字段缺失。
- 命名空间标签、共享普通集合、智能集合、个人评分与收藏、元信息复制、ZIP 导出。
- 目录监控与定期补扫、内置字典与自动分类规则、持久化任务、重试取消、回收站。
- 管理员创建成员、管理库和规则；会话登录、CSRF 校验、受保护的媒体下载。

视频、SQLite、脱离 Docker 的独立程序、视觉 AI 标签、语义搜索属于后续版本。

## 使用 Docker 启动

需要 Docker Engine / Docker Desktop 和 Compose v2。Windows 使用 Linux 容器。

1. 在项目目录执行 `python scripts/configure.py`，生成不进入版本控制的 `.env`；也可以复制 `.env.example` 后替换两个示例密码。管理员密码在 `GMV_ADMIN_PASSWORD` 中，至少 12 位。
2. 执行 `docker compose up -d --build`。
3. 打开 <http://localhost:8080>，使用 `admin` 与 `.env` 中的管理员密码登录。首次启动自动创建“生成作品”托管库。
4. 在“导入文件”中上传图片。需要读取既有目录时，按下面的挂载说明创建索引库。

数据库、原图与缓存使用独立持久卷。不要用 `docker compose down -v` 停止日常服务；普通停止使用 `docker compose stop`。

### 索引本地已有目录

在 `.env` 设置 `GMV_IMPORT_ROOT` 为宿主机目录，例如 Windows 的 `D:/AI/output`，或 Linux 的 `/mnt/ai-output`。重新执行 `docker compose up -d` 后，在“设置 → 添加媒体库”选择索引库，填写 **容器内路径 `/imports`**。

挂载默认只读。索引库回收站不删除磁盘源文件，重复扫描也不会自动恢复已删除记录。监控文件变化之外，每 5 分钟补扫一次；网络盘不提供可靠文件事件时仍可补扫。

### 搜索示例

```text
generator:novelai AND cfg:4..6
(prompt:"white hair" OR prompt:"silver hair") AND rating:>=4
prompt.tag:"white hair" AND NOT negative:"monochrome"
tag:hair:white AND model:portrait*
seed:18446744073709551615
model:missing
imported:2026-10-01..2026-10-31
```

`prompt:` 匹配文本包含；`prompt.tag:` 匹配解析后的完整 token。日期按 UTC 日期边界查询；`created` 表示导入时记录的源文件修改时间，并非保证准确的生成时间。无字段的多个词按 AND 组合，每个词在正向 Prompt、文件名和标签中搜索。

可视化筛选器可能生成 `field:=`（精确文本）或 `field:~`（包含）来明确语义。搜索条件可保存在地址栏或智能集合中；智能集合中的评分、收藏按当前查看者计算。

### Sidecar

完整文件名优先：`image.png.json` / `image.png.txt`。只有同目录中唯一媒体匹配时，才使用 `image.json` / `image.txt`。

通用 JSON 使用以下结构，或使用识别的生成器原始格式：

```json
{"schema_version":1,"generation":{"prompt":"white hair, blue eyes","negative":"bad hands","seed":"123456","model":"my-model","steps":28,"cfg":5}}
```

JSON 识别字段优先于内嵌元信息；TXT 仅补充缺失 Prompt。冲突记录保存原值和来源。重复内容的新 Sidecar 不覆盖首份资产信息。ComfyUI 多采样器或未知路径不猜测参数，保留完整图并告警；界面工作流可原样下载和复制。

## 开发、测试与运维

- [架构与接口](docs/ARCHITECTURE.md)
- [开发与测试](docs/DEVELOPMENT.md)
- [部署、备份、恢复与升级](docs/OPERATIONS.md)
- [验收记录与限制](docs/VALIDATION.md)
- [开发阶段与后续事项](docs/ROADMAP.md)

Python 依赖由 `uv.lock` 锁定，前端依赖由 `frontend/package-lock.json` 锁定。API 文档位于 `/docs`，生成的前端接口类型位于 `frontend/src/api.generated.ts`。

本项目不下载模型、不执行工作流、不向云端上传媒体。NovelAI 格式参考其官方元信息工具；当前内置字典是小型可理解规则集，不能替代视觉识别。
