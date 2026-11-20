# Sync and sharing

## Choose a repository

Your sync repository is a Git remote named `origin` for `~/ssh-hosts`. Use a private
GitHub/Gitea repository or a bare repository on a machine you control, such as a NAS,
home server, or VPS. Machines need network access to that remote; LAN, VPN, and
Tailscale connections work as long as Git/SSH can reach it.

Connect through `Ctrl-O` → **Connect sync repository** (`Kết nối kho đồng bộ`; **Change sync
repository** once one is set), or choose a remote on first launch. Pick a machine the app found on
the LAN/Tailscale, **Enter an address…** (`user@machine` or an IP), or **Use a URL…** such as
`git@github.com:you/ssh-hosts-private.git`. The repository must be private, and you still need
permission to access it. To host the repository yourself, run **Create the store on this machine**
once on an always-on macOS or Linux machine with SSH enabled.

## Synchronization behavior

hostbook starts background synchronization when opened, and the header shows the state ("in sync",
"3 changes not synced yet", "Cannot reach the store"). Changes are checked with `ssh -G` before
publication. If the remote is unavailable, local changes remain pending and can be pushed after
connectivity returns.

```bash
hostbook sync
```

`Ctrl-S` (or **Tools → Sync now**) shows a progress screen with elapsed time and a limit; `ctrl-c`
cancels, and your changes stay on this machine.

Merging works per host. Independent host changes can merge automatically; conflicting
changes to the same host add **Merge conflicts** (`Gộp xung đột`) to Tools, where you choose a version or
edit it. Invalid SSH configuration is rejected rather than silently propagated.

If Hostbook reports that it could not restore the previous state after Undo, it pauses
automatic publication while Git still has the revert in progress. In the store repository,
use `git status`, `git diff` and `git diff --cached` to review both unstaged and staged
changes. Preserve any edits you want to keep and restore the unintended Undo changes
before running `git revert --quit` to clear the recovery state. Then try **Sync now** again.

## Share selected hosts

Use the search box to narrow the list. Select individual hosts with Tab if needed,
then press Ctrl-Y (or use Right on a host, **Share this server**). With no selection, sharing uses the
matching hosts. Jump hosts the selection needs are added automatically and listed.

From the command line:

```bash
hostbook share '#prod web'
```

The package holds hosts, tags, descriptions, related groups, and optionally credentials. When the
selected hosts use secrets you choose **Create a package with secrets** (the recipient can log in
right away) or **Create a package without secrets** (the recipient picks how to log in). Review the
count of `prod` servers and secrets before confirming.

The package is encrypted with its own password, shown on the result screen with **Copy the
password**; send it through a separate channel. Export always saves a
`hostbook-<date>-<time>-<random>.hbshare` file in Downloads (or Home when Downloads is absent) and
also copies it to the clipboard when a clipboard tool is available. Sender and recipient do not
need to be connected.

## Import a share package

```bash
hostbook import workspace.hbshare
```

or **Tools → Import Hostbook package**. Only Hostbook packages are accepted; anything else (plain
lists, pasted text, SSH config files) is refused with "Only exported Hostbook packages (.hbshare)
are supported". Enter the package password; a wrong password or unexpected content stops before
anything is applied.

The review shows new, identical, changed and same-address servers, plus the secrets (new, already
there, renamed). Choose how to import: as suggested, update all, keep all of yours, or decide per
server (update, keep yours, keep both with the packaged one renamed `-2`, replace). Replacing or
updating a `prod` server needs its name typed. Nothing is written before you confirm. Afterwards the result
screen can show only the imported servers, check their connections (explicitly; never automatic), or undo
the import while it is still the latest change. See [Daily use](Daily-use).

Host lists themselves are ordinary Git-tracked configuration, not encrypted data.
Protect access to the repository even when the credentials inside it are encrypted.
