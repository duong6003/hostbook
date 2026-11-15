#!/usr/bin/env bash
# Home selection/empty-state recovery in private tmux and disposable data.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook; T=$(mktemp -d); socket=hb-recovery-$$
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=vi
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR TMUX
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$XDG_CONFIG_HOME/hostbook"
printf 'Host alpha\n  HostName 192.0.2.1\nHost beta\n  HostName 192.0.2.2\n' > "$HOSTBOOK_DIR/sample.conf"
git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
"$S" _index >/dev/null
end_ui(){ local p groups=''; for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do case $p in ''|*[!0-9]*|0|1) continue;; esac; groups="$groups $p"; kill -TERM -- "-$p" 2>/dev/null || :; done; sleep .1; for p in $groups; do kill -KILL -- "-$p" 2>/dev/null || :; done; tmux -L "$socket" kill-server 2>/dev/null || :; }
trap 'end_ui; rm -rf "$T"' EXIT
start(){ local cmd; printf -v cmd 'env HOSTBOOK_LIB_DIR=%q HOSTBOOK_LANG_DIR=%q HOSTBOOK_LANG=%q HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q PATH=%q SHELL=/bin/bash /bin/bash %q' "$HOSTBOOK_LIB_DIR" "$HOSTBOOK_LANG_DIR" "$HOSTBOOK_LANG" "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$PATH" "$S"; tmux -L "$socket" new-session -d -x "${WIDTH:-100}" -y 24 -s ui "$cmd"; }
snap(){ tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :; }
wait_for(){ local n=0; while [ "$n" -lt 100 ]; do snap; grep -qF "$1" "$T/screen" && return; sleep .05; n=$((n+1)); done; cat "$T/screen"; printf 'FAIL: missing %s\n' "$1"; exit 1; }
k(){ tmux -L "$socket" send-keys -t ui "$@"; }
start; wait_for alpha; k Tab; wait_for 'mở 1 server'; k Escape; wait_for 'enter ssh'
! grep -q 'mở 1 server' "$T/screen" || { echo 'FAIL stale selection footer'; exit 1; }
k Home BTab; wait_for 'mở 1 server'; k Escape; wait_for 'enter ssh'
end_ui
printf 'PASS selection footer after Escape and Shift-Tab\n'
