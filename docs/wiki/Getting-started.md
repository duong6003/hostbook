# Getting started

## Install

Supports macOS, Linux, and Windows x64. macOS/Linux: You need Git, curl, tar, and OpenSSH. The installer supplies
fzf, gum, and age when missing or too old; tmux is needed for multi-host sessions.

```bash
curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash
```

The app installs under `~/.local`; sudo is not required. Dependencies use Homebrew
when available on macOS, or user-local release binaries otherwise. Open a new terminal and
run `hostbook`. For Bash/Zsh, the installer provides `hb` when available, or
`hop` if `hb` is taken and `hop` is free. If both are taken, use `hostbook`.

### Windows

Run from PowerShell in Windows Terminal (Windows 10/11 x64):

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/duong6003/hostbook/main/install.ps1 -OutFile "$env:TEMP\hostbook-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\hostbook-install.ps1"
```

The installer downloads a private portable Git for Windows runtime, fzf, gum, and age
from official releases and verifies SHA-256 before extraction. You do not need to
install Git Bash separately. No elevation or machine-wide settings are required;
PowerShell's execution policy is bypassed only for that process. It adds
`%LOCALAPPDATA%\hostbook\bin` to your **user** PATH. Close every Windows Terminal
window and reopen it for the updated environment; then type `hostbook` in PowerShell.
The installer also prints a full-path command that works immediately.

The same classic terminal interface runs on all platforms. Windows supports individual
SSH sessions, Git sync to an existing remote, encrypted credentials, and sharing.
Multi-host tmux sessions and setting up this Windows machine as an SSH Git hub are
not supported; use Linux/macOS or WSL for those capabilities. Windows ARM is not
currently supported by this installer.

Windows `~` means your user profile. Application/runtime files are under
`%LOCALAPPDATA%\hostbook`; hosts, settings, state, and SSH config use the same
user-relative locations listed below. The app uses bundled Git OpenSSH. Native
Windows OpenSSH and VS Code may use a different agent/config interpretation;
see [Secrets and security](Secrets-and-security).

## First launch

Choose the path that matches your setup:

- **First machine** (`Đây là máy đầu tiên`): create a host list and optionally import existing `~/.ssh/config` entries.
- **Use a remote repository** (`Dùng kho ở máy khác`): clone your existing host list onto this machine.
- **Host a repository here** (`Tạo kho trên máy này`): create a Git hub on an always-on machine.

The first-run flow also lets you choose an appearance preset. You can change it later
with `hostbook settings`.

## Add a host

Press `Ctrl-A`, or run:

```bash
hostbook add ubuntu@10.0.1.13
```

Give the host an alias. Addresses may include a user or port, such as
`ubuntu@10.0.1.13:2222`. hostbook suggests project, user, tags, jump host, and credentials
from related entries and shows where suggestions came from.

## Where your data lives

| Path | Contents |
| --- | --- |
| `~/ssh-hosts/` | Host configuration, groups, and encrypted secrets; synchronized through Git. |
| `~/.config/hostbook/` | Local appearance settings and custom themes; not synchronized. |
| `~/.local/state/hostbook/` | Local history, index, and status. |
| `~/.local/share/hostbook/` | Installed application checkout. |
| `~/.ssh/config` | Includes the host files under `~/ssh-hosts/`. |

Appearance settings respect `XDG_CONFIG_HOME`. For contributor/test overrides, see
[Development](Development).

## Update or uninstall

Update with `hostbook update`, or rerun the installer.

On Windows, remove the app/runtime and its user PATH entry with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\hostbook\app\install.ps1" -Uninstall
```

Your host list, encrypted vault, settings, and SSH config remain. For a custom
`-InstallRoot`, pass that same path to uninstall. Update with `hostbook update` or
rerun the Windows installer.

On macOS/Linux, remove `~/.local/share/hostbook`, `~/.local/state/hostbook`, and the
`~/.local/bin/hostbook` symlink. Remove the hostbook alias from your shell startup file.
Keep `~/ssh-hosts` to retain your hosts; standard SSH can continue to use them.
Local appearance settings remain in `~/.config/hostbook` unless you remove them too.
