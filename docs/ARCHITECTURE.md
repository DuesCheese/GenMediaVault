# 架构与接口

## 部署结构

浏览器 → 同源 FastAPI 应用（React 静态文件＋REST）→ PostgreSQL。

独立 worker 使用同一代码包和数据库，通过数据库租约领取任务，负责扫描、提取、缩略图、分类、重新解析与导出。应用和 worker 共享 `/data`；外部源目录通过 `/imports:ro` 挂载。数据库只在 Compose 内部网络暴露。

后端模块位于 `backend/genmedia`：`api` 处理 HTTP 与权限，`schemas` 定义输入输出，`services` 执行领域操作，`parsers` 提取与标准化，`search` 维护 AST 与 SQL 编译，`worker` 调度，`models` 定义持久化结构。

## 领域模型

- `libraries` 区分托管与索引；`assets` 是库内按 SHA-256 唯一的逻辑资产。
- `physical_files` 登记原件位置、文件状态和 Sidecar 指纹；相同内容可以有多个来源路径。
- `raw_metadata` 保存追加式提取快照、来源和解析器版本；任务重放不会产生完全相同的快照。
- `generations` 保存常用查询列和 JSONB 标准化字段、冲突与解析时间；`prompt_tokens` 保存正负极性、权重、位置和类别。
- `model_refs` / `asset_models` 建立模型和 LoRA 关联；`tags` / `asset_tags` 保存命名空间与 user、metadata、prompt、system、rule 来源。
- `user_assets` 保存用户自己的收藏、评分、备注和 keep/maybe/reject 状态。
- `collections` / `collection_assets` 区分共享普通集合与动态智能集合；自动规则成员和人工成员分别记录。
- `users` / `sessions` 保存账号与可撤销会话；数据库保存会话令牌摘要。`jobs` 保存请求人、输入、进度、结果、尝试次数和租约。

初始迁移使用冻结 SQL，不依赖未来可能变化的 ORM 模型。后续结构修改必须新增 Alembic revision，不能重新生成已发布的初始迁移。

解析器采用 `detect → extract → normalize` 协议。解析结果同时保留原始 bundle、标准化字段、来源、置信分数、冲突及告警；标准化 JSON 的 `parsing` 包含解析器版本、各来源分数和字段来源。分数用于表示格式识别强度，是启发式分数，不是经过统计校准的正确率。多节点歧义仍以告警和缺失字段表达。

## 搜索协议

`POST /api/v1/search` 接受 `query` 或 `ast`，二者互斥。还支持 `library_id`、`collection_id`、`trash`、`sort`、`cursor`、`limit`、`facets`。返回 `items`、`next_cursor`、标准化 `ast`，以及可选的计数与生成器分面。

```json
{"version":1,"root":{"type":"and","children":[{"type":"condition","field":"generator","op":"eq","value":"novelai"},{"type":"condition","field":"cfg","op":"between","value":[4,6]}]}}
```

组节点是 `and` / `or`，否定节点是 `not`＋`child`，叶节点是 `condition`＋`field/op/value`。字段和操作白名单、类型校验、8192 字符长度、100 节点及 12 层深度限制在 SQL 编译之前执行。叶节点不匹配缺失值，NOT 正确包含这些记录；`missing` 显式匹配缺失。

排序通过稳定的 `(排序值, asset_id)` 游标推进。游标包含查询、筛选和用户指纹，不能用于另一查询。评分、收藏查询绑定当前用户。规则复用 AST，但禁止个人字段。

`POST /search/parse` 双向转换 AST / DSL；`GET /search/fields` 提供字段类型；`GET /search/suggest?field=model&q=...` 提供字段或数据值建议。

## API 分组

统一前缀 `/api/v1`；完整协议见 `openapi.json`。

| 分组 | 主要接口 |
| --- | --- |
| 会话 | `POST /auth/login`、`GET /auth/me`、`POST /auth/logout`、`POST /auth/password` |
| 用户 | `GET/POST /users`、`PATCH /users/{id}`（管理员） |
| 媒体库 | `GET/POST /libraries`、`PATCH /libraries/{id}`、`POST /libraries/{id}/scan` |
| 导入 | `POST /imports`，multipart 的 `library_id`＋多个 `files`，返回任务 ID |
| 资产 | `GET /assets/{id}`、`/original`、`/thumbnail`、`/workflow`；`PATCH /assets/{id}/personal` |
| 批量 | `POST /assets/bulk`，最多 1000 个明确 ID，操作覆盖标签、集合、个人状态、回收、恢复、重新解析和导出 |
| 整理 | `GET /tags`；`GET/POST /collections`、`PATCH/DELETE /collections/{id}` |
| 规则 | `GET/POST /rules`、`PATCH/DELETE /rules/{id}`、`POST /rules/apply`（管理员） |
| 任务 | `GET /jobs`、`POST /jobs/{id}/retry` 或 `/cancel`；`GET /jobs/{id}/download` |
| 系统 | `GET /health`；`GET /system`（管理员） |

除健康检查和登录外，业务接口均要求登录。写请求和 POST 搜索需要 `X-CSRF-Token`，令牌来自登录或 `/auth/me`。导出包仅请求者可下载，因为包内包含其个人备注。原件、缩略图不通过公开静态目录提供。

## 文件与任务可靠性

文件在稳定等待窗口后读取；读取前后比较大小、修改时间与 Sidecar 指纹。哈希计算使用流式读取，库＋哈希事务锁协调并发导入。托管原件和缩略图先写临时文件再原子替换。

任务使用 `FOR UPDATE SKIP LOCKED`、90 秒租约与独立心跳。租约失效可重领，自动恢复最多三次，之后等待人工重试。扫描与批量任务逐项提交；失败报告保留最多 100 项，成功项目不回滚。完成的上传清理暂存文件，失败或取消任务保留暂存以便重试。

索引库删除保留墓碑记录，扫描不重新出现；不存在自动永久删除任务。外部源文件缺失不清除数据库中的原始元数据。未知元信息始终保留；重新标准化不能代替未来新增的原文件提取。
