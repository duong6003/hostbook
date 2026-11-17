#!/usr/bin/env bash
# Git and locking regressions: local disposable repositories only, no SSH connections.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME=$T/home HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh XDG_CONFIG_HOME=$T/config
export HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=en HOSTBOOK_KEYCHAIN=file:$T/keychain
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@example.test GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@example.test
unset SSH_AUTH_SOCK SSH_AGENT_PID
mkdir -p "$HOME/.ssh"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/library"
. "$T/library"
SELF=$HERE/../hostbook
trap 'cleanup; rm -rf "$T"' EXIT
pass=0; fail=0
check(){ if ( "$@" ); then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
fixture(){
  DIR=$T/$1/hosts; STATE=$T/$1/state; SSHCFG=$T/$1/ssh; SEC=$DIR/secrets
  IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; ST=$STATE/status; GRP=$DIR/groups.tsv; FLASH=$STATE/flash
  export HOSTBOOK_DIR=$DIR HOSTBOOK_STATE=$STATE HOSTBOOK_SSH_CONFIG=$SSHCFG
  LOCK_HELD=
  mkdir -p "$DIR" "$STATE"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host local\n  HostName 192.0.2.1\n  User fixture\n' > "$DIR/local.conf"
  g init -q && g checkout -qb main && g add -A && g commit -qm fixture && ensure_merge_setup && build_index full
}
remote_fixture(){
  fixture "$1" || return 1
  local root=${DIR%/*}
  git init -q --bare "$root/remote.git" && g remote add origin "$root/remote.git" && sync_quiet || return 1
  git --git-dir="$root/remote.git" symbolic-ref HEAD refs/heads/main
  PEER=$root/peer; git clone -q "$root/remote.git" "$PEER"
}
live_lock(){
  fixture live || return 1
  lock 0 || return 1; local owner=$LOCK_HELD
  touch -t 202001010000 "$STATE/sync.lock"
  ( LOCK_HELD=; ! lock 0 ) && [ "$(cat "$STATE/sync.lock/owner")" = "$owner" ] || return 1
  unlock; [ ! -d "$STATE/sync.lock" ]
}
foreign_unlock(){
  fixture foreign || return 1
  lock 0 || return 1; local owner=$LOCK_HELD
  ( LOCK_HELD=not-the-owner; unlock; cleanup )
  [ "$(cat "$STATE/sync.lock/owner")" = "$owner" ] || return 1
  unlock; [ ! -d "$STATE/sync.lock" ]
}
dead_lock(){
  fixture dead || return 1
  (exit 0) & local dead=$!; wait "$dead"
  mkdir "$STATE/sync.lock"; printf '%s\n' "$dead" > "$STATE/sync.lock/pid"
  lock 0 && unlock && [ ! -d "$STATE/sync.lock" ]
}
empty_lock(){
  fixture empty || return 1
  mkdir "$STATE/sync.lock"; ! lock 0 || return 1
  touch -t 202001010000 "$STATE/sync.lock"
  lock 0 && unlock && [ ! -d "$STATE/sync.lock" ]
}
busy_status(){
  fixture busy || return 1
  set_status ok; lock 0 || return 1
  ( LOCK_HELD=; sync_quiet; [ "$?" = 75 ] ) && [ "$(head -1 "$ST")" = ok ] || return 1
  : > "$ST.force"
  ( LOCK_HELD=; cmd_bg_sync; [ "$?" = 75 ] ) && [ "$(head -1 "$ST")" = ok ] || return 1
  unlock
}
parallel_recovery(){
  fixture parallel || return 1
  (exit 0) & local dead=$!; wait "$dead"
  mkdir "$STATE/sync.lock"; printf '%s\n' "$dead" > "$STATE/sync.lock/pid"
  local n pid pids= rc=0
  for n in 1 2 3 4 5 6; do
    bash -c '. "$1"; lock 15 || exit 1; mkdir "$STATE/critical" || exit 2; sleep .05; rmdir "$STATE/critical"; unlock' test "$T/library" &
    pids="$pids $!"
  done
  for pid in $pids; do wait "$pid" || rc=1; done
  [ "$rc" = 0 ] && [ ! -d "$STATE/sync.lock" ] && [ ! -d "$STATE/critical" ]
}
different_branches(){
  fixture branches || return 1
  local root=${DIR%/*}
  git init -q --bare "$root/remote.git" && git clone -q "$root/remote.git" "$root/peer" || return 1
  git -C "$root/peer" checkout -qb master
  printf 'Host remote\n  HostName 192.0.2.2\n' > "$root/peer/remote.conf"
  git -C "$root/peer" add -A && git -C "$root/peer" commit -qm remote && git -C "$root/peer" push -qu origin master || return 1
  git --git-dir="$root/remote.git" symbolic-ref HEAD refs/heads/master
  g remote add origin "$root/remote.git"
  sync_quiet && [ -f "$DIR/remote.conf" ] && [ "$(g rev-parse --abbrev-ref '@{u}')" = origin/master ] &&
    [ "$(g rev-parse HEAD)" = "$(git --git-dir="$root/remote.git" rev-parse master)" ] &&
    ! git --git-dir="$root/remote.git" show-ref --verify --quiet refs/heads/main
}
invalid_pull(){
  remote_fixture invalid || return 1
  local checkpoint; checkpoint=$(g rev-parse HEAD)
  printf 'Host broken\n  HostNmae 192.0.2.3\n' > "$PEER/broken.conf"
  git -C "$PEER" add -A && git -C "$PEER" commit -qm invalid && git -C "$PEER" push -q || return 1
  ! sync_quiet && [ "$(head -1 "$ST")" = invalid ] && [ "$(g rev-parse HEAD)" = "$checkpoint" ] &&
    [ ! -f "$DIR/broken.conf" ] && validate && [ ! -d "$STATE/sync.lock" ]
}
invalid_clean_repo(){
  fixture invalid-clean || return 1
  printf 'UnknownDirective yes\n' >> "$DIR/local.conf"; g add -A; g commit -qm invalid
  ! sync_quiet && [ "$(head -1 "$ST")" = invalid ]
}
rejected_push(){
  remote_fixture rejected || return 1
  local remote=${PEER%/*}/remote.git
  printf '#!/bin/sh\nexit 1\n' > "$remote/hooks/pre-receive"; chmod +x "$remote/hooks/pre-receive"
  printf '\nHost pending\n  HostName 192.0.2.4\n' >> "$DIR/local.conf"
  ! sync_quiet && ! bash "$SELF" sync > "${DIR%/*}/cli-output" 2>&1 &&
    [ "$(head -1 "$ST")" = pending ] && [ "$(sed -n 2p "$ST")" -gt 0 ] &&
    [ -z "$(g status --porcelain)" ] && [ ! -d "$STATE/sync.lock" ]
}
link_controls(){ clear(){ :; }; title(){ :; }; shown(){ :; }; pause(){ :; }; }
failed_link(){
  remote_fixture failed-link || return 1
  local previous; previous=$(remote_url)
  link_controls; pick_machine(){ printf 'file://%s/missing.git\n' "${DIR%/*}"; }
  ! connect_kho >/dev/null 2>&1 && [ "$(remote_url)" = "$previous" ] &&
    [ "$(g rev-parse --abbrev-ref '@{u}')" = origin/main ] && [ ! -d "$STATE/sync.lock" ]
}
invalid_link(){
  remote_fixture invalid-link || return 1
  local checkpoint previous; checkpoint=$(g rev-parse HEAD); previous=$(remote_url)
  printf 'Host broken\n  HostNmae 192.0.2.3\n' > "$PEER/broken.conf"
  git -C "$PEER" add -A && git -C "$PEER" commit -qm invalid && git -C "$PEER" push -q || return 1
  link_controls; pick_machine(){ printf 'file://%s\n' "$previous"; }
  ! connect_kho >/dev/null 2>&1 && [ "$(g rev-parse HEAD)" = "$checkpoint" ] &&
    [ "$(remote_url)" = "$previous" ] && [ ! -f "$DIR/broken.conf" ] && validate && [ ! -d "$STATE/sync.lock" ]
}
for test in live_lock foreign_unlock dead_lock empty_lock busy_status parallel_recovery different_branches invalid_pull invalid_clean_repo rejected_push failed_link invalid_link; do check "$test"; done
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
