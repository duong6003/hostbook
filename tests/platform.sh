#!/usr/bin/env bash
# Small platform-boundary checks; fixtures never touch real hostbook data.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR="$HERE/../lang"   # the library is sourced from a temporary copy: tell it where the texts are
export HOME="$T/home" HOSTBOOK_STATE="$T/state" HOSTBOOK_DIR="$T/hosts" XDG_CONFIG_HOME="$T/config"
mkdir -p "$HOME"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
# shellcheck disable=SC1090
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
: > "$IDX"
pass=0; fail=0
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
# Exercise actual installed-style paths from an unrelated working directory.
app="$T/app space's ü"; mkdir -p "$app" "$T/elsewhere"
cp "$HERE/../hostbook" "$app/hostbook"
[ ! -d "$HERE/../lib" ] || cp -R "$HERE/../lib" "$app/lib"
cp -R "$HERE/../lang" "$app/lang"
probe="$app/hostbook"
if ! is_windows; then ln -s "$app/hostbook" "$T/hostbook-link"; probe="$T/hostbook-link"; fi
check 'installed modules resolve through symlinks outside the app directory' '(cd "$T/elsewhere"; unset HOSTBOOK_LIB_DIR HOSTBOOK_LANG_DIR; bash "$probe" --version >/dev/null && bash "$probe" _header >/dev/null && bash "$probe" _preview h:example >/dev/null && printf "one 127.0.0.1\n" | bash "$probe" _import_parse >/dev/null && bash "$probe" _demo >/dev/null)'
check 'missing required module fails with its path while version still works' '(mv "$app/lib/import.sh" "$app/import.saved"; cd "$T/elsewhere"; unset HOSTBOOK_LIB_DIR HOSTBOOK_LANG_DIR; bash "$probe" --version >/dev/null && ! bash "$probe" _import_parse </dev/null 2>"$T/missing" && grep -F "$app/lib/import.sh" "$T/missing" >/dev/null; result=$?; mv "$app/import.saved" "$app/lib/import.sh"; exit "$result")'
# Unrelated internal callbacks must not parse the import feature.
mkdir -p "$T/modules"; cp "$HERE/../lib/ui.sh" "$T/modules/ui.sh"
printf 'return 1\n' > "$T/modules/import.sh"
check 'unrelated callbacks do not load import' '(export HOSTBOOK_LIB_DIR="$T/modules"; for cmd in _header "_preview h:example" _footer _list _demo; do bash "$app/hostbook" $cmd >/dev/null || exit 1; done)'
check 'import callbacks require import module' '(! HOSTBOOK_LIB_DIR="$T/modules" bash "$app/hostbook" _import_parse </dev/null >/dev/null 2>&1)'
if command -v tmux >/dev/null 2>&1; then
  session_check(){ (
    export TMUX_TMPDIR="$T/tmux-socket"; unset TMUX; mkdir -p "$TMUX_TMPDIR"
    tmux new-session -d -s hb-fixture "sleep 60" || exit 1
    tmux split-window -t hb-fixture "sleep 60" || exit 1
    tmux new-session -d -s unrelated "sleep 60" || exit 1
    list_data > "$T/sessions"
    tmux kill-server
    awk -F '\t' '$1 == "s:hb-fixture" && index($0, "2") {found=1} $1 == "s:unrelated" {bad=1} END {exit !(found && !bad)}' "$T/sessions"
  ); }
  check 'list sessions counts live panes and excludes unrelated sessions' 'session_check'
fi
# Replacing this with raw single quotes breaks callbacks under a path containing apostrophes.
SELF="$T/a space's hostbook"
printf '#!/usr/bin/env bash\nprintf callback-ok\n' > "$SELF"; chmod +x "$SELF"
check 'callback command safely quotes spaces and apostrophes' '[ "$(bash -c "$(self_cmd) _header")" = callback-ok ]'
check 'all emitted fzf callbacks use the quoted command' 'fzf_opts "$T" | grep -F "$(self_cmd) _header" >/dev/null'
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
check 'CRLF settings preserve values' 'cfg_load; [ "$(cfg list cards)" = compact ]'
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
# run_progress must stop the whole process tree (a grandchild such as git's ssh), not only its direct child
run_progress "x" 1 "$T/rp.out" bash -c 'sleep 4123 & sleep 4123' 2>/dev/null && rp=0 || rp=$?
check 'run_progress: the time limit stops the job and its grandchild' '[ "$rp" = 124 ] && ! ps -ef | grep -q "[s]leep 4123"'
{ bash -c 'sleep 4124 & sleep 4124' & rpid=$!; sleep 0.3; kill_tree $rpid; wait $rpid || :; } 2>/dev/null
check 'kill_tree: no survivor among the descendants' '! ps -ef | grep -q "[s]leep 4124"'
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
