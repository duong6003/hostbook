# Getting started

## Install

Supports macOS, Linux, and Windows x64.

### macOS / Linux

You need Git, curl, tar, and OpenSSH. The installer supplies fzf, gum, and age when missing
or too old; tmux is needed for multi-host sessions.

```bash
curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash
```

The installer clones the app into `~/.local/share/hostbook`. To install from your own clone
instead (for example to contribute), clone first and run the installer from it:

```bash
git clone https://github.com/duong6003/hostbook.git ~/.local/share/hostbook
bash ~/.local/share/hostbook/install.sh
```

When `~/.local/share/hostbook` is already a Git clone, the installer fast-forwards it
(`git pull --ff-only`) instead of cloning again, installs missing dependencies, and runs
`hostbook setup`. To keep the clone elsewhere, run it with `HOSTBOOK_HOME` pointing at
that clone: `HOSTBOOK_HOME=/path/to/hostbook bash /path/to/hostbook/install.sh`.

The app installs under `~/.local`; sudo is not required. Dependencies use Homebrew
when available on macOS, or user-local release binaries otherwise. Open a new terminal and
run `hostbook`. For Bash/Zsh, setup adds the alias `hb`, or `hop` if `hb` is taken and
`hop` is free. If both are taken, use `hostbook`.

### Windows

Run from PowerShell in Windows Terminal (Windows 10/11 x64):

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/duong6003/hostbook/main/install.ps1 -OutFile "$env:TEMP\hostbook-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\hostbook-install.ps1"
```

The installer clones the app from `https://github.com/duong6003/hostbook.git` with its own
bundled Git. `-Repository` (or the `HOSTBOOK_REPO` environment variable) accepts another Git URL
or local path, for example your own fork.

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

Run `hostbook`. When `~/ssh-hosts` does not exist, setup asks one question at a time.
Use the arrow keys and Enter; Escape cancels.

1. **Language** (`Language / Ngôn ngữ`): choose English or Vietnamese. The default follows your
   locale (`vi*` selects Vietnamese, anything else English). You can change it later in
   **Tools → Appearance → Language**; `HOSTBOOK_LANG=en` or `vi` overrides it for one run.
2. **Role.** Choose **Connect to and manage servers** (`Kết nối và quản lý server`) for a personal
   machine, or **Host a shared sync repository** for a machine other devices will sync with.
3. **Data** (personal machines only). Choose **Create a new server list** or **Get data from a
   sync repository**. For the latter, pick a machine found on your LAN/Tailscale, **Enter an
   address…**, or **Use a URL…** (for example `git@github.com:you/ssh-hosts-private.git`).
4. **Secure vault**, two steps. Type a vault protection password (at least ten characters, twice),
   then save the **recovery code**, which is shown once. This password only encrypts saved SSH keys
   and passwords; it is not a server login password. If the data you fetched already contains a
   vault, this step is skipped and the password is asked when needed.
5. **Existing SSH config.** With a new list and Host entries in `~/.ssh/config`, choose
   **Review and import** or **Later**. The vault exists before this step. A declined or cancelled
   import leaves your SSH config untouched; an accepted import saves a backup
   (`~/.ssh/config.bak.XXXXXX`) without overwriting an earlier one. `Host *` and `Match`
   blocks stay in `~/.ssh/config`. Includes, executable Match rules, inherited identity settings,
   and some ordering that cannot be preserved are rejected; adjust the source and retry.
6. **Keys on this machine.** If servers use key files outside the vault, you are offered to move
   them into it (the original files stay).
7. **Sync repository.** A personal machine with a new list is asked whether to connect one now;
   **Later** is fine. A repository host sets up the store on this machine (SSH must be on).
8. **Appearance.** Pick a preset with a live preview, or press Escape to choose later
   (**Tools → Appearance**). Enter then opens the server list.

The vault is required before entering the app; a sync repository is optional.

## Choose login credentials

The login form shows saved vault credentials immediately. Choose one, or choose **Add to vault…** to import a private key file, paste a private key, generate an Ed25519 key, or save a server password. After import, Hostbook selects the saved credential for the server. Imported files remain unchanged; managed connections use the encrypted vault copy rather than depending on the original file path. Canceling the source picker returns to the vault list.

**No saved login** is available for independently managed SSH agents and hardware keys. Import flows that require a vault key omit this option and passwords.

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
| `~/.config/hostbook/` | Local settings (`config`, including `lang`) and custom themes; not synchronized. |
| `~/.local/state/hostbook/` | Local history, index, and status. |
| `~/.local/share/hostbook/` | Installed application checkout. |
| `~/.ssh/config` | Includes the host files under `~/ssh-hosts/`. |

Settings respect `XDG_CONFIG_HOME`. For contributor/test overrides, see
[Development](Development).

## Update or uninstall

On macOS/Linux, `hostbook update` runs `git pull --ff-only` in the installed clone; a failed pull
changes nothing. To also refresh dependencies, rerun the one-line installer (or
`bash ~/.local/share/hostbook/install.sh`).
On Windows, `hostbook update` pulls the app clone and reruns `install.ps1`. If the clone has no
`.git` directory, update does nothing; rerun the installer.

On Windows, close hostbook, then remove the app/runtime and its user PATH entry with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\hostbook\app\install.ps1" -Uninstall
```

Uninstall also stops this installation's private SSH agent. Your host list, encrypted
vault, settings, and SSH config remain. For a custom
`-InstallRoot`, pass that same path to uninstall.

On macOS/Linux, remove `~/.local/share/hostbook`, `~/.local/state/hostbook`, and the
`~/.local/bin/hostbook` symlink. Remove the `hb`/`hop` alias from your shell startup file.
Keep `~/ssh-hosts` to retain your hosts; standard SSH can continue to use them.
Local appearance settings remain in `~/.config/hostbook` unless you remove them too.
