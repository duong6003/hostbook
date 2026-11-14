# Per-user Windows x64 installer. Run again to update; no elevation or machine settings.
[CmdletBinding()]
param(
    [string]$InstallRoot = $(if ($env:HOSTBOOK_WINDOWS_ROOT) { $env:HOSTBOOK_WINDOWS_ROOT } else { Join-Path $env:LOCALAPPDATA 'hostbook' }),
    [string]$Repository = $(if ($env:HOSTBOOK_REPO) { $env:HOSTBOOK_REPO } else { 'https://github.com/duong6003/hostbook.git' }),
    [switch]$Uninstall,
    [switch]$NoPath
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64') { throw 'hostbook currently supports Windows x64. Use the Linux installer inside WSL on other architectures.' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$bin = Join-Path $InstallRoot 'bin'
$app = Join-Path $InstallRoot 'app'
$configFile = Join-Path $InstallRoot 'install.json'
function Set-UserPath([bool]$Add) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($current -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $bin.TrimEnd('\') })
    if ($Add) { $parts += $bin }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}
if ($Uninstall) {
    if (!(Test-Path -LiteralPath $configFile)) { throw "No hostbook installation at $InstallRoot" }
    if (!$NoPath) { Set-UserPath $false }
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Host 'Removed hostbook and its managed runtime/dependencies. Your SSH hosts, vault and settings remain.'
    return
}
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$temp = Join-Path ([IO.Path]::GetTempPath()) ('hostbook-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
function Download([string]$Url, [string]$Hash) {
    $file = Join-Path $temp ([IO.Path]::GetFileName($Url))
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $file
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ine $Hash) { throw "SHA256 mismatch: $Url" }
    return $file
}
function Release-Zip([string]$Repo, [string]$Version, [string]$Name, [string]$Sums, [string]$Hash = '') {
    $base = "https://github.com/$Repo/releases/download/v$Version"
    if (!$Hash) {
        $content = (Invoke-WebRequest -UseBasicParsing -Uri "$base/$Sums").Content
        $line = @($content -split "`n" | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($Name) + '\s*$') })
        if ($line.Count -ne 1) { throw "Missing or ambiguous checksum for $Name" }
        $Hash = ($line[0] -split '\s+')[0]
    }
    $zip = Download "$base/$Name" $Hash
    $dest = Join-Path $temp ($Repo.Split('/')[-1])
    Expand-Archive -LiteralPath $zip -DestinationPath $dest
    return $dest
}
try {
    $runtime = Join-Path $InstallRoot 'runtime'
    if (!(Test-Path -LiteralPath (Join-Path $runtime 'bin\bash.exe'))) {
        Write-Host 'Setting up a private Git for Windows runtime (Bash, Git, OpenSSH and Unix tools)...'
        $archive = Download 'https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/PortableGit-2.56.1-64-bit.7z.exe' '9dd04a608ceb5f6ab32a73709e0ecb159d211da71b697c45ff81f6dd0c6b49c7'
        $runtime = Join-Path $InstallRoot 'runtime'
        # The official SFX requires doubled backslashes and runs post-install.bat itself.
        $process = Start-Process -FilePath $archive -ArgumentList @('-y', '-gm2', ('-InstallPath="' + $runtime.Replace('\', '\\') + '"')) -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Git runtime extraction failed ($($process.ExitCode))." }
    }
    $bash = Join-Path $runtime 'bin\bash.exe'
    $env:PATH = "$bin;$runtime\usr\bin;$runtime\mingw64\bin;$runtime\cmd;$env:PATH"
    & $bash --noprofile --norc -c 'for t in git awk ssh ssh-agent ssh-add ssh-keygen curl tar cygpath mktemp; do command -v "$t" >/dev/null || exit 1; done'
    if ($LASTEXITCODE -ne 0) { throw 'The selected Git for Windows runtime is incomplete.' }
    $fzf = Release-Zip 'junegunn/fzf' '0.75.0' 'fzf-0.75.0-windows_amd64.zip' 'fzf_0.75.0_checksums.txt'
    $gum = Release-Zip 'charmbracelet/gum' '2.0.2' 'gum_2.0.2_Windows_x86_64.zip' 'checksums.txt'
    $age = Release-Zip 'FiloSottile/age' '1.3.2' 'age-v1.3.2-windows-amd64.zip' '' 'f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7'
    foreach ($tool in @('fzf', 'gum', 'age', 'age-keygen', 'age-plugin-batchpass')) {
        $found = @(Get-ChildItem -LiteralPath $temp -Filter "$tool.exe" -Recurse)
        if ($found.Count -ne 1) { throw "Expected exactly one $tool.exe in verified archives." }
        Copy-Item -LiteralPath $found[0].FullName -Destination $bin -Force
    }
    $git = Join-Path $runtime 'cmd\git.exe'
    if (Test-Path -LiteralPath (Join-Path $app '.git')) {
        & $git -C $app -c core.autocrlf=false pull --ff-only
    } elseif (Test-Path -LiteralPath $app) {
        throw "Refusing to replace a non-Git directory: $app"
    } else {
        & $git -c core.autocrlf=false clone --depth 1 -- $Repository $app
    }
    if ($LASTEXITCODE -ne 0) { throw 'Cannot download/update hostbook. Your existing host data has not been changed.' }
    Copy-Item -LiteralPath (Join-Path $app 'windows\launch.ps1') -Destination (Join-Path $bin 'hostbook-launch.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $app 'windows\hostbook.cmd') -Destination $bin -Force
    @{ runtime = $runtime } | ConvertTo-Json | Set-Content -LiteralPath $configFile -Encoding UTF8
    & (Join-Path $bin 'hostbook-launch.ps1') setup
    if ($LASTEXITCODE -ne 0) { throw 'hostbook setup failed.' }
    if (!$NoPath) { Set-UserPath $true }
    Write-Host "Installed hostbook at $InstallRoot"
    Write-Host 'Close and reopen Windows Terminal (all windows), then type hostbook in PowerShell.'
    Write-Host ('Use now: & "' + (Join-Path $bin 'hostbook.cmd') + '"')
    Write-Host 'Windows supports single-host sessions; tmux multi-host sessions require macOS, Linux or WSL.'
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
