# End-to-end per-user install / native-boundary checks. Invoked on real Windows CI.
$ErrorActionPreference = 'Stop'
$source = Split-Path $PSScriptRoot -Parent
$testRoot = Join-Path $env:RUNNER_TEMP ("hostbook space ' " + [char]0x1ec7)
$root = Join-Path $testRoot 'install'
$env:HOME = Join-Path $testRoot 'home'
$env:HOSTBOOK_DIR = Join-Path $env:HOME 'ssh-hosts'
$env:HOSTBOOK_STATE = Join-Path $testRoot 'state'
$env:XDG_CONFIG_HOME = Join-Path $testRoot 'config'
$env:HOSTBOOK_SSH_CONFIG = Join-Path $testRoot 'ssh_config'
$env:GIT_AUTHOR_NAME = $env:GIT_COMMITTER_NAME = 'ci'
$env:GIT_AUTHOR_EMAIL = $env:GIT_COMMITTER_EMAIL = 'ci@example.com'
$env:HOSTBOOK_REPO = $source
New-Item -ItemType Directory -Force -Path $env:HOME | Out-Null
$unrelated = Join-Path $testRoot 'existing directory'
New-Item -ItemType Directory -Force -Path $unrelated | Out-Null
Set-Content -LiteralPath (Join-Path $unrelated 'sentinel') -Value 'unrelated data'
$rejected = $false
try { & (Join-Path $source 'install.ps1') -InstallRoot $unrelated } catch { $rejected = $true }
if (!$rejected -or !(Test-Path -LiteralPath (Join-Path $unrelated 'sentinel'))) { throw 'Installer must refuse a nonempty unrelated directory.' }
Write-Host 'ok    installer refuses an unrelated nonempty directory'
function Assert([bool]$Ok, [string]$Message) { if (!$Ok) { throw $Message }; Write-Host "ok    $Message" }
& (Join-Path $source 'install.ps1') -InstallRoot $root
Assert ($LASTEXITCODE -eq 0) 'portable runtime installation completes'
$launcher = Join-Path $root 'bin\hostbook.exe'
$env:HOSTBOOK_WINDOWS_ROOT = $root
$env:HOSTBOOK_PLATFORM_HELPER = Join-Path $root 'app\windows\platform.ps1'
$runtime = (Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json).runtime
$bash = Join-Path $runtime 'bin\bash.exe'
$env:PATH = "$root\bin;$runtime\usr\bin;$runtime\mingw64\bin;$runtime\cmd;$env:PATH"
Assert ((& $launcher --version) -match '^hostbook ') 'native launcher supports version under spaced Unicode path'
# A fresh command lookup finds the native executable even when PowerShell script policy is Restricted.
Assert ((Get-Command hostbook).Source -eq $launcher) 'bare hostbook resolves the native launcher'
& $bash --noprofile --norc -c 'cd "$1" && bash tests/platform.sh' test $source
Assert ($LASTEXITCODE -eq 0) 'shared callback quoting and Windows capability checks'
# Probe launcher argument transport independently of application argument interpretation.
$entry = Join-Path $root 'app\hostbook'
$saved = [IO.File]::ReadAllBytes($entry)
$env:HB_ARG_FILE = Join-Path $testRoot 'arguments'
try {
    [IO.File]::WriteAllText($entry, '#!/usr/bin/env bash' + "`n" + 'printf "%s\0" "$@" > "$(cygpath -u "$HB_ARG_FILE")"' + "`n", (New-Object Text.UTF8Encoding($false)))
    $values = @('a space', "apostrophe's", 'unicode-' + [char]0x1ec7, '', 'quote"inside', 'trailing\')
    $probe = New-Object Diagnostics.ProcessStartInfo
    $probe.FileName = $launcher
    $probe.UseShellExecute = $false
    $probe.Arguments = (($values | ForEach-Object { '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"' }) -join ' ')
    $process = [Diagnostics.Process]::Start($probe)
    $process.WaitForExit()
    Assert ($process.ExitCode -eq 0) 'native launcher argument probe exits successfully'
    $actual = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:HB_ARG_FILE)).Split([char]0)
    Assert (($actual[0..($values.Count - 1)] -join '|') -ceq ($values -join '|')) 'launcher preserves spaces, Unicode, apostrophes, empty arguments, quotes and trailing slash'
    & $launcher 'db|cache&literal'
    $query = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:HB_ARG_FILE)).TrimEnd([char]0)
    Assert ($query -eq 'db|cache&literal') 'PowerShell search operators are passed literally without a CMD hop'
} finally { [IO.File]::WriteAllBytes($entry, $saved) }
# Real native clipboard read/write, DPAPI encryption and NTFS permissions.
& $bash --noprofile --norc (Join-Path $source 'tests\windows.sh')
Assert ($LASTEXITCODE -eq 0) 'native Windows clipboard, callbacks, private temps and vault checks'
& $launcher update
Assert ($LASTEXITCODE -eq 0) 'hostbook update uses the same Windows installation'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User') -split ';'
Assert (@($userPath | Where-Object { $_ -eq (Join-Path $root 'bin') }).Count -eq 1) 'update keeps one user PATH entry'
& (Join-Path $source 'tests\windows-ssh.ps1')
Assert ($LASTEXITCODE -eq 0) 'real Windows SSH key and password login'
& (Join-Path $source 'tests\windows-fzf.ps1')
Assert ($LASTEXITCODE -eq 0) 'real native fzf preview and action callbacks'
Set-Content -LiteralPath (Join-Path $root 'sentinel') -Value 'unrelated data'
& (Join-Path $source 'install.ps1') -InstallRoot $root -Uninstall
Assert (!(Test-Path -LiteralPath (Join-Path $root 'app')) -and !(Test-Path -LiteralPath (Join-Path $root 'runtime'))) 'uninstall removes application and runtime'
Assert (Test-Path -LiteralPath (Join-Path $root 'sentinel')) 'uninstall preserves unrelated files in installation root'
Assert (([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') -notcontains (Join-Path $root 'bin')) 'uninstall removes user PATH entry'
Assert (Test-Path -LiteralPath $env:HOME) 'uninstall preserves user data'
Write-Host 'Windows checks passed; interactive screen/physical terminal and tmux tests are not covered here.'
