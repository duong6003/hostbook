#!/usr/bin/env bash
# Configuration/UI tests use an isolated HOME and sample data; leave the real repository, keychain and agent untouched.
set -eu
unset NO_COLOR
HERE=$(cd "$(dirname "$0")" && pwd)
S=$HERE/../hostbook
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state
export HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR="$HERE/../lang"   # the library is sourced from a temporary copy: tell it where the texts are
mkdir -p "$HOME" "$HOSTBOOK_DIR"
sed '/^# ---------- entry point ----------/,$d' "$S" > "$T/lib"
. "$T/lib"
SELF=$S
trap 'rm -rf "$T"' EXIT
pass=0
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else printf 'FAIL  %s\n' "$1"; exit 1; fi; }
# macOS tr may keep reading indefinitely if the parent process ignores SIGPIPE (CI).
python3 - "$T/lib" <<'PYTEST'
import os,signal,subprocess,sys
p=subprocess.Popen(['/bin/bash','-c','trap "" PIPE; . "$1"; recovery_new','test',sys.argv[1]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
try:
 out,_=p.communicate(timeout=3)
except subprocess.TimeoutExpired:
 os.killpg(p.pid,signal.SIGKILL); p.communicate()
 raise AssertionError('tạo mã khôi phục bị treo khi SIGPIPE bị bỏ qua')
assert p.returncode==0 and len(out)==20 and all(chr(c) in '0123456789ABCDEFGHJKMNPQRSTVWXYZ' for c in out),out
PYTEST
check 'mã khôi phục kết thúc cả khi SIGPIPE bị bỏ qua' 'true'
# Keep the existing UI while inheriting the terminal's background.
check 'fzf kế thừa nền terminal trên mọi vùng chung' 'case "$(fzf_colors)" in *,bg:-1,preview-bg:-1,gutter:-1,*) true;; *) false;; esac'
# Missing config, unsafe values and updates must not break defaults or execute content.
check 'cấu hình mới mặc định là thẻ' '[ "$(cfg list cards)" = cards ]'
cfg_set list minimal
printf '# giữ chú thích\npreview = bottom\n' >> "$CFG"
cfg_set list cards
check 'ghi cấu hình giữ các mục và chú thích khác' '[ "$(cfg list compact)" = cards ] && [ "$(cfg preview right)" = bottom ] && grep -q "# giữ chú thích" "$CFG"'
printf 'list = broken\nconnect_timeout = 0\nkey_ttl = 30m\n' > "$CFG"; cfg_load   # config is parsed once per process
check 'giá trị sai dùng mặc định và báo số dòng' '[ "$(cfg list cards)" = cards ] && [ "$(cfg connect_timeout 15)" = 15 ] && cfg_errors > "$T/errors" && grep -q "dòng 1" "$T/errors" && grep -q "dòng 2" "$T/errors"'
check 'thời gian TTL đổi phút sang giây' '[ "$(duration "$(cfg key_ttl 8h)")" = 1800 ]'
check 'không nhận tên theme vượt thư mục' '! cfg_valid theme ../outside'
check 'không ghi giá trị sai' '! cfg_set list broken && [ "$(cfg list cards)" = cards ]'
theme_builtin hostbook > "$T/tab-colors.theme"
printf 'accent = 212\t141\n' >> "$T/tab-colors.theme"
check 'theme không nhận nhiều màu cho một thuộc tính' '! theme_check "$T/tab-colors.theme" 2>/dev/null'
cfg_set icons nerd
apply_theme
check 'Nerd Font là ký tự UTF-8 trên bash 3.2' '[ "$(printf %s "$ICON_GROUP" | od -An -tx1 | tr -d " \n")" = ef88b3 ]'
cfg_set icons unicode
apply_theme
for theme in hostbook dracula nord gruvbox solarized-dark solarized-light light high-contrast terminal; do
 theme_builtin "$theme" > "$T/theme"
 check "theme $theme hợp lệ" 'theme_check "$T/theme"'
done
mkdir -p "$CONF/themes"; theme_builtin hostbook > "$CONF/themes/custom.theme"
printf 'accent = xanh\n' >> "$CONF/themes/custom.theme"
check 'theme sai chỉ đúng dòng' '! theme_check "$CONF/themes/custom.theme" 2> "$T/error" && grep -q "dòng 12" "$T/error"'
check 'màu ngoài 0–255 bị từ chối' '! valid_color 999'
check 'thời gian quá lớn bị từ chối' '! cfg_valid key_ttl 18446744073709551617h'
cfg_set theme custom
apply_theme
check 'theme sai trở về màu mặc định' '[ "$TH_ACCENT" = 212 ]'
export NO_COLOR=1
apply_theme
check 'NO_COLOR bỏ màu 256 và RGB' 'case "$CP$CU$CD$CG$CR$CY$(fzf_colors)" in *"38;5"*|*"38;2"*) false;; *) true;; esac'
unset NO_COLOR
cfg_set theme hostbook
apply_theme
check 'ANSI đổi sang mã màu cho gum và tmux' '[ "$(gum_color bright-red)" = 9 ] && [ "$(tmux_color bright-yellow)" = colour11 ]'
check 'RGB và ANSI chuyển thành escape hợp lệ' '[ "$(esc_fg "#ff0080")" = "$E[38;2;255;0;128m" ] && [ "$(esc_fg bright-red)" = "$E[91m" ]'
theme_builtin hostbook | sed 's/ = /=/' > "$CONF/themes/custom.theme"
cfg_set theme custom
apply_theme
check 'theme tự tạo không phụ thuộc khoảng trắng' '[ "$TH_ACCENT" = 212 ]'
# Existing install stays compact without prompting; newly created installs retain cards.
rm -f "$CFG"
mkdir -p "$DIR/.git"
cfg_init
check 'nâng cấp giữ kiểu compact' '[ "$(cfg list cards)" = compact ]'
printf 'h:booking-api\tbooking-api\tbooking\t10.0.1.11\tubuntu\tbackend cache worker prod\tMô tả dài cho máy chủ chính\tkey-prod\t2h\n' > "$T/rows"
printf 'g:booking\tbooking\t\t\t\t\tnhóm · 2 server · chia ô\t\tbooking-api booking-db\n' >> "$T/rows"
for style in compact minimal cards; do
 for width in 30 60 100 200; do
  list_rows "$style" "$width" < "$T/rows" > "$T/out"
  python3 - "$T/out" "$style" "$width" <<'PY'
import re,sys
s=open(sys.argv[1]).read(); style=sys.argv[2]; width=int(sys.argv[3])
rows=s.split('\0') if style=='cards' else s.splitlines()
assert (s.endswith('\0')) == (style=='cards')
for row in filter(None,rows):
 fields=row.split('\t'); assert fields[0] in ('h:booking-api','g:booking'),fields
 for line in re.sub(r'\x1b\[[0-9;]*m','',fields[1]).splitlines(): assert len(line)<=width,(len(line),width,line)
assert '#prod' in s and '@ubuntu' in s and 'secret:key-prod' in s
assert 'prod' in re.sub(r'\x1b\[[0-9;]*m','',rows[0].split('\t')[1])
PY
  check "$style: đúng khoá, bộ lọc và bề rộng $width" 'true'
  opts=(--ansi); [ "$style" != cards ] || opts+=(--read0)
  for query in '#prod' '@ubuntu' 'secret:key-prod'; do
    check "$style: fzf tìm $query trong dữ liệu ẩn" '[ "$(fzf "${opts[@]}" --filter "$query" --delimiter="$TAB" --with-nth=2.. --accept-nth=1 < "$T/out")" = h:booking-api ]'
  done
 done
done
footer=$(COLUMNS=100 cmd_footer h:booking-api | sed $'s/\033\\[[0-9;]*m//g')
check 'chân màn hình nằm trong cột danh sách' '[ ${#footer} -le 54 ]'
printf 'h:long\tserver-with-an-extremely-long-name-that-fills-the-entire-row\tp\t10.0.0.1\tu\tbackend cache worker prod\tdesc\ts\t1h\n' > "$T/long"
for style in cards compact minimal; do
 list_rows "$style" 30 < "$T/long" | tr '\0' '\n' | cut -f2 > "$T/long-view"
 check "$style: tên dài vẫn thấy prod" 'grep -q prod "$T/long-view"'
done
for preset in cards compact minimal dracula nord light high-contrast; do
 for width in 40 80; do
  check "demo $preset ở $width cột" 'FZF_PREVIEW_COLUMNS=$width "$S" _demo "preset=$preset" > "$T/demo" && grep -q booking "$T/demo"'
  check 'demo chỉ hiện dữ liệu trình bày và không sửa config' '! grep -q "secret:key-prod" "$T/demo" && [ "$(cfg list cards)" = compact ] && { case $preset in compact|nord|high-contrast) true;; *) grep -q "10.0.1.11" "$T/demo";; esac; }'

 done
done
cfg_set list cards
FZF_PREVIEW_COLUMNS=80 "$S" _demo separator=on > "$T/with-gap"
FZF_PREVIEW_COLUMNS=80 "$S" _demo separator=off > "$T/without-gap"
check 'xem trước thể hiện lựa chọn đường kẻ' '! cmp -s "$T/with-gap" "$T/without-gap"'
cfg_sum=$(cksum < "$CFG"); best=99999   # best of 3: one slow sample on a shared CI runner must not fail the suite
for i in 1 2 3; do t0=$(perl -MTime::HiRes=time -e 'print int(time*1000)'); FZF_PREVIEW_COLUMNS=100 "$S" _demo preset=compact >/dev/null
  t1=$(perl -MTime::HiRes=time -e 'print int(time*1000)'); [ $((t1-t0)) -lt $best ] && best=$((t1-t0)); done
check "demo < 200 ms (tốt nhất $best ms) và không ghi config" '[ $best -lt 200 ] && [ "$(cksum < "$CFG")" = "$cfg_sum" ]'
out_plain=$(FZF_PREVIEW_COLUMNS=100 "$S" _demo preset=cards); out_forced=$(CLICOLOR_FORCE=1 FZF_PREVIEW_COLUMNS=100 "$S" _demo preset=cards)
check "CLICOLOR_FORCE giữ đường gum có màu" '[ "$out_plain" != "$out_forced" ]'
printf '\n%s kiểm tra UI đạt\n'  "$pass"
