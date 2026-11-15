# Daily use

## Search and connect

Run `hostbook` and type to search names, tags, users, IP addresses, and descriptions.
Search also covers metadata that is not currently visible in a narrow layout.

| Query | Meaning |
| --- | --- |
| `web prod` | Match both words. |
| `db \| cache` | Match either word. |
| `!staging` | Exclude staging. |
| `^b-` | Match a name starting with `b-`. |
| `#prod` | Match a production tag. |
| `@deploy` | Match a user. |

Press Enter to connect. From the shell, `hostbook web1` connects directly when the
alias matches exactly or only one host matches.

## Keyboard reference

| Key | Action |
| --- | --- |
| `Enter` | Connect to a host, open a group, or resume a tmux session. |
| `Right` | Host actions: port forwarding, upload/download, copy IP, rename, move, or delete. |
| `Tab` | Select multiple hosts; Enter chooses how to open them. |
| `Ctrl-G` | Save selected hosts as a group. |
| `Ctrl-A` / `Ctrl-E` | Add / edit a host. |
| `Ctrl-O` | Tools (`Công cụ`): import, secrets, sync repository, undo, appearance, and checks. |
| `Ctrl-T` | Switch between two-line cards and the compact list. |
| `Ctrl-/` | Toggle the details preview. |
| `Ctrl-S` | Synchronize now. |
| `Ctrl-Y` | Share selected hosts, or all matching hosts if none are selected. |
| `?` | Built-in help. |
| `Esc` | Clear the query or selection before leaving. |

## Groups and tmux

Select hosts with Tab and press Enter. Open them as splits, synchronized panes, or
separate tmux windows. Save recurring selections with Ctrl-G. Existing hostbook tmux
sessions appear in the list so you can resume them.

Hosts tagged `prod` retain a visible production marker. Synchronized input across
production hosts requires confirmation and defaults to No.

## Import hosts

Use **Tools → Import Hostbook package** to choose an encrypted `.hbshare` file exported by Hostbook. The picker starts in Downloads when available and shows folders and `.hbshare` files. Ctrl-L enters a literal file or directory path; Escape cancels. A file with another extension can be entered by path, but its contents must still be a valid Hostbook package.

```bash
hostbook import workspace.hbshare
```

Enter the package password, review the server and credential changes, resolve conflicts, and confirm. Existing matching entries are skipped; changed entries offer keep/update/rename choices. Keys and passwords are encrypted into the receiving vault. Invalid packages and wrong passwords cannot reach the apply step. Canceling review leaves hosts and credentials unchanged.

After import, the result shows added, updated and skipped counts with host names and addresses. Choose **View imported servers** to show that subset; Escape returns to all hosts. Connection checks are explicit. The result can undo the import while its commit remains the latest change.

Plain lists, pasted data, clipboard import and arbitrary SSH config files are not public import formats. Initial setup can still offer a separate migration of this machine's existing SSH config. Importing an individual private key into Secrets remains separate from importing a host package.

## File transfer and forwarding

Select a host and press Right. The port-forwarding menu suggests ports from tags
(for example, `postgres` suggests 5432). Upload/download actions use SCP and the
host's effective SSH configuration.

## Useful commands

```bash
hostbook sync
hostbook check '#prod'
hostbook doctor
hostbook settings
```

Connection progress shows the current stage, including jump-host connections.
Failures include an explanation and access to the SSH log. The default timeout is
15 seconds; `HOSTBOOK_CONNECT_TIMEOUT` overrides the local setting.
