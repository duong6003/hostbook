# Exercise real native fzf callbacks; --filter never starts its interactive event loop.
$ErrorActionPreference = 'Stop'
if (!$env:HOSTBOOK_WINDOWS_ROOT) { throw 'Install hostbook and set HOSTBOOK_WINDOWS_ROOT first.' }
$root = $env:HOSTBOOK_WINDOWS_ROOT
$config = Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json
$fixture = Join-Path $env:RUNNER_TEMP ('hostbook fzf ' + [Guid]::NewGuid().ToString('N'))
$callback = Join-Path $fixture ("callback space ' " + [char]0x1ec7 + '.sh')
$driver = Join-Path $fixture 'driver.sh'
$utf8 = New-Object Text.UTF8Encoding($false)
$row = 'host space ' + [char]0x1ec7 + " apostrophe's"
New-Item -ItemType Directory -Path $fixture | Out-Null

# fzf opens CONIN$/CONOUT$ even when the CI process has redirected standard handles.
# https://github.com/junegunn/fzf/blob/v0.75.0/src/tui/light_windows.go
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class HostbookFzfConsole {
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool AllocConsole();
    [DllImport("kernel32.dll")] public static extern bool FreeConsole();
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
}
'@
$allocatedConsole = $false
$process = $null
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
[IO.File]::WriteAllText($callback, @'
#!/usr/bin/env bash
set -eu
out=$(cygpath -u "$HB_FZF_OUTPUT")
case $1 in
  start) printf '%s\n' "$BASH_VERSION" > "$out/start";;
  preview) printf '%s\n' "$2" > "$out/preview"; printf 'Preview: %s\n' "$2";;
  *) exit 2;;
esac
'@.Replace("`r`n", "`n") + "`n", $utf8)
[IO.File]::WriteAllText($driver, @'
#!/usr/bin/env bash
set -eu
app=$(cygpath -u "$HB_FZF_APP")
out=$(cygpath -u "$HB_FZF_OUTPUT")
callback=$(cygpath -u "$HB_FZF_CALLBACK")
export HOME="$out/home" HOSTBOOK_DIR="$out/hosts" HOSTBOOK_STATE="$out/state" XDG_CONFIG_HOME="$out/config"
mkdir -p "$HOME" "$HOSTBOOK_DIR"
sed '/^# ---------- entry point ----------/,$d' "$app" > "$out/lib"
. "$out/lib"
SELF=$app
cmd="bash $(printf '%q' "$callback")"
printf '%s\n' "$HB_FZF_ROW" | fzf --sync --no-mouse --layout=reverse \
  --listen "127.0.0.1:$HB_FZF_PORT" \
  --bind "start:execute-silent($cmd start)" \
  --preview "$cmd preview {}" --preview-window right,50%
'@.Replace("`r`n", "`n") + "`n", $utf8)

try {
    if ([HostbookFzfConsole]::GetConsoleWindow() -eq [IntPtr]::Zero) {
        if (![HostbookFzfConsole]::AllocConsole()) { throw 'Cannot allocate a console for the native fzf test.' }
        $allocatedConsole = $true
        [HostbookFzfConsole]::ShowWindow([HostbookFzfConsole]::GetConsoleWindow(), 0) | Out-Null
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $config.runtime 'bin\bash.exe'
    $start.Arguments = '--noprofile --norc "' + $driver.Replace('\', '/') + '"'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    $start.EnvironmentVariables['PATH'] = "$root\bin;$($config.runtime)\usr\bin;$($config.runtime)\mingw64\bin;$($config.runtime)\cmd;$env:PATH"
    $start.EnvironmentVariables['TERM'] = 'xterm-256color'
    $start.EnvironmentVariables['HB_FZF_APP'] = Join-Path $root 'app\hostbook'
    $start.EnvironmentVariables['HB_FZF_CALLBACK'] = $callback
    $start.EnvironmentVariables['HB_FZF_OUTPUT'] = $fixture
    $start.EnvironmentVariables['HB_FZF_ROW'] = $row
    $start.EnvironmentVariables['HB_FZF_PORT'] = [string]$port
    $start.EnvironmentVariables['FZF_DEFAULT_OPTS'] = ''
    $start.EnvironmentVariables['FZF_DEFAULT_OPTS_FILE'] = ''
    $start.EnvironmentVariables['FZF_API_KEY'] = [Guid]::NewGuid().ToString('N')
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (!(Test-Path -LiteralPath (Join-Path $fixture 'start')) -or !(Test-Path -LiteralPath (Join-Path $fixture 'preview'))) {
        if ($process.HasExited) { throw ('fzf exited before executing callbacks: ' + $stderr.GetAwaiter().GetResult()) }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'fzf did not execute both start and preview callbacks within 30 seconds.' }
        Start-Sleep -Milliseconds 100
    }
    $preview = [IO.File]::ReadAllText((Join-Path $fixture 'preview'), $utf8).TrimEnd([char[]]"`r`n")
    if ($preview -cne $row) { throw "Preview callback corrupted the selected row: $preview" }
    $bashVersion = [IO.File]::ReadAllText((Join-Path $fixture 'start'), $utf8).Trim()
    if ($bashVersion -notmatch '^\d+\.\d+') { throw 'Start callback did not execute in Bash.' }
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port" -Method Post -Body 'accept' -Headers @{'x-api-key'=$start.EnvironmentVariables['FZF_API_KEY']} -TimeoutSec 5 | Out-Null
    if (!$process.WaitForExit(10000)) { throw 'fzf did not exit within 10 seconds after accept.' }
    $selection = $stdout.GetAwaiter().GetResult().TrimEnd([char[]]"`r`n")
    if ($process.ExitCode -ne 0 -or $selection -cne $row) { throw ('fzf selection failed: ' + $stderr.GetAwaiter().GetResult()) }
} finally {
    if ($process -and !$process.HasExited) { & taskkill.exe /PID $process.Id /T /F | Out-Null }
    if ($allocatedConsole) { [HostbookFzfConsole]::FreeConsole() | Out-Null }
}
Write-Host 'ok    native fzf start and preview callbacks use Bash with spaced Unicode/apostrophe paths'
