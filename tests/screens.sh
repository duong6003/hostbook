#!/usr/bin/env bash
# Screen-system checks S1-S11 (docs/design/screens.md, "Rules the tests check"): one screen of each type is opened at
# 60, 100 and 200 columns in vi, and again in en, in private tmux servers with isolated data. Each language x width pair
# is a worker with its own sandbox, run in parallel, so the whole suite takes about as long as one worker.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
command -v tmux >/dev/null || { echo 'Bỏ qua screens: chưa có tmux'; exit 0; }
command -v python3 >/dev/null || { echo 'Bỏ qua screens: chưa có python3'; exit 0; }
HERE=$(cd "$(dirname "$0")" && pwd); S=$HERE/../hostbook; LANGD=$HERE/../lang
T=$(mktemp -d); N=$$
ARTIFACTS=${HOSTBOOK_TEST_ARTIFACTS:-$T/captures}; mkdir -p "$ARTIFACTS"
unset FZF_DEFAULT_OPTS FZF_DEFAULT_COMMAND NO_COLOR COLORFGBG

# --- teardown, as in tests/tui.sh: kill each pane's process group first, then check nothing is left (#3)
end_ui(){ # socket
  local p groups=''
  for p in $(tmux -L "$1" list-panes -a -F '#{pane_pid}' 2>/dev/null); do
    case $p in ''|*[!0-9]*|0|1) continue;; esac
    echo "$p" >> "$T/pgids"; groups="$groups $p"; kill -TERM -- "-$p" 2>/dev/null || :
  done
  sleep .2
  for p in $groups; do kill -KILL -- "-$p" 2>/dev/null || :; done
  tmux -L "$1" kill-server 2>/dev/null || :
}
leak_check(){
  local i=0 left
  [ -f "$T/pgids" ] || return 0
  while :; do
    left=$(ps -axo pgid=,pid=,command= | awk -v f="$T/pgids" 'BEGIN{while((getline l<f)>0)g[l]=1} ($1 in g)')
    [ -z "$left" ] && return 0
    i=$((i+1)); [ "$i" -lt 50 ] || { printf '%s\n' "$left"; echo 'FAIL screens: process còn sót sau khi đóng session'
      kill -KILL $(printf '%s\n' "$left" | awk '{print $2}') 2>/dev/null || :; return 1; }
    sleep .1
  done
}
cleanup(){
  local s i=0
  for s in "$T"/*/socket; do [ -f "$s" ] && end_ui "$(cat "$s")"; done
  while ! rm -rf "$T" 2>/dev/null; do i=$((i+1)); [ "$i" -lt 30 ] || return 1; sleep .1; done
}
trap cleanup EXIT

# --- the theme's error colour, as the app computes it (S6)
sed '/^# ---------- entry point ----------/,$d' "$S" > "$T/lib"
CR=$(HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR=$LANGD HOME=$T XDG_CONFIG_HOME=$T/none HOSTBOOK_STATE=$T/lib-state bash -c ". '$T/lib'; SELF='$S'; apply_theme; printf '%s' \"\$CR\"")
[ -n "$CR" ] || { echo 'FAIL screens: không lấy được màu lỗi của theme'; exit 1; }

# S11, part 1: both language files define the same keys
if ! diff <(sed -n 's/^\(L_[a-z_0-9]*\)=.*/\1/p' "$LANGD/vi.sh" | sort) <(sed -n 's/^\(L_[a-z_0-9]*\)=.*/\1/p' "$LANGD/en.sh" | sort) > "$T/keydiff"; then
  cat "$T/keydiff"; echo 'FAIL S11: lang/vi.sh và lang/en.sh khác nhau về danh sách key'; exit 1
fi
echo 'ok    S11 mọi key của lang/vi.sh có trong lang/en.sh và ngược lại'

# One worker: language $1, width $2. Everything it prints goes to its own log; a failure makes it exit 1.
worker(){
  local lang=$1 width=$2 bd=$3 W=$T/$1-$2-$3 socket=hb-sc-$N-$1-$2-$3 tag="$1 $2 cột border=$3"
  mkdir -p "$W/home/.ssh" "$W/hosts" "$W/config/hostbook" "$W/bin" "$W/state" "$W/kc"
  echo "$socket" > "$W/socket"
  [ "$bd" = none ] && printf 'border = none\n' > "$W/config/hostbook/config"
  export HOME=$W/home XDG_CONFIG_HOME=$W/config HOSTBOOK_DIR=$W/hosts HOSTBOOK_STATE=$W/state HOSTBOOK_SSH_CONFIG=$W/ssh_config HOSTBOOK_KEYCHAIN=file:$W/kc/id
  export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
  # clipboard tools that touch a file inside the sandbox, never the real clipboard
  printf '#!/bin/sh\ncat "%s/clip" 2>/dev/null || exit 1\n' "$W" > "$W/bin/pbpaste"; printf '#!/bin/sh\ncat > "%s/clip"\n' "$W" > "$W/bin/pbcopy"
  local c; for c in wl-paste xclip xsel; do cp "$W/bin/pbpaste" "$W/bin/$c"; done; cp "$W/bin/pbcopy" "$W/bin/wl-copy"; chmod +x "$W"/bin/*
  export PATH=$W/bin:$PATH
  printf 'Include %s/*.conf\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
  printf '# tags: prod api\n# desc: API payments\nHost shop-api\n  HostName 10.20.1.11\n  User deploy\n# tags: web\nHost shop-web\n  HostName 10.20.1.12\n  User deploy\nHost dead-box\n  HostName 10.255.255.1\n  User deploy\n' > "$HOSTBOOK_DIR/shop.conf"
  mkdir -p "$HOSTBOOK_DIR/secrets"; for c in vault.pub vault.key.age vault.recovery.age; do printf 'fixture\n' > "$HOSTBOOK_DIR/secrets/$c"; done
  git -C "$HOSTBOOK_DIR" init -q; git -C "$HOSTBOOK_DIR" config user.name test; git -C "$HOSTBOOK_DIR" config user.email test@example.com
  git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm fixture
  "$S" _index >/dev/null
  # two app changes, so that Undo has something real to offer
  HOSTBOOK_LANG=vi "$S" _rename shop-web shop-www >/dev/null 2>&1; HOSTBOOK_LANG=vi "$S" _rename shop-www shop-web >/dev/null 2>&1
  mkdir -p "$HOME/docs"; echo hi > "$HOME/note.txt"; echo x > "$HOME/.secretrc"   # file picker: a folder, a file, a hidden file
  . "$LANGD/vi.sh"; [ "$lang" = vi ] || . "$LANGD/$lang.sh"

  local out=$W/out cap=$W/cap
  : > "$out"
  say(){ printf '%s\n' "$*" >> "$out"; }
  die(){ cat "$cap" >> "$out" 2>/dev/null; say "FAIL $tag: $*"; end_ui "$socket"; echo 1 > "$W/failed"; exit 1; }
  has_rule(){ local i=0; while [ $i -lt 30 ]; do snap; grep -qF -- "${1:0:20}" "$cap" && return 0; sleep .1; i=$((i+1)); done; return 1; }   # a rule may wrap on a narrow screen: its first words are enough
  # fmt: a lang value printed as the screen prints it (printf format with arguments)
  fmt(){ local f=$1; shift; printf -- "$f" "$@"; }
  start(){ # [command args]
    local cmd; printf -v cmd 'env HOSTBOOK_LANG=%s HOSTBOOK_LANG_DIR=%q HOME=%q XDG_CONFIG_HOME=%q HOSTBOOK_DIR=%q HOSTBOOK_STATE=%q HOSTBOOK_SSH_CONFIG=%q HOSTBOOK_KEYCHAIN=%q PATH=%q SHELL=/bin/bash /bin/bash %q %s' \
      "$lang" "$LANGD" "$HOME" "$XDG_CONFIG_HOME" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$HOSTBOOK_SSH_CONFIG" "$HOSTBOOK_KEYCHAIN" "$PATH" "$S" "${1:-}"
    tmux -L "$socket" new-session -d -x "$width" -y 36 -s ui "$cmd"
  }
  k(){ tmux -L "$socket" send-keys -t ui "$@"; }
  typ(){ tmux -L "$socket" send-keys -t ui -l -- "$1"; }
  snap(){ tmux -L "$socket" capture-pane -p -t ui > "$cap" 2>/dev/null || :; }
  wait_for(){ # text [seconds]: poll for a literal on screen
    local i=0 max=$(( ${2:-8} * 10 ))
    while [ "$i" -lt "$max" ]; do snap; grep -qF -- "$1" "$cap" && return 0; sleep .1; i=$((i+1)); done
    die "không thấy '$1'"
  }
  gone(){ # text: poll until a literal is no longer on screen
    local i=0
    while [ "$i" -lt 50 ]; do snap; grep -qF -- "$1" "$cap" || return 0; sleep .1; i=$((i+1)); done
    die "'$1' vẫn còn trên màn hình"
  }
  find_host(){ typ "$1"; wait_for '1/3'; }   # type into the search box until one server is left
  menu_pick(){ typ "$1"; sleep .4; k Enter; }   # filter a menu by the item's label, then enter
  # the checks every screen goes through. $1 = name, $2 = kind (normal | progress | report)
  check(){
    local name=$1 kind=${2:-normal} f=$ARTIFACTS/$lang-$width-$bd-$1.txt bad='' line first esc i=0
    # a loaded CI runner can catch fzf mid-redraw: wait (bounded) for the frame's first line before judging it
    while [ "$i" -lt 30 ]; do snap; case $(sed -n 1p "$cap") in *'hostbook ›'*) break;; esac; sleep .1; i=$((i+1)); done
    cp "$cap" "$f"
    first=$(sed -n 1p "$cap")
    case $first in *'hostbook ›'*) ;; *) bad="$bad S1 (dòng đầu: '$first')";; esac
    case $kind in
      progress) grep -qF 'ctrl-c' "$cap" || bad="$bad S2 (không có ctrl-c)";;
      report) grep -qF -- "$L_pause_back" "$cap" && grep -q 'esc' "$cap" || bad="$bad S2 (không có esc ở dòng cuối)";;
      *) esc=$(grep -F 'esc ' "$cap" | tail -1)
         case $esc in *"esc $L_key_cancel"*|*"esc $L_key_back"*) ;; *) bad="$bad S2 (footer thiếu 'esc' đúng ngôn ngữ: '$esc')";; esac;;
    esac
    if grep -qE 'navigate|submit|toggle|select •' "$cap"; then bad="$bad S2 (chữ tiếng Anh của gum)"; fi
    # S3: capture-pane -J joins lines the terminal wrapped, so a line wider than the screen shows up as too long
    tmux -L "$socket" capture-pane -p -J -t ui > "$W/joined" 2>/dev/null
    line=$(python3 -c 'import sys
w=int(sys.argv[2])
for i,l in enumerate(open(sys.argv[1],encoding="utf-8").read().split("\n"),1):
    if len(l.rstrip())>w: print("%d:%d" % (i,len(l.rstrip()))); break' "$W/joined" "$width")
    [ -z "$line" ] || bad="$bad S3 (dòng $line rộng hơn $width)"
    if grep -qE '/Users/|/home/' "$cap" || grep -qF -- "$HOME" "$cap"; then bad="$bad S7 (lộ đường dẫn thư mục home)"; fi
    if grep -qiE '\b(commit|push|pull|rebase)\b|\bHEAD\b' "$cap"; then bad="$bad S10 (chữ git trên màn hình)"; fi
    if grep -qF '‹L_' "$cap"; then bad="$bad S11 (thiếu key ngôn ngữ ‹L_…›)"; fi
    [ -z "$bad" ] || die "$name:$bad"
  }
  fin(){ end_ui "$socket"; }   # one screen done: end its session
  summary(){ say "ok    $tag: $1"; }

  # ---- action menu (→ on a server): S6 delete last and in the error colour
  start; wait_for shop-web; find_host shop-web; k Right; wait_for "$L_act_ssh"; check menu
  tmux -L "$socket" capture-pane -e -p -t ui > "$W/menu-e"
  grep -qF -- "${CR}$L_act_delete" "$W/menu-e" || die "S6: '$L_act_delete' không có màu lỗi của theme"
  snap; last=$(grep -nF "$L_act_delete" "$cap" | tail -1 | cut -d: -f1)
  after=$(sed -n "$((last+1)),\$p" "$cap" | sed '/^╰/,$d' | grep -v '^│ *│$' | grep -vF 'esc ' | grep -vF "$L_menu_search_recovery" | grep -vE '^(─)*$' | grep -c '[^ ]' || :)   # border=none has no ╰ line: the footer follows the list
  [ "$after" = 0 ] || die "S6: sau '$L_act_delete' còn $after dòng khác trong menu"
  # ---- danger, normal server (same menu)
  menu_pick "$L_act_delete"; wait_for "$(fmt "$L_delete_yes" shop-web)"; check danger-normal; fin
  summary 'menu → (S1 S2 S3 S6 S7 S10), xác nhận xoá server thường'

  # ---- single field: rename on a PROD server. S4 rule before typing, S5 one ✗ in place
  start; wait_for shop-api; find_host shop-api; k Right; wait_for "$L_act_ssh"; menu_pick "$L_act_rename"; wait_for "$L_rename_label"
  has_rule "$L_rename_rule" || die "S4: không thấy quy tắc '$L_rename_rule' trước khi gõ"
  check rename-field
  k C-u; typ 'bad name'; wait_for "$ICON_ERR_CHECK"; typ '!'; sleep .5; snap
  [ "$(grep -c '✗' "$cap")" = 1 ] || die "S5: cần đúng 1 dòng ✗, thấy $(grep -c '✗' "$cap")"
  grep -qF 'bad name!' "$cap" || die 'S5: ô nhập mất chữ đã gõ'
  check rename-error; fin
  summary 'ô nhập đổi tên (S1 S2 S3 S4 S5 S7 S10)'

  # ---- multi-step: ^a, address then step 2
  start; wait_for shop-api; k C-a; wait_for "$L_add_addr_label"
  has_rule "$L_add_addr_rule" || die "S4: bước 1 không hiện quy tắc '$L_add_addr_rule'"
  check add-step1; typ 'deploy@10.0.9.9'; sleep .3; k Enter; wait_for "$(fmt "$L_add_step" 2 4)"; check add-step2; fin
  summary 'thêm server bước 1 và 2 (S1 S2 S3 S4 S7 S10)'

  # ---- confirm: undo from Tools
  start; wait_for shop-api; k C-o; wait_for "$L_tools_title"; menu_pick "$L_act_undo"; wait_for "$L_undo_yes"; check confirm-undo; fin
  summary 'xác nhận hoàn tác (S1 S2 S3 S7 S10)'

  # ---- danger on a PROD server: the name must be typed. S8
  start; wait_for shop-api; find_host shop-api; k Right; wait_for "$L_act_ssh"; menu_pick "$L_act_delete"
  wait_for "$(fmt "$L_delete_type_label" shop-api)"
  has_rule "$(fmt "$L_delete_type_rule" shop-api)" || die 'S4: không thấy quy tắc gõ tên PROD trước khi gõ'
  check danger-prod
  k Enter; sleep .8; snap
  grep -qF -- "$(fmt "$L_delete_type_label" shop-api)" "$cap" || die 'S8: enter trên ô trống làm màn hình xoá biến mất'
  grep -q 'Host shop-api' "$HOSTBOOK_DIR/shop.conf" && grep -q '^shop-api' "$HOSTBOOK_STATE/index.tsv" || die 'S8: enter trên ô trống đã xoá server PROD'
  fin
  summary 'xoá server PROD cần gõ tên, enter trên ô trống giữ server (S1 S2 S3 S4 S7 S8 S10)'

  # ---- progress, then the result menu: an unreachable host
  start; wait_for dead-box; find_host dead-box; k Right; wait_for "$L_act_ssh"; menu_pick "$L_act_check"
  wait_for 'ctrl-c' 15; sleep 1.2; check progress progress
  grep -qE '[0-9]+s */ *[^0-9]*[0-9]+s' "$cap" || die 'S9: thiếu thời gian đã chạy / giới hạn'
  wait_for "$L_act_chk_done" 30; check result; fin
  summary 'kiểm tra kết nối: đang chạy (S9) và kết quả (S1 S2 S3 S7 S10)'

  # ---- report: Tools → this machine (doctor)
  start; wait_for shop-api; k C-o; wait_for "$L_tools_title"; menu_pick "$L_act_doctor"; wait_for "$L_pause_back" 20; check report report
  k Escape; wait_for "$L_tools_title" 5; fin
  summary 'báo cáo kiểm tra máy, esc quay lại (S1 S2 S3 S7 S10)'

  # ---- password prompt (gum input) and the tag picker (fzf multi): no English gum help, esc offered
  start; wait_for shop-api; k C-o; wait_for "$L_tools_title"; menu_pick "$L_act_secrets"; wait_for "$L_login_add_vault"; menu_pick "$L_login_add_vault"; wait_for "$L_act_key_file"; k Down Down Down Enter
  wait_for "esc $L_key_cancel"; check password-prompt; fin
  start; wait_for shop-api; k C-a; wait_for "$L_add_addr_label"; typ 'deploy@10.0.9.9'; k Enter; wait_for "$(fmt "$L_add_step" 2 4)"; typ newbox; k Enter; wait_for "$(fmt "$L_add_step" 3 4)"; k Enter; wait_for "$(fmt "$L_add_step" 4 4)"
  wait_for "$L_act_no_login"; menu_pick "$L_act_no_login"; wait_for "$L_act_add_edit"; menu_pick "$L_act_add_edit"
  wait_for "$L_add_edit_head"; wait_for "$L_f_jump"; typ "$L_f_tags"; gone "$L_f_jump"; k Enter
  wait_for "$L_tags_other"; wait_for "$L_tags_head"; check tag-picker; fin
  summary 'ô mật khẩu và chọn tag (S1 S2 S3 S7 S10)'

  local overview_input
  printf '%s\n' $'summary-private\t1' $'summary-shared\t2\treceive-only\trefs/heads/main' \
    $'host\tprivate\tdb\t1' $'host\tshared\tdb\t2' $'host\tshared\tonly-shared\t3' > "$W/stores.tsv"
  printf -v overview_input '_store_overview_screen < %q' "$W/stores.tsv"
  start "$overview_input"
  wait_for "$L_store_overview_title"
  wait_for "db  ·  $L_store_overview_private_label"
  wait_for "db  ·  $L_store_overview_shared_label"
  typ db; gone only-shared
  check stores
  k Escape; fin
  summary 'tìm host trùng tên theo kho (S1 S2 S3 S7 S10)'
  start '_store_overview_host shared db 192.0.2.90 fixture 22 "" transfer'
  wait_for "$L_store_overview_connect"
  wait_for "fixture@192.0.2.90:22"
  check store-host
  k Escape; fin

  # ---- file picker: send a file
  start; wait_for shop-api; find_host shop-api; k Right; wait_for "$L_act_ssh"; menu_pick "$L_act_send"; wait_for note.txt; check file-picker
  if grep -qF .secretrc "$cap"; then die 'file picker hiện file ẩn'; fi
  fin
  summary 'chọn file (S1 S2 S3 S7 S10, file ẩn không hiện)'
  return 0
}

ICON_ERR_CHECK='✗'
pids=''
# border = none (fzf's minimal style) must keep the breadcrumb: vi and en at two widths
combos='vi:60:rounded vi:100:rounded vi:200:rounded en:60:rounded en:100:rounded en:200:rounded vi:60:none en:100:none'
for c in $combos; do
  IFS=: read -r lang width bd <<< "$c"
  ( worker "$lang" "$width" "$bd" ) > "$T/$lang-$width-$bd.log" 2>&1 &
  pids="$pids $!"
done
rc=0
for p in $pids; do wait "$p" || rc=1; done
for c in $combos; do
  IFS=: read -r lang width bd <<< "$c"
  cat "$T/$lang-$width-$bd/out" 2>/dev/null; sed 's/^/log: /' "$T/$lang-$width-$bd.log" | grep . || :
done
# S11, part 2: no capture shows a missing key (each capture was checked as it was taken; this catches a leftover)
if grep -l '‹L_' "$ARTIFACTS"/*.txt 2>/dev/null; then echo 'FAIL S11: có màn hình hiện key ngôn ngữ bị thiếu'; rc=1; fi
leak_check || rc=1
if [ "$rc" = 0 ]; then echo 'ok    screens: không còn process sau khi đóng session'; else echo 'FAIL screens'; fi
exit $rc
