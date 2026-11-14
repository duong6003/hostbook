#!/usr/bin/env bash
# Requires the native Windows runtime; never count a Unix skip as a pass.
set -eu
case $(uname -s) in MINGW*|MSYS*) ;; *) echo 'SKIP windows.sh: requires real Windows'; exit 0;; esac
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME=$T/home HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state XDG_CONFIG_HOME=$T/config HOSTBOOK_SSH_CONFIG=$T/ssh_config
mkdir -p "$HOME" "$HOSTBOOK_DIR"
export HOSTBOOK_MASTER='windows-vault-fixture'
S=$HERE/../hostbook
sed '/^# ---------- entry point ----------/,$d' "$S" > "$T/lib"
. "$T/lib"; SELF=$S
trap 'cleanup; rm -rf "$T"' EXIT
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
