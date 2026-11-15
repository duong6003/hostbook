#!/usr/bin/env bash
# Test the real fzf screen in an isolated tmux server, using only sample hosts and temporary configuration.
set -eu
unset SSH_AUTH_SOCK SSH_AGENT_PID
command -v tmux >/dev/null || { echo 'Bỏ qua TUI: chưa có tmux'; exit 0; }
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook
T=$(mktemp -d); socket=hb-ui-$$
# Do not rely on the app for teardown (#3): kill each pane's whole process group first (pane pid is its session and
# group leader). close_check below tests what a real terminal close does.
end_ui(){
  local p groups=''
  for p in $(tmux -L "$socket" list-panes -a -F '#{pane_pid}' 2>/dev/null); do
    case $p in ''|*[!0-9]*|0|1) continue;; esac   # -0/-1 would signal our own group or every process
    echo "$p" >> "$T/pgids"; groups="$groups $p"; kill -TERM -- "-$p" 2>/dev/null || :
  done
  sleep .2
  # A pane closes once its leader exits while gum may survive in the group, so KILL the groups just signalled, not live panes.
  for p in $groups; do kill -KILL -- "-$p" 2>/dev/null || :; done
  tmux -L "$socket" kill-server 2>/dev/null || :
}
leak_check(){
  local i=0 left
  while :; do
    left=$(ps -axo pgid=,pid=,command= | awk -v f="$T/pgids" 'BEGIN{while((getline l<f)>0)g[l]=1} ($1 in g)')
    [ -z "$left" ] && return 0
    i=$((i+1)); [ "$i" -lt 50 ] || { printf '%s\n' "$left"; echo 'FAIL TUI: process còn sót sau khi đóng session'
      kill -KILL $(printf '%s\n' "$left" | awk '{print $2}') 2>/dev/null || :; return 1; }   # report, then do not leave them running
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
export HOSTBOOK_KEYCHAIN="file:$T/keychain/id"
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
mkdir -p "$HOSTBOOK_DIR/secrets"; for f in vault.pub vault.key.age vault.recovery.age; do printf 'fixture\n' > "$HOSTBOOK_DIR/secrets/$f"; done
git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
fixture_commit=$(git -C "$HOSTBOOK_DIR" rev-parse HEAD)
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
  printf -v cmd 'env HOSTBOOK_LIB_DIR=%q HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR=%q HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q PATH=%q SHELL=/bin/bash COLORFGBG=%q /bin/bash %q %s' \
    "$HERE/../lib" "$HERE/../lang" "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$PATH" "${COLORFGBG:-}" "$S" "${2:-}"
  tmux -L "$socket" new-session -d -x "$1" -y 36 -s ui "$cmd"
}
# An unknown underscore-prefixed argument remains an ordinary search query.
printf '\nHost _web-a\n  HostName 192.0.2.1\nHost _web-b\n  HostName 192.0.2.2\n' >> "$HOSTBOOK_DIR/booking.conf"
"$S" _index >/dev/null
start 100 _web
wait_for _web-a
wait_for _web-b
end_ui
# Search can commit external edits in the background; HEAD is not the fixture baseline.
"$S" _sync_now >/dev/null
git -C "$HOSTBOOK_DIR" show "$fixture_commit:booking.conf" > "$HOSTBOOK_DIR/booking.conf"
! grep -q '^Host _web-' "$HOSTBOOK_DIR/booking.conf"
"$S" _index >/dev/null
echo 'ok    TUI tìm hai server với truy vấn bắt đầu bằng dấu gạch dưới'
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

# Closing the terminal on a gum menu must end hostbook and gum and clear $STATE/tmp (#4).
# A closing terminal only HUPs the pane leader, which is exactly a plain kill-session: do not use end_ui here.
export HOSTBOOK_STATE=$T/close-state; "$S" _index >/dev/null
close_check(){
  tmux -L "$socket" list-panes -a -F '#{pane_pid}' >> "$T/pgids"
  tmux -L "$socket" kill-session -t ui
  leak_check
  if [ -n "$(ls "$HOSTBOOK_STATE/tmp" 2>/dev/null)" ]; then ls "$HOSTBOOK_STATE/tmp"; echo "FAIL TUI: đóng terminal ở $1 còn file tạm"; exit 1; fi
  printf 'ok    TUI đóng terminal ở %s: không còn process, đã xoá file tạm\n' "$1"
}
start 100
wait_for booking-api
tmux -L "$socket" send-keys -t ui Down Right   # the group row comes first
wait_for 'SSH vào server'
close_check 'menu server'
start 100 settings
wait_for 'Kiểu danh sách'
close_check 'Giao diện'

# Cursor moves must not wait for a hostbook boot (#1, ~100 ms each), and → still acts on the focused row.
export HOSTBOOK_DIR=$T/many/hosts HOSTBOOK_STATE="$T/many/it's state" HOSTBOOK_SSH_CONFIG=$T/many/ssh_config
mkdir -p "$HOSTBOOK_DIR"; printf 'Include %s/*.conf\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
for i in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20; do printf 'Host many%s\n  HostName 127.0.0.1\n' "$i"; done > "$HOSTBOOK_DIR/many.conf"
mkdir -p "$HOSTBOOK_DIR/secrets"; for f in vault.pub vault.key.age vault.recovery.age; do printf 'fixture\n' > "$HOSTBOOK_DIR/secrets/$f"; done
git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
"$S" _index >/dev/null
printf 'list = compact\n' > "$XDG_CONFIG_HOME/hostbook/config"
start 100
wait_for many01
sleep 1   # let the first focus and preview settle
ms=$(perl -MTime::HiRes=time,sleep -e '
  sub row { my @l = split /\n/, `tmux -L $ARGV[0] capture-pane -p -t ui`; for (0..$#l) { return $_ if $l[$_] =~ /^│ ▌/ } -1 }
  my $want = row() + 10; my $s = time; system("tmux", "-L", $ARGV[0], "send-keys", "-t", "ui", ("Down") x 10);
  while (time - $s < 5) { if (row() == $want) { printf "%.0f", (time - $s) * 1000; exit } sleep .005 } print 9999' "$socket")
if [ "$ms" -ge 300 ]; then cat "$T/screen"; echo "FAIL TUI: 10 × ↓ mất $ms ms (cần < 300 ms)"; exit 1; fi
tmux -L "$socket" send-keys -t ui Tab Right   # one row selected, focus on the next: → acts on the focused row
wait_for 'SSH vào server'
grep -q many12 "$T/screen" || { cat "$T/screen"; echo 'FAIL TUI: → không mở server đang trỏ'; exit 1; }
end_ui
printf 'ok    TUI 10 × ↓ trong %s ms, → mở server đang trỏ khi có chọn bằng tab\n' "$ms"

# Opening the home screen with no remote and no change must not reload the list (#6): count _list callbacks.
# The initial list is piped in by the process itself, so any _list call here comes from a sync notification or a resize.
# The state dir above holds a space and an apostrophe: the resize scripts must quote it.
sed 's|^  _list) cmd_list;;|  _list) echo x >> "$HB_LIST_LOG"; cmd_list;;|; s|_bg_sync) cmd_bg_sync;;|_bg_sync) echo x >> "$HB_SYNC_LOG"; cmd_bg_sync;;|' "$S" > "$T/hostbook-count"
grep -q HB_LIST_LOG "$T/hostbook-count" && grep -q HB_SYNC_LOG "$T/hostbook-count" || { echo 'FAIL TUI: không gắn được bộ đếm _list'; exit 1; }
export HB_LIST_LOG=$T/list.log HB_SYNC_LOG=$T/sync.log; : > "$HB_LIST_LOG"; : > "$HB_SYNC_LOG"
S_REAL=$S; S=$T/hostbook-count
start 100
wait_for many01
i=0; until [ -s "$HB_SYNC_LOG" ]; do sleep .1; i=$((i+1)); [ "$i" -lt 50 ] || { echo 'FAIL TUI: _bg_sync không chạy'; exit 1; }; done
i=0; while ps -axo command= | grep -q 'hostbook-count _bg_sync$'; do sleep .1; i=$((i+1)); [ "$i" -lt 100 ] || { echo 'FAIL TUI: _bg_sync không kết thúc'; exit 1; }; done
sleep .3   # a control-port message sent at its end would arrive by now
[ ! -s "$HB_LIST_LOG" ] || { echo "FAIL TUI: mở màn hình không thay đổi gì nhưng _list chạy $(wc -l < "$HB_LIST_LOG") lần"; exit 1; }
# The footer is not allowed to write the current row back (a stale write would make → open the wrong server).
foot=$(ls -t "$HOSTBOOK_STATE"/tmp/*/foot | sed -n 1p)
if grep -E '>.*current' "$foot" >/dev/null; then cat "$foot"; echo 'FAIL TUI: script footer sau resize ghi lại current'; exit 1; fi
# The footer line after a resize must equal what _footer prints for that width (a reference pane of that width), also after the
# preview auto-hides (<36 cols), after a cursor move, after widening again and after two quick resizes.
footer_at(){ # width → _footer's line for a host row
  local line='' i=0
  tmux -L "$socket" new-session -d -x "$1" -y 5 -s ref "env HOME=$(printf %q "$HOME") XDG_CONFIG_HOME=$(printf %q "$XDG_CONFIG_HOME") HOSTBOOK_DIR=$(printf %q "$HOSTBOOK_DIR") HOSTBOOK_STATE=$(printf %q "$HOSTBOOK_STATE") HOSTBOOK_SSH_CONFIG=$(printf %q "$HOSTBOOK_SSH_CONFIG") /bin/bash $(printf %q "$S_REAL") _footer h:many02; sleep 30"
  while [ -z "$line" ] && [ "$i" -lt 50 ]; do line=$(tmux -L "$socket" capture-pane -p -t ref | sed -n 1p | sed 's/ *$//'); i=$((i+1)); sleep .1; done
  tmux -L "$socket" kill-session -t ref; printf '%s' "$line"
}
screen_footer(){ tmux -L "$socket" capture-pane -p -t ui | sed -e 's/^│ *//' -e 's/ *│.*$//' -e 's/ *$//'; }   # lines without the border; the footer is one of them
wait_footer(){ # width: poll until a whole line equals the reference footer, then check it stays
  local want i=0; want=$(footer_at "$1"); [ -n "$want" ] || { echo 'FAIL TUI: không lấy được footer mẫu'; exit 1; }
  until screen_footer | grep -qxF -- "$want"; do i=$((i+1)); [ "$i" -lt 60 ] || { tmux -L "$socket" capture-pane -p -t ui; echo "FAIL TUI: footer ở $1 cột khác _footer: '$want'"; exit 1; }; sleep .1; done
  sleep .7; screen_footer | grep -qxF -- "$want" || { tmux -L "$socket" capture-pane -p -t ui; echo "FAIL TUI: footer ở $1 cột bị ghi đè bằng footer cũ"; exit 1; }
}
tmux -L "$socket" resize-window -t ui -x 30
tmux -L "$socket" send-keys -t ui Down
wait_footer 30
tmux -L "$socket" resize-window -t ui -x 100
tmux -L "$socket" send-keys -t ui Down
wait_footer 100
tmux -L "$socket" resize-window -t ui -x 50; tmux -L "$socket" resize-window -t ui -x 30
wait_footer 30
n=$(wc -l < "$HB_LIST_LOG" | tr -d ' ')
[ "$n" -le 4 ] || { echo "FAIL TUI: 4 lần resize reload _list $n lần"; exit 1; }
S=$S_REAL
end_ui
printf 'ok    TUI mở màn hình không reload; footer đúng bề rộng sau resize, thu nhỏ, mở rộng và resize liên tiếp\n'

# Fresh install: apply the suggested light theme only after the fzf list has loaded.
for width in 60 100 200; do
 export HOME=$T/first-$width/home XDG_CONFIG_HOME=$T/first-$width/config HOSTBOOK_DIR=$T/first-$width/hosts HOSTBOOK_STATE=$T/first-$width/state HOSTBOOK_SSH_CONFIG=$T/first-$width/ssh_config
 export COLORFGBG='0;15'
 mkdir -p "$HOME"
 start "$width"
 wait_for 'Choose a language'   # the first screen picks the language; Tiếng Việt is preselected from HOSTBOOK_LANG
 tmux -L "$socket" send-keys -t ui Enter
 wait_for 'Bắt đầu thế nào?'
 grep -qx 'lang = vi' "$XDG_CONFIG_HOME/hostbook/config"
 tmux -L "$socket" send-keys -t ui Down Enter
 wait_for 'Mật khẩu chính'
 tmux -L "$socket" send-keys -t ui 'synthetic-password' Enter
 wait_for 'Nhập lại'
 tmux -L "$socket" send-keys -t ui 'synthetic-password' Enter
 wait_for 'Mã khôi phục'
 tmux -L "$socket" send-keys -t ui Enter
 wait_for 'Kết nối kho ngay'
 tmux -L "$socket" send-keys -t ui n   # confirm screens: y/n or ↑↓ enter
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
