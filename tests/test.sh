#!/usr/bin/env bash
# Test hostbook core functionality with temporary data (leave the real ~/ssh-hosts untouched): bash tests/test.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh_config
S="$HERE/../hostbook"; pass=0; fail=0
# Fully isolated: leave the real Keychain, ssh-agent, ~/.config and ~/.ssh untouched (temporary HOME)
export HOME="$T/home"; mkdir -p "$HOME/.ssh"
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
export HOSTBOOK_KEYCHAIN="file:$T/keychain/id" HOSTBOOK_MASTER="mat-khau-chinh-thu" XDG_CONFIG_HOME="$T/config"
AG=$(mktemp -d /tmp/hbt-XXXXXX); eval "$(ssh-agent -a "$AG/a.sock")" >/dev/null
[ "${SSH_AUTH_SOCK:-}" = "$AG/a.sock" ] || { echo "Không dựng được ssh-agent riêng cho kiểm thử, dừng"; exit 1; }
trap 'ssh-agent -k >/dev/null 2>&1; rm -rf "$T" "$AG"' EXIT
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
strip(){ sed $'s/\033\\[[0-9;]*m//g'; }

mkdir -p "$HOSTBOOK_DIR"; printf 'Include %s/*.conf\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
cat > "$HOSTBOOK_DIR/project-a.conf" <<'EOF'
# tags: prod bastion
# desc: Cổng vào project A
Host a-bastion
  HostName 203.0.113.10
  ProxyJump none

# tags: prod web hcm
# desc: Nginx chính
Host a-web1
  HostName 10.0.1.11

# tags: prod web hcm
Host a-web2
  HostName 10.0.1.12

# tags: prod db postgres
Host a-db
  HostName 10.0.1.20

Host a-*
  User ubuntu
  ProxyJump a-bastion
  IdentityFile ~/.ssh/id_rsa
EOF
cat > "$HOSTBOOK_DIR/project-b.conf" <<'EOF'
# tags: prod api
Host b-api
  HostName 10.2.0.5
  User deploy
  Port 2222

# tags: staging web api
Host b-staging
  HostName 10.2.0.9
  User deploy
EOF
git -C "$HOSTBOOK_DIR" init -q && git -C "$HOSTBOOK_DIR" add -A && git -C "$HOSTBOOK_DIR" commit -qm init

bash -n "$S"; check "cú pháp bash" '[ $? -eq 0 ]'

idx=$("$S" _index)
row(){ printf '%s\n' "$idx" | awk -F'\t' -v n="$1" -v c="$2" '$1==n{print $c}'; }
check "đọc đủ 6 server, bỏ qua wildcard" '[ "$(printf "%s\n" "$idx" | grep -c .)" = 6 ]'
check "wildcard áp ProxyJump/User như ssh hiểu" '[ "$(row a-web1 6)" = a-bastion ] && [ "$(row a-web1 4)" = ubuntu ]'
check "ProxyJump none của bastion thành rỗng" '[ -z "$(row a-bastion 6)" ]'
check "đọc tags, desc, port" '[ "$(row a-web1 8)" = "prod web hcm" ] && [ "$(row a-web1 9)" = "Nginx chính" ] && [ "$(row b-api 5)" = 2222 ]'
check "server không có desc để trống" '[ -z "$(row a-web2 9)" ] && [ "$(row a-web2 8)" = "prod web hcm" ]'

printf '%s\tb-api\n%s\tb-api\n' "$(date +%s)" "$(date +%s)" >> "$HOSTBOOK_STATE/history"
list=$("$S" _list | strip)
check "server dùng gần đây đứng đầu" '[ "$(printf "%s\n" "$list" | grep "^h:" | head -1 | cut -f1)" = h:b-api ]'
check "dòng có tag và user để tìm theo #tag, @user" 'printf "%s\n" "$list" | grep "^h:a-db" | grep -q "#postgres" && printf "%s\n" "$list" | grep "^h:b-api" | grep -q "@deploy"'
check "fzf: #prod web !b- ra đúng a-web1, a-web2" '[ "$(printf "%s\n" "$list" | fzf --filter "#prod web !b-" --delimiter="	" --with-nth=2.. --accept-nth=1 | sort | tr "\n" " ")" = "h:a-web1 h:a-web2 " ]'
check "fzf: @deploy ra b-api, b-staging" '[ "$(printf "%s\n" "$list" | fzf --filter "@deploy" --delimiter="	" --with-nth=2.. --accept-nth=1 | sort | tr "\n" " ")" = "h:b-api h:b-staging " ]'
check "fzf: ^b- neo đầu tên" '[ "$(printf "%s\n" "$list" | fzf --filter "^b-" --delimiter="	" --with-nth=2.. --accept-nth=1 | wc -l | tr -d " ")" = 2 ]'

prev=$("$S" _preview h:a-web1 | strip)
check "xem trước có PROD, ProxyJump, mô tả" 'printf "%s" "$prev" | grep -q "● PROD" && printf "%s" "$prev" | grep -q "ProxyJump a-bastion" && printf "%s" "$prev" | grep -q "Nginx chính"'

# Preview must do no network I/O: a fake nc records calls, and an unreachable TEST-NET address must return quickly
printf 'Host z-ghost\n  HostName 192.0.2.1\n  User root\n' > "$HOSTBOOK_DIR/project-z.conf"
mkdir -p "$T/fakebin"; printf '#!/bin/sh\necho "$@" >> "%s/nc.calls"\n' "$T" > "$T/fakebin/nc"; chmod +x "$T/fakebin/nc"
"$S" _preview h:z-ghost >/dev/null; t0=$(perl -MTime::HiRes=time -e 'print int(time*1000)')
pz=$(PATH="$T/fakebin:$PATH" "$S" _preview h:z-ghost | strip); t1=$(perl -MTime::HiRes=time -e 'print int(time*1000)')
check "xem trước server không với tới được: < 300 ms, không gọi nc" '[ $((t1-t0)) -lt 300 ] && [ ! -e "$T/nc.calls" ] && printf "%s" "$pz" | grep -q "HostName 192.0.2.1"'
check "xem trước chưa có kết quả kiểm tra: gợi ý Kiểm tra kết nối" 'printf "%s" "$pz" | grep -q "→ Kiểm tra kết nối"'
printf 'z-ghost\tfail\tkhông có phản hồi\na-web1\tok\tđăng nhập được\n' > "$HOSTBOOK_STATE/last-check.tsv"
check "xem trước hiện kết quả kiểm tra gần nhất" '"$S" _preview h:z-ghost | strip | grep -q "Kiểm tra lần cuối: không vào được (không có phản hồi) · 0 phút trước" && "$S" _preview h:a-web1 | strip | grep -q "Kiểm tra lần cuối: vào được · 0 phút trước"'
rm -f "$HOSTBOOK_STATE/last-check.tsv" "$HOSTBOOK_DIR/project-z.conf"

check "đọc địa chỉ user@ip:port" '[ "$("$S" _parse ops@10.3.0.50:2200 | tr "\n" " ")" = "ops 10.3.0.50 2200 " ]'
check "đọc địa chỉ chỉ có IP" '[ "$("$S" _parse 10.0.1.13 | tr "\n" " ")" = " 10.0.1.13  " ]'
check "từ chối địa chỉ có dấu cách / cổng sai" '! "$S" _parse "a b" >/dev/null && ! "$S" _parse "1.2.3.4:abc" >/dev/null'
sg(){ "$S" _suggest_host "$@"; }
fv(){ printf '%s\n' "$sug" | awk -F'\t' -v k="$1" '$1=="field" && $2==k {print $3}'; }
sug=$(sg 10.0.1.13)
check "gợi ý tiền tố tên từ server cùng dải, có lý do" '[ "$(fv prefix)" = a- ] && printf "%s\n" "$sug" | grep "^field	prefix" | grep -q "cùng dải 10.0.1.x"'
sug=$(sg 10.0.1.13 a-web3)
check "cùng dải + cùng tiền tố: tự chọn dự án, lý do có cả hai" 'printf "%s\n" "$sug" | grep -q "^auto	1" && printf "%s\n" "$sug" | grep "^project	project-a" | grep "●●●" | grep "cùng dải 10.0.1.x (3 server)" | grep -q "tên bắt đầu bằng a- (4 server)"'
check "có server giống nhất để sao chép thiết lập" 'printf "%s\n" "$sug" | grep -q "^peer	a-web[12]	"'
check "đoán user, máy trung gian (>= 80% server), chưa có secret" '[ "$(fv user)" = ubuntu ] && [ "$(fv jump)" = a-bastion ] && [ -z "$(fv secret)" ]'
check "tags: có ở >= 60% server của dự án + từ trong tên" '[ "$(fv tags)" = "prod web" ]'
sug=$(sg 10.2.0.77 b-worker "" 2201)
check "giữ cổng đã dán, đoán dự án và user theo server cùng dải" 'printf "%s\n" "$sug" | grep -q "^group	project-b" && [ "$(fv port)" = 2201 ] && [ "$(fv user)" = deploy ]'
sug=$(sg 172.16.0.5 lab1 root)
check "địa chỉ lạ: không đoán dự án, giữ user đã ghi" 'printf "%s\n" "$sug" | grep -q "^auto	0" && ! printf "%s\n" "$sug" | grep -q "^project" && [ "$(fv user)" = root ]'
sug=$(sg 10.0.9.9 zzz)
check "chỉ cùng dải /16: có gợi ý yếu nhưng không tự chọn" 'printf "%s\n" "$sug" | grep "^project	project-a" | grep -q "●○○" && printf "%s\n" "$sug" | grep -q "^auto	0"'
printf 'Host x1\n  HostName x1.booking.vn\nHost x2\n  HostName x2.booking.vn\nHost y1\n  HostName y1.abc.com.vn\n' > "$HOSTBOOK_DIR/dom.conf"
sug=$(sg api.booking.vn moi)
check "tên miền: nhận ra cùng đuôi" 'printf "%s\n" "$sug" | grep "^project	dom" | grep -q "cùng đuôi .booking.vn (2 server)"'
sug=$(sg z.def.com.vn moi)
check "com.vn không bị coi là tên miền chung" '! printf "%s\n" "$sug" | grep -q "^project"'
rm "$HOSTBOOK_DIR/dom.conf"; "$S" _index >/dev/null
sug=$(HOSTBOOK_DIR="$T/trong" HOSTBOOK_STATE="$T/trong-state" "$S" _suggest_host 1.2.3.4 x)
check "danh sách trống: không đoán dự án, user là user của máy" '! printf "%s\n" "$sug" | grep -q "^project" && [ "$(fv user)" = "$(id -un)" ]'

printf '# tags: prod web\nHost a-web3\n  HostName 10.0.1.13\n  User ubuntu\n  ProxyJump none\n  IdentityFile ~/.ssh/id_rsa\n' > "$T/blk"
"$S" _write project-a "$T/blk"
check "chèn khối mới trước khối wildcard" '[ "$(grep -n "Host a-web3" "$HOSTBOOK_DIR/project-a.conf" | cut -d: -f1)" -lt "$(grep -n "Host a-\*" "$HOSTBOOK_DIR/project-a.conf" | cut -d: -f1)" ]'
idx=$("$S" _index)
check "giá trị riêng thắng wildcard (ProxyJump none)" '[ -z "$(row a-web3 6)" ] && [ "$(row a-web3 3)" = 10.0.1.13 ]'
printf 'Host x\n  HostNmae 1.2.3.4\n' > "$HOSTBOOK_DIR/broken.conf"
check "phát hiện config lỗi kèm số dòng" '"$S" _validate | grep -q "broken.conf: line 2"'
rm "$HOSTBOOK_DIR/broken.conf"
check "config hợp lệ sau khi bỏ lỗi" '"$S" _validate'

check "gợi ý tên nhóm từ tag chung" '[ "$("$S" _suggest a-web1 a-web2)" = prod-web ]'
printf 'prod-web\tsync\ta-web1 a-web2\n' > "$HOSTBOOK_DIR/groups.tsv"
check "tránh trùng tên nhóm" '[ "$("$S" _suggest a-web1 a-web2)" = prod-web-2 ]'
check "không có tag chung thì lấy tên" '[ "$("$S" _suggest b-api a-db)" = b-api ] || [ -n "$("$S" _suggest b-api a-db)" ]'
check "nhóm hiện trong danh sách" '"$S" _list | strip | grep -q "^g:prod-web.*nhóm · 2 server · chạy đồng loạt"'

# ---- repository sync (use a bare repository in a temporary directory) ----
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "a-web3"
check "chưa có kho: header báo rõ" '"$S" _header | strip | grep -q "Chưa có kho"'
git init -q --bare "$T/kho.git"; git -C "$HOSTBOOK_DIR" remote add origin "$T/kho.git"
"$S" _sync_now
check "lần đầu: đẩy lên kho và đặt upstream" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = ok ] && git -C "$T/kho.git" log --format=%s | grep -q "^a-web3$" && git -C "$T/kho.git" log --format=%s | grep -q "Bật gộp theo từng server"'
git clone -q "$T/kho.git" "$T/other"; printf '\n# tags: prod search\nHost c-search1\n  HostName 10.3.0.40\n' > "$T/other/project-c.conf"
git -C "$T/other" add -A; git -C "$T/other" commit -qm "c-search1"; git -C "$T/other" push -q
"$S" _sync_now
check "máy khác thêm server: tự kéo về và báo tên" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = pulled ] && sed -n 2p "$HOSTBOOK_STATE/status" | grep -q c-search1'
check "header hiện server mới" '"$S" _header | strip | grep -q "↓ Server mới từ máy khác: c-search1"'
printf '\nHost a-web4\n  HostName 10.0.1.14\n' >> "$HOSTBOOK_DIR/project-a.conf"
"$S" _sync_now
check "sửa ngoài sshm: kiểm tra, commit, đẩy" '[ "$(git -C "$T/kho.git" log -1 --format=%s)" = "Sửa ngoài hostbook" ]'
printf 'Host bad\n  HostNmae 1\n' > "$HOSTBOOK_DIR/bad.conf"
"$S" _sync_now
check "file lỗi: không commit, header báo lỗi" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = invalid ] && [ -n "$(git -C "$HOSTBOOK_DIR" status --porcelain)" ] && "$S" _header | strip | grep -q "File lỗi.*bad.conf"'
rm "$HOSTBOOK_DIR/bad.conf"
git -C "$HOSTBOOK_DIR" remote set-url origin "$T/khong-co.git"
printf '\nHost a-web5\n  HostName 10.0.1.15\n' >> "$HOSTBOOK_DIR/project-a.conf"
"$S" _sync_now
check "mất kết nối kho: giữ thay đổi chờ đẩy" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = pending ] && "$S" _header | strip | grep -q "thay đổi chờ đẩy lên kho"'
git -C "$HOSTBOOK_DIR" remote set-url origin "$T/kho.git"
"$S" _sync_now
check "có lại kết nối: đẩy phần đang chờ" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = ok ] && git -C "$T/kho.git" log --format=%s | grep -q "Sửa ngoài hostbook"'

check "chân màn hình đổi khi chọn nhiều" 'FZF_SELECT_COUNT=3 "$S" _footer h:a-web1 | strip | grep -q "mở 3 server"'
check "chân màn hình cho nhóm" 'FZF_SELECT_COUNT=0 "$S" _footer g:prod-web | strip | grep -q "mở nhóm"'
check "chân màn hình tính sẵn khớp _footer (focus/multi không boot lại)" '( mkdir -p "$T/ft"; "$S" _footers "$T/ft" >/dev/null
  for t in h g s x; do [ "$(cat "$T/ft/f-$t")" = "$(FZF_SELECT_COUNT=0 "$S" _footer $t:a)" ] || exit 1; done
  for c in 3 12; do [ "$(printf "$(cat "$T/ft/f-m${#c}")" $c)" = "$(FZF_SELECT_COUNT=$c "$S" _footer h:a-web1)" ] || exit 1; done )'

# ---- regression tests for bugs found during review ----
printf 'webs\t\ta-web1 a-web2\n' >> "$HOSTBOOK_DIR/groups.tsv"
check "nhóm chưa có kiểu mở vẫn đếm đúng số server" '"$S" _list | strip | grep -q "^g:webs.*nhóm · 2 server · hỏi khi mở"'

cat > "$HOSTBOOK_DIR/wild.conf" <<'EOF'
Host w-one
  HostName 10.9.0.1
Host w-*
  User ops
  Port 2222
  ProxyJump a-bastion
EOF
blk=$("$S" _block w-new 10.9.0.2 ops 22 "" "~/.ssh/id_rsa" "" "")
check "chọn 'không' máy trung gian: ghi ProxyJump none khi wildcard có ProxyJump" 'printf "%s" "$blk" | grep -q "ProxyJump none"'
check "cổng 22 được ghi rõ khi wildcard đặt cổng khác" 'printf "%s" "$blk" | grep -q "Port 22"'
printf '%s\n' "$blk" > "$T/blk2"; "$S" _write wild "$T/blk2"; idx=$("$S" _index)
check "server mới thật sự không đi qua máy trung gian, đúng cổng" '[ -z "$(row w-new 6)" ] && [ "$(row w-new 5)" = 22 ]'
rm "$HOSTBOOK_DIR/wild.conf"

printf 'Host bad;touch${IFS}/tmp/hb-pwn\n  HostName 10.0.0.1\n' > "$HOSTBOOK_DIR/evil.conf"
idx=$("$S" _index)
check "tên server chứa ký tự shell bị bỏ qua và ghi lại" '! printf "%s\n" "$idx" | cut -f1 | grep -q ";" && grep -q "bad;touch" "$HOSTBOOK_STATE/skipped"'
rm "$HOSTBOOK_DIR/evil.conf"; "$S" _index >/dev/null
check "địa chỉ chứa ký tự shell bị từ chối" '! "$S" _parse "a;b@1.2.3.4" >/dev/null && ! "$S" _parse "1.2.3.4;id" >/dev/null'

git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "trước khi thử đồng thời"
rm -f "$HOSTBOOK_STATE/status"
check "có kho nhưng chưa kiểm tra lần nào thì không báo đã đồng bộ" '"$S" _header | strip | grep -q "chưa kiểm tra" && ! "$S" _header | strip | grep -q "đã đồng bộ"'
for i in 1 2 3 4 5; do printf '\nHost race%s\n  HostName 10.7.0.%s\n' $i $i >> "$HOSTBOOK_DIR/project-a.conf"; "$S" _sync_now 2>>"$T/race.err" & "$S" _bg_sync 2>>"$T/race.err" & done; wait
"$S" _sync_now
check "đồng bộ chạy đồng thời không gây lỗi khoá git" '! grep -q "index.lock" "$T/race.err"'
check "sau đó máy và kho khớp nhau" '[ "$(git -C "$HOSTBOOK_DIR" rev-parse HEAD)" = "$(git -C "$T/kho.git" rev-parse HEAD)" ] && [ -z "$(git -C "$HOSTBOOK_DIR" status --porcelain)" ]'

check "thư mục trạng thái chỉ chủ máy đọc được" '[ "$(stat -c %a "$HOSTBOOK_STATE" 2>/dev/null || stat -f %Lp "$HOSTBOOK_STATE")" = 700 ]'

mkdir -p "$T/fakebin"
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = -G ] && exec %s "$@"; done\necho "FAKESSH $*"\n' "$(command -v ssh)" > "$T/fakebin/ssh"; chmod +x "$T/fakebin/ssh"
out=$(PATH="$T/fakebin:$PATH" "$S" a-web1 2>&1 | strip)
check "gõ đúng tên thì vào thẳng dù có tên dài hơn khớp (a-web1 vs a-web10…)" 'printf "%s" "$out" | grep -q "FAKESSH.*a-web1$"'

printf '#!/bin/sh\necho "0.44.1 (debian)"\n' > "$T/fakebin/fzf"; chmod +x "$T/fakebin/fzf"
out=$(PATH="$T/fakebin:$PATH" "$S" 2>&1 </dev/null | strip)
check "fzf quá cũ: báo rõ và chỉ cách cài lại" 'printf "%s" "$out" | grep -q "fzf 0.44.1 quá cũ" && printf "%s" "$out" | grep -q "install.sh"'
rm "$T/fakebin/fzf"
check "--version" '"$S" --version | grep -q "^hostbook [0-9]"'

# ---- delete, rename and move between projects ----
"$S" _index >/dev/null
"$S" _rename a-web1 a-front1 >/dev/null
idx=$("$S" _index)
check "đổi tên: dòng Host, nhóm và lịch sử theo tên mới" '[ -n "$(row a-front1 3)" ] && [ -z "$(row a-web1 3)" ] && grep -q "a-front1" "$HOSTBOOK_DIR/groups.tsv" && ! grep -q "a-web1 " "$HOSTBOOK_DIR/groups.tsv"'
"$S" _rename a-bastion a-gate >/dev/null; idx=$("$S" _index)
check "đổi tên máy trung gian: server khác đi qua tên mới" '[ "$(row a-web2 6)" = a-gate ] && [ "$(row a-front1 6)" = a-gate ]'
before=$(printf '%s\n' "$idx" | awk -F'\t' '$1=="a-db"{print $3,$4,$5,$6}')
"$S" _move a-db project-b >/dev/null; idx=$("$S" _index)
after=$(printf '%s\n' "$idx" | awk -F'\t' '$1=="a-db"{print $3,$4,$5,$6}')
check "chuyển dự án: giữ nguyên user và máy trung gian thừa hưởng từ wildcard cũ" '[ "$(row a-db 2)" = project-b ] && [ "$after" = "$before" ]'
check "chuyển dự án: tags đi theo khối" '[ "$(row a-db 8)" = "prod db postgres" ]'
"$S" _delete a-web2 >/dev/null; idx=$("$S" _index)
check "xoá: mất khỏi danh sách, dòng tags không bị bỏ lại" '[ -z "$(row a-web2 3)" ] && ! grep -B1 "^Host a-web3" "$HOSTBOOK_DIR/project-a.conf" | grep -q "prod web hcm"'
check "xoá: bỏ khỏi nhóm, nhóm vẫn còn" '! grep -q "a-web2" "$HOSTBOOK_DIR/groups.tsv" && grep -q "^prod-web" "$HOSTBOOK_DIR/groups.tsv"'
check "các thao tác trên đều được commit" '[ -z "$(git -C "$HOSTBOOK_DIR" status --porcelain)" ] && git -C "$HOSTBOOK_DIR" log --format=%s | grep -q "^xoá a-web2"'

# ---- import multiple hosts ----
parsed=$(printf 'n-web1 ubuntu@10.0.1.31 #prod #web\nn-api, deploy@10.2.0.33:2222\n10.0.1.40\ndb1.example.com\nbad;name 1.2.3.4\n# ghi chú\n' | "$S" _import_parse 2>"$T/imp.err")
check "nhập: đọc tên, user, cổng, tags; dấu phẩy cũng được" 'printf "%s\n" "$parsed" | grep -q "^n-web1	ubuntu	10.0.1.31	-	prod web$" && printf "%s\n" "$parsed" | grep -q "^n-api	deploy	10.2.0.33	2222	-$"'
check "nhập: tên lấy từ tên máy khi chỉ có địa chỉ" 'printf "%s\n" "$parsed" | grep -q "^db1	-	db1.example.com"'
check "nhập: báo đúng dòng lỗi" 'grep -q "dòng 3: thiếu tên" "$T/imp.err" && grep -q "dòng 5: tên" "$T/imp.err"'
printf '%s\n' "$parsed" > "$T/imp.tsv"; "$S" _import_write "$T/imp.tsv" >/dev/null; idx=$("$S" _index)
check "nhập: đoán dự án theo dải IP, user theo dự án khi không ghi" '[ "$(row n-web1 2)" = project-a ] && [ "$(row n-api 2)" = project-b ] && [ "$(row n-api 5)" = 2222 ] && [ "$(row db1 4)" != "" ]'

cat > "$T/old_config" <<'EOF'
Host *
  ServerAliveInterval 30
# tags: prod
Host x-web1
  HostName 1.1.1.1
Host x-web2
  HostName 1.1.1.2
Host x-*
  User ops
Host lone
  HostName 2.2.2.2
Match host foo
  User bar
EOF
mkdir -p "$T/split"; n=$("$S" _split "$T/old_config" "$T/split" "$T/split.global")
check "tách ssh config: nhóm theo phần đầu tên, lẻ vào imported" '[ "$n" = 4 ] && grep -q "Host x-web1" "$T/split/x.conf" && grep -q "Host x-\*" "$T/split/x.conf" && grep -q "Host lone" "$T/split/imported.conf"'
check "tách ssh config: comment tags đi theo khối, Host * và Match ở lại" 'grep -B1 "Host x-web1" "$T/split/x.conf" | grep -q "# tags: prod" && grep -q "ServerAliveInterval" "$T/split.global" && grep -q "Match host foo" "$T/split.global"'

# ---- sync two machines: merge each host separately ----
"$S" _sync_now; git -C "$T/other" pull -q
printf '\n# tags: kho\nHost from-kho\n  HostName 10.6.0.1\n' >> "$T/other/project-b.conf"
sed -i.b 's/HostName 10.0.1.12$/HostName 10.0.1.112/' "$T/other/project-a.conf"; rm -f "$T/other/project-a.conf.b"
git -C "$T/other" commit -qam "máy khác thêm và sửa"; git -C "$T/other" push -q
printf '\nHost from-local\n  HostName 10.6.0.2\n' >> "$HOSTBOOK_DIR/project-b.conf"
"$S" _sync_now 2>/dev/null; idx=$("$S" _index)
check "hai máy cùng thêm server vào cuối một file: gộp sạch, không báo xung đột" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" != conflict ] && [ -n "$(row from-kho 3)" ] && [ -n "$(row from-local 3)" ]'
check "kho có server của cả hai máy" 'git -C "$T/kho.git" show HEAD:project-b.conf | grep -q from-local && git -C "$T/kho.git" show HEAD:project-b.conf | grep -q from-kho'

git -C "$T/other" pull -q
sed -i.b 's/HostName 10.0.1.11$/HostName 10.0.1.211/' "$T/other/project-a.conf"; rm -f "$T/other/project-a.conf.b"
git -C "$T/other" commit -qam "sửa a-front1 ở máy khác"; git -C "$T/other" push -q
sed -i.b 's/HostName 10.0.1.11$/HostName 10.0.1.99/' "$HOSTBOOK_DIR/project-a.conf"; rm -f "$HOSTBOOK_DIR/project-a.conf.b"
printf '\nHost from-local2\n  HostName 10.6.0.3\n' >> "$HOSTBOOK_DIR/project-b.conf"
"$S" _sync_now 2>/dev/null
check "cùng sửa một server: báo xung đột" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = conflict ]'
"$S" _resolve theirs >"$T/resolve.out" 2>&1; idx=$("$S" _index)
check "lấy bản kho: server bị sửa hai nơi theo kho, server mới của máy này vẫn còn" '[ "$(row a-front1 3)" = 10.0.1.211 ] && [ -n "$(row from-local2 3)" ] && [ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = ok ]'
check "sau khi gộp, kho có cả hai phía" 'git -C "$T/kho.git" show HEAD:project-b.conf | grep -q from-local2'

check "tên kho hiển thị gọn" '[ "$(cd "$HOSTBOOK_DIR" && git remote set-url origin git@github.com:me/hosts.git && "$S" _kho_addr)" = "git@github.com" ] && [ "$(cd "$HOSTBOOK_DIR" && git remote set-url origin ssh://git@gitea.lan:2222/me/hosts.git && "$S" _kho_addr)" = "gitea.lan:2222" ]'
git -C "$HOSTBOOK_DIR" remote set-url origin "$T/kho.git"

# ---- layout adapts to terminal width ----
"$S" _index >/dev/null
narrow=$(COLUMNS=60 "$S" _list | strip | grep '^h:a-gate' | cut -f2)
narrow_search=$(COLUMNS=60 "$S" _list | strip | grep '^h:a-gate' | cut -f3)
wide=$(COLUMNS=200 "$S" _list | strip | grep '^h:a-gate' | cut -f2)
vis(){ printf '%s' "$1" | awk -v w="$2" '{ print substr($0,1,w) }'; }
check "màn hẹp: phần nhìn thấy có tên, tag, IP" 'vis "$narrow" 56 | grep -q "a-gate.*prod.*203.0.113.10"'
check "màn hẹp: user và dự án bị đẩy ra sau nhưng vẫn tìm được" '! vis "$narrow" 56 | grep -q "@ubuntu" && printf "%s" "$narrow_search" | grep -q "@ubuntu" && printf "%s" "$narrow_search" | grep -q "project-a"'
check "màn rộng: thấy cả user, dự án, mô tả" 'vis "$wide" 120 | grep -q "@ubuntu.*project-a.*Cổng vào project A"'
check "tag bị cắt vẫn tìm được" 'COLUMNS=60 "$S" _list | strip | grep "^h:a-db" | grep -q "#postgres"'
printf '#!/bin/sh\necho runner-very-long-hostname-from-a-ci-machine-1234567890\n' > "$T/fakebin/hostname"; chmod +x "$T/fakebin/hostname"
check "phần đầu màn hẹp không tràn, kể cả khi tên máy rất dài" '[ -z "$(COLUMNS=50 PATH="$T/fakebin:$PATH" "$S" _header | strip | awk "length(\$0) > 48")" ] && COLUMNS=50 PATH="$T/fakebin:$PATH" "$S" _header | strip | grep -q "server"'
rm -f "$T/fakebin/hostname"

# ---- explain connection failures from SSH logs ----
out=$(printf 'debug1: Connecting to 203.0.113.10 [203.0.113.10] port 22.\nssh: connect to host 203.0.113.10 port 22: Operation timed out\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "hết thời gian: báo không có phản hồi, hỏng ở bước mạng" 'printf "%s" "$out" | grep -q "Không có phản hồi từ 203.0.113.10:22" && printf "%s" "$out" | grep -q "✗ Mạng tới"'
out=$(printf 'debug1: Connecting to 203.0.113.10 [203.0.113.10] port 22.\ndebug1: Connection established.\ndebug1: Remote protocol version 2.0\ndebug1: Next authentication method: password\nPermission denied (publickey,password).\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "sai mật khẩu: mạng và SSH ổn, hỏng ở đăng nhập" 'printf "%s" "$out" | grep -q "✓ Mạng tới" && printf "%s" "$out" | grep -q "✓ Server SSH trả lời" && printf "%s" "$out" | grep -q "✗ Đăng nhập" && printf "%s" "$out" | grep -q "Sai mật khẩu"'
out=$(printf 'debug1: Remote protocol version 2.0\nAuthenticated to 203.0.113.10 ([203.0.113.10]:22) using "publickey".\nConnection timed out during banner exchange\nConnection to UNKNOWN port 65535 timed out\nKilled by signal 1.\n' > "$T/c.log"; "$S" _explain a-front1 "$T/c.log" | strip)
check "qua máy trung gian: vào được máy trung gian, hỏng ở chặng sau" 'printf "%s" "$out" | grep -q "✓ Vào máy trung gian a-gate" && printf "%s" "$out" | grep -q "✗ Từ a-gate tới" && ! printf "%s" "$out" | grep -q "Killed by signal"'
out=$(printf 'debug1: Remote protocol version 2.0\nHost key verification failed.\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "host key đổi: cảnh báo, hỏng ở bước xác minh" 'printf "%s" "$out" | grep -q "Host key của a-gate đã thay đổi" && printf "%s" "$out" | grep -q "✗ Xác minh host key"'

# ---- shell shortcuts during setup ----
H1=$(mktemp -d); printf 'hb(){ echo "lệnh hb riêng của người dùng"; }\n' > "$H1/.bashrc"
HOME=$H1 SHELL=/bin/bash "$S" setup >/dev/null 2>&1
check "hb đã có người dùng: không đè, dùng hop" 'grep -qx "alias hop=hostbook" "$H1/.bashrc" && ! grep -q "alias hb=hostbook" "$H1/.bashrc"'
H2=$(mktemp -d); printf '# hostbook: tìm và kết nối server SSH\nalias s=hostbook\n' > "$H2/.bashrc"
HOME=$H2 SHELL=/bin/bash "$S" setup >/dev/null 2>&1; HOME=$H2 SHELL=/bin/bash "$S" setup >/dev/null 2>&1
check "máy cũ có alias s: chuyển sang hb, chạy lại không thêm trùng" '[ "$(grep -c "alias hb=hostbook" "$H2/.bashrc")" = 1 ] && ! grep -q "alias s=" "$H2/.bashrc" && [ "$(grep -c "^# hostbook" "$H2/.bashrc")" = 1 ]'
rm -rf "$H1" "$H2"

# ---- M1: secret vault ----
SECD="$HOSTBOOK_DIR/secrets"
check "mật khẩu chính sai khi chưa có kho: không tạo gì" '! "$S" _vault_id 2>/dev/null | grep -q ok'
code=$("$S" _vault_init "$HOSTBOOK_MASTER")
check "tạo kho secret: đủ 3 file, mã khôi phục đúng dạng" '[ -s "$SECD/vault.pub" ] && [ -s "$SECD/vault.key.age" ] && [ -s "$SECD/vault.recovery.age" ] && printf "%s" "$code" | grep -qE "^HB(-[0-9A-HJKMNP-TV-Z]{4}){5}$"'
check "danh tính được nhớ, khớp vault.pub" '"$S" _vault_id | grep -q ok'
rm -rf "$T/keychain"
check "quên danh tính + sai mật khẩu chính: không mở được" '! HOSTBOOK_MASTER=sai-mat-khau "$S" _vault_id 2>/dev/null | grep -q ok'
check "đúng mật khẩu chính: mở được và nhớ lại" '"$S" _vault_id | grep -q ok && ls "$T/keychain" | grep -q id'

ssh-keygen -q -t ed25519 -N "" -f "$T/k1" -C k1
nm=$("$S" _secret_add_key "$T/k1" key-a 2>/dev/null)
check "thêm key: tạo .age .meta .pub, .pub quyền 600" '[ "$nm" = key-a ] && [ -s "$SECD/key-a.age" ] && grep -q "^type=key" "$SECD/key-a.meta" && [ "$(stat -c %a "$SECD/key-a.pub" 2>/dev/null || stat -f %Lp "$SECD/key-a.pub")" = 600 ]'
check "giải mã ra đúng key" '"$S" _secret_get key-a | cmp -s - "$T/k1"'
check "nội dung .age không chứa key dạng thường" '! grep -q "OPENSSH PRIVATE KEY" "$SECD/key-a.age"'
nm=$("$S" _secret_add_key "$T/k1" key-khac 2>/dev/null)
check "thêm lại cùng key: trả tên cũ, không tạo secret mới" '[ "$nm" = key-a ] && [ ! -f "$SECD/key-khac.meta" ]'
ssh-keygen -q -t ed25519 -N "abc123" -f "$T/k2" -C k2
HOSTBOOK_KEY_PASSPHRASE=abc123 "$S" _secret_add_key "$T/k2" key-pp >/dev/null 2>&1
"$S" _secret_get key-pp > "$T/k2.dec"; chmod 600 "$T/k2.dec"
check "key có passphrase: được gỡ trước khi vào kho" 'ssh-keygen -y -P "" -f "$T/k2.dec" >/dev/null 2>&1'
echo "rác" > "$T/bad.key"
check "nội dung không phải key: từ chối" '! "$S" _secret_add_key "$T/bad.key" key-bad >/dev/null 2>&1 && [ ! -f "$SECD/key-bad.meta" ]'
ssh-keygen -q -t ed25519 -N "" -f "$T/k3"
check "tên secret không hợp lệ: từ chối" '! "$S" _secret_add_key "$T/k3" "Ten Sai" >/dev/null 2>&1 && ! "$S" _secret_add_key "$T/k3" vault >/dev/null 2>&1'
"$S" _secret_add_password mk-deploy "p@ss w0rd" >/dev/null 2>&1
check "secret mật khẩu: lưu và giải mã đúng" '[ "$("$S" _secret_get mk-deploy)" = "p@ss w0rd" ] && grep -q "^type=password" "$SECD/mk-deploy.meta" && [ ! -f "$SECD/mk-deploy.pub" ]'

b=$("$S" _block s-web1 10.50.0.1 ops 22 "" key-a "prod" "")
check "khối Host với secret key: # secret, IdentityFile .pub, IdentitiesOnly" 'printf "%s" "$b" | grep -q "^# secret: key-a$" && printf "%s" "$b" | grep -q "IdentityFile .*secrets/key-a.pub$" && printf "%s" "$b" | grep -q "IdentitiesOnly yes"'
b2=$("$S" _block s-api 10.50.0.2 ops 22 "" mk-deploy "" "")
check "khối Host với secret mật khẩu: tắt key, ưu tiên mật khẩu" 'printf "%s" "$b2" | grep -q "PubkeyAuthentication no" && printf "%s" "$b2" | grep -q "PreferredAuthentications password" && ! printf "%s" "$b2" | grep -q IdentityFile'
b3=$("$S" _block s-none 10.50.0.3 ops 22 "" "" "" "")
check "khối Host không secret: không có dòng đăng nhập nào" '! printf "%s" "$b3" | grep -qE "secret|IdentityFile|PubkeyAuthentication"'
{ printf '%s\n\n%s\n\n%s\n' "$b" "$b2" "$b3"; printf '\nHost sw-1\n  HostName 10.50.1.1\n\nHost sw-*\n  User ops\n  IdentityFile %s/key-a.pub\n  IdentitiesOnly yes\n' "$SECD"; } > "$HOSTBOOK_DIR/sec.conf"
idx=$("$S" _index)
check "chỉ mục có trường secret, kể cả suy ra từ khối wildcard" '[ "$(row s-web1 12)" = key-a ] && [ "$(row s-api 12)" = mk-deploy ] && [ -z "$(row s-none 12)" ] && [ "$(row sw-1 12)" = key-a ]'
check "tìm server theo secret" '"$S" _list | strip | grep "^h:s-web1" | grep -q "secret:key-a"'
check "secret của server gồm cả secret của máy trung gian" '[ "$("$S" _secrets_for s-web1)" = key-a ]'
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "sec.conf"

"$S" _set_host_secret s-web1 mk-deploy >/dev/null; idx=$("$S" _index)
check "đổi đăng nhập key → mật khẩu: không sót dòng cũ" '[ "$(row s-web1 12)" = mk-deploy ] && ! sed -n "/^Host s-web1/,/^$/p" "$HOSTBOOK_DIR/sec.conf" | grep -qE "IdentityFile|IdentitiesOnly" && sed -n "/^Host s-web1/,/^$/p" "$HOSTBOOK_DIR/sec.conf" | grep -q "PubkeyAuthentication no"'
"$S" _set_host_secret s-web1 "" >/dev/null; idx=$("$S" _index)
check "đổi sang ssh tự lo: gỡ hết dòng đăng nhập" '[ -z "$(row s-web1 12)" ] && ! sed -n "/^# tags: prod/,/^$/p" "$HOSTBOOK_DIR/sec.conf" | grep -qE "secret|PubkeyAuthentication|PreferredAuth|IdentityFile"'
"$S" _set_host_secret s-web1 key-a >/dev/null; idx=$("$S" _index)
check "đổi lại sang key: đủ dòng, config hợp lệ" '[ "$(row s-web1 12)" = key-a ] && [ "$(grep -c "^# secret: key-a" "$HOSTBOOK_DIR/sec.conf")" = 1 ] && "$S" _validate'

"$S" _secret_rename key-a key-chinh >/dev/null; idx=$("$S" _index)
check "đổi tên secret: file, dòng # secret và đường dẫn .pub đều theo" '[ -f "$SECD/key-chinh.age" ] && [ ! -f "$SECD/key-a.age" ] && [ "$(row s-web1 12)" = key-chinh ] && [ "$(row sw-1 12)" = key-chinh ] && ! grep -q "key-a" "$HOSTBOOK_DIR/sec.conf"'
"$S" _secret_delete mk-deploy >/dev/null; idx=$("$S" _index)
check "xoá secret: gỡ file và các dòng đăng nhập của nó" '[ ! -f "$SECD/mk-deploy.age" ] && [ -z "$(row s-api 12)" ] && ! grep -qE "mk-deploy|PubkeyAuthentication|PreferredAuth" "$HOSTBOOK_DIR/sec.conf" && [ "$(row s-web1 12)" = key-chinh ]'

# Migrate legacy keys: use a test key in a temporary directory; never read the real ~/.ssh
ssh-keygen -q -t ed25519 -N "" -f "$T/legacy_key"
for f in "$HOSTBOOK_DIR"/*.conf; do sed -i.b "s|~/.ssh/id_rsa|$T/legacy_key|" "$f"; rm -f "$f.b"; done
printf '\nHost old-1\n  HostName 10.60.0.1\n  User u\n  IdentityFile %s/legacy_key\n' "$T" >> "$HOSTBOOK_DIR/sec.conf"
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "legacy"; "$S" _index >/dev/null
n=$("$S" _migrate 2>/dev/null | tail -1); idx=$("$S" _index)
check "chuyển key cũ: vào kho secret, config viết lại, hợp lệ" '[ "$n" = 1 ] && [ -f "$SECD/legacy-key.age" ] && [ "$(row old-1 12)" = legacy-key ] && [ "$(row a-gate 12)" = legacy-key ] && ! grep -rq "$T/legacy_key" "$HOSTBOOK_DIR"/*.conf && "$S" _validate'
check "chuyển key cũ: file gốc còn nguyên" '[ -s "$T/legacy_key" ]'
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "migrated"

"$S" _load key-chinh
check "nạp key vào agent riêng (không ghi file)" 'ssh-add -l | grep -qF "$(sed -n "s/^fp=//p" "$SECD/key-chinh.meta")"'
"$S" lock >/dev/null
check "hb lock gỡ key khỏi agent" '! ssh-add -l 2>/dev/null | grep -qF "$(sed -n "s/^fp=//p" "$SECD/key-chinh.meta")"'

touch "$SECD/leak.key" "$SECD/ghi-chu.txt"; "$S" _sync_now
check "file lạ trong secrets/ không bị đưa vào kho" '! git -C "$HOSTBOOK_DIR" ls-files | grep -qE "leak.key|ghi-chu" && git -C "$HOSTBOOK_DIR" ls-files | grep -q "secrets/key-chinh.age"'
rm -f "$SECD/leak.key" "$SECD/ghi-chu.txt"

"$S" _secret_add_password mk-x "mat-khau-1" >/dev/null 2>&1; "$S" _index >/dev/null
printf '\n# secret: mk-x\nHost pw-1\n  HostName 10.70.0.1\n  User tester\n  PubkeyAuthentication no\n' >> "$HOSTBOOK_DIR/sec.conf"; "$S" _index >/dev/null
check "askpass: lần hỏi đầu trả mật khẩu đã lưu" '[ "$(HB_ASKPASS_HOST=pw-1 HB_ASKPASS_COUNT="$T/cnt1" "$S" _askpass "tester@10.70.0.1'"'"'s password: ")" = mat-khau-1 ]'
check "askpass: server hỏi lại (mật khẩu sai) thì không gửi lại nữa" '[ -z "$(HB_ASKPASS_NOTTY=1 HB_ASKPASS_HOST=pw-1 HB_ASKPASS_COUNT="$T/cnt1" "$S" _askpass "tester@10.70.0.1'"'"'s password: ")" ]'
check "askpass: chọn secret theo user@máy trong câu hỏi" '[ "$(HB_ASKPASS_HOST=s-web1 HB_ASKPASS_COUNT="$T/cnt2" "$S" _askpass "tester@10.70.0.1'"'"'s password: ")" = mat-khau-1 ]'
check "askpass: câu hỏi lạ khi không có terminal thì trả rỗng" '[ -z "$(HB_ASKPASS_NOTTY=1 "$S" _askpass "Verification code: ")" ]'

# Two machines modify the same secret
"$S" _sync_now; git -C "$T/other" pull -q
printf 'ban-cua-kho\n' | age -e -a -r "$(cat "$SECD/vault.pub")" > "$T/other/secrets/mk-x.age"
git -C "$T/other" commit -qam "đổi mật khẩu ở máy khác"; git -C "$T/other" push -q
printf 'ban-cua-may-nay\n' | age -e -a -r "$(cat "$SECD/vault.pub")" > "$SECD/mk-x.age"
"$S" _sync_now 2>/dev/null
check "hai máy cùng sửa một secret: báo xung đột" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = conflict ]'
"$S" _resolve theirs >/dev/null 2>&1
check "lấy bản kho cho secret" '[ "$("$S" _secret_get mk-x)" = ban-cua-kho ] && [ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = ok ]'

new=$("$S" _vault_recover "$code" "mat-khau-moi-12345" 2>/dev/null | tail -1)
check "mã khôi phục: đặt được mật khẩu chính mới, secret cũ vẫn đọc được" 'printf "%s" "$new" | grep -q "^HB-" && [ "$new" != "$code" ] && rm -rf "$T/keychain" && [ "$(HOSTBOOK_MASTER=mat-khau-moi-12345 "$S" _secret_get mk-x)" = ban-cua-kho ]'
rm -rf "$T/keychain"
check "sau khi khôi phục: mật khẩu cũ và mã cũ hết hiệu lực" '! "$S" _vault_id 2>/dev/null | grep -q ok && ! "$S" _vault_recover "$code" "mat-khau-khac-12345" >/dev/null 2>&1'
export HOSTBOOK_MASTER=mat-khau-moi-12345

# ---- M3: share and import packages ----
check "bảng từ đủ 128, không trùng" '[ "$("$S" _share_words | wc -l | tr -d " ")" = 128 ] && [ -z "$("$S" _share_words | sort | uniq -d)" ]'
check "mật khẩu gói: 4 từ và 2 chữ số" '"$S" _share_pw | grep -qE "^([a-z]+-){4}[0-9]{2}$"'
"$S" _sync_now 2>/dev/null; "$S" _index >/dev/null
printf 'g-chia-se\tsplit\ta-front1 s-web1 b-api\n' >> "$HOSTBOOK_DIR/groups.tsv"
"$S" _share_build "$T/pk" 1 a-front1 s-web1 pw-1 >/dev/null 2>&1
check "gói: khối được làm phẳng (user và máy trung gian thừa hưởng từ wildcard vẫn có)" 'sed -n "/^Host a-front1/,/^$/p" "$T/pk/conf/project-a.conf" | grep -q "User ubuntu" && sed -n "/^Host a-front1/,/^$/p" "$T/pk/conf/project-a.conf" | grep -q "ProxyJump a-gate"'
check "gói: máy trung gian tự được kèm" 'grep -q "^Host a-gate" "$T/pk/conf/project-a.conf" && grep -q "^hosts=4" "$T/pk/manifest"'
check "gói: chỉ kèm secret được dùng, đủ key và mật khẩu" 'grep -q "PRIVATE KEY" "$T/pk/secrets/key-chinh.key" && [ "$(cat "$T/pk/secrets/mk-x.pass")" = ban-cua-kho ] && [ ! -f "$T/pk/secrets/key-pp.key" ]'
check "gói: nhóm chỉ giữ thành viên có trong gói" 'grep -q "^g-chia-se	split	a-front1 s-web1$" "$T/pk/groups.tsv"'
"$S" _share_build "$T/pk0" 0 s-web1 >/dev/null 2>&1
check "gói không kèm secret: không có secret, không còn dòng đăng nhập" '[ ! -d "$T/pk0/secrets" ] && ! grep -qE "secret|IdentityFile|IdentitiesOnly" "$T/pk0/conf/sec.conf"'
"$S" _share_pack mat-khau-goi a-front1 s-web1 pw-1 > "$T/a.hbshare" 2>/dev/null
check "gói đã mã hoá: có dòng đầu nhận diện, không lộ tên server hay key" 'head -1 "$T/a.hbshare" | grep -q "^# hostbook-share v1 · 4 server" && grep -q "BEGIN AGE ENCRYPTED FILE" "$T/a.hbshare" && ! grep -qE "a-front1|PRIVATE KEY|10\.0\.1" "$T/a.hbshare"'
check "sai mật khẩu gói: không mở được" '! HB_PACK_PW=sai "$S" _import_classify "$T/a.hbshare" >/dev/null 2>&1'
cls=$(HB_PACK_PW=mat-khau-goi "$S" _import_classify "$T/a.hbshare" 2>/dev/null)
check "nhập lại vào chính máy gửi: mọi server giống hệt" '[ "$(printf "%s\n" "$cls" | grep -c .)" = 4 ] && [ "$(printf "%s\n" "$cls" | awk -F"\t" "\$2==\"same\"" | grep -c .)" = 4 ]'

# Machine B: already has s-web1 (at a different address) and a host with the same address as pw-1
mkdir -p "$T/b"; printf 'Include %s/b/*.conf\n' "$T" > "$T/bcfg"
printf 'Host s-web1\n  HostName 10.99.0.1\n  User ops\n\nHost trung-ip\n  HostName 10.70.0.1\n  User x\n' > "$T/b/sec.conf"
git -C "$T/b" init -q; git -C "$T/b" add -A; git -C "$T/b" commit -qm init
B(){ HOSTBOOK_DIR="$T/b" HOSTBOOK_STATE="$T/bs" HOSTBOOK_SSH_CONFIG="$T/bcfg" HOSTBOOK_KEYCHAIN="file:$T/bkc/id" HB_PACK_PW=mat-khau-goi "$S" "$@"; }
cls=$(B _import_classify "$T/a.hbshare" 2>/dev/null)
cl(){ printf '%s\n' "$cls" | awk -F'\t' -v n="$1" '$1==n{print $2}'; }
check "máy khác: phân loại mới / khác / trùng địa chỉ" '[ "$(cl a-front1)" = new ] && [ "$(cl a-gate)" = new ] && [ "$(cl s-web1)" = diff ] && [ "$(cl pw-1)" = ipdup ] && printf "%s\n" "$cls" | grep "^s-web1" | grep -q "HostName 10.99.0.1 → 10.50.0.1"'
cp -R "$T/b" "$T/b2"
before=$(git -C "$T/b" rev-list --count HEAD)
B _import_apply "$T/a.hbshare" pack >/dev/null 2>&1
bidx=$(B _index); brow(){ printf '%s\n' "$bidx" | awk -F'\t' -v n="$1" -v c="$2" '$1==n{print $c}'; }
check "nhập theo gói: server mới được thêm, server khác được cập nhật" '[ "$(brow a-front1 3)" = 10.0.1.211 ] && [ "$(brow a-front1 6)" = a-gate ] && [ "$(brow s-web1 3)" = 10.50.0.1 ] && [ "$(brow pw-1 3)" = 10.70.0.1 ] && B _validate'
check "nhập: kho secret được tạo ở máy nhận, secret đọc được, server trỏ đúng secret" '[ "$(B _secret_get mk-x)" = ban-cua-kho ] && B _secret_get key-chinh | grep -q "PRIVATE KEY" && [ "$(brow s-web1 12)" = key-chinh ] && [ "$(brow pw-1 12)" = mk-x ]'
check "nhập: nhóm đi theo" 'grep -q "^g-chia-se	split	a-front1 s-web1" "$T/b/groups.tsv"'
check "nhập là một commit (hoàn tác được một lần)" 'git -C "$T/b" log -1 --format=%s | grep -q "Nhập gói" && git -C "$T/b" show --stat HEAD | grep -q "sec.conf" && ! git -C "$T/b" show HEAD~1:project-a.conf >/dev/null 2>&1'
B2(){ HOSTBOOK_DIR="$T/b2" HOSTBOOK_STATE="$T/b2s" HOSTBOOK_SSH_CONFIG="$T/b2cfg" HOSTBOOK_KEYCHAIN="file:$T/b2kc/id" HB_PACK_PW=mat-khau-goi "$S" "$@"; }
printf 'Include %s/b2/*.conf\n' "$T" > "$T/b2cfg"
B2 _import_apply "$T/a.hbshare" mine >/dev/null 2>&1; bidx=$(B2 _index)
check "giữ bản của tôi: chỉ thêm server mới, không đụng server đang có" '[ "$(brow a-front1 3)" = 10.0.1.211 ] && [ "$(brow s-web1 3)" = 10.99.0.1 ] && [ -z "$(brow pw-1 3)" ]'
"$S" _share_pack mat-khau-goi s-web1 > "$T/again.hbshare" 2>/dev/null
check "nhập lần hai một key đã có: dùng lại secret theo fingerprint, không tạo bản sao" 'B _import_classify "$T/again.hbshare" 2>/dev/null | grep -q "^s-web1	same" && [ "$(ls "$T/b/secrets" | grep -c "key-chinh")" = 3 ]'
python3 - "$T" <<'PYEOF'
import tarfile, io, sys
t=sys.argv[1]
with tarfile.open(t+'/evil.tgz','w:gz') as tf:
    for name,data in (('manifest',b'version=1\nfrom=x\nhosts=1\n'),('../../evil.conf',b'Host evil\n')):
        ti=tarfile.TarInfo(name); ti.size=len(data); tf.addfile(ti, io.BytesIO(data))
PYEOF
{ echo "# hostbook-share v1 · 1 server · từ x"; AGE_PASSPHRASE=mat-khau-goi age -e -j batchpass -a < "$T/evil.tgz"; } > "$T/evil.hbshare"
check "gói có đường dẫn ../ bị từ chối" '! B _import_classify "$T/evil.hbshare" >/dev/null 2>&1 && [ ! -f "$T/evil.conf" ] && [ ! -f "$T/../evil.conf" ]'

echo; echo "$pass ok, $fail lỗi"; [ $fail -eq 0 ]
