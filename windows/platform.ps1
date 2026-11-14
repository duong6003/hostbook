# Native Windows boundaries. No secret is passed on the command line.
param([Parameter(Mandatory=$true)][string]$Action, [string]$Path, [string]$Executable)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
switch ($Action) {
    'private-dir' {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetOwner($sid)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        if (Test-Path -LiteralPath $Path) {
            if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Private directory must not be a link.' }
            Set-Acl -LiteralPath $Path -AclObject $acl
        } else {
            [IO.Directory]::CreateDirectory($Path, $acl) | Out-Null
        }
        $actual = Get-Acl -LiteralPath $Path
        if (!$actual.AreAccessRulesProtected) { throw 'Private directory ACL inheritance is still enabled.' }
        foreach ($entry in $actual.Access) {
            if ($entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value -or $entry.AccessControlType -ne 'Allow') { throw 'Unexpected private directory ACL.' }
        }
    }
    'agent-start' {
        $start = New-Object Diagnostics.ProcessStartInfo
        if (!(Test-Path -LiteralPath $Executable)) { throw "Bash runtime not found: $Executable" }
        $start.FileName = $Executable
        $start.WorkingDirectory = Split-Path $Executable -Parent
        $start.Arguments = '--noprofile --norc "' + $Path.Replace('\', '/') + '"'
        $start.UseShellExecute = $true
        $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
        $process = [Diagnostics.Process]::Start($start)
        if (!$process.WaitForExit(10000)) { $process.Kill(); throw 'SSH agent startup timed out.' }
        if ($process.ExitCode -ne 0) { throw 'SSH agent startup failed.' }
    }
    'cache-set' {
        Add-Type -AssemblyName System.Security
        $plain = $utf8.GetBytes([Console]::In.ReadToEnd())
        try { [IO.File]::WriteAllBytes($Path, [Security.Cryptography.ProtectedData]::Protect($plain, $null, 'CurrentUser')) }
        finally { [Array]::Clear($plain, 0, $plain.Length) }
    }
    'cache-get' {
        Add-Type -AssemblyName System.Security
        $plain = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($Path), $null, 'CurrentUser')
        try { [Console]::Write($utf8.GetString($plain)) }
        finally { [Array]::Clear($plain, 0, $plain.Length) }
    }
    'clipboard-get' { [Console]::Write((Get-Clipboard -Raw)) }
    'clipboard-set' { Set-Clipboard -Value ([Console]::In.ReadToEnd()) }
    default { throw "Unknown platform action: $Action" }
}
