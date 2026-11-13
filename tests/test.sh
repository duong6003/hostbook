#!/usr/bin/env bash
# Kiểm thử phần lõi của hostbook trên dữ liệu tạm (không đụng ~/ssh-hosts thật): bash tests/test.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh_config
S="$HERE/../hostbook"; pass=0; fail=0
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
check "fzf: #prod web !b- ra đúng a-web1, a-web2" '[ "$(printf "%s\n" "$list" | fzf --filter "#prod web !b-" --delimiter="	" --with-nth=2 --accept-nth=1 | sort | tr "\n" " ")" = "h:a-web1 h:a-web2 " ]'
check "fzf: @deploy ra b-api, b-staging" '[ "$(printf "%s\n" "$list" | fzf --filter "@deploy" --delimiter="	" --with-nth=2 --accept-nth=1 | sort | tr "\n" " ")" = "h:b-api h:b-staging " ]'
check "fzf: ^b- neo đầu tên" '[ "$(printf "%s\n" "$list" | fzf --filter "^b-" --delimiter="	" --with-nth=2 --accept-nth=1 | wc -l | tr -d " ")" = 2 ]'

prev=$("$S" _preview h:a-web1 | strip)
check "xem trước có PROD, ProxyJump, mô tả" 'printf "%s" "$prev" | grep -q "● PROD" && printf "%s" "$prev" | grep -q "ProxyJump a-bastion" && printf "%s" "$prev" | grep -q "Nginx chính"'

check "đọc địa chỉ user@ip:port" '[ "$("$S" _parse ops@10.3.0.50:2200 | tr "\n" " ")" = "ops 10.3.0.50 2200 " ]'
check "đọc địa chỉ chỉ có IP" '[ "$("$S" _parse 10.0.1.13 | tr "\n" " ")" = " 10.0.1.13  " ]'
check "từ chối địa chỉ có dấu cách / cổng sai" '! "$S" _parse "a b" >/dev/null && ! "$S" _parse "1.2.3.4:abc" >/dev/null'
check "gợi ý tiền tố tên theo dải IP" '[ "$("$S" _prefix 10.0.1.13)" = a- ]'

inf=$("$S" _infer 10.0.1.13 a-web3 "" "")
iv(){ printf '%s\n' "$inf" | sed -n "s/^$1=//p"; }
check "đoán dự án theo dải IP" '[ "$(iv group)" = project-a ] && [ "$(iv src_group)" = "cùng dải 10.0.1.x" ]'
check "đoán user, máy trung gian, key" '[ "$(iv user)" = ubuntu ] && [ "$(iv jump)" = a-bastion ] && [ "$(iv key)" = "~/.ssh/id_rsa" ]'
check "đoán tags: tag chung + tag từ tên" '[ "$(iv tags)" = "prod web" ]'
inf=$("$S" _infer 10.2.0.77 b-worker "" 2201)
check "giữ user/port đã dán, đoán dự án b" '[ "$(iv group)" = project-b ] && [ "$(iv port)" = 2201 ] && [ "$(iv user)" = deploy ]'
inf=$("$S" _infer 172.16.0.5 lab1 "root" "")
check "IP lạ: dự án nhiều server nhất, giữ user" '[ "$(iv group)" = project-a ] && [ "$(iv user)" = root ] && [ "$(iv src_group)" = "dự án có nhiều server nhất" ]'

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

# ---- đồng bộ với kho (kho giả lập là bare repo trong thư mục tạm) ----
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm "a-web3"
check "chưa có kho: header báo rõ" '"$S" _header | strip | grep -q "Chưa có kho"'
git init -q --bare "$T/kho.git"; git -C "$HOSTBOOK_DIR" remote add origin "$T/kho.git"
"$S" _sync_now
check "lần đầu: đẩy lên kho và đặt upstream" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = ok ] && [ "$(git -C "$T/kho.git" log --oneline | wc -l | tr -d " ")" = 2 ]'
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
check "file lỗi: không commit, header báo lỗi" '[ "$(sed -n 1p "$HOSTBOOK_STATE/status")" = invalid ] && [ -n "$(git -C "$HOSTBOOK_DIR" status --porcelain)" ] && "$S" _header | strip | grep -q "File lỗi, chưa đồng bộ: bad.conf"'
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

# ---- các lỗi đã sửa sau lần rà soát ----
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

check "thư mục trạng thái chỉ chủ máy đọc được" '[ "$(stat -f %Lp "$HOSTBOOK_STATE" 2>/dev/null || stat -c %a "$HOSTBOOK_STATE")" = 700 ]'

mkdir -p "$T/fakebin"
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = -G ] && exec %s "$@"; done\necho "FAKESSH $*"\n' "$(command -v ssh)" > "$T/fakebin/ssh"; chmod +x "$T/fakebin/ssh"
out=$(PATH="$T/fakebin:$PATH" "$S" a-web1 2>&1 | strip)
check "gõ đúng tên thì vào thẳng dù có tên dài hơn khớp (a-web1 vs a-web10…)" 'printf "%s" "$out" | grep -q "FAKESSH.*a-web1$"'

printf '#!/bin/sh\necho "0.44.1 (debian)"\n' > "$T/fakebin/fzf"; chmod +x "$T/fakebin/fzf"
out=$(PATH="$T/fakebin:$PATH" "$S" 2>&1 </dev/null | strip)
check "fzf quá cũ: báo rõ và chỉ cách cài lại" 'printf "%s" "$out" | grep -q "fzf 0.44.1 quá cũ" && printf "%s" "$out" | grep -q "install.sh"'
rm "$T/fakebin/fzf"
check "--version" '"$S" --version | grep -q "^hostbook [0-9]"'

echo; echo "$pass ok, $fail lỗi"; [ $fail -eq 0 ]
