// Native entry point: avoid CMD interpreting fzf search operators such as | and &.
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

class HostbookLauncher {
    static string Quote(string value) {
        return "\"" + Regex.Replace(Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }
    static int Main(string[] args) {
        try {
            Console.InputEncoding = new UTF8Encoding(false);
            Console.OutputEncoding = new UTF8Encoding(false);
            string root = Directory.GetParent(Path.GetDirectoryName(typeof(HostbookLauncher).Assembly.Location)).FullName;
            string runtime = File.ReadAllText(Path.Combine(root, "runtime.path")).Trim();
            var start = new ProcessStartInfo(Path.Combine(runtime, "bin", "bash.exe"));
            start.UseShellExecute = false;
            start.Arguments = string.Join(" ", new[] { "--noprofile", "--norc", Path.Combine(root, "app", "hostbook").Replace('\\', '/') }.Concat(args).Select(Quote));
            start.EnvironmentVariables["PATH"] = Path.Combine(root, "bin") + ";" + Path.Combine(runtime, "usr", "bin") + ";" + Path.Combine(runtime, "mingw64", "bin") + ";" + Path.Combine(runtime, "cmd") + ";" + Environment.GetEnvironmentVariable("PATH");
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("HOME")))
                start.EnvironmentVariables["HOME"] = Environment.GetEnvironmentVariable("USERPROFILE");
            string term = Environment.GetEnvironmentVariable("TERM");
            if (string.IsNullOrEmpty(term) || term == "dumb") start.EnvironmentVariables["TERM"] = "xterm-256color";
            start.EnvironmentVariables["HOSTBOOK_WINDOWS_ROOT"] = root;
            using (var process = Process.Start(start)) {
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch (Exception error) {
            Console.Error.WriteLine("hostbook: " + error.Message);
            return 1;
        }
    }
}
