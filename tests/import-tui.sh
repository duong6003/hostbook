#!/usr/bin/env bash
# Real import review and result screens in private 24-row tmux sessions.
set -eu
unset SSH_AUTH_SOCK SSH_AGENT_PID
for tool in tmux fzf gum git ssh; do command -v "$tool" >/dev/null || { echo "SKIP import-tui: missing $tool"; exit 0; }; done
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook
T=$(mktemp -d); socket=hb-import-ui-$$
end_ui(){
  local p groups=''
  for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do
    case $p in ''|*[!0-9]*|0|1) continue;; esac
    groups="$groups $p"; kill -TERM -- "-$p" 2>/dev/null || :
  done
  sleep .2
  for p in $groups; do kill -KILL -- "-$p" 2>/dev/null || :; done
  tmux -L "$socket" kill-server 2>/dev/null || :
}
cleanup(){ end_ui; rm -rf "$T"; }
trap cleanup EXIT
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR
wait_for(){
  local text=$1 i=0
  while [ "$i" -lt 120 ]; do
    tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :
    grep -Fq "$text" "$T/screen" && return 0
    sleep .1; i=$((i+1))
  done
  cat "$T/screen"; echo "FAIL import TUI: did not see $text"; return 1
}
run_case(){
  local lang=$1 width=$2 border=$3 count=$4 name="$1-$2-$3-$4" root="$T/$1-$2-$3-$4" cmd i keys=()
  export HOME="$root/home" XDG_CONFIG_HOME="$root/config" HOSTBOOK_DIR="$root/hosts" HOSTBOOK_STATE="$root/state" HOSTBOOK_SSH_CONFIG="$root/ssh-config" HOSTBOOK_KEYCHAIN="file:$root/keychain/id"
  mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$XDG_CONFIG_HOME/hostbook"
  printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
  printf 'lang = %s\nborder = %s\n' "$lang" "$border" > "$XDG_CONFIG_HOME/hostbook/config"
  git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" commit --allow-empty -qm fixture
  i=1; : > "$root/input.conf"
  while [ "$i" -le "$count" ]; do
    printf '# tags: staging api\n# desc: very-long-unbroken-description-abcdefghijklmnopqrstuvwxyz-0123456789\nHost very-long-server-name-%02d\n  HostName 192.0.2.%s\n  User fixture\n' "$i" "$i" >> "$root/input.conf"
    i=$((i+1))
  done
  . "$HERE/../lang/$lang.sh"
  printf -v cmd 'env HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q HOSTBOOK_KEYCHAIN=%q HOSTBOOK_LANG=%q PATH=%q SHELL=/bin/bash /bin/bash %q import %q' \
    "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$HOSTBOOK_KEYCHAIN" "$lang" "$PATH" "$S" "$root/input.conf"
  if [ "$lang" = vi ] && [ "$width" = 44 ] && [ "$border" = rounded ] && [ "$count" = 30 ]; then printf 'previous-result\n' > "$HOSTBOOK_STATE/last-import"; fi
  tmux -L "$socket" new-session -d -x "$width" -y 24 -s ui "$cmd"
  wait_for "$L_import_review_title"
  wait_for 'very-long-server-name-01'
  if [ "$count" = 30 ]; then
    keys=(); i=1; while [ "$i" -lt "$count" ]; do keys+=(Down); i=$((i+1)); done
    tmux -L "$socket" send-keys -t ui "${keys[@]}"; wait_for 'very-long-server-name-30'
    keys=(); i=1; while [ "$i" -lt "$count" ]; do keys+=(Up); i=$((i+1)); done
    tmux -L "$socket" send-keys -t ui "${keys[@]}"; wait_for 'very-long-server-name-01'
  fi
  if [ "$lang" = vi ] && [ "$width" = 44 ] && [ "$border" = rounded ] && [ "$count" = 30 ]; then
    tmux -L "$socket" send-keys -t ui Escape
    i=0; while tmux -L "$socket" has-session -t ui 2>/dev/null; do i=$((i+1)); [ "$i" -lt 100 ] || { echo 'FAIL import TUI: canceled review did not exit'; return 1; }; sleep .05; done
    end_ui
    [ "$(cat "$HOSTBOOK_STATE/last-import")" = previous-result ] && ! ls "$HOSTBOOK_DIR"/*.conf >/dev/null 2>&1 && [ -z "$(ls -A "$HOSTBOOK_STATE/tmp" 2>/dev/null)" ] || { echo 'FAIL import TUI: canceled review wrote data or left temporary files'; return 1; }
    tmux -L "$socket" new-session -d -x "$width" -y 24 -s ui "$cmd"
    wait_for "$L_import_review_title"; wait_for 'very-long-server-name-01'
  fi
  tmux -L "$socket" send-keys -t ui Enter
  wait_for "$L_imp_next"
  for label in "$L_act_imp_view" "$L_act_imp_check" "$L_act_imp_later" "$L_act_imp_undo"; do
    grep -Fq "$label" "$T/screen" || { cat "$T/screen"; echo "FAIL import TUI $name: action missing: $label"; return 1; }
  done
  if [ "$width" = 44 ] && [ "$count" = 30 ]; then
    tmux -L "$socket" send-keys -t ui Down Down Down Enter
    wait_for "$L_undo_q"
    tmux -L "$socket" send-keys -t ui n
    wait_for "$L_imp_next"
    grep -Fq "$L_act_imp_undo" "$T/screen" || return 1
  fi
  end_ui
  printf 'ok    import review/results %s\n' "$name"
}
for lang in vi en; do
 for width in 44 60 100; do
  for border in rounded none; do
   for count in 8 30; do run_case "$lang" "$width" "$border" "$count"; done
  done
 done
done
