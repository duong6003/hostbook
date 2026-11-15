# hostbook

**Your SSH hosts, one search away.**

A terminal SSH host manager for macOS, Linux, and Windows x64. Find a host, open a group in tmux,
and keep your SSH configuration in sync through a Git repository you control.

![hostbook showing searchable SSH hosts and connection details](docs/assets/hostbook.png)

## Built for everyday SSH

- **Find and connect.** Search names, tags, users, addresses, and descriptions.
- **Work across servers.** Save groups and open tmux splits, tabs, or synchronized sessions on macOS/Linux.
- **Keep machines in sync.** Store standard SSH config in Git, with per-host conflict resolution.
- **Bring your credentials.** Manage reusable SSH keys and passwords in an age-encrypted vault.
- **Share a workspace.** Export selected hosts, with or without their credentials, as a password-encrypted package.
- **Make it yours.** Choose compact lists or two-line cards, color themes, English or Vietnamese, and your terminal's background.

No hostbook account or hosted service required. Your hosts remain standard SSH
configuration, usable with `ssh`, `scp`, and VS Code Remote-SSH.

## Get started

The repository is private, so install from a clone you have access to.

macOS / Linux (needs Git, curl, tar, and OpenSSH):

```bash
gh repo clone duong6003/hostbook ~/.local/share/hostbook
# or: git clone git@github.com:duong6003/hostbook.git ~/.local/share/hostbook
bash ~/.local/share/hostbook/install.sh
```

Windows (PowerShell, with Git or `gh` available for the first clone):

```powershell
gh repo clone duong6003/hostbook "$env:TEMP\hostbook-src"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\hostbook-src\install.ps1" -Repository git@github.com:duong6003/hostbook.git
```

Close and reopen your terminal (all Windows Terminal windows on Windows), then run:

```bash
hostbook
```

The installer sets up the runtime and dependencies without admin access. Windows uses
`%LOCALAPPDATA%\hostbook`; macOS/Linux use `~/.local` and provide `hb` when available.
Windows supports single-host SSH, sync, the encrypted vault, and sharing; tmux sessions
and hosting a Git hub over SSH require macOS/Linux or WSL. The interface is available in
English and Vietnamese (chosen on first run, changeable in **Tools → Appearance → Language**);
this README and the documentation are in English.

If the repository is made public again, the one-line installer
(`curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash`) works again.
Details and updates: [Getting started](https://github.com/duong6003/hostbook/wiki/Getting-started).

[Getting started](https://github.com/duong6003/hostbook/wiki/Getting-started)
 · [User guide](https://github.com/duong6003/hostbook/wiki)
 · [Appearance](https://github.com/duong6003/hostbook/wiki/Appearance)
 · [Security](https://github.com/duong6003/hostbook/wiki/Secrets-and-security)

## Contribute

See the [development guide](https://github.com/duong6003/hostbook/wiki/Development)
for architecture, local checks, and documentation updates.
[Report a bug](https://github.com/duong6003/hostbook/issues).

Released under the [MIT license](LICENSE).
