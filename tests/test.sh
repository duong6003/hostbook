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

# ---- xoá, đổi tên, chuyển dự án ----
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

# ---- nhập nhiều server ----
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

# ---- đồng bộ hai máy: gộp theo từng server ----
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

# ---- layout co giãn theo màn hình ----
"$S" _index >/dev/null
narrow=$(COLUMNS=60 "$S" _list | strip | grep '^h:a-gate' | cut -f2)
wide=$(COLUMNS=200 "$S" _list | strip | grep '^h:a-gate' | cut -f2)
vis(){ printf '%s' "$1" | awk -v w="$2" '{ print substr($0,1,w) }'; }
check "màn hẹp: phần nhìn thấy có tên, tag, IP" 'vis "$narrow" 56 | grep -q "a-gate.*#prod.*203.0.113.10"'
check "màn hẹp: user và dự án bị đẩy ra sau nhưng vẫn tìm được" '! vis "$narrow" 56 | grep -q "@ubuntu" && printf "%s" "$narrow" | grep -q "@ubuntu" && printf "%s" "$narrow" | grep -q "project-a"'
check "màn rộng: thấy cả user, dự án, mô tả" 'vis "$wide" 120 | grep -q "@ubuntu.*project-a.*Cổng vào project A"'
check "tag bị cắt vẫn tìm được" 'COLUMNS=60 "$S" _list | strip | grep "^h:a-db" | grep -q "#postgres"'
printf '#!/bin/sh\necho runner-very-long-hostname-from-a-ci-machine-1234567890\n' > "$T/fakebin/hostname"; chmod +x "$T/fakebin/hostname"
check "phần đầu màn hẹp không tràn, kể cả khi tên máy rất dài" '[ -z "$(COLUMNS=50 PATH="$T/fakebin:$PATH" "$S" _header | strip | awk "length(\$0) > 48")" ] && COLUMNS=50 PATH="$T/fakebin:$PATH" "$S" _header | strip | grep -q "server"'
rm -f "$T/fakebin/hostname"

# ---- giải thích lỗi kết nối từ nhật ký ssh ----
out=$(printf 'debug1: Connecting to 203.0.113.10 [203.0.113.10] port 22.\nssh: connect to host 203.0.113.10 port 22: Operation timed out\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "hết thời gian: báo không có phản hồi, hỏng ở bước mạng" 'printf "%s" "$out" | grep -q "Không có phản hồi từ 203.0.113.10:22" && printf "%s" "$out" | grep -q "✗ Mạng tới"'
out=$(printf 'debug1: Connecting to 203.0.113.10 [203.0.113.10] port 22.\ndebug1: Connection established.\ndebug1: Remote protocol version 2.0\ndebug1: Next authentication method: password\nPermission denied (publickey,password).\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "sai mật khẩu: mạng và SSH ổn, hỏng ở đăng nhập" 'printf "%s" "$out" | grep -q "✓ Mạng tới" && printf "%s" "$out" | grep -q "✓ Server SSH trả lời" && printf "%s" "$out" | grep -q "✗ Đăng nhập" && printf "%s" "$out" | grep -q "Sai mật khẩu"'
out=$(printf 'debug1: Remote protocol version 2.0\nAuthenticated to 203.0.113.10 ([203.0.113.10]:22) using "publickey".\nConnection timed out during banner exchange\nConnection to UNKNOWN port 65535 timed out\nKilled by signal 1.\n' > "$T/c.log"; "$S" _explain a-front1 "$T/c.log" | strip)
check "qua máy trung gian: vào được máy trung gian, hỏng ở chặng sau" 'printf "%s" "$out" | grep -q "✓ Vào máy trung gian a-gate" && printf "%s" "$out" | grep -q "✗ Từ a-gate tới" && ! printf "%s" "$out" | grep -q "Killed by signal"'
out=$(printf 'debug1: Remote protocol version 2.0\nHost key verification failed.\n' > "$T/c.log"; "$S" _explain a-gate "$T/c.log" | strip)
check "host key đổi: cảnh báo, hỏng ở bước xác minh" 'printf "%s" "$out" | grep -q "Host key của a-gate đã thay đổi" && printf "%s" "$out" | grep -q "✗ Xác minh host key"'

# ---- lối tắt khi cài ----
H1=$(mktemp -d); printf 'hb(){ echo "lệnh hb riêng của người dùng"; }\n' > "$H1/.bashrc"
HOME=$H1 SHELL=/bin/bash "$S" setup >/dev/null 2>&1
check "hb đã có người dùng: không đè, dùng hop" 'grep -qx "alias hop=hostbook" "$H1/.bashrc" && ! grep -q "alias hb=hostbook" "$H1/.bashrc"'
H2=$(mktemp -d); printf '# hostbook: tìm và kết nối server SSH\nalias s=hostbook\n' > "$H2/.bashrc"
HOME=$H2 SHELL=/bin/bash "$S" setup >/dev/null 2>&1; HOME=$H2 SHELL=/bin/bash "$S" setup >/dev/null 2>&1
check "máy cũ có alias s: chuyển sang hb, chạy lại không thêm trùng" '[ "$(grep -c "alias hb=hostbook" "$H2/.bashrc")" = 1 ] && ! grep -q "alias s=" "$H2/.bashrc" && [ "$(grep -c "^# hostbook" "$H2/.bashrc")" = 1 ]'
rm -rf "$H1" "$H2"

echo; echo "$pass ok, $fail lỗi"; [ $fail -eq 0 ]
