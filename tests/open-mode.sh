#!/usr/bin/env bash
# Exercise mode selection and safety with fake SSH and a disposable tmux server.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=en
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
need_tmux(){ :; }; valid_name(){ :; }; host_exists(){ :; }; prepare_login(){ :; }; record(){ :; }; is_prod(){ [ "${PROD:-0}" = 1 ]; }
ui_menu(){ return 1; }
ui_choice(){ printf '%s' "$UI_POS" > "$T/position"; [ "${CANCEL:-0}" = 0 ] || return 1; echo "${PICK:-0}"; }
ui_confirm(){ return 1; }; attach(){ :; }; pane_cmd(){ echo fake; }
tmux(){ printf '%s\n' "$*" >> "$T/tmux"; case $1 in has-session|list-windows) return 1;; show) echo C-b;; new-session) echo '$0';; esac; }
rm -f "$STATE/lastmode"; CANCEL=1 open_many '' a b || :
[ "$(cat "$T/position")" = 1 ] && [ ! -e "$STATE/lastmode" ] || { echo 'FAIL missing preference must preselect Split without writing on cancel'; exit 1; }
printf 'invalid\n' > "$STATE/lastmode"; CANCEL=1 open_many '' a b || :
[ "$(cat "$T/position")" = 1 ] && [ "$(cat "$STATE/lastmode")" = invalid ] || { echo 'FAIL invalid preference'; exit 1; }
for pair in 'split 1' 'sync 2' 'tabs 3'; do set -- $pair; echo "$1" > "$STATE/lastmode"; CANCEL=1 open_many '' a b || :; [ "$(cat "$T/position")" = "$2" ] || exit 1; done
: > "$T/tmux"; PROD=1 PICK=1 open_many '' a b || :
! grep -q 'new-session\|split-window\|new-window' "$T/tmux" || { echo 'FAIL rejected PROD creates panes'; exit 1; }
rm -f "$T/position"; : > "$T/tmux"
group_field(){ printf 'split\n'; }
open_many saved a b
[ ! -f "$T/position" ] && grep -q '^split-window' "$T/tmux" || { echo 'FAIL saved group mode'; exit 1; }
rm -f "$T/position"; : > "$T/tmux"
TMUX=mock open_many '' a
[ ! -f "$T/position" ] && grep -q '^new-window' "$T/tmux" || { echo 'FAIL single host shortcut'; exit 1; }
echo 'PASS split default, saved modes/group, cancellation, single host and PROD guard'

if command -v tmux >/dev/null; then
  unset TMUX SSH_AUTH_SOCK SSH_AGENT_PID
  tmux(){ command tmux -S "$T/tmux.sock" "$@"; }
  trap 'tmux kill-server 2>/dev/null || :; cleanup; rm -rf "$T"' EXIT
  session=$(tmux -f /dev/null new-session -dP -F '#{session_id}' -s hb-saved 'sleep 60')
  tmux new-window -d -t "$session:" 'sleep 60'
  attach(){ printf '%s\n' "$1" > "$T/attached"; }
  pane_cmd(){ printf "sh -c 'sleep 60' fixture\n"; }
  session_windows=$(tmux list-windows -t "$session" -F '#{window_id}')
  group_field(){ echo "$mode_fixture"; }
  for mode_fixture in split tabs; do
    for window in $session_windows; do tmux set-window-option -t "$window" synchronize-panes on; done
    open_many saved a b
    [ "$(cat "$T/attached")" = "$session" ]
    [ "$(tmux list-windows -t "$session" -F '#{window_id}')" = "$session_windows" ]
    [ "$(tmux list-windows -t "$session" -F '#{synchronize-panes}')" = $'0\n0' ]
  done
  rm "$T/attached"
  (
    tmux(){
      [ "$1" != set-window-option ] || return 1
      command tmux -S "$T/tmux.sock" "$@"
    }
    mode_fixture=split
    if open_many saved a b; then exit 1; fi
    [ ! -e "$T/attached" ]
  )
  ui_confirm(){ : > "$T/confirmed"; return 0; }
  mode_fixture=sync
  ! open_many saved a b
  [ ! -e "$T/attached" ] && [ ! -e "$T/confirmed" ]
  [ "$(tmux list-windows -t "$session" -F '#{window_id}')" = "$session_windows" ]
  tmux kill-session -t "$session"
  PROD=1 open_many saved a b
  [ -e "$T/confirmed" ] && [ -e "$T/attached" ]
  [ "$(tmux list-windows -t "$(cat "$T/attached")" -F '#{synchronize-panes}')" = 1 ]
  echo 'PASS existing Split/Tabs disable synchronization and Sync requires a new confirmed session'
  tmux kill-session -t "$(cat "$T/attached")"
  tmux(){
    if [ "$1" = split-window ]; then
      ( mode_fixture=split; open_many saved a b ) || return 1
    fi
    command tmux -S "$T/tmux.sock" "$@"
  }
  PROD=1 open_many saved a b
  session=$(cat "$T/attached")
  [ "$(tmux list-windows -t "$session" -F '#{synchronize-panes}')" = 0 ]
  [ "$(tmux show -t "$session" -v status-right)" = " $(t tmux_hint '^b' '^b') " ]
  echo 'PASS a concurrent Split resume cannot be overridden by the fresh Sync opener'
  tmux kill-session -t "$session"
  rm "$T/attached"
  prepare_login(){ tmux new-session -d -s hb-saved 'sleep 60'; }
  if PROD=1 open_many saved a b; then exit 1; fi
  [ ! -e "$T/attached" ]
  [ "$(tmux list-panes -t 'hb-saved:' -F '#{synchronize-panes}')" = 0 ]
  echo 'PASS duplicate session creation never modifies or attaches the competing session'
else
  echo 'SKIP real tmux session checks (tmux unavailable)'
fi
