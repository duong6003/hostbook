#!/usr/bin/env bash
# Home selection/empty-state recovery in private tmux and disposable data.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook; T=$(mktemp -d); socket=hb-recovery-$$
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=vi
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR TMUX
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$XDG_CONFIG_HOME/hostbook"
printf 'Include %s/*.conf\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
printf 'Host alpha\n  HostName 192.0.2.1\nHost beta\n  HostName 192.0.2.2\n' > "$HOSTBOOK_DIR/sample.conf"
git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
"$S" _index >/dev/null
end_ui(){ local p groups=''; for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do case $p in ''|*[!0-9]*|0|1) continue;; esac; groups="$groups $p"; kill -TERM -- "-$p" 2>/dev/null || :; done; sleep .1; for p in $groups; do kill -KILL -- "-$p" 2>/dev/null || :; done; tmux -L "$socket" kill-server 2>/dev/null || :; }
trap 'end_ui; rm -rf "$T"' EXIT
start(){ local cmd; printf -v cmd 'env HOSTBOOK_LIB_DIR=%q HOSTBOOK_LANG_DIR=%q HOSTBOOK_LANG=%q HOSTBOOK_ONLY=%q HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q PATH=%q SHELL=/bin/bash /bin/bash %q' "$HOSTBOOK_LIB_DIR" "$HOSTBOOK_LANG_DIR" "$HOSTBOOK_LANG" "${HOSTBOOK_ONLY:-}" "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$PATH" "$S"; tmux -L "$socket" new-session -d -x "${WIDTH:-100}" -y 24 -s ui "$cmd"; }
snap(){ tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :; }
wait_for(){ local n=0; while [ "$n" -lt 100 ]; do snap; grep -qF "$1" "$T/screen" && return; sleep .05; n=$((n+1)); done; cat "$T/screen"; printf 'FAIL: missing %s\n' "$1"; exit 1; }
k(){ tmux -L "$socket" send-keys -t ui "$@"; }
start; wait_for alpha; k Tab; wait_for 'mở 1 server'; k Escape; wait_for 'enter ssh'
! grep -q 'mở 1 server' "$T/screen" || { echo 'FAIL stale selection footer'; exit 1; }
k Home BTab; wait_for 'mở 1 server'; k Escape; wait_for 'enter ssh'
end_ui
printf 'PASS selection footer after Escape and Shift-Tab\n'
# Match recovery must not clear the query or lose selection/footer state.
for lang in vi en; do
 for border in rounded none; do
  for width in 44 60 100; do
   export HOSTBOOK_LANG=$lang WIDTH=$width
   printf 'border = %s\nlist = compact\n' "$border" > "$XDG_CONFIG_HOME/hostbook/config"
   if [ "$lang" = vi ]; then missing='Không có kết quả'; clear_hint='Esc xoá tìm kiếm'; kept='Vẫn giữ lựa chọn'; else missing='No matches'; clear_hint='Esc clears search'; kept='Selection kept'; fi
   start; wait_for alpha
   k -l 'no-such-host'; wait_for 'no-such'; wait_for "$missing"; wait_for "$clear_hint"
   k BSpace BSpace BSpace; k -l 'zzzz'; wait_for "$missing"
   k Escape; wait_for alpha
   ! grep -qF "$missing" "$T/screen" || { echo 'FAIL stale no-match message'; exit 1; }
   k Tab; if [ "$lang" = vi ]; then wait_for 'mở 1 server'; else wait_for 'open 1 servers'; fi
   k -l 'no-such-host'; wait_for "$missing"; wait_for "$kept"; k Escape; wait_for alpha
   if [ "$lang" = vi ]; then wait_for 'mở 1 server'; else wait_for 'open 1 servers'; fi
   k Escape; if [ "$lang" = vi ]; then wait_for 'enter ssh'; else wait_for 'enter ssh'; fi
   k -l 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
   wait_for "$missing"; k Escape; wait_for alpha
   end_ui
  done
 done
done
printf 'PASS query recovery with selection, long text and both languages/borders\n'
# Enter acts on selected hosts even when the current query has no matches.
export HOSTBOOK_LANG=en WIDTH=100
printf 'border = rounded\nlist = compact\n' > "$XDG_CONFIG_HOME/hostbook/config"
start; wait_for alpha; k Tab Tab; wait_for 'open 2 servers'
k -l 'nothing-matches'; wait_for 'Selection kept'; k Enter; wait_for 'How to open?'
k Escape; wait_for alpha; end_ui
printf 'PASS selected hosts can still be opened from no matches\n'

# Escape first clears the query, then selection, then leaves an imported-only view.
printf 'alpha\n' > "$T/only"
export HOSTBOOK_ONLY=$T/only
start; wait_for alpha
! grep -q beta "$T/screen" || { echo 'FAIL filtered view'; exit 1; }
k Tab; wait_for 'open 1 servers'; k -l 'no-match'; wait_for 'Selection kept'
k Escape; wait_for alpha; wait_for 'open 1 servers'
k Escape; wait_for 'enter ssh'
k Escape; wait_for beta; end_ui
unset HOSTBOOK_ONLY
printf 'PASS imported-view Escape hierarchy\n'
printf 'ghost\n' > "$T/only"
export HOSTBOOK_ONLY=$T/only
start; wait_for 'Nothing in this view'; wait_for 'Esc back'
k Escape; wait_for beta; end_ui
unset HOSTBOOK_ONLY
printf 'PASS empty imported view can return to all hosts\n'

# Empty data keeps the real Add and Tools/Import instructions even after typing.
export HOSTBOOK_DIR=$T/empty HOSTBOOK_STATE=$T/empty-state
mkdir -p "$HOSTBOOK_DIR" "$HOSTBOOK_STATE"
git -C "$HOSTBOOK_DIR" init -q
"$S" _index >/dev/null
for lang in vi en; do
 for border in rounded none; do
  for width in 44 60 100; do
   export HOSTBOOK_LANG=$lang WIDTH=$width
   printf 'border = %s\nlist = compact\n' "$border" > "$XDG_CONFIG_HOME/hostbook/config"
   if [ "$lang" = vi ]; then empty='Chưa có server nào.'; add='thêm server đầu tiên'; import='nhập nhiều server'; missing='Không có kết quả'; else empty='No servers yet.'; add='add your first server'; import='import many servers'; missing='No matches'; fi
   start; wait_for "$empty"; wait_for "$add"; wait_for "$import"
   k -l 'nothing-matches'; wait_for "$empty"
   ! grep -qF "$missing" "$T/screen" || { echo 'FAIL empty data reported as search miss'; exit 1; }
   k Escape; wait_for "$empty"; end_ui
  done
 done
done
printf 'PASS no-data instructions in both languages/borders\n'
