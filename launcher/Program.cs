using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace GenMediaLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string hash;
            using (var sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToLowerInvariant()))).Replace("-", "");
            bool created;
            using (var mutex = new Mutex(true, "Local\\GenMediaVault-" + hash, out created))
            {
                if (!created) { if (args.Length == 0) MessageBox.Show("此目录的启动器已经打开。", "GenMedia Vault"); return 2; }
                if (args.Length > 0)
                {
                    // WinExe has no console code page; explicit writers support redirected CLI pipes.
                    Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
                    Console.SetError(new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true });
                    var engine = new LauncherEngine(root, Console.WriteLine);
                    try
                    {
                        if (args[0] == "--start") engine.Start(CancellationToken.None).GetAwaiter().GetResult();
                        else if (args[0] == "--stop") engine.Stop(CancellationToken.None).GetAwaiter().GetResult();
                        else { Console.WriteLine("GenMediaVault.exe [--start | --stop] (command mode does not open a browser)"); return 2; }
                        return 0;
                    }
                    catch (Exception error) { Console.Error.WriteLine(engine.Config.Redact(error.Message)); return 1; }
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new LauncherForm(root));
                return 0;
            }
        }
    }

    public sealed class LauncherForm : Form
    {
        private readonly LauncherEngine engine;
        private readonly RichTextBox log;
        private readonly Label status;
        private readonly ProgressBar progress;
        private readonly Button start, stop, cancel, settings, account;
        private CancellationTokenSource operation;
        private readonly object logLock = new object();
        private static readonly Color Background = Color.FromArgb(17, 22, 21);
        private static readonly Color Surface = Color.FromArgb(29, 36, 32);
        private static readonly Color Ink = Color.FromArgb(225, 231, 222);
        private static readonly Color Accent = Color.FromArgb(213, 187, 141);

        public LauncherForm(string root)
        {
            Text = "GenMedia Vault · 启动器"; ClientSize = new Size(830, 640); MinimumSize = new Size(730, 580);
            StartPosition = FormStartPosition.CenterScreen; BackColor = Background; ForeColor = Ink;
            Font = new Font("Microsoft YaHei UI", 10); AutoScaleMode = AutoScaleMode.Dpi;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            engine = new LauncherEngine(root, AddLog);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(24), ColumnCount = 1, RowCount = 7 };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 76));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 54));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 46));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 32));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 8));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            Controls.Add(layout);
            var heading = new Label { Text = "GenMedia Vault\n你的生成图片档案库 · Windows 启动器", Dock = DockStyle.Fill,
                Font = new Font("Microsoft YaHei UI", 17, FontStyle.Bold), ForeColor = Accent };
            layout.Controls.Add(heading, 0, 0);
            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
            start = MakeButton("启动服务", async delegate { await Operate(true); }, true);
            stop = MakeButton("停止服务", async delegate { await Operate(false); }, false);
            actions.Controls.Add(start); actions.Controls.Add(stop);
            actions.Controls.Add(MakeButton("打开网页", delegate { TryAction(() => LauncherEngine.OpenUrl(engine.Config.Url)); }, false));
            cancel = MakeButton("取消操作", delegate { if (operation != null) operation.Cancel(); }, false); cancel.Enabled = false;
            actions.Controls.Add(cancel); layout.Controls.Add(actions, 0, 1);
            var tools = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
            account = MakeButton("初始账号", delegate { ShowAccount(); }, false);
            settings = MakeButton("设置", delegate { ShowSettings(); }, false);
            tools.Controls.Add(account); tools.Controls.Add(settings);
            tools.Controls.Add(MakeButton("项目目录", delegate { TryAction(() => Process.Start(new ProcessStartInfo("explorer.exe", ChildProcess.Quote(root)) { UseShellExecute = true })); }, false));
            tools.Controls.Add(MakeButton("安装 Docker", delegate { TryAction(() => LauncherEngine.OpenUrl("https://www.docker.com/products/docker-desktop/")); }, false));
            layout.Controls.Add(tools, 0, 2);
            status = new Label { Text = "准备就绪 · 首次启动需要 Docker Desktop 和网络", Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft };
            layout.Controls.Add(status, 0, 3);
            progress = new ProgressBar { Dock = DockStyle.Fill, Style = ProgressBarStyle.Blocks, Height = 4 };
            layout.Controls.Add(progress, 0, 4);
            log = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, BackColor = Surface, ForeColor = Ink,
                BorderStyle = BorderStyle.None, Font = new Font("Microsoft YaHei UI", 9), DetectUrls = false };
            layout.Controls.Add(log, 0, 5);
            layout.Controls.Add(new Label { Text = "停止服务会保留数据。关闭本窗口后，已启动的服务仍会运行。", Dock = DockStyle.Fill,
                ForeColor = Color.FromArgb(155, 169, 157), TextAlign = ContentAlignment.MiddleLeft }, 0, 6);
            Shown += delegate { AddLog("项目位置：" + root); AddLog("点击“启动服务”开始。首次会生成本机独立密码；已有 .env 会保留。升级请覆盖原项目目录，保留 .env 与 Docker 数据卷。"); };
            FormClosing += delegate(object sender, FormClosingEventArgs e)
            { if (operation != null) { e.Cancel = true; MessageBox.Show(this, "操作尚未结束。可点击“取消操作”，待停止等待后再关闭窗口。", Text); } };
        }
        private Button MakeButton(string title, EventHandler action, bool primary)
        {
            var button = new Button { Text = title, AutoSize = true, Height = 38, MinimumSize = new Size(116, 36),
                FlatStyle = FlatStyle.Flat, Margin = new Padding(0, 3, 10, 3), Padding = new Padding(6, 3, 6, 3),
                BackColor = primary ? Accent : Surface, ForeColor = primary ? Background : Ink };
            button.FlatAppearance.BorderColor = Color.FromArgb(60, 74, 63); button.Click += action; return button;
        }
        private void TryAction(Action action) { try { action(); } catch (Exception error) { MessageBox.Show(this, engine.Config.Redact(error.Message), "无法完成操作"); } }
        private void AddLog(string message)
        {
            if (IsDisposed) return;
            if (InvokeRequired) { BeginInvoke((Action<string>)AddLog, message); return; }
            string safe = DateTime.Now.ToString("HH:mm:ss") + "  " + engine.Config.Redact(message) + Environment.NewLine;
            if (log.TextLength > 160000) log.Clear();
            log.AppendText(safe); log.SelectionStart = log.TextLength; log.ScrollToCaret();
            try
            {
                lock (logLock)
                {
                    string dir = Path.Combine(engine.Config.Root, "data"); Directory.CreateDirectory(dir);
                    string file = Path.Combine(dir, "launcher.log");
                    if (File.Exists(file) && new FileInfo(file).Length > 2 * 1024 * 1024) File.WriteAllText(file, "", Encoding.UTF8);
                    File.AppendAllText(file, safe, Encoding.UTF8);
                }
            }
            catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
        private async Task Operate(bool starting)
        {
            operation = new CancellationTokenSource(); start.Enabled = stop.Enabled = settings.Enabled = account.Enabled = false; cancel.Enabled = true;
            progress.Style = ProgressBarStyle.Marquee; status.Text = starting ? "正在启动…首次下载请耐心等待" : "正在停止服务…";
            try
            {
                if (starting) { await engine.Start(operation.Token); status.Text = "运行中 · " + engine.Config.Url; LauncherEngine.OpenUrl(engine.Config.Url); }
                else { await engine.Stop(operation.Token); status.Text = "已停止 · 数据保留"; }
            }
            catch (OperationCanceledException) { AddLog("已取消等待。已创建的容器和数据会保留；可再次启动或停止服务。"); status.Text = "操作已取消"; }
            catch (Exception error) { AddLog("错误：" + error.Message); status.Text = "操作未完成 · 请查看日志"; MessageBox.Show(this, engine.Config.Redact(error.Message), "GenMedia Vault", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
            finally { operation.Dispose(); operation = null; start.Enabled = stop.Enabled = settings.Enabled = account.Enabled = true; cancel.Enabled = false; progress.Style = ProgressBarStyle.Blocks; }
        }
        private void ShowAccount()
        {
            TryAction(delegate {
                engine.Config.Ensure();
                using (var dialog = new Form { Text = "本机初始管理员账号", ClientSize = new Size(600, 225), StartPosition = FormStartPosition.CenterParent,
                    Font = Font, FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false })
                {
                    var user = new TextBox { Text = engine.Config.Get("GMV_ADMIN_USERNAME", "admin"), ReadOnly = true, Left = 20, Top = 35, Width = 555 };
                    var password = new TextBox { Text = engine.Config.Get("GMV_ADMIN_PASSWORD", ""), ReadOnly = true, UseSystemPasswordChar = true, Left = 20, Top = 97, Width = 555 };
                    dialog.Controls.Add(new Label { Text = "用户名", Left = 20, Top = 12 }); dialog.Controls.Add(user);
                    dialog.Controls.Add(new Label { Text = "初始密码（来自本机 .env）", Left = 20, Top = 73, Width = 400 }); dialog.Controls.Add(password);
                    var reveal = new CheckBox { Text = "显示密码", Left = 20, Top = 135, Width = 120 }; reveal.CheckedChanged += delegate { password.UseSystemPasswordChar = !reveal.Checked; };
                    var copy = new Button { Text = "复制密码", Left = 155, Top = 132, Width = 100, Height = 30 }; copy.Click += delegate { Clipboard.SetText(password.Text); };
                    dialog.Controls.Add(reveal); dialog.Controls.Add(copy);
                    dialog.Controls.Add(new Label { Text = "若已在系统中修改密码，请使用修改后的密码。不要分享 .env。", Left = 20, Top = 180, Width = 555, Height = 35 });
                    dialog.ShowDialog(this);
                }
            });
        }
        private void ShowSettings()
        {
            TryAction(delegate {
                engine.Config.Ensure();
                using (var dialog = new Form { Text = "本机启动设置", ClientSize = new Size(600, 255), StartPosition = FormStartPosition.CenterParent,
                    Font = Font, FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false })
                {
                    var port = new NumericUpDown { Minimum = 1, Maximum = 65535, Value = engine.Config.Port, Left = 20, Top = 35, Width = 140 };
                    var folder = new TextBox { Text = engine.Config.Get("GMV_IMPORT_ROOT", "./data/imports"), Left = 20, Top = 102, Width = 455 };
                    dialog.Controls.Add(new Label { Text = "网页端口", Left = 20, Top = 12, Width = 160 }); dialog.Controls.Add(port);
                    dialog.Controls.Add(new Label { Text = "索引目录（只读挂载，应用内路径为 /imports）", Left = 20, Top = 77, Width = 550 }); dialog.Controls.Add(folder);
                    var browse = new Button { Text = "选择目录", Left = 485, Top = 98, Width = 90, Height = 32 };
                    browse.Click += delegate { using (var picker = new FolderBrowserDialog()) if (picker.ShowDialog(dialog) == DialogResult.OK) folder.Text = picker.SelectedPath; }; dialog.Controls.Add(browse);
                    dialog.Controls.Add(new Label { Text = "保存后再次点击“启动服务”应用设置。已有账号、数据库密码和其他配置保留。", Left = 20, Top = 146, Width = 555, Height = 40 });
                    var save = new Button { Text = "保存设置", Left = 455, Top = 202, Width = 120, Height = 32 };
                    save.Click += delegate { try { engine.Config.SaveSettings((int)port.Value, folder.Text); dialog.Close(); AddLog("设置已保存，再次启动服务后生效。"); } catch (Exception error) { MessageBox.Show(dialog, error.Message); } };
                    dialog.Controls.Add(save); dialog.ShowDialog(this);
                }
            });
        }
    }
}
