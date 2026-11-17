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
pass=0; fail=0; skipped=0
check(){ local rc; if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else rc=$?; if [ "$rc" = 77 ]; then skipped=$((skipped+1)); printf 'SKIP  %s (fixture capability unavailable)\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; fi; }
# Exercise actual installed-style paths from an unrelated working directory.
app="$T/app space's ü"; mkdir -p "$app" "$T/elsewhere"
cp "$HERE/../hostbook" "$app/hostbook"
[ ! -d "$HERE/../lib" ] || cp -R "$HERE/../lib" "$app/lib"
cp -R "$HERE/../lang" "$app/lang"
probe="$app/hostbook"
if ! is_windows; then ln -s "$app/hostbook" "$T/hostbook-link"; probe="$T/hostbook-link"; fi
check 'installed modules resolve through symlinks outside the app directory' '(cd "$T/elsewhere"; unset HOSTBOOK_LIB_DIR HOSTBOOK_LANG_DIR; bash "$probe" --version >/dev/null && bash "$probe" _header >/dev/null && bash "$probe" _preview h:example >/dev/null && printf "one 127.0.0.1\n" | bash "$probe" _import_parse >/dev/null && bash "$probe" _demo >/dev/null)'
check 'missing required module fails with its path while version still works' '(mv "$app/lib/import.sh" "$app/import.saved"; cd "$T/elsewhere"; unset HOSTBOOK_LIB_DIR HOSTBOOK_LANG_DIR; bash "$probe" --version >/dev/null && ! bash "$probe" _import_parse </dev/null 2>"$T/missing" && grep -F "$app/lib/import.sh" "$T/missing" >/dev/null; result=$?; mv "$app/import.saved" "$app/lib/import.sh"; exit "$result")'
check 'missing host UI fails human flows and unknown-underscore search, not callbacks or version' '(mv "$app/lib/hosts.sh" "$app/hosts.saved"; cd "$T/elsewhere"; unset HOSTBOOK_LIB_DIR HOSTBOOK_LANG_DIR; bash "$probe" --version >/dev/null && bash "$probe" _header >/dev/null && bash "$probe" _demo >/dev/null && ! bash "$probe" settings </dev/null 2>"$T/missing-hosts" && grep -F "$app/lib/hosts.sh" "$T/missing-hosts" >/dev/null && ! bash "$probe" _web </dev/null 2>"$T/missing-search-hosts" && grep -F "$app/lib/hosts.sh" "$T/missing-search-hosts" >/dev/null; result=$?; mv "$app/hosts.saved" "$app/lib/hosts.sh"; exit "$result")'
# Unrelated internal callbacks must not parse the import feature.
mkdir -p "$T/modules"; cp "$HERE/../lib/ui.sh" "$T/modules/ui.sh"
printf 'return 1\n' > "$T/modules/import.sh"
check 'unrelated callbacks do not load import' '(export HOSTBOOK_LIB_DIR="$T/modules"; for cmd in _header "_preview h:example" _footer _list _demo; do bash "$app/hostbook" $cmd >/dev/null || exit 1; done)'
check 'import callbacks require import module' '(! HOSTBOOK_LIB_DIR="$T/modules" bash "$app/hostbook" _import_parse </dev/null >/dev/null 2>&1)'
printf 'echo import-needed >&2; return 1\n' > "$T/modules/import.sh"
check 'underscore searches load import for the home screen' '(! HOSTBOOK_LIB_DIR="$T/modules" bash "$app/hostbook" _web </dev/null >/dev/null 2>"$T/search-error" && grep -q import-needed "$T/search-error")'
if command -v tmux >/dev/null 2>&1; then
  session_check(){ (
    export TMUX_TMPDIR="$T/tmux-socket"; unset TMUX; mkdir -p "$TMUX_TMPDIR"
    tmux new-session -d -s hb-fixture "sleep 60" || exit 1
    tmux split-window -t hb-fixture "sleep 60" || exit 1
    tmux new-session -d -s hb-second "sleep 60" || exit 1
    tmux new-session -d -s unrelated "sleep 60" || exit 1
    tmux set-option -g remain-on-exit on
    tmux new-window -t hb-fixture "exit 0" || exit 1
    i=0; while ! tmux list-panes -a -F '#{pane_dead}' | grep -qx 1; do
      i=$((i+1)); [ "$i" -lt 40 ] || { tmux kill-server; exit 1; }; sleep .05
    done
    list_data > "$T/sessions"
    tmux kill-server
    awk -F '\t' '$1 == "s:hb-fixture" && index($0, "2") {found=1} $1 == "s:hb-second" {second=1} $1 == "s:unrelated" {bad=1} END {exit !(found && second && !bad)}' "$T/sessions"
  ); }
  check 'list sessions counts live panes and excludes unrelated sessions' 'session_check'
fi
# Simulate MSYS option parsing even when this suite runs on Unix.
check 'Windows path conversion protects option-like names and preserves drive paths' '(
  is_windows(){ return 0; }
  cygpath(){ [ "$#" = 3 ] && [ "$1" = -u ] && [ "$2" = -- ] || return 2; case "$3" in --help) printf "%s\n" --help;; "C:/Users/fixture name/key") printf "%s\n" "/c/Users/fixture name/key";; *) return 1;; esac; }
  [ "$(unix_path --help)" = --help ] && [ "$(unix_path "C:/Users/fixture name/key")" = "/c/Users/fixture name/key" ]
)'
if is_windows; then
  check 'native cygpath accepts option-like names and drive paths' '(
    [ "$(unix_path --help)" = "$(cygpath -u -- --help)" ] && [ "$(unix_path "C:/Users/fixture name/key")" = "$(cygpath -u -- "C:/Users/fixture name/key")" ]
  )'
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
  cmd_list(){ :; }; cmd_header(){ :; }; cmd_footer(){ :; }
  fzf(){ local arg command; for arg in "$@"; do
    case $arg in ctrl-y:*) command=${arg#*execute-silent(}; command=${command%%)+transform:*}; bash -c "$command" || :;; esac
    done; return 130; }
  home
  [ -f "$STATE/share-request" ]
)'
# Failed host/secret changes must restore their own files without resetting local work.
change_fixture(){
  local tx=$1
  unset SSH_AUTH_SOCK SSH_AGENT_PID
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2
  HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv; ST=$STATE/status; FLASH=$STATE/flash
  mkdir -p "$DIR" "$STATE" "$SEC"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf '# secret: credential\nHost old\n  HostName 192.0.2.1\n  User fixture\n' > "$DIR/a.conf"
  printf 'Host other\n  HostName 192.0.2.2\n  ProxyJump old\n' > "$DIR/b.conf"
  printf 'group\tsync\told other\n' > "$GRP"; printf '1\told\n' > "$HIST"
  printf 'synthetic ciphertext\n' > "$SEC/credential.age"; printf 'type=password\n' > "$SEC/credential.meta"
  printf 'tracked note\n' > "$DIR/note"
  g init -q; g -c user.name=test -c user.email=test@example.com add -A
  g -c user.name=test -c user.email=test@example.com commit -qm fixture
  # Touched and unrelated tracked files both have different staged and working versions.
  printf '# staged\n' >> "$DIR/a.conf"; printf '# staged\n' >> "$DIR/b.conf"; printf 'staged\n' >> "$DIR/note"; g add -A
  printf '# unstaged\n' >> "$DIR/a.conf"; printf '# unstaged\n' >> "$DIR/b.conf"; printf 'unstaged\n' >> "$DIR/note"
  printf 'Host untracked\n  HostName 192.0.2.3\n' > "$DIR/untracked.conf"
  printf 'untracked secret\n' > "$SEC/local.age"
  printf 'pre-existing scratch\n' > "$DIR/a.conf.tmp"
  chmod 640 "$DIR/a.conf"; chmod 400 "$SEC/credential.age"
  build_index full
  mkdir "$tx/before"; cp -Rp "$DIR/." "$tx/before/"; cp -p "$HIST" "$tx/history"
}
change_preserved(){
  local tx=$1
  # Compare all working files and the exact Git index, including untracked files and custom modes.
  diff -r -x .git "$tx/before" "$DIR" && cmp "$tx/before/.git/index" "$DIR/.git/index" &&
    [ "$(git -C "$tx/before" rev-parse HEAD)" = "$(g rev-parse HEAD)" ] &&
    cmp "$tx/history" "$HIST" && [ ! -d "$STATE/sync.lock" ] &&
    [ "$(perm "$DIR/a.conf")" = "$(perm "$tx/before/a.conf")" ] &&
    [ "$(perm "$SEC/credential.age")" = "$(perm "$tx/before/secrets/credential.age")" ] &&
    host_exists old && ! host_exists renamed && [ "$(host_field other 6)" = old ]
}
change_failure(){ (
  local cmd=$1 mode=$2 tx="$T/change $1-$2"
  change_fixture "$tx" || exit 1
  local args=(old)
  case $cmd in
    rename_host_raw) args+=(renamed);;
    move_host_raw) args+=(new-project);;
    set_host_secret) args+=("");;
    secret_rename_raw) args=(credential renamed);;
    secret_delete_raw) args=(credential);;
  esac
  # Keep raw mutations real, but fail after they have written/deleted/renamed their paths.
  eval "$(declare -f "$cmd" | sed "1s/^$cmd/original_change/")"
  eval "$cmd(){ original_change \"\$@\" || return 1; : > \"\$tx/wrote\"; [ \"\$mode\" != command ]; }"
  if [ "$mode" = validation ]; then
    eval "$(declare -f validate | sed '1s/^validate/original_validate/')"
    validate(){
      if [ -f "$tx/wrote" ]; then printf 'injected validation failure\n'; return 1; fi
      original_validate
    }
  fi
  ! apply_change fixture "$cmd" "${args[@]}" > "$tx/result" 2>&1 || exit 1
  [ -f "$tx/wrote" ] && change_preserved "$tx"
); }
for change_cmd in delete_host_raw rename_host_raw move_host_raw set_host_secret secret_rename_raw secret_delete_raw; do
  for change_mode in command validation; do
    check "$change_cmd: failed $change_mode preserves staged, unstaged and untracked data" 'change_failure "$change_cmd" "$change_mode"'
  done
done
change_io_failure(){ (
  local cmd=$1 tx="$T/io-$1-${2:-first}" target args=(old)
  change_fixture "$tx" || exit 1
  case $cmd in
    rename_host_raw) args+=(renamed);;
    move_host_raw) args+=(new-project);;
    set_host_secret) args+=("");;
    secret_rename_raw) args=(credential renamed);;
    secret_delete_raw) args=(credential);;
  esac
  target=$DIR/a.conf.tmp; [ "$cmd" != secret_rename_raw ] || target=$SEC/credential.age
  case ${2:-} in late-secret) target=$SEC/credential.meta;; history) target=$HIST.tmp;; esac
  mv(){ if [ "$1" = "$target" ]; then : > "$tx/io-failed"; return 1; fi; command mv "$@"; }
  rm(){ local a; for a in "$@"; do if [ "$cmd" = secret_delete_raw ] && [ "$a" = "$SEC/credential.age" ]; then : > "$tx/io-failed"; return 1; fi; done; command rm "$@"; }
  ! apply_change fixture "$cmd" "${args[@]}" > "$tx/result" 2>&1 && [ -f "$tx/io-failed" ] && change_preserved "$tx"
); }
for change_cmd in delete_host_raw rename_host_raw move_host_raw set_host_secret secret_rename_raw secret_delete_raw; do
  check "$change_cmd: a failed filesystem operation triggers complete rollback" 'change_io_failure "$change_cmd"'
done
check 'secret rename rolls back ciphertext already moved before a later metadata failure' 'change_io_failure secret_rename_raw late-secret'
check 'host rename rolls back config and groups when the final history write fails' 'change_io_failure rename_host_raw history'
check 'a failed commit restores mutated files and the pre-existing staged index' '(
  tx=$T/change-commit; change_fixture "$tx"
  git(){ case " $* " in *" commit "*) return 1;; esac; command git "$@"; }
  ! apply_change fixture rename_host_raw old renamed > "$tx/result" 2>&1 && change_preserved "$tx"
)'
check 'successful changes still validate, index and commit under the lock' '(
  tx=$T/change-success; change_fixture "$tx"
  g config user.name fixture; g config user.email fixture@example.com
  apply_change fixture rename_host_raw old renamed > "$tx/result" 2>&1 &&
    host_exists renamed && ! host_exists old && [ "$(host_field other 6)" = renamed ] &&
    grep -q renamed "$HIST" && grep -q renamed "$GRP" && [ -z "$(g status --porcelain)" ] && [ ! -d "$STATE/sync.lock" ]
)'
check 'a failed UI notification never rolls back an already successful data commit' '(
  tx=$T/change-notice; change_fixture "$tx"
  g config user.name fixture; g config user.email fixture@example.com
  flash(){ return 1; }; mkdir "$ST.force"
  apply_change fixture rename_host_raw old renamed > "$tx/result" 2>&1 &&
    host_exists renamed && ! host_exists old && [ -z "$(g status --porcelain)" ] && [ ! -d "$STATE/sync.lock" ]
)'
check 'move accepts an existing project with uppercase and underscore characters' '(
  tx=$T/change-existing-project; change_fixture "$tx"
  g config user.name fixture; g config user.email fixture@example.com
  printf "Host resident\n  HostName 192.0.2.4\n" > "$DIR/Prod_eu.conf"
  apply_change fixture move_host_raw old Prod_eu > "$tx/result" 2>&1 &&
    [ "$(host_field old 2)" = Prod_eu ] && [ "$(host_field resident 2)" = Prod_eu ] && validate &&
    [ -z "$(g status --porcelain)" ] && [ ! -d "$STATE/sync.lock" ]
)'
check 'move keeps strict new project names and rejects traversal' '(
  tx=$T/change-new-project; change_fixture "$tx"
  printf "Host outside\n  HostName 192.0.2.5\n" > "$tx/outside.conf"; cp "$tx/outside.conf" "$tx/outside-before"
  for project in Prod_new prod_new ../outside; do
    ! apply_change fixture move_host_raw old "$project" > "$tx/result" 2>&1 || exit 1
    change_preserved "$tx" && cmp "$tx/outside.conf" "$tx/outside-before" || exit 1
  done
)'
check 'apply rejects invalid pre-existing config under lock before writing' '(
  tx=$T/change-invalid; change_fixture "$tx"
  printf "BadConfigOption yes\n" >> "$DIR/b.conf"; cp "$DIR/b.conf" "$tx/before/b.conf"
  delete_host_raw(){ : > "$tx/wrote"; return 1; }
  ! apply_change fixture delete_host_raw old > "$tx/result" 2>&1 && [ ! -f "$tx/wrote" ] && change_preserved "$tx"
)'
check 'secret rename rejects an occupied orphan destination under lock' '(
  tx=$T/change-collision; change_fixture "$tx"
  printf "other instance ciphertext\n" > "$SEC/renamed.age"; cp "$SEC/renamed.age" "$tx/before/secrets/renamed.age"
  ! apply_change fixture secret_rename_raw credential renamed > "$tx/result" 2>&1 && change_preserved "$tx"
)'
check 'apply rechecks a host rename destination under lock' '(
  tx=$T/change-host-collision; change_fixture "$tx"
  ! apply_change fixture rename_host_raw old other > "$tx/result" 2>&1 && change_preserved "$tx"
)'
check 'apply refuses symlinked scratch destinations without changing their targets' '(
  tx=$T/change-symlink; change_fixture "$tx"
  printf "outside fixture\n" > "$tx/outside"; rm "$DIR/a.conf.tmp"; ln -s "$tx/outside" "$DIR/a.conf.tmp" 2>/dev/null || exit 77
  [ -L "$DIR/a.conf.tmp" ] || exit 77
  ! apply_change fixture delete_host_raw old > "$tx/result" 2>&1 && [ "$(cat "$tx/outside")" = "outside fixture" ] &&
    [ -L "$DIR/a.conf.tmp" ] && cmp "$DIR/a.conf" "$tx/before/a.conf" && cmp "$DIR/.git/index" "$tx/before/.git/index" && [ ! -d "$STATE/sync.lock" ]
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
printf '\n%s passed, %s failed, %s skipped\n' "$pass" "$fail" "$skipped"
[ "$fail" = 0 ]
