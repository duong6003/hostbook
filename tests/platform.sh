#!/usr/bin/env bash
# Small platform-boundary checks; fixtures never touch real hostbook data.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" HOSTBOOK_STATE="$T/state" HOSTBOOK_DIR="$T/hosts" XDG_CONFIG_HOME="$T/config"
mkdir -p "$HOME"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
# shellcheck disable=SC1090
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
: > "$IDX"
pass=0; fail=0
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
# Replacing this with raw single quotes breaks callbacks under a path containing apostrophes.
SELF="$T/a space's hostbook"
printf '#!/usr/bin/env bash\nprintf callback-ok\n' > "$SELF"; chmod +x "$SELF"
check 'callback command safely quotes spaces and apostrophes' '[ "$(bash -c "$(self_cmd) _header")" = callback-ok ]'
check 'all emitted fzf callbacks use the quoted command' 'fzf_opts | grep -F "$(self_cmd) _header" >/dev/null'
printf 'Host example\n%s\n' "$(login_lines "$T/a space.pub")" > "$T/ssh"
check 'SSH key path preserves spaces through OpenSSH config parsing' 'ssh -F "$T/ssh" -G example 2>/dev/null | grep -F "identityfile $T/a space.pub" >/dev/null'
mkdir -p "$DIR"
printf 'Host example\r\n%s\r\n' "$(login_lines "$T/a space.pub")" > "$DIR/fixture.conf"
: > "$T/a space.pub"
check 'legacy key discovery keeps the entire quoted path' '[ "$(legacy_keys | cut -f1)" = "$T/a space.pub" ]'
# Crypto is outside this boundary: keep its result fixed while exercising the real config migration.
check 'migration rewrites a quoted legacy path containing spaces' '(ensure_index(){ :; }; secret_add_key(){ echo fixture; }; migrate_keys_raw >/dev/null; grep -q "secrets/fixture.pub" "$DIR/fixture.conf")'
printf 'Host crlf\r\n  HostName 127.0.0.1\r\n  User fixture\r\n' > "$DIR/crlf.conf"
printf 'Include "%s/*.conf"\n' "$DIR" > "$T/crlf-ssh"
SSHCFG=$T/crlf-ssh
check 'CRLF Host lines remain discoverable' '[ "$(index_file "$DIR/crlf.conf" | cut -f1)" = crlf ]'
mkdir -p "$CONF"; printf 'list = compact\r\n' > "$CFG"
check 'CRLF settings preserve values' '[ "$(cfg list cards)" = compact ]'
printf 'fixture\ttabs\tcrlf example\r\n' > "$GRP"
check 'CRLF groups preserve host names' '[ "$(group_field fixture 3)" = "crlf example" ]'
is_windows(){ return 0; }
export HOSTBOOK_KEYCHAIN="file:$T/cache/identity"
check 'Windows refuses plaintext file identity cache overrides' '[ "$(kc_backend)" = windows ]'
check 'Windows multi-host says unsupported before probing tmux' '! (pause(){ :; }; need_tmux) > "$T/tmux" 2>&1 && grep -qi Windows "$T/tmux"'
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
