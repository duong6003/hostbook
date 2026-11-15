#!/usr/bin/env bash
# Shared input helpers: all fixture files and callbacks stay in a disposable directory.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh_config" XDG_CONFIG_HOME="$T/config"
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$XDG_CONFIG_HOME" "$T/session"
. "$HERE/lib/ui.sh"
UI_AT= TAB=$'\t' NL=$'\n' CD= C0=
t(){ printf '%s' "$1"; }
ui_keys(){ :; }
unix_path(){ printf '%s\n' "$1"; }
ui_menu(){
  printf '%s\n' "${UI_QUERY:-}" >> "$T/queries"
  local n; n=$(cat "$T/step"); printf '%s' "$((n+1))" > "$T/step"
  cat "$T/menu$n"
}
ui_field(){ printf '%s\n' "$TEST_PATH"; }
printf 0 > "$T/step"
printf 'typed filter\nctrl-l\n\n' > "$T/menu0"
TEST_PATH="\$(touch executed) literal 'ệ.txt"; export TEST_PATH
printf 'safe\n' > "$HOME/$TEST_PATH"
printf x > "$HOME/unreadable"; chmod 000 "$HOME/unreadable"
out=$(file_pick "$HOME" pick '' '' "$T/session")
[ "$out" = "~/$TEST_PATH" ] || { printf 'FAIL literal relative path: %s\n' "$out"; exit 1; }
[ ! -e "$HOME/executed" ] || { echo 'FAIL path executed'; exit 1; }
printf 0 > "$T/step"; : > "$T/queries"
printf 'saved filter\nesc\n\n' > "$T/menu0"
file_pick "$HOME" pick '' '' "$T/session" >/dev/null && { echo 'FAIL escape'; exit 1; }
printf 0 > "$T/step"; printf 'saved filter\nctrl-l\n\n' > "$T/menu0"
out=$(file_pick "$HOME" pick '' '' "$T/session")
[ "$(tail -1 "$T/queries")" = 'saved filter' ] || { echo 'FAIL restored query'; exit 1; }
# Gum is replaced here only to check its version-specific contract and cancellation.
mkdir -p "$T/bin"
cat > "$T/bin/gum" <<'SH'
#!/bin/bash
if [ "$1" = --version ]; then exit 99; fi
printf '%s\n' "$@" > "$TEST_GUM_ARGS"
[ "${TEST_GUM_CANCEL:-}" != 1 ] || exit 130
printf 'first\nsecond\n'
SH
chmod +x "$T/bin/gum"
export PATH="$T/bin:$PATH" TEST_GUM_ARGS="$T/gum-args"
ui_keys(){ printf "%s " "$@"; }
ui_label(){ printf 'hostbook › Test'; }
ui_head(){ printf '%s\n' "$@"; }
term_rows(){ printf 24; }
gum_color(){ printf -v "$2" 7; }
TH_ACCENT=7 TH_TITLE=7
term_cols(){ printf 60; }
multiline=$(ui_multiline Label value)
[ "$multiline" = "$(printf 'first\nsecond')" ] || { echo 'FAIL multiline text'; exit 1; }
grep -q 'ctrl+j' "$T/gum-args" && grep -q 'enter' "$T/gum-args" || { echo 'FAIL gum 2 instructions'; exit 1; }
TEST_GUM_CANCEL=1; export TEST_GUM_CANCEL
if ui_multiline Label value >/dev/null; then echo 'FAIL multiline cancel'; exit 1; fi
PATH=${PATH#*:}; export PATH
printf 'Shared picker input tests passed.\n'
# Exercise real fzf key handling rather than only the scripted helper boundary.
command -v tmux >/dev/null && command -v fzf >/dev/null || { echo 'SKIP real picker: tmux or fzf missing'; exit 0; }
socket=hb-input-$$
end_ui(){ local p; for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do case $p in ''|*[!0-9]*|0|1) continue;; esac; kill -TERM -- "-$p" 2>/dev/null || :; kill -KILL -- "-$p" 2>/dev/null || :; done; tmux -L "$socket" kill-server 2>/dev/null || :; }
trap 'end_ui; rm -rf "$T"' EXIT
cat > "$T/editor" <<SH
#!/bin/sh
touch "$T/editor-used"
SH
chmod +x "$T/editor"
export EDITOR="$T/editor" VISUAL="$T/editor"
export HOSTBOOK_LIB_DIR="$HERE/lib" HOSTBOOK_LANG_DIR="$HERE/lang" HOSTBOOK_LANG=en
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR
sed '/^# ---------- entry point ----------/,$d' "$HERE/hostbook" > "$T/lib"
start(){
 local cmd; printf -v cmd '. %q; SELF=%q; at Test; file_pick "$HOME" Pick "" "" %q > %q; printf "%%s" "$?" > %q; sleep 10' "$T/lib" "$HERE/hostbook" "$T/session" "$T/picked" "$T/status"
 rm -f "$T/status"; tmux -L "$socket" new-session -d -x "${INPUT_WIDTH:-60}" -y 24 -s ui "/bin/bash -c $(printf %q "$cmd")"
}
wait_text(){ local n=0; while [ "$n" -lt 100 ]; do tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :; grep -qF "$1" "$T/screen" && return; sleep .05; n=$((n+1)); done; cat "$T/screen"; echo "FAIL missing $1"; exit 1; }
wait_done(){ local n=0; while [ ! -f "$T/status" ]; do sleep .05; n=$((n+1)); [ "$n" -lt 100 ] || { cat "$T/screen"; echo 'FAIL no result'; exit 1; }; done; }
k(){ tmux -L "$socket" send-keys -t ui "$@"; }
: > "$T/session/query"
start; wait_text 'Pick'; k -l 'no-match'; sleep .3; k Escape; wait_done
[ "$(cat "$T/status")" != 0 ] && [ "$(cat "$T/session/query")" = no-match ] || { echo 'FAIL escape preserves filtered query'; exit 1; }
end_ui
start; wait_text 'no-match'; k C-l; wait_text 'Enter a file or folder path'; k Escape; wait_text 'no-match'
k C-l; wait_text 'Enter a file or folder path'; k -l 'missing-file'; k Enter; wait_text 'Cannot read path'; wait_text 'no-match'
if [ ! -r "$HOME/unreadable" ]; then
  k C-l; wait_text 'Enter a file or folder path'; k -l 'unreadable'; k Enter; wait_text 'Cannot read path'
fi
k C-l; wait_text 'Enter a file or folder path'; k -l -- "$TEST_PATH"; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/picked")" = "~/$TEST_PATH" ] || { echo 'FAIL literal path selection'; cat "$T/picked"; exit 1; }
[ ! -e "$HERE/executed" ] || { echo 'FAIL command substitution executed'; exit 1; }
end_ui
printf 'Real picker query restoration and literal path passed.\n'
start_multiline(){
 local cmd; printf -v cmd '. %q; SELF=%q; at Test; ui_multiline "Paste input" %q > %q; printf "%%s" "$?" > %q; sleep 10' "$T/lib" "$HERE/hostbook" "${1:-}" "$T/picked" "$T/status"
 rm -f "$T/status"; tmux -L "$socket" new-session -d -x "${INPUT_WIDTH:-60}" -y 24 -s ui "/bin/bash -c $(printf %q "$cmd")"
}
start_multiline; wait_text 'Paste input'
wait_text 'ctrl+j'
[ -z "${HOSTBOOK_TEST_ARTIFACTS:-}" ] || { mkdir -p "$HOSTBOOK_TEST_ARTIFACTS"; cp "$T/screen" "$HOSTBOOK_TEST_ARTIFACTS/multiline-${INPUT_WIDTH:-60}.txt"; }
k -l 'first'; sleep .2
k C-j
sleep .2; k -l 'second'; sleep .2
k Enter
wait_done
[ "$(cat "$T/status")" = 0 ] && [ "$(cat "$T/picked")" = "$(printf 'first\nsecond')" ] || { echo 'FAIL multiline submit'; cat "$T/picked"; exit 1; }
end_ui
start_multiline 'previous draft'; wait_text 'previous draft'; k Enter; wait_done
[ "$(cat "$T/picked")" = 'previous draft' ] || { echo 'FAIL existing multiline draft'; exit 1; }
end_ui
draft=$'  leading and trailing  \n\nlast line  \n\n'
printf '%s\n' "$draft" > "$T/expected-draft"
start_multiline "$draft"; wait_text 'leading and trailing'; k Enter; wait_done
[ "$(cat "$T/status")" = 0 ] && cmp -s "$T/expected-draft" "$T/picked" || { echo 'FAIL exact multiline draft'; exit 1; }
end_ui
start_multiline 'previous draft'; wait_text 'previous draft'; k C-c; wait_done
[ "$(cat "$T/status")" != 0 ] || { echo 'FAIL multiline cancel'; exit 1; }
end_ui
version=$(gum --version); set -- $version; version=${3#v}; version=${version%%.*}
if [ "$version" -ge 2 ]; then
  start_multiline; wait_text 'Paste input'; k Escape; wait_done
  [ "$(cat "$T/status")" != 0 ] || { echo 'FAIL multiline escape'; exit 1; }
  end_ui
else echo 'SKIP multiline Escape: gum 0.14 does not handle it'; fi
[ ! -e "$T/editor-used" ] || { echo 'FAIL editor started'; exit 1; }
echo 'Real multiline submit and cancel passed.'
mkdir -p "$HOME/folder" "$HOME/.ssh"
printf x > "$HOME/folder/nested"
printf x > "$HOME/.secret"
printf x > "$HOME/.ssh/id_test"
: > "$T/session/query"; printf '%s\n' "$HOME" > "$T/session/dir"
start; wait_text 'Pick'
! grep -qF '.secret' "$T/screen" || { echo 'FAIL hidden initially shown'; exit 1; }
k C-o; wait_text '.secret'; k Escape; wait_done; end_ui
: > "$T/session/query"
start; wait_text 'Pick'; k C-l; wait_text 'Enter a file or folder path'; k -l 'folder'; k Enter; wait_text 'nested'
k -l 'nested'; sleep .2; k Enter; wait_done
[ "$(cat "$T/picked")" = '~/folder/nested' ] || { echo 'FAIL relative directory navigation'; cat "$T/picked"; exit 1; }
end_ui
start; wait_text 'nested'; k C-l; wait_text 'Enter a file or folder path'; k -l '~/.ssh/id_test'; k Enter; wait_done
[ "$(cat "$T/picked")" = '~/.ssh/id_test' ] || { echo 'FAIL tilde path'; cat "$T/picked"; exit 1; }
end_ui
: > "$T/session/query"
start; wait_text 'Pick'; k C-l; wait_text 'Enter a file or folder path'; k -l '/'; k Enter; sleep .2; k Escape; wait_done
[ "$(cat "$T/session/dir")" = / ] || { echo 'FAIL root session'; exit 1; }
end_ui
longdir="$HOME/$(printf '%080d' 0)-ệ"
mkdir -p "$longdir"; printf x > "$longdir/long-result"
printf '%s\n' "$HOME" > "$T/session/dir"; : > "$T/session/query"
start; wait_text 'Pick'; k C-l; wait_text 'Enter a file or folder path'; k -l -- "$longdir"; k Enter; wait_text 'long-result'
k -l 'long-result'; sleep .2; k Enter; wait_done
[ "$(cat "$T/picked")" = "~/${longdir##*/}/long-result" ] || { echo 'FAIL long Unicode directory'; cat "$T/picked"; exit 1; }
end_ui
cmd=; printf -v cmd '. %q; SELF=%q; at Test; pick_key_file > %q; printf "%%s" "$?" > %q; sleep 10' "$T/lib" "$HERE/hostbook" "$T/picked" "$T/status"
rm -f "$T/status"; tmux -L "$socket" new-session -d -x "${INPUT_WIDTH:-60}" -y 24 -s ui "/bin/bash -c $(printf %q "$cmd")"
wait_text 'id_test'; k Enter; wait_done
[ "$(cat "$T/picked")" = '~/.ssh/id_test' ] || { echo 'FAIL key picker'; cat "$T/picked"; exit 1; }
end_ui
echo 'Hidden toggle, relative directory and existing key picker passed.'
