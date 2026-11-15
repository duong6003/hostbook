#!/usr/bin/env bash
# Real shared-menu and picker no-match recovery in an isolated tmux server.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd); T=$(mktemp -d); socket=hb-empty-$$
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/lib HOSTBOOK_LANG_DIR=$HERE/lang
export HOSTBOOK_LANG=${INPUT_LANG:-en}
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR
mkdir -p "$HOME/empty" "$HOSTBOOK_DIR" "$XDG_CONFIG_HOME/hostbook" "$T/session"
[ "${INPUT_BORDER:-rounded}" != none ] || printf 'border = none\n' > "$XDG_CONFIG_HOME/hostbook/config"
sed '/^# ---------- entry point ----------/,$d' "$HERE/hostbook" > "$T/lib"
end_ui(){ local p; for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do case $p in ''|*[!0-9]*|0|1) continue;; esac; kill -TERM -- "-$p" 2>/dev/null || :; kill -KILL -- "-$p" 2>/dev/null || :; done; tmux -L "$socket" kill-server 2>/dev/null || :; }
trap 'end_ui; rm -rf "$T"' EXIT
start(){ local cmd; printf -v cmd '. %q; SELF=%q; at Test; %s > %q; printf "%%s" "$?" > %q; sleep 10' "$T/lib" "$HERE/hostbook" "$1" "$T/out" "$T/status"; rm -f "$T/status"; tmux -L "$socket" new-session -d -x "${INPUT_WIDTH:-60}" -y 24 -s ui "/bin/bash -c $(printf %q "$cmd")"; }
wait_text(){ local n=0; while [ "$n" -lt 100 ]; do tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :; grep -qF "$1" "$T/screen" && return; sleep .05; n=$((n+1)); done; cat "$T/screen"; echo "FAIL missing: $1"; exit 1; }
wait_done(){ local n=0; while [ ! -f "$T/status" ]; do sleep .05; n=$((n+1)); [ "$n" -lt 100 ] || { echo 'FAIL no result'; exit 1; }; done; }
k(){ tmux -L "$socket" send-keys -t ui "$@"; }
case $HOSTBOOK_LANG in vi) hint='Không thấy kết quả'; empty='Thư mục trống';; *) hint='No matches'; empty='This folder is empty';; esac
start 'UI_FOOT="existing shortcut" ui_menu Pick alpha beta'
wait_text alpha
k -l 'an-unmatched-query-that-is-longer-than-the-terminal-width-and-does-not-exist'
wait_text "$hint"; wait_text '0/2'; wait_text 'existing shortcut'
k C-a; k C-u; wait_text alpha
wait_text '2/2'
k -l alphax; wait_text "$hint"; k BSpace; wait_text alpha
wait_text '1/2'
k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/out")" = 0 ] || { echo 'FAIL menu output contract'; exit 1; }
end_ui
start 'file_pick "$HOME/empty" Pick "" "" "'$T'/session"'
wait_text "$empty"; ! grep -qF "$hint" "$T/screen" || { echo 'FAIL empty folder reported as a failed search'; exit 1; }
k -l 'search-in-empty-folder'; sleep .2
tmux -L "$socket" capture-pane -p -t ui > "$T/screen"
! grep -qF "$hint" "$T/screen" || { echo 'FAIL hint in empty folder'; exit 1; }
k Escape; wait_done; [ "$(cat "$T/status")" != 0 ] || { echo 'FAIL picker escape'; exit 1; }
end_ui
printf x > "$HOME/a-file"
printf '%s\n' "$HOME" > "$T/session/dir"; : > "$T/session/query"
start 'file_pick "$HOME" Pick "" "" "'$T'/session"'
wait_text a-file; k -l 'no-such-file-long-query'; wait_text "$hint"; wait_text '0/2'; wait_text 'ctrl-l'
k Escape; wait_done
[ "$(cat "$T/session/query")" = no-such-file-long-query ] || { echo 'FAIL picker query save'; exit 1; }
end_ui
start 'file_pick "$HOME" Pick "" "" "'$T'/session"'
wait_text no-such-file-long-query; wait_text "$hint"; k C-a; k C-u; wait_text a-file
k Escape; wait_done
[ ! -s "$T/session/query" ] || [ "$(cat "$T/session/query")" = '' ] || { echo 'FAIL picker clear query'; exit 1; }
end_ui
echo 'Shared menu search recovery and empty folder passed.'
