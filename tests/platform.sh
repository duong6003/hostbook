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
check 'index notices edits when the host directory contains spaces' '(
  DIR="$T/hosts space"; mkdir -p "$DIR"
  SSHCFG=$T/space-ssh; printf "Include \"%s/*.conf\"\n" "$DIR" > "$SSHCFG"
  printf "Host edited\n  HostName 127.0.0.1\n" > "$DIR/hosts.conf"; build_index full
  printf "Host edited\n  HostName 127.0.0.2\n" > "$DIR/hosts.conf"; build_index
  [ "$(host_field edited 3)" = 127.0.0.2 ]
)'
check 'cloned Windows repositories can commit without global Git identity' '(
  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
  git init -q "$T/seed"
  git -C "$T/seed" -c user.name=seed -c user.email=seed@example.com commit -q --allow-empty -m seed
  DIR=$T/cloned; git clone -q "$T/seed" "$DIR"
  is_windows(){ return 0; }; native_path(){ printf "%s\n" "$1"; }
  ensure_merge_setup
  printf "Host cloned\n  HostName 127.0.0.1\n" > "$DIR/cloned.conf"
  g add cloned.conf; g commit -qm edited
)'
check 'home Ctrl-Y callback quotes an apostrophe in the state directory' '(
  STATE="$T/state space$(printf "\047")s"; mkdir -p "$STATE"; unset HOSTBOOK_ONLY
  clip_offer(){ :; }; cmd_list(){ :; }; cmd_header(){ :; }; cmd_footer(){ :; }
  fzf(){ local arg command; for arg in "$@"; do
    case $arg in ctrl-y:*) command=${arg#*execute-silent(}; command=${command%%)+transform:*}; bash -c "$command" || :;; esac
    done; return 130; }
  home
  [ -f "$STATE/share-request" ]
)'
is_windows(){ return 0; }
export HOSTBOOK_KEYCHAIN="file:$T/cache/identity"
check 'Windows refuses plaintext file identity cache overrides' '[ "$(kc_backend)" = windows ]'
check 'Windows multi-host says unsupported before probing tmux' '! (pause(){ :; }; need_tmux) > "$T/tmux" 2>&1 && grep -qi Windows "$T/tmux"'
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
