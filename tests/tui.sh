#!/usr/bin/env bash
# Test the real fzf screen in an isolated tmux server, using only sample hosts and temporary configuration.
set -eu
command -v tmux >/dev/null || { echo 'Bỏ qua TUI: chưa có tmux'; exit 0; }
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook
T=$(mktemp -d); socket=hb-ui-$$
# kill-session only HUPs the pane leader; hostbook defers it while gum/fzf runs and gum never exits.
# So kill each pane's whole process group first (pane pid is its session and group leader).
end_ui(){
  local p
  for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do
    echo "$p" >> "$T/pgids"; kill -TERM -- "-$p" 2>/dev/null || :
  done
  sleep .2
  for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do kill -KILL -- "-$p" 2>/dev/null || :; done
  tmux -L "$socket" kill-server 2>/dev/null || :
}
leak_check(){
  local i=0 left
  while :; do
    left=$(ps -axo pgid=,ppid=,command= | awk -v f="$T/pgids" 'BEGIN{while((getline l<f)>0)g[l]=1} ($1 in g)')
    [ -z "$left" ] && return 0
    i=$((i+1)); [ "$i" -lt 30 ] || { printf '%s\n' "$left"; echo 'FAIL TUI: process còn sót sau khi đóng session'; return 1; }
    sleep .1
  done
}
cleanup(){
  end_ui
  local i=0
  # Background preview/sync processes may still be cleaning up temporary files after receiving HUP.
  while ! rm -rf "$T" 2>/dev/null; do i=$((i+1)); [ "$i" -lt 30 ] || return 1; sleep .1; done
}
trap cleanup EXIT
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh_config
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
ARTIFACTS=${HOSTBOOK_TEST_ARTIFACTS:-$T/captures}; mkdir -p "$ARTIFACTS"
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR
mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR" "$XDG_CONFIG_HOME/hostbook" "$T/bin"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/pbpaste"; chmod +x "$T/bin/pbpaste"
export PATH=$T/bin:$PATH
printf 'Include %s/*.conf\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
cat > "$HOSTBOOK_DIR/booking.conf" <<'CONF'
# tags: prod api
# desc: API chính
Host booking-api
  HostName 127.0.0.1
  User ubuntu
# tags: staging web
Host booking-web
  HostName 127.0.0.2
  User deploy
CONF
printf 'booking-prod\tsplit\tbooking-api booking-web\n' > "$HOSTBOOK_DIR/groups.tsv"
git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
"$S" _index >/dev/null
# Bounded wait for a real rendered state, rather than sleeping a fixed duration.
wait_for(){
  local i=0
  while [ "$i" -lt 60 ]; do
    tmux -L "$socket" capture-pane -p -t ui > "$T/screen" 2>/dev/null || :
    if grep -q "$1" "$T/screen"; then return 0; fi
    sleep .1; i=$((i+1))
  done
  cat "$T/screen"; echo "FAIL TUI: không thấy $1"; exit 1
}
start(){
  local cmd
  printf -v cmd 'env HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q PATH=%q SHELL=/bin/bash COLORFGBG=%q /bin/bash %q %s' \
    "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$PATH" "${COLORFGBG:-}" "$S" "${2:-}"
  tmux -L "$socket" new-session -d -x "$1" -y 36 -s ui "$cmd"
}
for width in 60 100 200; do
 for style in compact cards minimal; do
  printf 'list = %s\n' "$style" > "$XDG_CONFIG_HOME/hostbook/config"
  start "$width"
  wait_for booking-
  if [ "$width" -ge 100 ]; then wait_for "Nhóm · mở bằng"; fi
  cp "$T/screen" "$ARTIFACTS/$style-$width.txt"
  if grep -q 'secret:\|key-prod\|unknown option\|error:' "$T/screen"; then cat "$T/screen"; exit 1; fi
  if [ "$width" -ge 100 ]; then
    tmux -L "$socket" send-keys -t ui C-_
    i=0
    while :; do
      tmux -L "$socket" capture-pane -p -t ui > "$T/expanded"
      if ! grep -q 'secret:' "$T/expanded"; then break; fi
      i=$((i+1)); if [ "$i" -ge 60 ]; then cat "$T/expanded"; echo 'FAIL TUI: đóng preview lộ dữ liệu ẩn'; exit 1; fi
      sleep .1
    done
    tmux -L "$socket" send-keys -t ui C-_
  fi
  tmux -L "$socket" send-keys -t ui '#prod'
  wait_for '1/3'
  tmux -L "$socket" send-keys -t ui C-t
  if [ "$style" = cards ]; then want=compact; else want=cards; fi
  i=0; until grep -q "list = $want" "$XDG_CONFIG_HOME/hostbook/config"; do sleep .1; i=$((i+1)); [ "$i" -lt 60 ] || exit 1; done
  end_ui
  printf 'ok    TUI %s ở %s cột: tìm #prod, ^t lưu kiểu\n' "$style" "$width"
 done
 start "$width" settings
 wait_for 'Kiểu danh sách'
 tmux -L "$socket" send-keys -t ui Enter
 wait_for 'Thẻ (mặc định)'
 tmux -L "$socket" send-keys -t ui Down
 wait_for compact
 cp "$XDG_CONFIG_HOME/hostbook/config" "$T/before"
 tmux -L "$socket" send-keys -t ui Escape
 wait_for 'Kiểu danh sách'
 cmp "$T/before" "$XDG_CONFIG_HOME/hostbook/config"
 end_ui
 printf 'ok    TUI settings ở %s cột: xem trước và esc không lưu\n' "$width"
done

# The CLI result must open the actual imported subset, rather than exit after selection.
cat > "$T/import.conf" <<'CONF'
# tags: dev
Host dev-api
  HostName 192.0.2.10
  User ubuntu
Host dev-db
  HostName 192.0.2.11
  User ubuntu
CONF
start 100 "import $T/import.conf"
wait_for 'Nhập?'
tmux -L "$socket" send-keys -t ui Enter
wait_for 'Tiếp theo'
grep -q '2 mới' "$T/screen"
grep -q '192.0.2.10' "$T/screen"
cp "$T/screen" "$ARTIFACTS/import-result.txt"
tmux -L "$socket" send-keys -t ui Enter
wait_for 'Đang xem 2 server vừa nhập'
wait_for dev-api
if grep -q booking-api "$T/screen"; then cat "$T/screen"; exit 1; fi
cp "$T/screen" "$ARTIFACTS/import-filtered.txt"
tmux -L "$socket" send-keys -t ui Escape
wait_for booking-api
end_ui
printf 'ok    TUI import CLI: kết quả, xem riêng server vừa nhập, esc về tất cả\n'

# Fresh install: apply the suggested light theme only after the fzf list has loaded.
for width in 60 100 200; do
 export HOME=$T/first-$width/home XDG_CONFIG_HOME=$T/first-$width/config HOSTBOOK_DIR=$T/first-$width/hosts HOSTBOOK_STATE=$T/first-$width/state HOSTBOOK_SSH_CONFIG=$T/first-$width/ssh_config
 export COLORFGBG='0;15'
 mkdir -p "$HOME"
 start "$width"
 wait_for 'Bắt đầu thế nào?'
 tmux -L "$socket" send-keys -t ui Down Enter
 wait_for 'Kết nối kho ngay'
 tmux -L "$socket" send-keys -t ui Right Enter
 wait_for 'light · right'
 tmux -L "$socket" send-keys -t ui Enter
 wait_for 'enter để bắt đầu'
 grep -qx 'theme = light' "$XDG_CONFIG_HOME/hostbook/config"
 grep -qx 'list = cards' "$XDG_CONFIG_HOME/hostbook/config"
 tmux -L "$socket" send-keys -t ui Enter
 wait_for 'Chưa có server nào'
 end_ui
 printf 'ok    TUI cài mới ở %s cột: nền sáng chọn light, lưu và mở app\n' "$width"
done
end_ui
leak_check
