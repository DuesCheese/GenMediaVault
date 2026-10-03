using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using GenMediaLauncher;

internal static class LauncherTests
{
    private static int checks;
    private static void Check(bool value, string description) { if (!value) throw new Exception(description); checks++; }
    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--echo") { foreach (string arg in args.Skip(1)) Console.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(arg))); return 0; }
        if (args.Length > 0 && args[0] == "--wait") { Thread.Sleep(20000); return 0; }
        string dist = Path.GetFullPath(Path.Combine(Environment.CurrentDirectory, "dist"));
        string root = Path.Combine(dist, "launcher-tests-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(root);
            string project = Path.Combine(root, "中文 空格 & test"); Directory.CreateDirectory(project);
            var config = new LocalConfig(project);
            bool refused = false;
            try { config.Ensure(); } catch (Exception) { refused = true; }
            Check(refused && !File.Exists(Path.Combine(project, ".env")), "Incomplete ZIP must not create configuration");
            File.WriteAllText(Path.Combine(project, "compose.yaml"), "services: {}", Encoding.UTF8);
            Check(config.Ensure(), "First run creates .env");
            string original = File.ReadAllText(Path.Combine(project, ".env"));
            config.Validate();
            Check(config.Get("POSTGRES_PASSWORD", "").Length == 64, "Database password length");
            Check(config.Get("GMV_ADMIN_PASSWORD", "") != config.Get("POSTGRES_PASSWORD", ""), "Independent credentials");
            Check(config.Get("COMPOSE_PROJECT_NAME", "").StartsWith("gmv-"), "Stable deployment project identity");
            Check(!config.Ensure() && File.ReadAllText(Path.Combine(project, ".env")) == original, "Never replace existing configuration");
            File.AppendAllText(Path.Combine(project, ".env"), "# preserve custom options\nCUSTOM_SETTING=hello\n");
            config.SaveSettings(18085, "F:/中文 目录/$images");
            Check(config.Port == 18085 && config.Get("GMV_IMPORT_ROOT", "") == "F:/中文 目录/$images", "Chinese import path and custom port");
            Check(config.Get("CUSTOM_SETTING", "") == "hello", "Custom config preserved");
            Check(File.ReadAllText(Path.Combine(project, ".env")).Contains("# preserve custom options"), "Config comments preserved");
            Check(File.ReadAllText(Path.Combine(project, ".env")).Contains("GMV_IMPORT_ROOT='F:/中文 目录/$images'"), "Prevent Compose interpolation in paths");
            Check(original.Contains(config.Get("GMV_ADMIN_PASSWORD", "")), "Settings preserve passwords");
            var logged = new List<string>(); var engine = new LauncherEngine(project, logged.Add);
            engine.Log("password=" + config.Get("GMV_ADMIN_PASSWORD", "") + ", database=" + config.Get("POSTGRES_PASSWORD", ""));
            Check(logged[0].Contains("[已隐藏]") && !logged[0].Contains(config.Get("GMV_ADMIN_PASSWORD", "")), "Logs redact credentials");
            var arguments = new[] { "", "plain", "中文 空格 & file", "C:\\path with spaces\\", "embedded\"quote", "a\\\\\"b", "trailing\\\\" };
            string exe = Process.GetCurrentProcess().MainModule.FileName;
            var echoed = new List<string>();
            int exit = ChildProcess.Run(exe, "--echo " + String.Join(" ", arguments.Select(ChildProcess.Quote)), project,
                                        echoed.Add, CancellationToken.None, 10).GetAwaiter().GetResult();
            Check(exit == 0 && arguments.SequenceEqual(echoed.Select(s => Encoding.UTF8.GetString(Convert.FromBase64String(s)))), "Windows argument quoting round trip");
            bool timedOut = false;
            try { ChildProcess.Run(exe, "--wait", project, null, CancellationToken.None, 1).GetAwaiter().GetResult(); }
            catch (TimeoutException) { timedOut = true; }
            Check(timedOut, "Hung command times out");
            bool cancelled = false;
            using (var cancellation = new CancellationTokenSource())
            {
                cancellation.CancelAfter(200);
                try { ChildProcess.Run(exe, "--wait", project, null, cancellation.Token, 10).GetAwaiter().GetResult(); }
                catch (OperationCanceledException) { cancelled = true; }
            }
            Check(cancelled, "Cancel interrupts the CLI process");
            var help = new List<string>();
            int helpExit = ChildProcess.Run(Path.Combine(Path.GetDirectoryName(exe), "GenMediaVault.exe"), "--help", project,
                help.Add, CancellationToken.None, 10).GetAwaiter().GetResult();
            Check(helpExit == 2 && help.Any(line => line.Contains("GenMediaVault.exe")), "WinExe command mode supports redirected output without a console");
            Console.WriteLine("Launcher checks passed: " + checks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
        finally
        {
            string resolved = Path.GetFullPath(root);
            if (resolved.StartsWith(dist + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) && Directory.Exists(resolved)) Directory.Delete(resolved, true);
        }
    }
}
