#!/usr/bin/env bash
# Exercise mode selection and safety without SSH; optionally verify options in a private tmux server.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=en
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
need_tmux(){ :; }; valid_name(){ :; }; host_exists(){ :; }; prepare_login(){ :; }; record(){ :; }; is_prod(){ [ "${PROD:-0}" = 1 ]; }
sshx(){
  [ "$1" = -G ] && [ "${SSH_G_FAIL:-0}" = 0 ] || return 1
  printf 'hostname %s%s\nuser %s\nport %s\nproxycommand %s\n' "$2" "${TARGET_SUFFIX:-}" "${TARGET_USER:-fixture}" "${TARGET_PORT:-22}" "${TARGET_PROXY:-none}"
}
pane_rows(){ local host; for host in "$@"; do printf '%s|%s|\n' "$host" "$(pane_fingerprint "$host")"; done; }
ui_menu(){ return 1; }
ui_choice(){ printf '%s' "$UI_POS" > "$T/position"; [ "${CANCEL:-0}" = 0 ] || return 1; echo "${PICK:-0}"; }
ui_confirm(){ return 1; }; attach(){ :; }; pane_cmd(){ echo fake; }
tmux(){ printf '%s\n' "$*" >> "$T/tmux"; case $1 in has-session) return 1;; show) echo C-b;; list-windows) echo @1;; list-panes) pane_rows a b;; esac; }
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
grep -q '^set -p -t hb-saved @hostbook_host a$' "$T/tmux" && grep -q '^set -p -t hb-saved @hostbook_host b$' "$T/tmux" || { echo 'FAIL missing pane provenance'; exit 1; }
: > "$T/tmux"
TARGET_PROXY=fixture-private-marker PROD=0 PICK=1 open_many '' a b
grep -q 'synchronize-panes on' "$T/tmux" || { echo 'FAIL fresh sync session'; exit 1; }
! grep -q 'fixture-private-marker' "$T/tmux" && grep -q '@hostbook_fingerprint [0-9a-f]\{64\}$' "$T/tmux" || { echo 'FAIL persisted raw connection data'; exit 1; }
rm -f "$T/position"; : > "$T/tmux"
TMUX=mock open_many '' a
[ ! -f "$T/position" ] && grep -q '^new-window' "$T/tmux" || { echo 'FAIL single host shortcut'; exit 1; }
echo 'PASS split default, saved modes/group, cancellation, single host and PROD guard' 

# Reopening a named group resumes its live panes, but must apply the new broadcast mode.
group_field(){ printf '%s\n' "${GROUP_MODE:-split}"; }
attach(){ printf 'attach\n' >> "$T/tmux"; }
flash(){ printf '%s\n' "$*" > "$T/flash"; }
ui_confirm(){ printf '%s\n' "$*" >> "$T/confirm"; [ "${ALLOW_PROD:-0}" = 1 ]; }
tmux(){
  printf '%s\n' "$*" >> "$T/tmux"
  case $1 in
    has-session) return 0;;
    list-windows) printf '@11\n@12\n';;
    list-panes) if [ -n "${PANE_ROWS:-}" ]; then printf '%s\n' "$PANE_ROWS"; else pane_rows a b; fi;;
    show) echo C-b;;
    set-window-option) [ "${FAIL_MODE:-0}" = 0 ];;
  esac
}
for mode in split tabs; do
  : > "$T/tmux"; : > "$T/confirm"
  GROUP_MODE=$mode PROD=1 open_many saved a b
  grep -q '^set-window-option -t @11 synchronize-panes off$' "$T/tmux" &&
    grep -q '^set-window-option -t @12 synchronize-panes off$' "$T/tmux" &&
    [ "$(tail -1 "$T/tmux")" = attach ] && [ ! -s "$T/confirm" ] &&
    ! grep -q 'new-session\|split-window\|new-window\|synchronize-panes on\|ALL AT ONCE' "$T/tmux" || {
      echo "FAIL resumed $mode must disable every stale synchronized window before attaching"; exit 1;
    }
done
: > "$T/tmux"; : > "$T/confirm"
GROUP_MODE=sync PROD=1 open_many saved a b && { echo 'FAIL resumed PROD sync accepted cancellation'; exit 1; }
grep -q -- '--default=false' "$T/confirm" && ! grep -q 'attach\|set-window-option' "$T/tmux" || { echo 'FAIL resumed PROD sync bypassed guard'; exit 1; }
: > "$T/tmux"; : > "$T/confirm"
GROUP_MODE=sync PROD=1 ALLOW_PROD=1 open_many saved a b
grep -q 'synchronize-panes on' "$T/tmux" && [ "$(tail -1 "$T/tmux")" = attach ] || { echo 'FAIL approved resumed sync'; exit 1; }
: > "$T/tmux"
GROUP_MODE=split FAIL_MODE=1 open_many saved a b && { echo 'FAIL mode update failure accepted'; exit 1; }
! grep -q '^attach$' "$T/tmux" || { echo 'FAIL attached after mode update failed'; exit 1; }
echo 'PASS resumed group applies broadcast mode and retains PROD cancellation'

# Deleting/removing a host changes group metadata, but its old live pane remains in tmux.
# Unknown provenance (older sessions or manually added panes) must fail closed too.
rows_ab=$(PROD=0 pane_rows a b)
for retained in "$(PROD=0 pane_rows a old-prod)" "$rows_ab${NL}||" "$(PROD=0 pane_rows a a b)"; do
  : > "$T/tmux"; : > "$T/confirm"; rm -f "$T/flash"
  GROUP_MODE=sync PROD=0 PANE_ROWS=$retained open_many saved a b && { echo 'FAIL unsafe retained pane set accepted'; exit 1; }
  grep -q 'synchronize-panes off' "$T/tmux" &&
    ! grep -q '^attach$\|synchronize-panes on' "$T/tmux" && [ ! -s "$T/confirm" ] &&
    grep -Fq "$(t err_open_session_sync)" "$T/flash" || { echo 'FAIL unsafe session must stay unsynchronized without attaching'; exit 1; }
done
: > "$T/tmux"
GROUP_MODE=sync PROD=0 PANE_ROWS="$(PROD=0 pane_rows b a)" open_many saved a b
grep -q 'synchronize-panes on' "$T/tmux" && [ "$(tail -1 "$T/tmux")" = attach ] || { echo 'FAIL equal retained host set rejected'; exit 1; }
echo 'PASS removed PROD, unknown and duplicate retained panes fail closed; equal host sets resume'

# A live SSH pane keeps its old target even when edit/import repoints the same alias.
for retained in "$(PROD=1 pane_rows a b)" "$(TARGET_SUFFIX=-old pane_rows a b)" \
                "$(TARGET_USER=old-user pane_rows a b)" "$(TARGET_PORT=2222 pane_rows a b)" \
                "$(TARGET_PROXY='ssh old-jump -W %h:%p' pane_rows a b)"; do
  : > "$T/tmux"; : > "$T/confirm"
  GROUP_MODE=sync PROD=0 PANE_ROWS=$retained open_many saved a b && { echo 'FAIL changed safety/target identity accepted'; exit 1; }
  ! grep -q '^attach$\|synchronize-panes on' "$T/tmux" && [ ! -s "$T/confirm" ] || { echo 'FAIL repointed alias enabled sync'; exit 1; }
done
: > "$T/tmux"
GROUP_MODE=sync SSH_G_FAIL=1 PANE_ROWS=$rows_ab open_many saved a b && { echo 'FAIL unavailable effective config accepted'; exit 1; }
! grep -q '^attach$\|synchronize-panes on' "$T/tmux" || exit 1
echo 'PASS same-alias PROD reclassification, target/login/port/proxy changes and unavailable config fail closed'
if command -v shasum >/dev/null 2>&1; then
  native_fingerprint=$(pane_fingerprint a)
  portable_fingerprint=$(
    have(){ [ "$1" != sha256sum ] && command -v "$1" >/dev/null 2>&1; }
    pane_fingerprint a
  )
  [ "$native_fingerprint" = "$portable_fingerprint" ] || { echo 'FAIL portable SHA-256 fallback'; exit 1; }
  echo 'PASS existing portable shasum fallback yields the same fingerprint'
fi

# Exercise real tmux option inheritance and inactive windows in a private server.
if command -v tmux >/dev/null 2>&1; then
  (
    unset TMUX TMUX_PANE
    socket="$T/tmux.sock"
    tmux(){ command tmux -S "$socket" -f /dev/null "$@"; }
    trap 'tmux kill-server 2>/dev/null || :' EXIT
    if ! tmux new-session -d -s hb-saved -n first 'cat' 2> "$T/tmux-start-error"; then
      printf 'SKIP real tmux resume checks (private server unavailable: %s)\n' "$(cat "$T/tmux-start-error")"
      exit 0
    fi
    tmux split-window -t '=hb-saved:' 'cat'
    tmux new-window -t '=hb-saved:' -n second 'cat'
    tmux split-window -t '=hb-saved:' 'cat'
    tmux set-window-option -g synchronize-panes on
    tmux list-panes -s -t '=hb-saved:' -F '#{pane_id}' > "$T/panes.before"
    set -- a b c d
    while IFS= read -r pane; do
      tmux set -p -t "$pane" @hostbook_host "$1"
      tmux set -p -t "$pane" @hostbook_fingerprint "$(PROD=1 pane_fingerprint "$1")"
      shift
    done < "$T/panes.before"
    for mode in split tabs; do
      for window in $(tmux list-windows -t '=hb-saved' -F '#{window_id}'); do tmux set-window-option -t "$window" synchronize-panes on; done
      GROUP_MODE=$mode PROD=1 open_many saved a b c d
      [ "$(tmux list-windows -t '=hb-saved' -F '#{synchronize-panes}' | sort -u)" = 0 ] || { echo "FAIL real resumed $mode stayed synchronized"; exit 1; }
      tmux list-panes -s -t '=hb-saved:' -F '#{pane_id}' > "$T/panes.after"
      cmp "$T/panes.before" "$T/panes.after" || exit 1
      case $(tmux show-options -v -t '=hb-saved:' status-right) in *'ALL AT ONCE'*) echo 'FAIL stale sync hint'; exit 1;; esac
    done
    GROUP_MODE=sync PROD=0 open_many saved a b c && { echo 'FAIL real removed pane accepted for sync'; exit 1; }
    [ "$(tmux list-windows -t '=hb-saved' -F '#{synchronize-panes}' | sort -u)" = 0 ] || exit 1
    GROUP_MODE=sync PROD=0 open_many saved a b c d && { echo 'FAIL real reclassified pane accepted for sync'; exit 1; }
    [ "$(tmux list-windows -t '=hb-saved' -F '#{synchronize-panes}' | sort -u)" = 0 ] || exit 1
    GROUP_MODE=sync PROD=1 ALLOW_PROD=1 open_many saved a b c d
    [ "$(tmux display-message -p -t '=hb-saved:' '#{synchronize-panes}')" = 1 ] || { echo 'FAIL real resumed sync not enabled'; exit 1; }
    echo 'PASS real tmux stale sync disabled across windows, live panes retained, sync restored after approval'
  )
else
  echo 'SKIP real tmux resume checks (tmux unavailable)'
fi
