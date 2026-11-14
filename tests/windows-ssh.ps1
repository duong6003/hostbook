# Real SSH client/vault smoke test on a disposable Windows CI runner.
# Only this server fixture needs elevation; installing/running hostbook does not.
$ErrorActionPreference = 'Stop'
$root = $env:HOSTBOOK_WINDOWS_ROOT
if (!$root) { throw 'Set HOSTBOOK_WINDOWS_ROOT to the installation under test.' }
$launcher = Join-Path $root 'bin\hostbook.exe'
$runtime = (Get-Content -LiteralPath (Join-Path $root 'install.json') -Raw | ConvertFrom-Json).runtime
$bash = Join-Path $runtime 'bin\bash.exe'
$git = Join-Path $runtime 'cmd\git.exe'
$sshd = Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
if (!(Test-Path -LiteralPath $sshd)) { throw 'The Windows SSH fixture requires the in-box OpenSSH server (Windows Server 2025 runner).' }
$admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'The disposable SSH server fixture requires an elevated CI runner.' }

function Quote-Native([string]$Value) {
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
function Invoke-Hostbook([string[]]$Arguments, [switch]$AllowFailure) {
    Write-Host "probe hostbook $($Arguments[0])"
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $launcher
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $start.Arguments = (($Arguments | ForEach-Object { Quote-Native $_ }) -join ' ')
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit(60000)) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        throw "hostbook $($Arguments[0]) timed out."
    }
    if (!$stdout.Wait(5000) -or !$stderr.Wait(5000)) { throw "hostbook $($Arguments[0]) exited, but a descendant kept captured output handles open." }
    $result = [pscustomobject]@{ ExitCode = $process.ExitCode; Out = $stdout.Result.TrimEnd("`r", "`n"); Error = $stderr.Result }
    if ($result.ExitCode -ne 0 -and !$AllowFailure) { throw "hostbook $($Arguments[0]) failed ($($result.ExitCode)): $($result.Error)" }
    return $result
}
function Write-Utf8([string]$Path, [string]$Value) { [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($false))) }
function Posix-Path([string]$Path) {
    $value = & (Join-Path $runtime 'usr\bin\cygpath.exe') -u -- $Path
    if ($LASTEXITCODE -ne 0) { throw 'Cannot convert fixture path.' }
    return [string]$value
}
function Protect-ServerFile([string]$Path) {
    $acl = New-Object Security.AccessControl.FileSecurity
    $administrators = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl.SetOwner($administrators)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($administrators, 'FullControl', 'Allow')))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system, 'FullControl', 'Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}

$id = [Guid]::NewGuid().ToString('N')
# Construct Unicode without relying on Windows PowerShell 5.1's source-file encoding.
$fixture = Join-Path ([IO.Path]::GetTempPath()) ("hostbook SSH th" + [char]0x1eed + " O'Connor " + $id)
$user = 'hbt' + $id.Substring(0, 12)
$service = 'HostbookSshTest' + $id.Substring(0, 8)
$password = 'Hostbook-CI-9!' + $id
$variables = @('HOME', 'HOSTBOOK_DIR', 'HOSTBOOK_STATE', 'HOSTBOOK_SSH_CONFIG', 'XDG_CONFIG_HOME', 'HOSTBOOK_MASTER', 'HOSTBOOK_KEYCHAIN', 'HOSTBOOK_SESSION_CACHE', 'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'HB_ASKPASS_NOTTY', 'HB_ASKPASS_ANSWER', 'HB_ASKPASS_LOG', 'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'HB_SSH_TEST_ROOT', 'PATH')
$previous = @{}
foreach ($name in $variables) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$userCreated = $false
$serviceCreated = $false
try {
    New-Item -ItemType Directory -Path $fixture | Out-Null
    $fixtureHome = Join-Path $fixture 'home'
    $hosts = Join-Path $fixture 'hosts'
    New-Item -ItemType Directory -Path $hosts -Force | Out-Null
    $env:HOME = Posix-Path $fixtureHome
    $env:HOSTBOOK_DIR = Posix-Path $hosts
    $env:HOSTBOOK_STATE = Posix-Path (Join-Path $fixture 'state')
    $env:HOSTBOOK_SSH_CONFIG = Posix-Path (Join-Path $fixture 'ssh_config')
    $env:XDG_CONFIG_HOME = Posix-Path (Join-Path $fixture 'config')
    $env:HOSTBOOK_MASTER = 'hostbook-ci-master-password'
    $env:HOSTBOOK_KEYCHAIN = $null
    $env:HOSTBOOK_SESSION_CACHE = $null
    $env:SSH_AUTH_SOCK = $null
    $env:SSH_AGENT_PID = $null
    $env:HB_ASKPASS_NOTTY = '1'
    $env:HB_ASKPASS_ANSWER = $null
    $env:HB_ASKPASS_LOG = Posix-Path (Join-Path $fixture 'askpass.log')
    $env:GIT_AUTHOR_NAME = $env:GIT_COMMITTER_NAME = 'hostbook CI'
    $env:GIT_AUTHOR_EMAIL = $env:GIT_COMMITTER_EMAIL = 'hostbook@example.invalid'
    $env:HB_SSH_TEST_ROOT = $fixture
    $env:PATH = "$root\bin;$runtime\usr\bin;$runtime\mingw64\bin;$runtime\cmd;$env:PATH"

    $prepare = Join-Path $fixture 'prepare.sh'
    Write-Utf8 $prepare @'
set -eu
t=$(cygpath -u "$HB_SSH_TEST_ROOT")
ssh-keygen -q -t ed25519 -N '' -f "$t/host_key"
ssh-keygen -q -t ed25519 -N '' -f "$t/client_key"
'@
    & $bash --noprofile --norc $prepare.Replace('\', '/')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot generate fixture SSH keys.' }
    Protect-ServerFile (Join-Path $fixture 'host_key')
    New-LocalUser -Name $user -Password (ConvertTo-SecureString $password -AsPlainText -Force) -AccountNeverExpires | Out-Null
    $userCreated = $true
    Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545').Name -Member $user
    # sshd authenticates a real non-admin Windows account. Its public auth file is fixture-only.
    $sid = (Get-LocalUser -Name $user).SID
    $acl = Get-Acl -LiteralPath $fixture
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    Set-Acl -LiteralPath $fixture -AclObject $acl

    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    $serverConfig = Join-Path $fixture 'sshd_config'
    $serverPath = $fixture.Replace('\', '/')
    Write-Utf8 $serverConfig @"
Port $port
ListenAddress 127.0.0.1
PidFile "$serverPath/sshd.pid"
HostKey "$serverPath/host_key"
AuthorizedKeysFile "$serverPath/client_key.pub"
AllowUsers $user
PasswordAuthentication yes
PubkeyAuthentication yes
StrictModes no
LogLevel VERBOSE
ForceCommand echo HOSTBOOK_WINDOWS_SSH_OK
"@
    Protect-ServerFile $serverConfig
    & $sshd -t -f $serverConfig
    if ($LASTEXITCODE -ne 0) { throw 'Invalid fixture sshd configuration.' }
    $serverLog = Join-Path $fixture 'sshd.log'
    $binary = '"' + $sshd + '" -f "' + $serverConfig + '" -E "' + $serverLog + '"'
    New-Service -Name $service -BinaryPathName $binary -StartupType Manual | Out-Null
    $serviceCreated = $true
    Write-Host 'Starting isolated Windows SSH server'
    Start-Service -Name $service
    Write-Host 'Windows SSH service started'
    $listening = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $connect = $client.ConnectAsync('127.0.0.1', $port)
            if ($connect.Wait(250) -and $client.Connected) { $listening = $true; break }
        } catch { Start-Sleep -Milliseconds 250 } finally { $client.Dispose() }
    }
    if (!$listening) { throw 'The fixture SSH server did not start.' }

    Write-Utf8 (Join-Path $fixture 'ssh_config') @"
Include "$($env:HOSTBOOK_DIR)/*.conf"
Host *
  UserKnownHostsFile "$(Posix-Path (Join-Path $fixture 'known_hosts'))"
  StrictHostKeyChecking accept-new
  LogLevel ERROR
"@
    & $git -C $hosts init -q
    if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize isolated host repository.' }
    Invoke-Hostbook @('_vault_init', $env:HOSTBOOK_MASTER) | Out-Null
    Invoke-Hostbook @('_secret_add_key', (Posix-Path (Join-Path $fixture 'client_key')), 'windows-key') | Out-Null
    Invoke-Hostbook @('_secret_add_password', 'windows-password', $password) | Out-Null
    Invoke-Hostbook @('_secret_add_password', 'windows-wrong', 'intentionally-wrong') | Out-Null
    $blocks = @()
    foreach ($entry in @(@('e-key', 'windows-key'), @('e-pass', 'windows-password'), @('e-wrong', 'windows-wrong'))) {
        $blocks += (Invoke-Hostbook @('_block', $entry[0], '127.0.0.1', $user, [string]$port, '', $entry[1], '', '')).Out
    }
    Write-Utf8 (Join-Path $hosts 'windows.conf') (($blocks -join "`n`n") + "`n")
    & $git -C $hosts add -A
    & $git -C $hosts commit -qm fixture
    if ($LASTEXITCODE -ne 0) { throw 'Cannot commit isolated SSH fixture.' }
    Invoke-Hostbook @('_index') | Out-Null

    foreach ($hostName in @('e-key', 'e-pass')) {
        $result = Invoke-Hostbook @('_run', $hostName, 'echo', 'ignored-by-fixture-forcecommand')
        if ($result.Out -ne 'HOSTBOOK_WINDOWS_SSH_OK') { throw "Unexpected SSH result for $hostName`: $($result.Out) $($result.Error)" }
        Write-Host "ok    real SSH login using $hostName via the native launcher from PowerShell"
    }
    $agentProbe = Join-Path $fixture 'agent-probe.sh'
    Write-Utf8 $agentProbe '. "$HOSTBOOK_STATE/agent.env"; ssh-add -l >/dev/null 2>&1'
    & $bash --noprofile --norc $agentProbe.Replace('\', '/')
    if ($LASTEXITCODE -ne 0) { throw 'The managed SSH agent did not retain its key across invocations.' }
    Invoke-Hostbook @('lock') | Out-Null
    $agentProbe = Join-Path $fixture 'agent-probe.sh'
    Write-Utf8 $agentProbe '. "$HOSTBOOK_STATE/agent.env"; ssh-add -l >/dev/null 2>&1; [ $? -eq 1 ]'
    & $bash --noprofile --norc $agentProbe.Replace('\', '/')
    if ($LASTEXITCODE -ne 0) { throw 'hostbook lock did not remove keys from its fallback agent.' }
    Write-Host 'ok    lock finds the private agent across separate PowerShell invocations'
    if (!(Test-Path -LiteralPath (Join-Path $fixture 'askpass.log')) -or
        !(Select-String -LiteralPath (Join-Path $fixture 'askpass.log') -Pattern 'password' -Quiet)) { throw 'Password SSH did not exercise hostbook askpass.' }
    Write-Host 'ok    native Git SSH invokes the hostbook askpass script'

    $wrong = Invoke-Hostbook @('_run', 'e-wrong', 'echo', 'must-fail') -AllowFailure
    if ($wrong.ExitCode -eq 0) { throw 'An incorrect stored SSH password was accepted.' }
    Write-Host 'ok    incorrect password fails without hanging'
    $checks = (Invoke-Hostbook @('_check', 'e-key', 'e-pass', 'e-wrong')).Out
    foreach ($name in @('e-key', 'e-pass')) {
        if ($checks -notmatch ("(?m)^" + $name + "`tok`t")) { throw "Connection check did not pass for $name`: $checks" }
    }
    if ($checks -notmatch "(?m)^e-wrong`tfail`t") { throw "Connection check did not reject the wrong password: $checks" }
    Write-Host 'ok    parallel connection checks use key and password secrets'
    foreach ($path in @($hosts, (Join-Path $fixture 'state'))) {
        $residuals = @(Get-ChildItem -LiteralPath $path -File -Recurse -Force | Select-String -SimpleMatch 'BEGIN OPENSSH PRIVATE KEY')
        if ($residuals.Count) { throw ('Plaintext private key found at: ' + (($residuals | Select-Object -ExpandProperty Path -Unique) -join ', ')) }
    }
    Write-Host 'ok    no plaintext private keys remain in hostbook data/state'
} catch {
    $failure = $_
    Write-Host ("SSH probe failure: " + $failure.Exception.Message)
    if ($serviceCreated) { Stop-Service -Name $service -ErrorAction SilentlyContinue }
    $log = Join-Path $fixture 'sshd.log'
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 60 -ErrorAction SilentlyContinue | Write-Host }
    throw $failure
} finally {
    if ($serviceCreated) {
        Stop-Service -Name $service -ErrorAction SilentlyContinue
        & sc.exe delete $service | Out-Null
    }
    if ($userCreated) { Remove-LocalUser -Name $user -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath (Join-Path $fixture 'state\agent.env')) {
        $cleanup = Join-Path $fixture 'cleanup.sh'
        Write-Utf8 $cleanup '. "$HOSTBOOK_STATE/agent.env"; ssh-agent -k >/dev/null 2>&1'
        & $bash --noprofile --norc $cleanup.Replace('\', '/')
    }
    Remove-Item -LiteralPath $fixture -Force -Recurse -ErrorAction SilentlyContinue
    foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
