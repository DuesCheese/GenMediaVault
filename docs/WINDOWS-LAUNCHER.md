# Windows EXE 启动器

## 快速开始

1. 安装 Docker Desktop，启用 WSL 2 和 **Linux 容器**。首次启动需要联网下载容器镜像和构建依赖，不需要在宿主机安装 Python、Node.js 或 GPU 驱动。
2. 下载 Release 中的 `GenMediaVault-v0.6.0-Windows.zip`，**完整解压**到有写入权限的固定目录。
3. 双击根目录的 `GenMediaVault.exe`，点击 **启动服务**。启动器会检查 Docker、必要时请求启动 Docker Desktop，然后构建并启动 app、worker、PostgreSQL 和更新服务。
4. 就绪后自动打开浏览器。点击启动器的 **初始账号** 查看用户名及初始密码；首次运行随机生成的密码仅保存在本机 `.env`。

EXE 是 Docker Compose 的图形启动入口，不是脱离 Docker 的离线单文件版。Windows 10/11 使用系统 .NET Framework，EXE 不要求单独安装 .NET 6 SDK。发布的 EXE 未进行商业代码签名，可核对 Release 的 SHA-256；源码和编译脚本均包含在 ZIP 中。

## 按钮与设置

- **启动服务**：保留已有 `.env` 和数据卷，执行构建、迁移和健康检查。首次网络下载可能需要数分钟。
- **停止服务**：停止当前项目的容器，保留数据库、原图、缓存和附件。不会删除 Docker 卷。
- **打开网页**：打开当前端口的本机页面，默认 `http://localhost:8080`。
- **初始账号**：从 `.env` 读取初始凭据，密码默认遮挡，可显示或复制。修改过账号密码时，请使用修改后的密码。
- **设置**：修改网页端口、只读索引目录。再次点击启动服务应用设置；应用内填写挂载路径 `/imports`。
- **取消操作**：停止等待和当前 Docker 命令，不删除已创建的容器或数据。之后可重新启动或停止服务。
- **项目目录**：打开解压目录；日志位于 `data/launcher.log`，已对本机密码脱敏。

关闭启动器窗口不会停止已运行的服务。Docker Desktop 首次安装的协议、WSL 初始化或重启提示需要自行完成。若端口 8080 被其他程序占用，可在设置中修改。

在线更新、私人空间与分享说明见 [v0.6 指南](V0.6.md)。

## 数据与升级

首次生成 `.env` 时会写入此安装目录独立的 `COMPOSE_PROJECT_NAME`，确保不同安装目录互不覆盖。数据库和托管媒体保存在这个项目的 Docker 持久卷中。

更新前建议在应用设置中创建完整备份。将新 ZIP 的内容覆盖到原目录，**保留原 `.env`**，再点击启动服务。不要删除 Docker 卷；不要用 `docker compose down -v` 做普通停止。如果迁移安装目录，也必须保留原 `.env`（包括项目名和数据库密码）以及原 Docker 数据卷。

ZIP 不包含 `.env`、用户图片、数据库、备份、运行日志或本机依赖目录。传给其他人时直接使用官方 Release ZIP，避免重新压缩含私人数据的整个安装目录。

## 从源码构建

Windows PowerShell 在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_launcher.ps1 -Test
python scripts/package_release.py
```

编译使用 Windows .NET Framework 自带的 C# 编译器。输出为 `dist/GenMediaVault.exe`、发布 ZIP 和 `dist/SHA256SUMS.txt`。ZIP 内的 `RELEASE-MANIFEST.json` 列出每个文件的 SHA-256 和源码提交。

自动化部署也可执行 `GenMediaVault.exe --start` 或 `--stop`；命令模式不自动打开浏览器，失败返回非零退出码。
