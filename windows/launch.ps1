# Installed as bin/hostbook.ps1, invoked by hostbook.cmd from any Windows shell.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\install.json') -Raw | ConvertFrom-Json
$root = Split-Path $PSScriptRoot -Parent
# ProcessStartInfo preserves empty arguments and embedded quotes on Windows PowerShell 5.1 too.
function Quote-Native([string]$Value) {
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = Join-Path $config.runtime 'bin\bash.exe'
$start.UseShellExecute = $false
$entry = (Join-Path $root 'app\hostbook').Replace('\', '/')
$start.Arguments = ((@('--noprofile', '--norc', $entry) + $args | ForEach-Object { Quote-Native $_ }) -join ' ')
$start.EnvironmentVariables['PATH'] = "$root\bin;$($config.runtime)\usr\bin;$($config.runtime)\mingw64\bin;$($config.runtime)\cmd;$env:PATH"
if (!$env:HOME) { $start.EnvironmentVariables['HOME'] = $env:USERPROFILE }
if (!$env:TERM -or $env:TERM -eq 'dumb') { $start.EnvironmentVariables['TERM'] = 'xterm-256color' }
$start.EnvironmentVariables['HOSTBOOK_WINDOWS_ROOT'] = $root
$process = [Diagnostics.Process]::Start($start)
$process.WaitForExit()
exit $process.ExitCode
