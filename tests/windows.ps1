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
if (!$env:HOSTBOOK_DOWNLOAD_CACHE) { $env:HOSTBOOK_DOWNLOAD_CACHE = Join-Path $testRoot 'downloads' }
$cache = $env:HOSTBOOK_DOWNLOAD_CACHE
$ageHash = 'f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7'
$ageArchive = Join-Path (Join-Path $cache $ageHash) 'age-v1.3.2-windows-amd64.zip'
New-Item -ItemType Directory -Force -Path (Split-Path $ageArchive) | Out-Null
Set-Content -LiteralPath $ageArchive -Value 'corrupt archive'
$installTime = [Diagnostics.Stopwatch]::StartNew()
& (Join-Path $source 'install.ps1') -InstallRoot $root
$installTime.Stop()
Write-Host "Install (restored or cold cache): $($installTime.Elapsed.TotalSeconds) seconds"
Assert ($LASTEXITCODE -eq 0) 'portable runtime installation completes'
Assert ((Get-FileHash -LiteralPath $ageArchive -Algorithm SHA256).Hash -eq $ageHash) 'corrupt cached archive is replaced with a verified download'
$ageWriteTime = (Get-Item -LiteralPath $ageArchive).LastWriteTimeUtc
$launcher = Join-Path $root 'bin\hostbook.exe'
$env:HOSTBOOK_WINDOWS_ROOT = $root
$env:HOSTBOOK_PLATFORM_HELPER = Join-Path $root 'app\windows\platform.ps1'
$runtime = (Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json).runtime
$bash = Join-Path $runtime 'bin\bash.exe'
$env:PATH = "$root\bin;$runtime\usr\bin;$runtime\mingw64\bin;$runtime\cmd;$env:PATH"
Assert ((& $launcher --version) -match '^hostbook ') 'native launcher supports version under spaced Unicode path'
foreach ($module in @('ui', 'import', 'settings', 'hosts')) {
    Assert ((Get-Item -LiteralPath (Join-Path $root "app\lib\$module.sh")).Length -gt 0) "installed $module module is present"
}
Push-Location $testRoot
try { Assert ((& $launcher _header | Out-String).Trim().Length -gt 0) 'installed callback works outside the app directory' }
finally { Pop-Location }
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
# These probes are independent once installation/launcher work. Preserve every failure and still clean up.
$script:windowsFailures = @()
function Run-Check([string]$Name, [scriptblock]$Check) {
    Write-Host "::group::$Name"
    try { & $Check }
    catch {
        $script:windowsFailures += "${Name}: $($_.Exception.Message)"
        Write-Host ($_ | Out-String)
    } finally { Write-Host '::endgroup::' }
}
Run-Check 'Native clipboard, vault and first-machine setup' {
    & $bash --noprofile --norc (Join-Path $source 'tests\windows.sh')
    Assert ($LASTEXITCODE -eq 0) 'native Windows clipboard, private temps and vault checks'
}
Run-Check 'Isolated vault onboarding' {
    # Windows runs the checks that touch the file system and vault creation; the choice/menu logic is platform-neutral and runs on Unix
    $env:ONBOARDING_ONLY = 'new_install partial_vault symlink_vault_rejected failed_vault_creation canceled_first_import real_first_import_with_key'
    & $bash --noprofile --norc (Join-Path $source 'tests\onboarding.sh')
    Remove-Item Env:ONBOARDING_ONLY
    Assert ($LASTEXITCODE -eq 0) 'vault onboarding and canceled imports preserve fixture data'
}
Run-Check 'Import transactions and key handling' {
    # import-input/import-review (parsing and review logic, platform-neutral) run on Unix only
    foreach ($suite in @('hosts-module', 'settings-module', 'sync', 'discovery', 'import', 'import-keys')) {
        & $bash --noprofile --norc (Join-Path $source "tests\$suite.sh")
        Assert ($LASTEXITCODE -eq 0) "$suite isolated regressions"
    }
}
Run-Check 'Update' {
    $updateTime = [Diagnostics.Stopwatch]::StartNew()
    & $launcher update
    $updateTime.Stop()
    Write-Host "Update (warm cache): $($updateTime.Elapsed.TotalSeconds) seconds"
    Assert ((Get-Item -LiteralPath $ageArchive).LastWriteTimeUtc -eq $ageWriteTime) 'update reuses the verified age archive'
    Assert ($LASTEXITCODE -eq 0) 'hostbook update uses the same Windows installation'
    foreach ($module in @('ui', 'import', 'settings', 'hosts')) {
        Assert ((Get-Item -LiteralPath (Join-Path $root "app\lib\$module.sh")).Length -gt 0) "updated $module module is present"
    }
    Push-Location $testRoot
    try { Assert ((& $launcher _header | Out-String).Trim().Length -gt 0) 'updated callback works outside the app directory' }
    finally { Pop-Location }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User') -split ';'
    Assert (@($userPath | Where-Object { $_ -eq (Join-Path $root 'bin') }).Count -eq 1) 'update keeps one user PATH entry'
}
Run-Check 'Real SSH key and password login' {
    & (Join-Path $source 'tests\windows-ssh.ps1')
    Assert ($LASTEXITCODE -eq 0) 'real Windows SSH key and password login'
}
Run-Check 'Native interactive fzf callbacks' {
    & (Join-Path $source 'tests\windows-fzf.ps1')
    Assert ($LASTEXITCODE -eq 0) 'real native fzf preview and action callbacks'
}
Run-Check 'Uninstall' {
    # Uninstall must handle the app's persistent private agent without stopping an external Windows agent.
    $secretDir = Join-Path $env:HOSTBOOK_DIR 'secrets'
    New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
    Set-Content -LiteralPath (Join-Path $secretDir 'agent-probe.meta') -Value 'type=password' -Encoding Ascii
    & $launcher _load agent-probe
    Assert ($LASTEXITCODE -eq 0) 'private agent starts before uninstall'
    $prefix = $root.TrimEnd('\') + '\runtime\'
    $ownedAgents = @(Get-Process -Name ssh-agent -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    Assert ($ownedAgents.Count -gt 0) 'private agent is still running when uninstall begins'
    try {
        Set-Content -LiteralPath (Join-Path $root 'sentinel') -Value 'unrelated data'
        & (Join-Path $source 'install.ps1') -InstallRoot $root -Uninstall
        Assert (!(Test-Path -LiteralPath (Join-Path $root 'app')) -and !(Test-Path -LiteralPath (Join-Path $root 'runtime'))) 'uninstall removes application and runtime with an active private agent'
        Assert (Test-Path -LiteralPath (Join-Path $root 'sentinel')) 'uninstall preserves unrelated files in installation root'
        Assert (([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') -notcontains (Join-Path $root 'bin')) 'uninstall removes user PATH entry'
        Assert (Test-Path -LiteralPath (Join-Path $secretDir 'agent-probe.meta')) 'uninstall preserves host data'
    } finally {
        foreach ($agent in $ownedAgents) { if (!$agent.HasExited) { Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue } }
        if (Test-Path -LiteralPath (Join-Path $root 'install.json')) {
            try { & (Join-Path $source 'install.ps1') -InstallRoot $root -Uninstall } catch { Write-Host "Fixture cleanup: $_" }
        }
    }
}
if ($script:windowsFailures.Count) { throw ($script:windowsFailures -join "`n") }
Write-Host 'Windows checks passed; physical terminal rendering and tmux tests are not covered here.'
