# Getting started

## Install

Supports macOS and Linux. You need Git, curl, tar, and OpenSSH. The installer supplies
fzf, gum, and age when missing or too old; tmux is needed for multi-host sessions.

```bash
curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash
```

The app installs under `~/.local`; sudo is not required. Dependencies use Homebrew
when available on macOS, or user-local release binaries otherwise. Open a new terminal and
run `hostbook`. For Bash/Zsh, the installer provides `hb` when available, or
`hop` if `hb` is taken and `hop` is free. If both are taken, use `hostbook`.

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

To uninstall, remove `~/.local/share/hostbook`, `~/.local/state/hostbook`, and the
`~/.local/bin/hostbook` symlink. Remove the hostbook alias from your shell startup file.
Keep `~/ssh-hosts` to retain your hosts; standard SSH can continue to use them.
Local appearance settings remain in `~/.config/hostbook` unless you remove them too.
