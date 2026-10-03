using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GenMediaLauncher
{
    public sealed class LocalConfig
    {
        public readonly string Root;
        public readonly Dictionary<string, string> Values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public LocalConfig(string root) { Root = Path.GetFullPath(root); Reload(); }
        public void Reload()
        {
            Values.Clear();
            string file = Path.Combine(Root, ".env");
            if (!File.Exists(file)) return;
            foreach (string raw in File.ReadAllLines(file, Encoding.UTF8))
            {
                string line = raw.Trim();
                int split = line.IndexOf('=');
                if (line.StartsWith("#") || split < 1) continue;
                string value = line.Substring(split + 1).Trim();
                if (value.Length > 1 && ((value[0] == '"' && value[value.Length - 1] == '"') ||
                    (value[0] == '\'' && value[value.Length - 1] == '\''))) value = value.Substring(1, value.Length - 2);
                Values[line.Substring(0, split).Trim()] = value;
            }
        }
        public string Get(string key, string fallback) { string value; return Values.TryGetValue(key, out value) ? value : fallback; }
        public int Port
        {
            get { int port; if (!Int32.TryParse(Get("GMV_PORT", "8080"), out port) || port < 1 || port > 65535) throw new Exception("GMV_PORT 必须在 1–65535 之间，请在设置中修改。"); return port; }
        }
        public string Url { get { return "http://localhost:" + Port; } }
        public static string Secret()
        {
            byte[] bytes = new byte[32];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        }
        public bool Ensure()
        {
            if (!File.Exists(Path.Combine(Root, "compose.yaml"))) throw new Exception("未找到 compose.yaml。请完整解压 ZIP 后启动，不要只复制 EXE，也不要直接在压缩包内运行。");
            bool created = false;
            string file = Path.Combine(Root, ".env");
            if (!File.Exists(file))
            {
                // CreateNew ensures a second launcher cannot overwrite first-run credentials.
                using (var stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var writer = new StreamWriter(stream, new UTF8Encoding(false)))
                {
                    using (var sha = SHA256.Create())
                        writer.WriteLine("COMPOSE_PROJECT_NAME=gmv-" + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()))).Replace("-", "").Substring(0, 12).ToLowerInvariant());
                    writer.WriteLine("POSTGRES_PASSWORD=" + Secret());
                    writer.WriteLine("GMV_ADMIN_USERNAME=admin");
                    writer.WriteLine("GMV_ADMIN_PASSWORD=" + Secret());
                    writer.WriteLine("GMV_BIND=127.0.0.1\nGMV_PORT=8080\nGMV_SECURE_COOKIE=false\nGMV_IMPORT_ROOT=./data/imports");
                }
                created = true;
            }
            Reload();
            Directory.CreateDirectory(Path.Combine(Root, "data", "imports"));
            return created;
        }
        public void Validate()
        {
            foreach (string key in new[] { "POSTGRES_PASSWORD", "GMV_ADMIN_PASSWORD" })
                if (Get(key, "").Length < 12 || Get(key, "").StartsWith("change-this-"))
                    throw new Exception(key + " 尚未设置有效密码。请编辑 .env，保留已有数据库密码；不要删除已有配置。");
            int port = Port;
        }
        public string Redact(string message)
        {
            foreach (var item in Values)
                if ((item.Key.IndexOf("PASSWORD", StringComparison.OrdinalIgnoreCase) >= 0 || item.Key.IndexOf("TOKEN", StringComparison.OrdinalIgnoreCase) >= 0) && item.Value.Length > 0)
                    message = message.Replace(item.Value, "[已隐藏]");
            return message;
        }
        public void SaveSettings(int port, string importRoot)
        {
            if (port < 1 || port > 65535) throw new Exception("端口必须在 1–65535 之间。");
            importRoot = importRoot.Trim().Replace('\\', '/');
            if (String.IsNullOrWhiteSpace(importRoot) || importRoot.IndexOfAny(new[] { '\r', '\n', '\'', '\0' }) >= 0)
                throw new Exception("目录不能为空，且不能含单引号或换行。");
            var replacements = new Dictionary<string, string> { { "GMV_PORT", port.ToString() }, { "GMV_IMPORT_ROOT", "'" + importRoot + "'" } };
            string file = Path.Combine(Root, ".env");
            var lines = new List<string>();
            foreach (string line in File.ReadAllLines(file, Encoding.UTF8))
            {
                int split = line.IndexOf('='); string key = split > 0 ? line.Substring(0, split).Trim() : "";
                string replacement;
                if (replacements.TryGetValue(key, out replacement)) { lines.Add(key + "=" + replacement); replacements.Remove(key); }
                else lines.Add(line);
            }
            foreach (var item in replacements) lines.Add(item.Key + "=" + item.Value);
            string temporary = file + ".launcher-tmp";
            File.WriteAllLines(temporary, lines, new UTF8Encoding(false));
            File.Replace(temporary, file, null);
            Reload();
        }
    }

    public static class ChildProcess
    {
        public static string Quote(string value)
        {
            var result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); result.Append(c); slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        public static async Task<int> Run(string executable, string arguments, string root, Action<string> log, CancellationToken cancellation, int timeoutSeconds)
        {
            var start = new ProcessStartInfo(executable, arguments) { WorkingDirectory = root, UseShellExecute = false,
                CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            using (var process = new Process { StartInfo = start })
            {
                process.Start();
                Task output = Pump(process.StandardOutput, log), error = Pump(process.StandardError, log);
                var watch = Stopwatch.StartNew();
                try
                {
                    while (!process.HasExited)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        if (watch.Elapsed.TotalSeconds > timeoutSeconds) throw new TimeoutException("命令等待超时。请检查 Docker、网络和日志后重试。");
                        await Task.Delay(200, cancellation);
                    }
                    await Task.WhenAll(output, error);
                    return process.ExitCode;
                }
                finally
                {
                    if (!process.HasExited) { process.Kill(); process.WaitForExit(5000); }
                }
            }
        }
        private static async Task Pump(StreamReader reader, Action<string> log)
        { string line; while ((line = await reader.ReadLineAsync()) != null) if (log != null) log(line); }
    }

    public sealed class LauncherEngine
    {
        public readonly LocalConfig Config;
        private readonly Action<string> logger;
        public LauncherEngine(string root, Action<string> log) { Config = new LocalConfig(root); logger = log; }
        public void Log(string message) { if (logger != null) logger(Config.Redact(message)); }
        public string DockerPath()
        {
            string installed = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Docker", "Docker", "resources", "bin", "docker.exe");
            if (File.Exists(installed)) return installed;
            foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            { try { string path = Path.Combine(directory.Trim('"'), "docker.exe"); if (File.Exists(path)) return path; } catch (ArgumentException) { } }
            throw new Exception("未找到 Docker Desktop。请安装并启用 Linux 容器，然后重试。可使用窗口中的“安装 Docker”按钮打开官方页面。");
        }
        private Task<int> Docker(string arguments, CancellationToken cancel, bool quiet, int timeout)
        { return ChildProcess.Run(DockerPath(), arguments, Config.Root, quiet ? null : (Action<string>)Log, cancel, timeout); }
        private async Task<string> ContainerOS(CancellationToken cancel, int timeout)
        {
            string platform = "";
            int result = await ChildProcess.Run(DockerPath(), "info --format {{.OSType}}", Config.Root,
                line => { if (line.Trim() == "linux" || line.Trim() == "windows") platform = line.Trim(); }, cancel, timeout);
            return result == 0 ? platform : "";
        }
        public async Task Start(CancellationToken cancel)
        {
            if (Config.Ensure()) Log("首次配置已创建。随机密码仅保存到本机 .env，可点击“初始账号”查看。");
            Config.Validate();
            Log("正在检查 Docker Desktop…");
            string osType = "";
            try { osType = await ContainerOS(cancel, 15); } catch (TimeoutException) { }
            if (osType.Length == 0)
            {
                string desktop = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Docker", "Docker", "Docker Desktop.exe");
                if (!File.Exists(desktop)) throw new Exception("Docker 引擎未启动。请打开 Docker Desktop 并切换到 Linux 容器。");
                Process.Start(new ProcessStartInfo(desktop) { UseShellExecute = true, WindowStyle = ProcessWindowStyle.Hidden });
                Log("已请求启动 Docker Desktop，等待引擎就绪…");
                for (int i = 0; i < 60 && osType.Length == 0; i++)
                {
                    await Task.Delay(3000, cancel);
                    try { osType = await ContainerOS(cancel, 10); } catch (TimeoutException) { }
                }
                if (osType.Length == 0) throw new Exception("Docker 尚未就绪。首次安装可能需要完成 Docker 欢迎界面、启用 WSL 2 或重启 Windows。");
            }
            if (osType != "linux") throw new Exception("请在 Docker Desktop 中切换为 Linux containers。");
            if (await Docker("compose version", cancel, false, 15) != 0) throw new Exception("需要 Docker Compose v2，请更新 Docker Desktop。");
            Log("正在构建并启动服务。首次运行需联网下载依赖，可能需要数分钟。\n数据保存在 Docker 持久卷中，关闭本窗口不会停止服务。");
            int result = await Docker("compose --ansi never up -d --build --wait --wait-timeout 300", cancel, false, 3600);
            if (result != 0) throw new Exception("启动失败。请查看上方日志：常见原因是端口被占用、网络下载失败或 Docker 内存不足。可在设置中调整端口后重试。");
            Log("服务已启动：" + Config.Url);
        }
        public async Task Stop(CancellationToken cancel)
        {
            if (!File.Exists(Path.Combine(Config.Root, ".env"))) throw new Exception("尚无本机配置，无需停止。");
            Log("正在停止本项目的服务；数据库和媒体卷会保留…");
            if (await Docker("compose --ansi never stop", cancel, false, 180) != 0) throw new Exception("停止失败，请检查 Docker 状态和日志。");
            Log("服务已停止，数据保留。");
        }
        public static void OpenUrl(string url) { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    }
}
