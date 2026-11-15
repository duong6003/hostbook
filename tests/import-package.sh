#!/usr/bin/env bash
# Export/import real encrypted packages using synthetic credentials only.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang" HOSTBOOK_LANG=en
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
setup_fixture(){
  export HOME="$T/$1/home" XDG_CONFIG_HOME="$T/$1/config" HOSTBOOK_DIR="$T/$1/hosts" HOSTBOOK_STATE="$T/$1/state" HOSTBOOK_SSH_CONFIG="$T/$1/ssh-config" HOSTBOOK_KEYCHAIN="file:$T/$1/keychain/id" HOSTBOOK_MASTER=synthetic-master-password
  mkdir -p "$HOME/Downloads" "$HOME/.ssh" "$HOSTBOOK_DIR"
  printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
  . "$T/lib"; SELF=$HERE/../hostbook
  clear(){ :; }; pause(){ :; }; flash(){ :; }; ui_menu(){ echo 0; }; copy(){ cat > "$T/clipboard"; }; share_password(){ echo synthetic-package-password; }
  g init -q; g commit --allow-empty -qm fixture
  vault_create "$HOSTBOOK_MASTER" >/dev/null || return 1
  g add -A; g commit -qm vault; build_index full
}
setup_fixture sender || exit 1
ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/synthetic" || exit 1
secret_add_key "$HOME/.ssh/synthetic" synthetic-key >/dev/null || exit 1
secret_add_password synthetic-password synthetic-server-password >/dev/null || exit 1
printf '# secret: synthetic-key\nHost key-host\n  HostName 192.0.2.40\n  IdentityFile "%s/synthetic-key.pub"\n# secret: synthetic-password\nHost password-host\n  HostName 192.0.2.41\n' "$SEC" > "$DIR/test.conf"
g add -A; g commit -qm hosts; build_index full
share_flow key-host password-host > "$T/export-output" 2>&1 || exit 1
set -- "$HOME/Downloads/"*.hbshare
[ "$#" = 1 ] && [ -s "$1" ] && cmp -s "$1" "$T/clipboard" || { echo 'FAIL small export must write a file even when clipboard works'; exit 1; }
cp "$1" "$T/export.hbshare"
share_flow key-host password-host > "$T/second-export-output" 2>&1 || exit 1
set -- "$HOME/Downloads/"*.hbshare
[ "$#" = 2 ] || { echo 'FAIL repeated export overwrote the first package'; exit 1; }
( tar(){ return 1; }; age_pw_enc(){ cat; }; ! share_pack "$T/sender/hosts" synthetic > /dev/null 2>&1 ) || { echo 'FAIL archive failure was hidden'; exit 1; }

! grep -q synthetic-server-password "$T/export.hbshare" || exit 1
(
  setup_fixture receiver || exit 1
  import_review_screen(){ return 0; }; import_done(){ return 0; }
  before=$(g rev-parse HEAD)
  HB_PACK_PW=wrong import_flow "$T/export.hbshare" > "$T/wrong-output" 2>&1 && exit 1
  [ "$(g rev-parse HEAD)" = "$before" ] || exit 1
  HB_PACK_PW=synthetic-package-password import_flow "$T/export.hbshare" > "$T/import-output" 2>&1 || exit 1
  [ "$(secret_get synthetic-password)" = synthetic-server-password ] || exit 1
  secret_get synthetic-key > "$T/received-key"
  cmp -s "$T/received-key" "$T/sender/home/.ssh/synthetic" || exit 1
  [ "$(host_field key-host 12)" = synthetic-key ] && [ "$(host_field password-host 12)" = synthetic-password ] || exit 1
  before=$(g rev-parse HEAD)
  HB_PACK_PW=synthetic-package-password import_flow "$T/export.hbshare" > "$T/repeat-output" 2>&1 || exit 1
  [ "$(g rev-parse HEAD)" = "$before" ] && [ "$(wc -l < "$IDX" | tr -d ' ')" = 2 ] || exit 1
  ! rg -q 'synthetic-server-password|BEGIN OPENSSH PRIVATE KEY' "$STATE/tmp" 2>/dev/null || exit 1
) || { echo 'FAIL encrypted package round trip'; exit 1; }
echo 'ok    encrypted export file → import into a separate vault → duplicate import without changes'
(
  setup_fixture canceled || exit 1
  import_review_screen(){ return 1; }
  before=$(g rev-parse HEAD)
  ! HB_PACK_PW=synthetic-package-password import_flow "$T/export.hbshare" > "$T/cancel-output" 2>&1 && [ "$(g rev-parse HEAD)" = "$before" ] && [ ! -e "$SEC/synthetic-key.age" ] && [ ! -e "$DIR/test.conf" ]
) || { echo 'FAIL canceled package review'; exit 1; }
echo 'ok    canceled review leaves the receiver unchanged'
