# GenMedia Vault v0.5.0

新增 Windows EXE 启动器。下载 `GenMediaVault-v0.5.0-Windows.zip`，完整解压后双击 `GenMediaVault.exe`，点击“启动服务”。

启动器会检查并启动 Docker Desktop、构建应用、运行数据库迁移，服务就绪后打开网页。首次自动生成本机管理员密码，可通过“初始账号”查看；设置页支持端口和只读索引目录。停止服务保留全部数据卷。

发布包包含源码、启动器源码、编译脚本、数据库迁移和使用文档；不包含用户图片、数据库、密码、备份或本机运行日志。

运行要求：Windows 10/11、Docker Desktop（Linux 容器）；首次构建需要网络。EXE 是 Docker 启动入口，不是离线单文件程序；宿主机无需安装 Python、Node.js 或 GPU。

保留 v0.4 的全局标签分组、翻译词库同步与自动归组，以及上传解析、搜索、ZIP 导出和管理员完整备份。

校验：使用 `SHA256SUMS.txt` 核对发布 ZIP，压缩包内 `RELEASE-MANIFEST.json` 提供逐文件校验值。

升级：先备份，再覆盖原项目目录；保留原 `.env` 和 Docker 数据卷。详细操作见 `docs/WINDOWS-LAUNCHER.md`。
