#!/usr/bin/env bash
# Exercise mode selection and safety without starting SSH or tmux sessions.
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
tmux(){ printf '%s\n' "$*" >> "$T/tmux"; case $1 in has-session) return 1;; show) echo C-b;; esac; }
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
