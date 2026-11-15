#!/usr/bin/env bash
# Exercise vault setup with synthetic credentials and disposable repositories only.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang" HOSTBOOK_LANG=vi
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
case_setup(){
  export HOME=$T/$1/home XDG_CONFIG_HOME=$T/$1/config HOSTBOOK_DIR=$T/$1/hosts HOSTBOOK_STATE=$T/$1/state HOSTBOOK_SSH_CONFIG=$T/$1/ssh-config
  export HOSTBOOK_KEYCHAIN=file:$T/$1/identity HOSTBOOK_MASTER=synthetic-vault-password
  mkdir -p "$HOME/.ssh"
  : > "$HOME/.ssh/config"
  printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
  . "$T/lib"
  SELF=$HERE/../hostbook
  trap 'cleanup' EXIT
  clear(){ :; }; box(){ :; }; title(){ :; }; pause(){ :; }
  ui_menu(){ echo 1; }; ui_confirm(){ return 1; }; set_language(){ return 0; }; setting_pick(){ return 1; }
  ask_new_pw(){ printf '%s' "$HOSTBOOK_MASTER"; }
  show_recovery(){ echo shown > "$STATE/recovery-shown"; }
}
pass=0; fail=0
check(){ if "$@"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
new_install(){ (
  case_setup new
  first_run > "$T/new-result" 2>&1 || exit 1
  vault_exists && [ -s "$STATE/recovery-shown" ] && [ "$(kc_get | age-keygen -y)" = "$(cat "$SEC/vault.pub")" ]
); }
canceled_install(){ (
  case_setup canceled
  ask_new_pw(){ return 1; }
  if first_run > "$T/canceled-result" 2>&1; then exit 1; fi
  [ -d "$DIR/.git" ] && ! vault_exists
); }
cloned_vault(){ (
  case_setup clone
  seed=$T/seed; mkdir -p "$seed"; git init -q "$seed"
  cp -R "$T/new/hosts/secrets" "$seed/"
  git -C "$seed" add -A; git -C "$seed" commit -qm vault
  before=$(cat "$seed/secrets/vault.pub")
  ui_menu(){ echo 0; }; pick_machine(){ echo "file://$seed"; }
  gum(){ if [ "$1" = spin ]; then while [ "$1" != -- ]; do shift; done; shift; "$@"; else command gum "$@"; fi; }
  ask_new_pw(){ echo unexpected > "$STATE/unexpected"; return 1; }
  vault_identity(){ echo unexpected > "$STATE/unexpected"; return 1; }
  first_run > "$T/cloned-result" 2>&1 || exit 1
  [ "$(cat "$SEC/vault.pub")" = "$before" ] && [ ! -e "$STATE/unexpected" ] && ! kc_get >/dev/null 2>&1
); }
partial_vault(){ (
  case_setup partial
  mkdir -p "$SEC"; printf 'existing recipient\n' > "$SEC/vault.pub"
  ask_new_pw(){ echo unexpected > "$STATE/unexpected"; return 1; }
  ! vault_init > "$T/partial-result" 2>&1 && [ "$(cat "$SEC/vault.pub")" = 'existing recipient' ] && [ ! -e "$STATE/unexpected" ]
); }
failed_vault_creation(){ (
  case_setup create_failure
  mkdir -p "$DIR"; git -C "$DIR" init -q; g add -A; g commit -qm fixture --allow-empty
  age_pw_enc(){ return 1; }
  ! vault_init > "$T/create-failure" 2>&1 && [ -s "$SEC/vault.pub" ] && [ ! -s "$SEC/vault.key.age" ] && [ ! -e "$STATE/recovery-shown" ] && ! vault_init > "$T/create-failure-again" 2>&1
); }
canceled_first_import(){ (
  case_setup import_cancel
  printf 'Include ~/ssh-hosts/*.conf\nHost original\n  HostName 192.0.2.1\n  IdentityFile ~/.ssh/original\n' > "$HOME/.ssh/config"
  cp "$HOME/.ssh/config" "$T/original-config"; printf 'older backup\n' > "$HOME/.ssh/config.bak"
  ui_confirm(){ return 0; }; import_flow(){ ! grep -q '^Include' "$1" && return 1; return 2; }
  ! first_run > "$T/import-cancel-result" 2>&1 && cmp "$T/original-config" "$HOME/.ssh/config" && [ "$(cat "$HOME/.ssh/config.bak")" = 'older backup' ] && ! ls "$DIR"/*.conf >/dev/null 2>&1
); }
accepted_first_import(){ (
  case_setup import_accept
  printf 'Include ~/ssh-hosts/*.conf\nHost original\n  HostName 192.0.2.1\n  IdentityFile ~/.ssh/original\nHost *\n  ServerAliveInterval 30\n' > "$HOME/.ssh/config"
  cp "$HOME/.ssh/config" "$T/accepted-original"; printf 'older backup\n' > "$HOME/.ssh/config.bak"
  ui_confirm(){ case "$*" in *"$(t first_import_q)"*) return 0;; *) return 1;; esac; }
  import_flow(){ ! grep -q '^Include' "$1" || return 1; printf 'original\n' > "$STATE/last-import"; }
  first_run > "$T/import-accept-result" 2>&1 && cmp "$T/accepted-original" "$HOME/.ssh/config.bak" && grep -q '^Host \*' "$HOME/.ssh/config" && ! grep -q '^Host original' "$HOME/.ssh/config"
); }
declined_import_with_include(){ (
  case_setup declined_include
  printf 'Include ~/.ssh/extra\nHost original\n  HostName 192.0.2.1\n' > "$HOME/.ssh/config"
  printf 'older backup\n' > "$HOME/.ssh/config.bak"
  first_run > "$T/declined-include-result" 2>&1 && grep -q '^Host original' "$HOME/.ssh/config" && [ "$(cat "$HOME/.ssh/config.bak")" = 'older backup' ]
); }
first_run_exec_rejected_before_ssh(){ (
  case_setup unsafe
  printf 'Host original\n  HostName 192.0.2.1\nMatch exec "touch %s"\n  User evil\n' "$T/executed" > "$HOME/.ssh/config"
  ui_confirm(){ return 0; }
  ssh(){ case ${2:-} in */empty) command ssh "$@";; *) : > "$T/ssh-called"; return 0;; esac; }
  ! first_run > "$T/unsafe-result" 2>&1 && [ ! -e "$T/ssh-called" ] && [ ! -e "$T/executed" ] && grep -q '^Host original' "$HOME/.ssh/config"
); }
control_whitespace_rejected_before_ssh(){ (
  case_setup control_whitespace
  local byte failed=0
  for byte in 015 013 014; do
    rm -f "$T/control-executed"
    printf "\\$byte" > "$T/control-ssh"
    printf 'Match exec "touch %s"\n  User bad\n' "$T/control-executed" >> "$T/control-ssh"
    if import_config_guard "$T/control-ssh" >/dev/null 2>&1; then
      ssh -F "$T/control-ssh" -G example >/dev/null 2>&1 || :
      failed=1
    fi
    [ ! -e "$T/control-executed" ] || failed=1
  done
  [ "$failed" = 0 ]
); }
key_only_choices(){ (
  case_setup choices
  secret_names(){ printf 'password\nkey\n'; }
  secret_meta(){ if [ "$1" = key ]; then echo key; else echo password; fi; }
  secret_users(){ :; }; secret_label(){ echo example; }
  ui_menu(){ printf '%s\n' "$@" > "$T/menu-choices"; echo 0; }
  result=$(pick_login '' '' key) || exit 1
  [ "$result" = key ] && [ -s "$T/menu-choices" ] && ! grep -F "$(t act_pw_new)" "$T/menu-choices" && ! grep -F "$(t act_no_login)" "$T/menu-choices" && ! grep -q '^password|' "$T/menu-choices"
); }
match_exec_rejected(){ (
  case_setup match
  printf 'Host safe\n  HostName 192.0.2.1\nMatch exec "touch %s"\n  User evil\n' "$T/match-executed" > "$T/match.conf"
  declare -F import_config_guard >/dev/null || exit 1
  ! import_config_guard "$T/match.conf" || exit 1
  printf 'Match "exec" "touch %s"\n' "$T/match-executed" > "$T/match.conf"
  ! import_config_guard "$T/match.conf" && [ ! -e "$T/match-executed" ]
); }
check new_install
check canceled_install
if [ -d "$T/new/hosts/secrets" ]; then check cloned_vault; else fail=$((fail+1)); echo 'FAIL  cloned_vault: new-install fixture has no vault'; fi
check partial_vault
check failed_vault_creation
check canceled_first_import
check accepted_first_import
check first_run_exec_rejected_before_ssh
check declined_import_with_include
check match_exec_rejected
check control_whitespace_rejected_before_ssh
check key_only_choices
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
