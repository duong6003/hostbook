# Troubleshooting

## Start with doctor

```bash
hostbook doctor
```

Doctor checks dependencies, host names, configuration, permissions, and sync status.
If a dependency is missing or too old, rerun the installer. If the command is not
found, reopen the shell and check that `~/.local/bin` is in PATH. On Windows,
close all Windows Terminal windows before reopening; check
`$env:LOCALAPPDATA\hostbook\bin` in your user PATH or use the full-path command
printed by the installer. Rerun `install.ps1` if a managed runtime/dependency is missing.
Corporate execution/download restrictions may require your organization's approved
installation process; the installer never changes machine execution policies.

## A connection fails

Read the error explanation and open the SSH log from the connection screen. Common
causes include an unreachable host, wrong port/user, a jump-host failure, an unloaded
vault key, rejected credentials, or a changed SSH host key.

To use managed keys outside the app, run `hostbook unlock` first. The default
connection timeout is 15 seconds. Override it for a single launch with:

```bash
HOSTBOOK_CONNECT_TIMEOUT=30 hostbook
```

Do not bypass a changed host-key warning without checking the server's identity.

## Sync is pending or conflicted

Check access to the Git remote and its network path. Run `hostbook sync` after
connectivity returns. Invalid host configuration must be corrected before syncing.
For conflicting edits to the same host, use the app's conflict-resolution flow.
See [Sync and sharing](Sync-and-sharing).

## The preview disappeared

Press Ctrl-/. On narrow terminals, the right preview can be hidden automatically;
widen the window or choose the bottom preview in `hostbook settings`.

## Colors or icons look wrong

Choose the light/high-contrast preset, ASCII icons, or `NO_COLOR=1 hostbook`.
Nerd Font icons require a compatible font. Background images and transparency are
controlled by your terminal; see [Appearance](Appearance).

## Report a problem

[Open an issue](https://github.com/duong6003/hostbook/issues) with your OS, shell,
`hostbook --version`, dependency versions, steps to reproduce, and a redacted error
log. Remove private hostnames, addresses, credentials, and tokens before sharing.
