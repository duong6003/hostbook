# Sync and sharing

## Choose a repository

Your sync repository is a Git remote named `origin` for `~/ssh-hosts`. Use a private
GitHub/Gitea repository or a bare repository on a machine you control, such as a NAS,
home server, or VPS. Machines need network access to that remote; LAN, VPN, and
Tailscale connections work as long as Git/SSH can reach it.

Connect through `Ctrl-O` -> `Kết nối kho`, or choose a remote on first launch.
The app can discover candidate machines on LAN/Tailscale; you still need permission
to access the repository.

## Synchronization behavior

hostbook starts background synchronization when opened. Changes are checked with
`ssh -G` before publication. If the remote is unavailable, local changes remain
pending and can be pushed after connectivity returns.

```bash
hostbook sync
```

Merging works per host. Independent host changes can merge automatically; conflicting
changes to the same host open `Gộp xung đột`, where you choose a version or edit it.
Invalid SSH configuration is rejected rather than silently propagated.

## Share selected hosts

Use the search box to narrow the list. Select individual hosts with Tab if needed,
then press Ctrl-Y. With no selection, sharing uses the matching hosts.

From the command line:

```bash
hostbook share '#prod web'
```

The share flow exports hosts, tags, descriptions, related groups, and **credentials
by default**. Review the selection before sharing. The bundle is encrypted with its
own password; send that password through a separate channel. A shared file does not
require connectivity between the sender and recipient.

## Import a share bundle

```bash
hostbook import workspace.hbshare
```

The import flow previews the bundle, compares hosts and credential fingerprints,
and lets you keep, update, or rename conflicting entries. After import, connection
checks are offered explicitly; they do not run automatically.

Host lists themselves are ordinary Git-tracked configuration, not encrypted data.
Protect access to the repository even when the credentials inside it are encrypted.
