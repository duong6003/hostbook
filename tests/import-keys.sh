#!/usr/bin/env bash
# Imported SSH keys: disposable home, repository, keychain and agent state.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/config" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh-config"
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang" HOSTBOOK_LANG=en HOSTBOOK_KEYCHAIN="file:$T/keychain/id" HOSTBOOK_MASTER='synthetic-master-password'
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE"
printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
clear(){ :; }; pause(){ :; }; ui_menu(){ echo 0; }; ui_choice(){ : > "$T/key-compact"; echo 0; }; ask_secret_name(){ echo imported-key; }
git -C "$DIR" init -q
printf 'tracked original\n' > "$DIR/note"; git -C "$DIR" add note; git -C "$DIR" commit -qm fixture
vault_create "$HOSTBOOK_MASTER" >/dev/null || exit 1
git -C "$DIR" add secrets; git -C "$DIR" commit -qm vault
printf 'staged work\n' >> "$DIR/note"; git -C "$DIR" add note; git -C "$DIR" diff --cached --binary > "$T/preexisting-index"
mkdir -p "$T/key space" "$T/stage"
ssh-keygen -q -t ed25519 -N '' -f "$T/key space/id" || exit 1
cp "$T/key space/id" "$T/original-key"
printf 'Host new-one\n  HostName 192.0.2.1\n  IdentityFile="%s"\n' "$T/key space/id" > "$T/stage/one.conf"
printf 'new-one\n' > "$T/stage/added"
pass=0; fail=0
check(){ if "$@"; then pass=$((pass+1)); echo "ok    $1"; else fail=$((fail+1)); echo "FAIL  $1"; fi; }
external_key(){
  declare -F import_config_keys >/dev/null || return 1
  import_config_keys "$T/stage" >/dev/null 2>&1 || return 1
  git -C "$DIR" diff --cached --binary > "$T/after-key-index"
  cmp "$T/preexisting-index" "$T/after-key-index" && [ "$(git -C "$DIR" show HEAD:note)" = 'tracked original' ] && cmp "$T/original-key" "$T/key space/id" && [ -e "$T/key-compact" ] && [ -s "$SEC/imported-key.age" ] && [ -s "$SEC/imported-key.pub" ] && grep -q '^# secret: imported-key$' "$T/stage/one.conf" && ! grep -Fq "$T/key space/id" "$T/stage/one.conf"
}
deduplicate_fingerprint(){
  cp "$T/key space/id" "$T/key space/other"
  printf 'Host new-two\n  HostName 192.0.2.2\n  IdentityFile "%s"\n' "$T/key space/other" > "$T/stage/one.conf"
  printf 'new-two\n' > "$T/stage/added"
  import_config_keys "$T/stage" >/dev/null 2>&1 && grep -q '^# secret: imported-key$' "$T/stage/one.conf" && [ "$(find "$SEC" -name '*.meta' | wc -l | tr -d ' ')" = 1 ] && cmp "$T/original-key" "$T/key space/other"
}
missing_key_cancel(){
  printf 'Host missing\n  HostName 192.0.2.3\n  IdentityFile "%s"\n' "$T/not-there" > "$T/stage/one.conf"
  printf 'missing\n' > "$T/stage/added"
  (pick_login(){ return 1; }; ! import_config_keys "$T/stage" >/dev/null 2>&1)
}
multiple_keys_rejected(){
  printf 'Host many\n  HostName 192.0.2.4\n  IdentityFile "%s"\n  IdentityFile "%s"\n' "$T/key space/id" "$T/key space/other" > "$T/stage/one.conf"
  printf 'many\n' > "$T/stage/added"
  ! import_config_keys "$T/stage" >/dev/null 2>&1
}
incomplete_managed_rejected(){
  cp "$SEC/imported-key.pub" "$SEC/incomplete.pub"
  printf 'type=key\nfp=invalid\n' > "$SEC/incomplete.meta"
  printf 'Host broken\n  HostName 192.0.2.5\n  IdentityFile "%s"\n' "$SEC/incomplete.pub" > "$T/stage/one.conf"
  printf 'broken\n' > "$T/stage/added"
  (ui_choice(){ : > "$T/incomplete-prompt"; return 1; }; ! import_config_keys "$T/stage" >/dev/null 2>&1) && [ ! -e "$T/incomplete-prompt" ]
}
key_commit_failure_preserves_index(){
  ssh-keygen -q -t ed25519 -N '' -f "$T/second-key" || return 1
  cp "$T/second-key" "$T/second-original"
  printf '#!/bin/sh\nexit 1\n' > "$DIR/.git/hooks/pre-commit"; chmod +x "$DIR/.git/hooks/pre-commit"
  printf 'Host failed-key\n  HostName 192.0.2.12\n  IdentityFile "%s"\n' "$T/second-key" > "$T/stage/one.conf"
  printf 'failed-key\n' > "$T/stage/added"
  (ask_secret_name(){ echo failed-key; }; ! import_config_keys "$T/stage" >/dev/null 2>&1) || return 1
  rm -f "$DIR/.git/hooks/pre-commit"
  git -C "$DIR" diff --cached --binary > "$T/after-failed-key-index"
  cmp "$T/preexisting-index" "$T/after-failed-key-index" && cmp "$T/second-original" "$T/second-key" && [ ! -e "$SEC/failed-key.pub" ] && [ ! -e "$SEC/failed-key.meta" ] && [ ! -e "$SEC/failed-key.age" ] && [ ! -d "$STATE/sync.lock" ]
}
invalid_key_clipboard_never_opens_editor(){
  (clip_get(){ printf 'not a private key\n'; }; IMPORT_KEY_MODE=1; VISUAL=/nonexistent-editor; ! secret_add_key clip should-not-exist > "$T/key-clipboard-output" 2>&1) && grep -Fq "$(t err_import_key_clipboard)" "$T/key-clipboard-output" && [ ! -e "$SEC/should-not-exist.meta" ]
}
package_secret_names_unique(){
  mkdir -p "$T/collisions/secrets"
  printf 'type=password\n' > "$T/collisions/secrets/vault.key.meta"
  printf 'type=password\n' > "$T/collisions/secrets/vault.recovery.meta"
  printf 'one' > "$T/collisions/secrets/vault.key.pass"
  printf 'two' > "$T/collisions/secrets/vault.recovery.pass"
  pack_secret_map "$T/collisions" > "$T/maps" || return 1
  [ "$(cut -f2 "$T/maps" | sort -u | wc -l | tr -d ' ')" = 2 ]
}
managed_subdirectory_must_not_alias_basename(){
  local fake="$SEC/missing/imported-key.pub"
  printf 'Host subdir\n  HostName 192.0.2.10\n  IdentityFile "%s"\n' "$fake" > "$T/stage/one.conf"
  printf 'subdir\n' > "$T/stage/added"
  (pick_login(){ return 1; }; ! import_config_keys "$T/stage" >/dev/null 2>&1) && grep -Fq "$fake" "$T/stage/one.conf"
}
public_key_is_not_private_material(){
  mkdir -p "$T/public-package/secrets"
  cp "$SEC/imported-key.meta" "$T/public-package/secrets/example.meta"
  cp "$SEC/imported-key.pub" "$T/public-package/secrets/example.pub"
  cp "$SEC/imported-key.pub" "$T/public-package/secrets/example.key"
  ! pack_validate_secrets "$T/public-package" >/dev/null 2>&1
}
credential_write_is_locked(){ (
  local tx=$T/key-race
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  HOSTBOOK_KEYCHAIN="file:$tx/keychain/id"
  mkdir -p "$DIR" "$STATE"; : > "$HIST"; printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  git -C "$DIR" init -q; git -C "$DIR" commit --allow-empty -qm fixture; vault_create "$HOSTBOOK_MASTER" >/dev/null || exit 1
  git -C "$DIR" add secrets; git -C "$DIR" commit -qm vault
  ssh-keygen -q -t ed25519 -N '' -f "$tx/race-key" || exit 1
  lock(){ [ ! -e "$SEC/race-key.pub" ] || : > "$tx/wrote-before-lock"; g add -A && g commit --allow-empty -qm concurrent; LOCK_HELD=1; }
  unlock(){ LOCK_HELD=; }
  IMPORT_KEY_MODE=1 secret_add_key "$tx/race-key" race-key >/dev/null 2>&1 || exit 1
  [ ! -e "$tx/wrote-before-lock" ] && [ -s "$SEC/race-key.pub" ] && git -C "$DIR" cat-file -e HEAD:secrets/race-key.pub
); }
package_fingerprint_mismatch(){
  mkdir -p "$T/package/secrets"
  cp "$SEC/imported-key.meta" "$T/package/secrets/example.meta"
  cp "$SEC/imported-key.pub" "$T/package/secrets/example.pub"
  cp "$T/key space/id" "$T/package/secrets/example.key"
  declare -F pack_validate_secrets >/dev/null && pack_validate_secrets "$T/package" >/dev/null 2>&1 || return 1
  sed 's/^fp=.*/fp=SHA256:wrong/' "$T/package/secrets/example.meta" > "$T/package/secrets/meta.new"
  mv "$T/package/secrets/meta.new" "$T/package/secrets/example.meta"
  declare -F pack_validate_secrets >/dev/null && ! pack_validate_secrets "$T/package" >/dev/null 2>&1
}
check external_key
check deduplicate_fingerprint
check missing_key_cancel
check multiple_keys_rejected
check incomplete_managed_rejected
check key_commit_failure_preserves_index
check invalid_key_clipboard_never_opens_editor
check package_fingerprint_mismatch
check package_secret_names_unique
check managed_subdirectory_must_not_alias_basename
check public_key_is_not_private_material
check credential_write_is_locked
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
