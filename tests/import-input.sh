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
ui_menu(){
  printf '%s\n' "$@" >> "$T/menu-log"
  local n; n=$(cat "$T/pick-number" 2>/dev/null || echo 0); echo $((n+1)) > "$T/pick-number"
  sed -n "$((n+1))p" "$T/picks"
}
file_pick(){ [ "${PICK_CANCEL:-}" != 1 ] || return 1; printf '%s\n' "$INPUT_FILE"; }
ui_multiline(){ [ "${PASTE_CANCEL:-}" != 1 ] || return 1; printf '%s\n' "${PASTE_VALUE:-}"; }
clip_get(){ [ "${CLIP_FAIL:-}" != 1 ] || return 1; cat "$T/clipboard"; }
confirm(){ : > "$T/confirmed"; return 1; }
pick(){ echo 0; }
apply_import_config(){ : > "$T/applied"; return 1; }
import_done(){ : > "$T/result"; return 0; }
pass=0; fail=0
check(){ if "$@"; then pass=$((pass+1)); echo "ok    $1"; else fail=$((fail+1)); echo "FAIL  $1"; fi; }
reset_menu(){ : > "$T/menu-log"; echo 0 > "$T/pick-number"; rm -f "$T/applied" "$T/confirmed"; }
source_menu_offers_choices(){
  reset_menu; printf '3\n' > "$T/picks"
  ! import_flow > "$T/output" 2>&1 && grep -Fq "$(t import_source_file)" "$T/menu-log" && grep -Fq "$(t import_source_paste)" "$T/menu-log" && grep -Fq "$(t import_source_clipboard)" "$T/menu-log" && [ ! -e "$T/editor-called" ]
}
file_cancel_returns_to_source(){
  reset_menu; printf '0\n3\n' > "$T/picks"
  (PICK_CANCEL=1; ! import_flow > "$T/output" 2>&1) && [ "$(cat "$T/pick-number")" = 2 ] && [ ! -e "$T/editor-called" ]
}
paste_cancel_returns_to_source(){
  reset_menu; printf '1\n3\n' > "$T/picks"
  (PASTE_CANCEL=1; ! import_flow > "$T/output" 2>&1) && [ "$(cat "$T/pick-number")" = 2 ] && [ ! -e "$T/editor-called" ]
}
clipboard_unavailable_returns_to_source(){
  reset_menu; printf '2\n3\n' > "$T/picks"
  (CLIP_FAIL=1; ! import_flow > "$T/output" 2>&1) && [ "$(cat "$T/pick-number")" = 2 ] && [ ! -e "$T/editor-called" ]
}
private_key_rejected(){
  reset_menu; printf '%s\n' '-----BEGIN OPENSSH PRIVATE KEY-----' 'sensitive' > "$T/private"
  ! import_flow "$T/private" > "$T/output" 2>&1 && [ ! -s "$T/menu-log" ] && ! grep -q sensitive "$T/output" && [ ! -e "$T/applied" ] && [ ! -e "$T/editor-called" ]
}
binary_rejected(){
  reset_menu; printf 'Host safe\000unsafe\n' > "$T/binary"
  ! import_flow "$T/binary" > "$T/output" 2>&1 && [ ! -s "$T/menu-log" ] && [ ! -e "$T/confirmed" ] && [ ! -e "$T/applied" ] && [ ! -e "$T/editor-called" ]
}
malformed_package_rejected(){
  reset_menu; printf '# hostbook-share v9\nHost unsafe\n' > "$T/malformed"
  ! import_flow "$T/malformed" > "$T/output" 2>&1 && [ ! -s "$T/menu-log" ] && [ ! -e "$T/editor-called" ]
}
quoted_path_bypasses_menu(){
  reset_menu; INPUT_FILE="$T/a space's ü.conf"
  printf 'Host test-host\n  HostName 192.0.2.1\n' > "$INPUT_FILE"
  rm -f "$T/confirmed"
  ! import_flow "$INPUT_FILE" > "$T/output" 2>&1 && [ -e "$T/confirmed" ] && [ ! -s "$T/menu-log" ] && [ ! -e "$T/editor-called" ]
}
paste_keeps_last_submitted_draft(){
  reset_menu; printf '1\n1\n3\n' > "$T/picks"; echo 0 > "$T/paste-number"
  (ui_multiline(){
    local n; n=$(cat "$T/paste-number"); echo $((n+1)) > "$T/paste-number"
    if [ "$n" = 0 ]; then printf '%s\n' '-----BEGIN OPENSSH PRIVATE KEY-----' 'draft-secret'; return 0; fi
    printf '%s' "$2" > "$T/last-draft"; return 1
  }; ! import_flow > "$T/output" 2>&1) && grep -q 'draft-secret' "$T/last-draft" && ! grep -q 'draft-secret' "$T/output" && [ ! -e "$T/editor-called" ]
}
real_package_cleanup_and_offer_suppression(){
  local p="$T/share" hash
  mkdir -p "$p/conf"; printf 'version=1\nfrom=fixture\n' > "$p/manifest"; : > "$p/groups.tsv"
  printf 'Host private-stage-marker\n  HostName 192.0.2.70\n' > "$p/conf/test.conf"
  { printf '# hostbook-share v1 · 1 server\n'; tar -czf - -C "$p" . | AGE_PASSPHRASE=fixture age -e -j batchpass -a; } > "$T/share.hbshare" || return 1
  (import_pack(){ local opened; opened=$(HB_PACK_PW=fixture pack_open "$1") || return 1; grep -q private-stage-marker "$opened/conf/test.conf"; }; import_flow "$T/share.hbshare" > "$T/output" 2>&1) || return 1
  hash=$(c=$(head -c 200000 "$T/share.hbshare"); printf '%s' "$c" | sha256)
  [ "$(cat "$STATE/last-offer")" = "$hash" ] || return 1
  (clip_get(){ cat "$T/share.hbshare"; }; ui_confirm(){ : > "$T/offered-again"; return 1; }; clip_offer) || return 1
  [ ! -e "$T/offered-again" ] && ! rg -q private-stage-marker "$STATE/tmp" 2>/dev/null
}
check source_menu_offers_choices
check file_cancel_returns_to_source
check paste_cancel_returns_to_source
check clipboard_unavailable_returns_to_source
check private_key_rejected
check binary_rejected
check malformed_package_rejected
check quoted_path_bypasses_menu
check paste_keeps_last_submitted_draft
check real_package_cleanup_and_offer_suppression
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
