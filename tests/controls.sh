#!/usr/bin/env bash
# Real fzf controls in a private tmux server; all config and terminal processes are disposable.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); socket=hb-controls-$$
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=en
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR
mkdir -p "$HOME" "$HOSTBOOK_DIR"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
end_ui(){ local p; for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do case $p in ''|*[!0-9]*|0|1) continue;; esac; kill -TERM -- "-$p" 2>/dev/null || :; kill -KILL -- "-$p" 2>/dev/null || :; done; tmux -L "$socket" kill-server 2>/dev/null || :; }
trap 'end_ui; rm -rf "$T"' EXIT
start(){
 local cmd; printf -v cmd '. %q; SELF=%q; at Test; %s >%q; printf "%%s" "$?" >%q; sleep 10' "$T/lib" "$HERE/../hostbook" "$1" "$T/result" "$T/status"
 rm -f "$T/result" "$T/status"
 tmux -L "$socket" new-session -d -x "${CONTROL_WIDTH:-60}" -y 24 -s ui "/bin/bash -c $(printf %q "$cmd")"
}
wait_text(){ local n=0; while [ "$n" -lt 100 ]; do tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :; grep -qF "$1" "$T/screen" && return; sleep .05; n=$((n+1)); done; cat "$T/screen"; echo "FAIL missing: $1"; exit 1; }
wait_done(){ local n=0; while [ ! -f "$T/status" ]; do sleep .05; n=$((n+1)); [ "$n" -lt 100 ] || { echo 'FAIL no result'; exit 1; }; done; }
k(){ tmux -L "$socket" send-keys -t ui "$@"; }
start 'UI_POS=2 ui_choice Pick alpha beta gamma'
wait_text beta
[ -z "${HOSTBOOK_TEST_ARTIFACTS:-}" ] || { mkdir -p "$HOSTBOOK_TEST_ARTIFACTS"; cp "$T/screen" "$HOSTBOOK_TEST_ARTIFACTS/choice-${CONTROL_WIDTH:-60}.txt"; }
! grep -q 'filter' "$T/screen" || { echo 'FAIL unexpected search'; exit 1; }
k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/result")" = 1 ] || { echo 'FAIL current choice'; exit 1; }
end_ui
echo 'PASS compact choice preselection and apply'
start 'ui_toggle Switch off'
wait_text '[Off]'
[ -z "${HOSTBOOK_TEST_ARTIFACTS:-}" ] || cp "$T/screen" "$HOSTBOOK_TEST_ARTIFACTS/toggle-${CONTROL_WIDTH:-60}.txt"
k Left; wait_text '[On]'; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/result")" = on ] || { echo 'FAIL toggle'; exit 1; }
end_ui
start 'UI_SELECT="1 3" ui_checklist Tags alpha beta gamma'
wait_text '2 selected'
k Space; wait_text '1 selected'; k Enter; wait_done
[ "$(cat "$T/result")" = 2 ] || { echo 'FAIL selected tags'; cat "$T/result"; exit 1; }
end_ui
start 'ui_checklist Tags alpha beta'
wait_text '0 selected'; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ ! -s "$T/result" ] || { echo 'FAIL empty selection'; exit 1; }
end_ui
start 'UI_SELECT=1 ui_checklist Tags alpha beta'
wait_text '1 selected'; k Escape; wait_done
[ "$(cat "$T/status")" != 0 ] || { echo 'FAIL cancel'; exit 1; }
end_ui
echo 'PASS toggle, selection, empty apply and cancel'
start 'UI_CHECK_ACTION=2 ui_checklist Tags alpha beta Other'
wait_text '0 selected'; k Down Down Space; sleep .2
tmux -L "$socket" capture-pane -p -t ui > "$T/screen"
! grep -q '1 selected' "$T/screen" || { echo 'FAIL Other counted'; exit 1; }
k Enter; wait_done
[ "$(cat "$T/result")" = 2 ] || { echo 'FAIL Other selection'; exit 1; }
end_ui
echo 'PASS action does not count as a tag'
start ': > "$IDX"; pick_tags demo "alpha beta"'
wait_text '2 selected'; k Down Down Space Enter
wait_text 'Add tags'; k Escape
wait_text '2 selected'; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/result")" = 'alpha beta' ] || { echo 'FAIL custom cancel retained checklist'; cat "$T/result"; exit 1; }
end_ui
start ': > "$IDX"; pick_tags demo "alpha beta"'
wait_text '2 selected'; k Space Down Space Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ ! -s "$T/result" ] || { echo 'FAIL clearing all tags'; exit 1; }
end_ui
echo 'PASS tags custom cancel and clear all'
start 'setting_pick separator'
wait_text '[On]'; wait_text 'booking-api'; k Right; wait_text '[Off]'; k Escape; wait_done
[ "$(cat "$T/status")" = 1 ] && ! grep -q 'separator = off' "$XDG_CONFIG_HOME/hostbook/config" 2>/dev/null || { echo 'FAIL setting cancelled'; exit 1; }
end_ui
start 'setting_pick separator'
wait_text '[On]'; k Right; wait_text '[Off]'; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && grep -q 'separator = off' "$XDG_CONFIG_HOME/hostbook/config" || { echo 'FAIL setting applied'; exit 1; }
end_ui
echo 'PASS binary setting cancel and apply'
