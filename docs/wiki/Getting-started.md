# Getting started

## Install

Hostbook supports macOS and Linux.

You need Git, curl, tar, and OpenSSH. Linux also needs `flock` from util-linux.
The installer supplies fzf, gum, and age when missing
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

Close other running Hostbook sessions before updating. A session started with the
old locking code can still try to recover a stale lock while the new version is running.

`hostbook update` runs `git pull --ff-only` in the installed clone. To also refresh
dependencies, rerun the one-line installer or run `bash ~/.local/share/hostbook/install.sh`.

To uninstall, remove `~/.local/share/hostbook`, `~/.local/state/hostbook`, and the
`~/.local/bin/hostbook` symlink. Remove the `hb`/`hop` alias from your shell startup file.
Keep `~/ssh-hosts` to retain your hosts; standard SSH can continue to use them.
Local appearance settings remain in `~/.config/hostbook` unless you remove them too.
