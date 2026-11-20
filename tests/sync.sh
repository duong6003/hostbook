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
empty_lock_replaced_during_probe(){
  fixture replaced || return 1
  mkdir "$STATE/sync.lock" && touch -t 202001010000 "$STATE/sync.lock" || return 1
  find(){
    command find "$@" || return 1
    mv "$STATE/sync.lock" "$STATE/previous" && mkdir "$STATE/sync.lock" &&
      : > "$STATE/sync.lock/new-writer"
  }
  ! lock 0 && [ -f "$STATE/sync.lock/new-writer" ] && [ -z "$LOCK_HELD" ]
}
metadata_gate_serializes_acquisition(){
  fixture gate || return 1
  local child i=0 rc=0
  bash -c '. "$1"
    hold_gate(){
      local i=0
      : > "$STATE/gate-ready"
      until [ -e "$STATE/gate-release" ]; do
        [ "$i" -lt 250 ] || return 1
        sleep .02; i=$((i+1))
      done
    }
    lock_edit 0 hold_gate' test "$T/library" &
  child=$!
  until [ -e "$STATE/gate-ready" ]; do
    [ "$i" -lt 100 ] || { wait "$child"; return 1; }
    sleep .02; i=$((i+1))
  done
  if lock 0 || [ -d "$STATE/sync.lock" ]; then rc=1; fi
  : > "$STATE/gate-release"
  wait "$child" && [ "$rc" = 0 ] && lock 0 && unlock && [ ! -d "$STATE/sync.lock" ]
}
missing_lock_tool_is_reported(){
  fixture missing-tool || return 1
  LOCK_TOOL=hostbook-fixture-missing-lock-tool
  ! lock 0 > "$STATE/lock-output" 2>&1 &&
    grep -Fq "$(t dep_missing "$LOCK_TOOL")" "$STATE/lock-output" &&
    ! check_deps report > "$STATE/doctor-output" 2>&1 &&
    grep -Fq "$(t dep_missing "$LOCK_TOOL")" "$STATE/doctor-output" &&
    [ ! -e "$STATE/sync.lock" ] && [ ! -e "$STATE/sync.lock.gate" ]
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
    bash -c '. "$1"; lock 15 || exit 1; mkdir "$STATE/critical" || exit 2; sleep .05; rmdir "$STATE/critical" || exit 3; unlock' test "$T/library" &
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
resolved_rejected_push(){ # automatic|ours: convergent or conflicting final host blocks
  local mode=$1 remote checkpoint merged ahead rc output
  remote_fixture "resolved-$mode" || return 1
  remote=${PEER%/*}/remote.git; output=${DIR%/*}/resolver-output
  printf 'Host local\n  HostName 192.0.2.2\n  User fixture\n' > "$DIR/local.conf"
  g add -A && g commit -qm intermediate || return 1
  printf 'Host local\n  HostName 192.0.2.3\n  User fixture\n' > "$DIR/local.conf"
  g add -A && g commit -qm final || return 1
  if [ "$mode" = automatic ]; then
    cp "$DIR/local.conf" "$PEER/local.conf"
  else
    printf 'Host local\n  HostName 192.0.2.4\n  User fixture\n' > "$PEER/local.conf"
  fi
  git -C "$PEER" add -A && git -C "$PEER" commit -qm peer && git -C "$PEER" push -q || return 1
  checkpoint=$(git --git-dir="$remote" rev-parse main)
  printf '#!/bin/sh\nexit 1\n' > "$remote/hooks/pre-receive"; chmod +x "$remote/hooks/pre-receive"
  ! sync_quiet && [ "$(head -1 "$ST")" = conflict ] || return 1
  resolve_conflict ours > "$output" 2>&1; rc=$?
  merged=$(g rev-parse HEAD); ahead=$(g rev-list --count '@{u}..HEAD')
  [ "$rc" = 1 ] && [ "$(head -1 "$ST")" = pending ] && [ "$(sed -n 2p "$ST")" = "$ahead" ] &&
    [ "$ahead" = 3 ] && [ "$(g rev-list --parents -n 1 HEAD | awk '{print NF}')" = 3 ] &&
    [ "$(git --git-dir="$remote" rev-parse main)" = "$checkpoint" ] &&
    [ "$(host_field local 3)" = 192.0.2.3 ] && validate &&
    [ -z "$(g status --porcelain)" ] && [ ! -d "$STATE/sync.lock" ] || return 1
  if [ "$mode" = automatic ]; then
    grep -F "$(t rc_pending "$ahead")" "$output" >/dev/null &&
      ! grep -F "$(t rc_merged)" "$output" >/dev/null || return 1
  else
    grep -F "$(t rc_pending "$ahead")" "$FLASH" >/dev/null &&
      ! grep -F "$(t done_merged)" "$FLASH" >/dev/null || return 1
  fi
  [ "$(sync_text)" = "$(t sync_pending "$ahead")" ] || return 1
  # A fresh pending status must retry immediately, rather than take the 60-second ok shortcut.
  ! cmd_bg_sync && [ "$(head -1 "$ST")" = pending ] && [ "$(sed -n 2p "$ST")" = "$ahead" ] &&
    [ "$(g rev-parse HEAD)" = "$merged" ] && [ "$(git --git-dir="$remote" rev-parse main)" = "$checkpoint" ] || return 1
  rm "$remote/hooks/pre-receive"
  cmd_bg_sync && [ "$(head -1 "$ST")" = ok ] && [ "$(g rev-parse HEAD)" = "$merged" ] &&
    [ "$(git --git-dir="$remote" rev-parse main)" = "$merged" ] &&
    [ "$(g rev-list --count '@{u}..HEAD')" = 0 ] && [ ! -d "$STATE/sync.lock" ]
}
automatic_rejected_push(){ resolved_rejected_push automatic; }
selected_rejected_push(){ resolved_rejected_push ours; }
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
for test in live_lock foreign_unlock dead_lock empty_lock empty_lock_replaced_during_probe metadata_gate_serializes_acquisition missing_lock_tool_is_reported busy_status parallel_recovery different_branches invalid_pull invalid_clean_repo rejected_push automatic_rejected_push selected_rejected_push failed_link invalid_link; do check "$test"; done
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
