#!/usr/bin/env bash
# Test against a real SSH server running in Docker: bash tests/e2e.sh
# Skip automatically when Docker is unavailable. Leave the real ~/.ssh, ssh-agent and Keychain untouched.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || { echo "Bỏ qua e2e: không có Docker"; exit 0; }
PORT=${HB_E2E_PORT:-22022}; C=hb-e2e-sshd
T=$(mktemp -d)
export HOME="$T/home"; mkdir -p "$HOME/.ssh"
export HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh_config
export HOSTBOOK_KEYCHAIN="file:$T/keychain/id" HOSTBOOK_MASTER="mat-khau-chinh-thu" XDG_CONFIG_HOME="$T/config" HB_ASKPASS_NOTTY=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
AG=$(mktemp -d /tmp/hbe-XXXXXX); eval "$(ssh-agent -a "$AG/a.sock")" >/dev/null
[ "${SSH_AUTH_SOCK:-}" = "$AG/a.sock" ] || { echo "Không dựng được ssh-agent riêng, dừng"; exit 1; }
trap 'ssh-agent -k >/dev/null 2>&1; docker rm -f $C >/dev/null 2>&1; rm -rf "$T" "$AG"' EXIT
S="$HERE/../hostbook"; pass=0; fail=0
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
strip(){ sed $'s/\033\\[[0-9;]*m//g'; }

docker build -q -t hb-test-sshd "$HERE/sshd" >/dev/null || { echo "Không dựng được image"; exit 1; }
docker rm -f $C >/dev/null 2>&1; docker run -d --name $C -p 127.0.0.1:$PORT:22 hb-test-sshd >/dev/null
for i in $(seq 1 30); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 1; done

mkdir -p "$HOSTBOOK_DIR"; git -C "$HOSTBOOK_DIR" init -q
cat > "$HOSTBOOK_SSH_CONFIG" <<EOF
Include $HOSTBOOK_DIR/*.conf
Host *
  UserKnownHostsFile $T/known
  StrictHostKeyChecking accept-new
  LogLevel ERROR
EOF
"$S" _vault_init "$HOSTBOOK_MASTER" >/dev/null
ssh-keygen -q -t ed25519 -N "" -f "$T/k1"; ssh-keygen -q -t ed25519 -N "" -f "$T/decoy"
docker exec -i $C sh -c 'cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys' < "$T/k1.pub"
"$S" _secret_add_key "$T/k1" key-a >/dev/null 2>&1
"$S" _secret_add_password mk "matkhau123" >/dev/null 2>&1
"$S" _secret_add_password mk-sai "khong-dung" >/dev/null 2>&1
{ "$S" _block e-key 127.0.0.1 root $PORT "" key-a "prod" ""; echo
  "$S" _block e-pass 127.0.0.1 tester $PORT "" mk "" ""; echo
  "$S" _block e-wrong 127.0.0.1 tester $PORT "" mk-sai "" ""; echo
  "$S" _block e-jump 127.0.0.1 tester 22 e-key mk "" ""; echo
  "$S" _block e-dead 10.255.255.1 root 22 "" key-a "" ""; echo
  "$S" _block e-behind 10.255.255.2 root 22 e-dead key-a "" ""; echo
  printf '# secret: mk\nHost e-ask\n  HostName 127.0.0.1\n  Port %s\n  User tester\n  PubkeyAuthentication no\n  PreferredAuthentications password,keyboard-interactive\n  StrictHostKeyChecking ask\n  UserKnownHostsFile %s/known-ask\n' $PORT "$T"
} > "$HOSTBOOK_DIR/e2e.conf"
git -C "$HOSTBOOK_DIR" add -A; git -C "$HOSTBOOK_DIR" commit -qm e2e; "$S" _index >/dev/null

ssh-add -q "$T/decoy"; for i in 1 2 3 4 5 6; do ssh-keygen -q -t ed25519 -N "" -f "$T/d$i"; ssh-add -q "$T/d$i"; done
check "key trong kho secret: vào được dù agent có 7 key khác (ghim bằng .pub)" '[ "$("$S" _run e-key echo vao-duoc 2>/dev/null)" = vao-duoc ]'
check "key được nạp vào agent, không có file key dạng thường nào trong thư mục dữ liệu" 'ssh-add -l | grep -qF "$(sed -n "s/^fp=//p" "$HOSTBOOK_DIR/secrets/key-a.meta")" && ! grep -rl "PRIVATE KEY" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" 2>/dev/null | grep -q .'
check "mật khẩu trong kho secret: vào được qua askpass" '[ "$("$S" _run e-pass whoami 2>/dev/null)" = tester ]'
HB_LOG="$T/wrong.log" "$S" _run e-wrong true >/dev/null 2>&1; rc=$?
check "mật khẩu đã lưu sai: thất bại, không treo" '[ $rc -ne 0 ]'
check "khung lỗi nói đúng: mật khẩu trong secret không còn đúng" '"$S" _explain e-wrong "$T/wrong.log" | strip | grep -q "mật khẩu trong secret mk-sai"'
check "qua máy trung gian, hai chặng dùng hai secret khác nhau (key rồi mật khẩu)" '[ "$("$S" _run e-jump whoami 2>/dev/null)" = tester ]'

"$S" lock >/dev/null
ssh -F "$HOSTBOOK_SSH_CONFIG" -v -E "$T/locked.log" -o BatchMode=yes -o IdentityAgent=none e-key true </dev/null 2>>"$T/locked.log"
check "chưa nạp key: ssh thường thất bại và khung lỗi nhận ra kho secret chưa mở" '"$S" _explain e-key "$T/locked.log" | strip | grep -q "Kho secret chưa mở"'

HB_ASKPASS_LOG="$T/ask.log" "$S" _run e-ask true >/dev/null 2>&1; rc=$?
check "câu hỏi host key đi qua askpass; không trả lời thì không kết nối" 'grep -q "yes/no" "$T/ask.log" && [ $rc -ne 0 ] && [ ! -s "$T/known-ask" ]'
check "trả lời yes cho host key: vào được bằng mật khẩu đã lưu" '[ "$(HB_ASKPASS_ANSWER=yes "$S" _run e-ask whoami 2>/dev/null)" = tester ]'

cp "$HOSTBOOK_DIR/secrets/key-a.pub" "$T/old.pub"
"$S" _secret_replace key-a gen remove >/dev/null 2>&1
check "thay key: key mới được đưa lên server và vào được" '[ "$("$S" _run e-key echo moi 2>/dev/null)" = moi ] && ! cmp -s "$T/old.pub" "$HOSTBOOK_DIR/secrets/key-a.pub"'
check "thay key có gỡ key cũ: key cũ bị từ chối" '! ssh -F "$HOSTBOOK_SSH_CONFIG" -o BatchMode=yes -o IdentityAgent=none -o IdentitiesOnly=yes -o IdentityFile="$T/k1" -p $PORT root@127.0.0.1 true </dev/null 2>/dev/null'

"$S" _index >/dev/null; t0=$(date +%s)
res=$("$S" _check e-key e-pass e-wrong e-dead e-behind 2>/dev/null); t1=$(date +%s)
rs(){ printf '%s\n' "$res" | awk -F'\t' -v n="$1" '$1==n{print $2}'; }
check "kiểm tra kết nối: key và mật khẩu đúng thì vào được" '[ "$(rs e-key)" = ok ] && [ "$(rs e-pass)" = ok ]'
check "kiểm tra kết nối: mật khẩu sai báo đúng lý do" '[ "$(rs e-wrong)" = fail ] && printf "%s\n" "$res" | grep "^e-wrong" | grep -q "mật khẩu trong secret mk-sai"'
check "kiểm tra kết nối: server không tới được thì dừng trong thời hạn" '[ "$(rs e-dead)" = fail ] && [ $((t1 - t0)) -le 14 ]'
check "kiểm tra kết nối: server sau máy trung gian hỏng được bỏ qua, không thử" '[ "$(rs e-behind)" = skip ]'

echo; echo "$pass ok, $fail lỗi"; [ $fail -eq 0 ]
