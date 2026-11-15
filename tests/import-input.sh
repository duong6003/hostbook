#!/usr/bin/env bash
# Import source routing uses disposable data and never launches an editor.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/config" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh-config"
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang" HOSTBOOK_LANG=en HOSTBOOK_KEYCHAIN="file:$T/keychain/id"
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE" "$T/bin"
printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
printf '#!/bin/sh\nprintf called > "%s/editor-called"\nexit 1\n' "$T" > "$T/bin/editor"; chmod +x "$T/bin/editor"
export EDITOR="$T/bin/editor" VISUAL="$T/bin/editor"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
git -C "$DIR" init -q; git -C "$DIR" commit --allow-empty -qm fixture
build_index full
clear(){ :; }; pause(){ :; }
clear(){ :; }; pause(){ :; }; flash(){ :; }
pass=0; fail=0
check(){ if ( "$@" ); then pass=$((pass+1)); echo "ok    $1"; else fail=$((fail+1)); echo "FAIL  $1"; fi; }
import_review(){ return 1; }
file_pick(){ printf '%s\n' "$@" > "$T/picker-args"; return 1; }
package_picker_is_direct(){
  ui_choice(){ echo unexpected > "$T/unexpected-menu"; return 1; }
  ! import_flow > "$T/output" 2>&1 && [ -s "$T/picker-args" ] && grep -Fq '*.hbshare' "$T/picker-args" && [ ! -e "$T/unexpected-menu" ]
}
plain_content_is_rejected(){
  local data
  for data in 'Host wrong' 'test 192.0.2.1 ubuntu' '-----BEGIN OPENSSH PRIVATE KEY-----'; do
    printf '%s\n' "$data" > "$T/not-a-package.hbshare"
    ! import_flow "$T/not-a-package.hbshare" > "$T/output" 2>&1 || return 1
    grep -Fq "$(t err_import_package_only)" "$T/output" || return 1
  done
}
malformed_versions_are_rejected(){
  local version
  import_pack(){ touch "$T/unexpected-package"; return 1; }
  for version in v9 v10 v1broken; do
    printf '# hostbook-share %s\n' "$version" > "$T/malformed"
    ! import_flow "$T/malformed" > "$T/output" 2>&1 || return 1
  done
  [ ! -e "$T/unexpected-package" ]
}
make_package(){
  mkdir -p "$T/share/conf"
  printf 'version=1\nfrom=fixture\n' > "$T/share/manifest"; : > "$T/share/groups.tsv"
  printf 'Host package-host\n  HostName 192.0.2.70\n' > "$T/share/conf/test.conf"
  share_pack "$T/share" fixture > "$T/share.hbshare"
}
literal_paths_and_content_detection(){
  make_package || return 1
  mkdir -p "$T/cwd"; cp "$T/share.hbshare" "$T/cwd/--help"; cp "$T/share.hbshare" "$T/cwd/~literal"; cp "$T/share.hbshare" "$HOME/tilde"
  cp "$T/share.hbshare" "$T/a space's ü.data"
  import_pack(){ local opened; opened=$(HB_PACK_PW=fixture pack_open "$1") || return 1; grep -q package-host "$opened/conf/test.conf"; }
  cd "$T/cwd" || return 1
  local name
  for name in --help '~literal' '~/tilde' "$T/a space's ü.data"; do import_flow "$name" > "$T/output" 2>&1 || return 1; done
  ! rg -q package-host "$STATE/tmp" 2>/dev/null
}
wrong_password_and_damage_do_not_write(){
  make_package || return 1
  local before; before=$(g rev-parse HEAD)
  HB_PACK_PW=wrong import_flow "$T/share.hbshare" > "$T/output" 2>&1 && return 1
  printf '# hostbook-share v1\nnot encrypted\n' > "$T/broken.hbshare"
  HB_PACK_PW=fixture import_flow "$T/broken.hbshare" > "$T/output" 2>&1 && return 1
  [ "$(g rev-parse HEAD)" = "$before" ] && [ -z "$(g status --porcelain)" ]
}
invalid_file_can_be_retried(){
  printf 'not a package\n' > "$T/invalid.hbshare"; make_package || return 1
  rm -f "$T/picker-once"
  file_pick(){ if [ -e "$T/picker-once" ]; then echo "$T/share.hbshare"; else touch "$T/picker-once"; echo "$T/invalid.hbshare"; fi; }
  import_pack(){ HB_PACK_PW=fixture pack_open "$1" >/dev/null; }
  import_flow > "$T/output" 2>&1 && grep -Fq "$(t err_import_package_only)" "$T/output"
}
check package_picker_is_direct
check plain_content_is_rejected
check malformed_versions_are_rejected
check literal_paths_and_content_detection
check wrong_password_and_damage_do_not_write
check invalid_file_can_be_retried
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
