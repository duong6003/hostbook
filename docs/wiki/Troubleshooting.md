# Troubleshooting

## Start with doctor

```bash
hostbook doctor
```

**Tools → Check this machine** (`Kiểm tra máy này`) shows the same report, grouped as Software,
Configuration, Sync and Secret vault, with a `→` fix on every problem line. It checks dependencies,
tmux, `~/ssh-hosts` and its permissions, the `Include` in `~/.ssh/config`, the SSH config itself,
invalid values in the hostbook config (with file and line), sync status, and the vault.

If a dependency is missing or too old ("fzf 0.60 is too old, 0.63 or newer is needed", "age is
missing"), rerun the installer (see [Getting started](Getting-started)). If `hostbook` is not found, reopen the
shell and check that `~/.local/bin` is in PATH, or run `hostbook setup`. On Windows, close all Windows
Terminal windows before reopening; check `$env:LOCALAPPDATA\hostbook\bin` in your user PATH or use the
full-path command printed by the installer. Rerun `install.ps1` if a managed runtime/dependency is
missing. Corporate execution/download restrictions may require your organization's approved
installation process; the installer never changes machine execution policies.

## Install or update fails

- "Could not update" or "This copy was not installed with git" from `hostbook update`: the pull in
  `~/.local/share/hostbook` failed or that directory is not a Git clone. Run `git -C
  ~/.local/share/hostbook pull --ff-only` to see the Git error, or rerun the one-line installer.
- "Could not download the hostbook update" (Windows): the same pull failed; check that the
  app clone's remote is reachable by the bundled Git.

## A connection fails

Read the error explanation and open the SSH log from the connection screen. Common
causes include an unreachable host, wrong port/user, a jump-host failure, an unloaded
vault key, rejected credentials, or a changed SSH host key. The screen names the failing stage
(jump host, network, SSH server, login, host key) and offers actions such as **Try again**, **Edit this
server**, **Unlock and try again**, **Copy the public key**, **Change the password**, and **Show the
connection log**. `ctrl-c` cancels a connection and returns to the list.

"The secret vault is not open on this machine": the server logs in with a vault key that is not loaded.
Choose **Unlock and try again**, or run `hostbook unlock`. "Wrong master password": retry, or use the
recovery code with `hostbook vault recover`. "The secret vault did not open": the master password was
not accepted or the vault files are damaged; "The vault is incomplete" means vault files are missing,
so restore them from a backup (they are never overwritten).

To use managed keys outside the app, run `hostbook unlock` first. The default
connection timeout is 15 seconds. Override it for a single launch with:

```bash
HOSTBOOK_CONNECT_TIMEOUT=30 hostbook
```

Do not bypass a changed host-key warning ("The host key of … has changed") without checking the
server's identity; **I checked it, update the host key** is only for a server you know was reinstalled.

## Sync is pending or conflicted

The header says what is wrong: "Cannot reach the store", "N changes not synced yet", "The store and this
machine changed the same thing", or "File error, not synced". Check access to the Git remote and its
network path (VPN, Tailscale), then press `Ctrl-S` or run `hostbook sync`. "Another sync is running" means
wait and retry. "Could not get the repository from …" on first run means a wrong address or no access.
Invalid host configuration must be corrected before syncing, or use **Tools → Undo the last change**.
For conflicting edits to the same host, use **Tools → Merge conflicts**.
See [Sync and sharing](Sync-and-sharing).

## An import is refused

"Only exported Hostbook packages (.hbshare) are supported": Import accepts only packages created by
Share. "Wrong package password" or "The package has unexpected content": check the password and the
file. A file that cannot be read, is empty, or is a private key (import keys through Secrets) is
refused with its own message. Nothing is changed unless you confirm the review.

## The screen looks wrong

If the preview disappeared, press Ctrl-/. On narrow terminals, the right preview can be hidden
automatically; widen the window or choose the bottom preview in `hostbook settings`. To change the
language, use **Appearance → Language** or `HOSTBOOK_LANG=en hostbook`. For wrong colors or icons,
choose the light/high-contrast preset, ASCII icons, or `NO_COLOR=1 hostbook`. Nerd Font icons require a
compatible font. Background images and transparency are controlled by your terminal; see
[Appearance](Appearance).

## Report a problem

[Open an issue](https://github.com/duong6003/hostbook/issues) with your OS, shell,
`hostbook --version`, dependency versions, steps to reproduce, and a redacted error
log. Remove private hostnames, addresses, credentials, and tokens before sharing.
