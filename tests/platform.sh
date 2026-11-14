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
printf 'Host example\n%s\n' "$(login_lines "$T/a space.pub")" > "$DIR/fixture.conf"
: > "$T/a space.pub"
check 'legacy key discovery keeps the entire quoted path' '[ "$(legacy_keys | cut -f1)" = "$T/a space.pub" ]'
is_windows(){ return 0; }
export HOSTBOOK_KEYCHAIN="file:$T/cache/identity"
check 'Windows refuses plaintext file identity cache overrides' '[ "$(kc_backend)" = windows ]'
check 'Windows multi-host says unsupported before probing tmux' '! (pause(){ :; }; need_tmux) > "$T/tmux" 2>&1 && grep -qi Windows "$T/tmux"'
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
