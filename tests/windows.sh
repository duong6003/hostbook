#!/usr/bin/env bash
# Requires the native Windows runtime; never count a Unix skip as a pass.
set -eu
case $(uname -s) in MINGW*|MSYS*) ;; *) echo 'SKIP windows.sh: requires real Windows'; exit 0;; esac
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
# Literal apostrophes can defeat MSYS's heuristic conversion; native tools must receive explicit native paths.
T="$T/a space's"; mkdir -p "$T"
trap 'rm -rf "$T"' EXIT
export HOME=$T/home HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state XDG_CONFIG_HOME=$T/config HOSTBOOK_SSH_CONFIG=$T/ssh_config
mkdir -p "$HOME" "$HOSTBOOK_DIR"
export HOSTBOOK_MASTER='windows-vault-fixture'
S=$HERE/../hostbook
export HOSTBOOK_LANG_DIR=$HERE/../lang   # the copied lib cannot find lang/ next to itself
sed '/^# ---------- entry point ----------/,$d' "$S" > "$T/lib"
. "$T/lib"; SELF=$S
trap 'cleanup; rm -rf "$(dirname "$T")"' EXIT
check(){ "$@" || { printf 'FAIL %s\n' "$*" >&2; exit 1; }; }
text='Tiếng Việt · clipboard ✓'
printf %s "$text" | copy
check test "$(clip_get | tr -d '\r')" = "$text"
echo 'ok    native UTF-8 clipboard round trip'
d=$(tmpd); printf private-fixture > "$d/plain"
HB_ACL_PATH=$(cygpath -w "$d/plain") powershell.exe -NoProfile -NonInteractive -Command '
$a=Get-Acl -LiteralPath $env:HB_ACL_PATH; $s=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach($r in $a.Access){if($r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $s){throw "broad temp ACL"}}'
echo 'ok    plaintext temp file inherits owner-only NTFS ACL'
vault_create "$HOSTBOOK_MASTER" >/dev/null
check test -f "$HOSTBOOK_SESSION_CACHE"
check test "$(windows_tool cache-get "$(cygpath -w "$HOSTBOOK_SESSION_CACHE")" | age-keygen -y)" = "$(cat "$SEC/vault.pub")"
check test "$(grep -a -c 'AGE-SECRET-KEY' "$HOSTBOOK_SESSION_CACHE" || :)" = 0
check test "$(kc_backend)" = windows
echo 'ok    DPAPI cache decrypts for this user without plaintext identity persistence'
printf fixture | age_pw_enc '/slash-leading-master-password' > "$T/slash.age"
MSYS2_ENV_CONV_EXCL=AGE_PASSPHRASE AGE_PASSPHRASE='/slash-leading-master-password' HB_AGE_FILE=$(cygpath -w "$T/slash.age") powershell.exe -NoProfile -NonInteractive -Command '
if ((& age -d -j batchpass $env:HB_AGE_FILE) -ne "fixture") { throw "passphrase was changed by MSYS conversion" }'
echo 'ok    native age can decrypt a slash-leading passphrase unchanged across platforms'
# Filtering is a CLI-only probe; windows-fzf.ps1 tests real interactive callbacks.
printf 'host\n' | fzf --filter host >/dev/null
echo 'ok    native fzf CLI filter'
# Simulate the first-machine choice with only UI decisions stubbed; no global Git identity exists.
(
  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
  set_language(){ :; }; ui_menu(){ echo 1; }; ui_confirm(){ return 1; }; setting_pick(){ return 1; }
  clear(){ :; }; box(){ :; }; title(){ :; }; pause(){ :; }
  # The app deliberately uses set -u without errexit; run this sourced flow with the same semantics.
  first_run || { echo 'FAIL first-machine setup'; exit 1; }
  g log -1 --format=%s | grep -q .
)
echo 'ok    first-machine setup creates its first commit without a preconfigured Git identity'
# Exercise sharing through native age plus MSYS tar/SSH config in the same protected, spaced path.
printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
check secret_add_password shared-password 'synthetic-shared-secret' >/dev/null
host_block win-shared 127.0.0.1 fixture 22 '' shared-password '' '' > "$DIR/share.conf"
build_index full
bundle=$(tmpd); bundle=$bundle/package
check share_build "$bundle" 1 win-shared
check test "$(sed -n 's/^from=//p' "$bundle/manifest")" = "$(hostname | cut -d. -f1)"
share_pack "$bundle" '/shared-package-password' > "$T/share.hbshare"
opened=$(HB_PACK_PW='/shared-package-password' pack_open "$T/share.hbshare") || exit 1
check test "$(cat "$opened/secrets/shared-password.pass")" = synthetic-shared-secret
check test "$(pack_index "$opened" | cut -f1)" = win-shared
echo 'ok    encrypted share bundle round trip preserves credentials and parses SSH config on Windows'
