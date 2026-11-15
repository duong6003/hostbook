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

Use `Ctrl-O` -> `Nhập nhiều server` and choose a file, paste, or clipboard. The file picker supports Ctrl-L to enter a path. The paste editor submits with Enter, inserts a newline with Ctrl-J, and cancels with Ctrl-C. You can also run:

```bash
hostbook import hosts.txt
```

A text list can use one host per line:

```text
web1 ubuntu@10.0.1.11 #prod #web
api1 deploy@10.0.1.12:2222 #staging #api
```

Review the proposed hosts and key bindings before accepting; Escape cancels host changes. Text lists and SSH configurations add new aliases only: existing and repeated aliases are skipped. Unsupported SSH configuration directives are rejected with an error rather than imported with different behavior. Share bundles offer conflict choices and can update hosts.

After import, the result shows how many hosts were added, updated, or skipped,
with names, addresses, and projects. Choose **Xem các server vừa nhập** to open
the list filtered to that import; press Escape to return to all hosts. Existing
aliases are skipped when importing a text list or SSH config. The result can undo its import while that commit remains the latest change; if another commit arrives, use the regular undo workflow after reviewing it.

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
