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
| `Right` | Actions for the highlighted host or group (see below); on a tmux session, close it (asks first); with several selected, open together, check connections, share, or save as a group. |
| `Tab` / `Shift-Tab` | Select / unselect hosts; Enter chooses how to open them. |
| `Ctrl-G` | Save two or more selected hosts as a group. |
| `Ctrl-A` / `Ctrl-E` | Add / edit a host. |
| `Ctrl-O` | **Tools** (`Công cụ`): import, sync now, undo, sync repository, secrets, check this machine, appearance. |
| `Ctrl-T` | Switch between two-line cards and the compact list. |
| `Ctrl-/` | Show or hide the details preview. |
| `Ctrl-S` | Sync now (see the progress screen below). |
| `Ctrl-Y` | Share selected hosts, or all matching hosts if none are selected. |
| `Ctrl-U` | In menus and pickers with a search box, clear the whole query. |
| `?` | Built-in help. |
| `Esc` | Clear the query, then the selection, then an imported-only view, then quit. |

An empty list points to Add (`Ctrl-A`) and Tools → Import. When a query matches nothing, the
footer says so and that `Esc` clears the search; a kept selection is mentioned too.

## Host actions

`Right` on a host opens a menu with sections **Connect** (SSH into server, open in tmux or a new
tmux tab, forward a port), **Files** (send a file to the server, download a file), **Edit** (edit
config, rename, move to another project, change login), **More** (copy IP, copy ssh command, check
connection, share this server) and **Danger** (delete server, always last). On a group it offers
open, change how it opens, and delete (servers are kept).

Every screen except the search list uses the same full-screen frame: a breadcrumb at the top
(`hostbook › server › Rename`), a card describing the server, then the field or choices, and the
key hints at the bottom. `Esc` always goes back or cancels. Finished actions return to the search
screen and show a one-line confirmation in the header, for example `Renamed shop-api → shop-pay`.

## Deleting and undoing

Delete server shows what it removes (the Host block, group memberships, servers that use it as a
jump host) and that it can be undone. A server tagged `prod` additionally requires typing its exact
name; Enter on an empty field deletes nothing. Deleting a secret works the same way when a `prod`
server uses it, and updating or replacing a `prod` server from an imported package asks for the
name too.

**Tools → Undo the last change** (`Hoàn tác thay đổi gần nhất`) lists what the latest change
did, with the names of added, removed and edited servers, groups or secrets, and when it was made.
It asks to confirm. If a background sync brought in a newer change meanwhile, reopen Undo to see
the latest one.

## Groups and tmux

Select hosts with Tab and press Enter. Open them as split panes, run in sync (type once, send to
every pane), or one tab per server. Save recurring selections with Ctrl-G; a group remembers how it
opens. Existing hostbook tmux sessions appear in the list so you can resume them. Without tmux,
hostbook offers to install it with Homebrew on macOS; on Windows use one host at a time or WSL.

Reopening a group resumes its existing panes and layout. Split or Tabs turns off synchronized input
in every window of that session; Run in sync enables it in the active window after any PROD confirmation.
Run in sync requires the session's recorded hosts, production classifications and effective SSH
configurations to match the group. If they differ, or the session predates this record, close the
session and reopen the group to start a fresh one.

Hosts tagged `prod` retain a visible production marker. Run in sync across production hosts asks
"Run on N PROD servers at once?" and defaults to cancel.

## Import hosts

Use **Tools → Import Hostbook package** (`Nhập gói Hostbook`) to choose an encrypted `.hbshare`
file exported by Hostbook. The picker starts in Downloads when available and lists folders and
`.hbshare` files. Details of the file picker are under *File transfer* below. A file with another
extension can be entered by path, but its contents must still be a valid package.

```bash
hostbook import workspace.hbshare
```

Enter the package password, then review: new, identical, changed and duplicate-address servers,
and the secrets it carries. Choose **Import as suggested** (add new, skip identical), **Update all
from the package**, or **Keep all of mine**; for changed servers you can also decide one by one
(**Update from the package**, **Keep mine**, **Keep both**). Keys and passwords are encrypted into
your vault. Nothing is written until you confirm, so invalid packages, wrong passwords and a cancelled
review leave hosts and credentials unchanged.

The result shows added, updated and skipped counts with host names and addresses. Choose **Show the
imported servers** to filter the list to them (`Esc` returns to all hosts), **Check connections**, or
**Undo this import**; undo is offered while the import is still the latest change. Connection checks
never run automatically.

Plain lists, pasted data, the clipboard and arbitrary SSH config files are not public import
formats; use Share on the source machine. Only first-run setup can migrate this machine's existing
SSH config. Importing a private key into Secrets is separate from importing a host package.

## File transfer and forwarding

Select a host and press Right. **Forward a port** suggests the port from tags (for example,
`postgres` suggests 5432) and picks another local port if it is busy; Ctrl-C stops forwarding.
**Send a file to the server…** and **Download a file to this machine…** use SCP and the host's
effective SSH configuration.

File pickers (send a file, key from a file, import a package) start in `~` or Downloads, list
folders first, and hide dot files. `→` opens a folder, `←` goes up, `Enter` chooses, `Ctrl-O` shows or
hides hidden files (`file ẩn` in the Vietnamese UI), and `Ctrl-L` types a literal path.

## Checks and progress

```bash
hostbook sync
hostbook check '#prod'
hostbook doctor
hostbook settings
```

Connection checks and sync show a progress line with the current step, elapsed time and limit, and
`ctrl-c` to cancel; cancelling a sync keeps your changes on this machine. A check logs in to each
server and leaves at once without running a command: at most 8 seconds per server, 8 servers at a
time. Afterwards you can **Retry failed servers** or **Show error details**. Connecting shows each
stage, including jump hosts, and failures explain what is wrong and offer fixes such as the SSH
log, edit server, or unlock. The default timeout is 15 seconds (`connect_timeout`);
`HOSTBOOK_CONNECT_TIMEOUT` overrides it.
