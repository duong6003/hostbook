# Notes for agents

For the codebase layout, test commands and isolation variables, see [docs/wiki/Development.md](docs/wiki/Development.md).
Never test against the real `~/ssh-hosts`, ssh-agent, Keychain, `~/.ssh` or `~/.config`. Copy data into a temporary
directory and point `HOSTBOOK_DIR`, `HOSTBOOK_STATE`, `HOSTBOOK_SSH_CONFIG` and `XDG_CONFIG_HOME` at it.

## Performance rules

These rules come from the 2026-11-15 audit (tracking issue #9). Every fzf callback runs `bash hostbook _<cmd>`, which
re-reads the whole script. One boot costs ~90 ms on macOS, where each fork costs ~2 ms. Most lag the user felt came
from paying that cost on every key press.

**Hot paths** are anything that runs per key press, cursor move, selection or resize. That includes `--preview`, the
`focus`/`change`/`multi`/`resize`/`load` binds, `transform-*`, `reload`, `_list`, `_header`, `_footer` and `_demo`.

1. **No full boot in a synchronous fzf action on a hot path.** `transform-*`, `execute` and the `reload` of
   focus/change/multi/resize binds block input until the command exits (#1, #6). Prefer, in order:
   - precomputed strings switched by an inline `case {1} in …`;
   - fzf placeholders and variables (`{1}`, `$FZF_SELECT_COUNT`, `$FZF_QUERY`);
   - `bg-transform-*` (check it against `FZF_MIN`).
2. **No network or waiting in preview, header, footer or list code.** That means no `nc`, `ssh`, `git fetch`, `curl` or
   `sleep`. Reachability checks run only on an explicit user action (#2). Scrolling must never open connections to
   servers.
3. **Count forks on hot paths.**
   - Do not put `$(…)`, pipes or external tools inside loops.
   - Use builtins, parameter expansion and `printf -v`.
   - Read a file with one `awk` pass, not one pass per field (`host_field` ×6 is the anti-pattern).
4. **Compute per-process facts once**, for example `uname`, config values and theme escapes. Do not recompute
   install-time facts on every invocation (`check_deps` versions; #5).
5. **Background work reloads the UI only when something changed** (`notify_fzf` after sync; #6).
6. **Every child process must die with its terminal.**
   - `trap … EXIT` makes bash 3.2 defer SIGHUP while a `gum`/`fzf` child runs, so closing the terminal could orphan
     both (#4). Top-level commands therefore run under a trap-free parent (top of `hostbook`); keep it trap-free.
   - Tests that drive the app through tmux end sessions with `end_ui` in `tests/tui.sh` (kills each pane's process
     group, #3). A bare `kill-session` is only for tests that simulate a terminal close (`close_check`).
   - After TUI tests, this must print nothing:
     `ps -axo ppid=,command= | awk '$1==1 && /hostbook|gum choose/'`
7. **Budgets.** Measure them on macOS, the slowest platform for forks.

   | Path | Budget |
   | --- | --- |
   | Pointer response | ≤ 20 ms per key |
   | `_preview` | ≤ 100 ms |
   | Any synchronous callback | ≤ 50 ms |
   | `hostbook --version` | ≤ 30 ms |
   | `_demo` | ≤ 80 ms |

   A change that touches a hot path must state the before/after numbers in its commit message or PR.

## How to measure

Run these with the isolation variables set. `NAME` is a host from the test data.

```bash
# average of 5 runs of one callback
perl -MTime::HiRes=time -e 'open STDOUT,">/dev/null"; $s=time; system(@ARGV) for 1..5; printf STDERR "%.0f ms\n",(time-$s)*200' bash hostbook _footer h:NAME

# time spent per source line (xtrace inflates totals; compare lines, not absolutes)
PS4='+${LINENO}: ' bash -x hostbook _preview h:NAME 2>&1 >/dev/null | perl -MTime::HiRes=time -ne '
  BEGIN{$p=time} $n=time; if(defined $l){($k)=$l=~/^\++(\d+):/; $t{$k}+=($n-$p)*1000; $c{$k}++} $l=$_; $p=$n;
  END{printf "%6.1f ms %4d x line %s\n",$t{$_},$c{$_},$_ for (sort{$t{$b}<=>$t{$a}} keys %t)[0..14]}'
```

To measure real UI latency, start the app in a private tmux server (`tmux -L perf …`), send keys with
`tmux send-keys`, and poll `tmux capture-pane -p` every 5 ms until the pointer row (`▌`) or the expected text changes.
`tests/tui.sh` shows how to start the app with isolated data. Kill that server when done, and never use the user's own
tmux server.

## Screens and language

Every screen follows [docs/design/screens.md](docs/design/screens.md): pick one of its screen types, build it from the
`ui_*` helpers, and put every user-facing string in `lang/vi.sh` and `lang/en.sh` (read through `t`). Do not print a
screen by hand, do not call `gum choose/input/confirm` directly in new code, and do not add literal UI text to
`hostbook`. Run `bash tests/screens.sh` after changing a screen.
