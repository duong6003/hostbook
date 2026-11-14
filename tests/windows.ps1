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
function Assert([bool]$Ok, [string]$Message) { if (!$Ok) { throw $Message }; Write-Host "ok    $Message" }
& (Join-Path $source 'install.ps1') -InstallRoot $root -NoPath
Assert ($LASTEXITCODE -eq 0) 'portable runtime installation completes'
$launcher = Join-Path $root 'bin\hostbook.cmd'
$env:HOSTBOOK_WINDOWS_ROOT = $root
$env:HOSTBOOK_PLATFORM_HELPER = Join-Path $root 'app\windows\platform.ps1'
$bash = Join-Path $root 'runtime\bin\bash.exe'
$env:PATH = "$root\bin;$root\runtime\usr\bin;$root\runtime\mingw64\bin;$root\runtime\cmd;$env:PATH"
Assert ((& $launcher --version) -match '^hostbook ') 'cmd launcher supports version under spaced Unicode path'
# A fresh command lookup finds the CMD shim even when PowerShell script policy is Restricted.
Assert ((Get-Command hostbook).Source -eq $launcher) 'bare hostbook resolves the cmd launcher'
& $bash --noprofile --norc -c 'cd "$1" && bash tests/platform.sh' test $source
Assert ($LASTEXITCODE -eq 0) 'shared callback quoting and Windows capability checks'
# Probe launcher argument transport independently of application argument interpretation.
$entry = Join-Path $root 'app\hostbook'
$saved = [IO.File]::ReadAllBytes($entry)
$env:HB_ARG_FILE = Join-Path $testRoot 'arguments'
try {
    [IO.File]::WriteAllText($entry, '#!/usr/bin/env bash' + "`n" + 'printf "%s\0" "$@" > "$HB_ARG_FILE"' + "`n", (New-Object Text.UTF8Encoding($false)))
    $values = @('a space', "apostrophe's", 'unicode-' + [char]0x1ec7, '', 'quote"inside', 'trailing\')
    & (Join-Path $root 'bin\hostbook-launch.ps1') @values
    $actual = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:HB_ARG_FILE)).Split([char]0)
    Assert (($actual[0..($values.Count - 1)] -join '|') -ceq ($values -join '|')) 'launcher preserves spaces, Unicode, apostrophes, empty arguments, quotes and trailing slash'
} finally { [IO.File]::WriteAllBytes($entry, $saved) }
# Real native clipboard read/write, DPAPI encryption and NTFS permissions.
& $bash --noprofile --norc (Join-Path $source 'tests\windows.sh')
Assert ($LASTEXITCODE -eq 0) 'native Windows clipboard, callbacks, private temps and vault checks'
& (Join-Path $source 'tests\windows-fzf.ps1')
Assert ($LASTEXITCODE -eq 0) 'real native fzf preview and action callbacks'
& $launcher update
Assert ($LASTEXITCODE -eq 0) 'hostbook update uses the same Windows installation'
& (Join-Path $source 'tests\windows-ssh.ps1')
Assert ($LASTEXITCODE -eq 0) 'real Windows SSH key and password login'
& (Join-Path $source 'install.ps1') -InstallRoot $root -Uninstall -NoPath
Assert (!(Test-Path -LiteralPath $root)) 'uninstall removes installation'
Assert (Test-Path -LiteralPath $env:HOME) 'uninstall preserves user data'
Write-Host 'Windows checks passed; interactive screen/physical terminal and tmux tests are not covered here.'
