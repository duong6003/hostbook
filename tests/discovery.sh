#!/usr/bin/env bash
# Discovery uses synthetic CLI output and mocked controls; never contacts a real VPN or SSH host.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
export HOME=$T/home HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh XDG_CONFIG_HOME=$T/config
export HOSTBOOK_LIB_DIR=$HERE/../lib HOSTBOOK_LANG_DIR=$HERE/../lang HOSTBOOK_LANG=en HOSTBOOK_KEYCHAIN=file:$T/keychain
unset SSH_AUTH_SOCK SSH_AGENT_PID
mkdir -p "$HOME/.ssh"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/library"
. "$T/library"
trap 'cleanup; rm -rf "$T"' EXIT
pass=0; fail=0
check(){ if ( "$@" ); then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
have(){ case $1 in tailscale) return 0;; avahi-browse|dns-sd) return 1;; *) command -v "$1" >/dev/null 2>&1;; esac; }
tailscale(){ cat <<'STATUS'
100.64.0.1 laptop owner@ linux -
100.64.0.2 server owner@ linux -
100.64.0.3 offline-server owner@ linux -
100.64.0.4 sleeping owner@ linux offline
100.64.0.5 phone owner@ iOS -
100.64.0.6 tablet owner@ android -
STATUS
}
no_network(){ printf unexpected > "$T/network"; return 1; }
sshx(){ no_network; }; ssh(){ no_network; }; nc(){ no_network; }
ip_and_labels(){
  local found; found=$(discover) || return 1
  [ "$(printf '%s\n' "$found" | wc -l | tr -d ' ')" = 2 ] &&
    printf '%s\n' "$found" | grep -Fxq "100.64.0.2|server · $(t disc_ts)" &&
    printf '%s\n' "$found" | grep -Fxq "100.64.0.3|offline-server · $(t disc_ts)" && [ ! -e "$T/network" ]
}
picker_setup(){
  ME=local_user; UI_AT=
  clear(){ :; }; title(){ :; }; dimw(){ :; }
  run_progress(){ discover > "$3"; }
  ui_choice(){ echo 0; }; ui_menu(){ echo 0; }
}
editable_login(){
  picker_setup
  ui_field(){ printf '%s' "$2" > "$T/prefill"; printf 'remote_user@100.64.0.2'; }
  local result; result=$(pick_machine fixture 2>/dev/null) || return 1
  [ "$result" = remote_user@100.64.0.2 ] && [ "$(cat "$T/prefill")" = local_user@100.64.0.2 ] && [ ! -e "$T/network" ]
}
cancel_address(){
  picker_setup; ui_field(){ return 1; }
  ! pick_machine fixture >/dev/null 2>&1 && [ ! -e "$T/network" ]
}
for test in ip_and_labels editable_login cancel_address; do check "$test"; done
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
