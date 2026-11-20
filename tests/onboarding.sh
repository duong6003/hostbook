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
  ui_menu(){ echo 1; }; ui_choice(){ echo 0; }; ui_confirm(){ return 1; }; set_language(){ return 0; }; setting_pick(){ return 1; }
  import_review(){ return 0; }
  ask_new_pw(){ printf '%s' "$HOSTBOOK_MASTER"; }
  show_recovery(){ echo shown > "$STATE/recovery-shown"; }
}
pass=0; fail=0
check(){ local rc
  # ONBOARDING_ONLY="a b": run only the named checks.
  case " ${ONBOARDING_ONLY:-$1} " in *" $1 "*) ;; *) return 0;; esac
  if "$@"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else rc=$?; if [ "$rc" = 77 ]; then printf 'SKIP  %s (symlinks unavailable)\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; fi; }
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
  ui_choice(){ if [ "$1" = "$(t first_role)" ]; then echo 0; else echo 1; fi; }; pick_machine(){ echo "file://$seed"; }
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
  ! vault_init > "$T/partial-result" 2>&1 && ! bash "$HERE/../hostbook" _vault_init "$HOSTBOOK_MASTER" >/dev/null 2>&1 && [ "$(cat "$SEC/vault.pub")" = 'existing recipient' ] && [ ! -e "$SEC/vault.key.age" ] && [ ! -e "$STATE/unexpected" ]
); }
symlink_vault_rejected(){ (
  case_setup symlink_vault
  mkdir -p "$DIR" "$T/outside-vault"; ln -s "$T/outside-vault" "$SEC" 2>/dev/null || exit 77
  [ -L "$SEC" ] || exit 77
  ! bash "$HERE/../hostbook" _vault_init "$HOSTBOOK_MASTER" >/dev/null 2>&1 && [ -z "$(ls -A "$T/outside-vault")" ]
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
  ui_confirm(){ return 0; }; import_config_file(){ ! grep -q '^Include' "$1" && return 1; return 2; }
  ! first_run > "$T/import-cancel-result" 2>&1 && cmp "$T/original-config" "$HOME/.ssh/config" && [ "$(cat "$HOME/.ssh/config.bak")" = 'older backup' ] && ! ls "$DIR"/*.conf >/dev/null 2>&1
); }
accepted_first_import(){ (
  case_setup import_accept
  printf 'Include ~/ssh-hosts/*.conf\nHost original\n  HostName 192.0.2.1\n  IdentityFile ~/.ssh/original\nHost *\n  ServerAliveInterval 30\n' > "$HOME/.ssh/config"
  cp "$HOME/.ssh/config" "$T/accepted-original"; printf 'older backup\n' > "$HOME/.ssh/config.bak"
  ui_confirm(){ case "$*" in *"$(t first_import_q)"*) return 0;; *) return 1;; esac; }
  import_config_file(){ ! grep -q '^Include' "$1" || return 1; printf 'original\n' > "$STATE/last-import"; }
  first_run > "$T/import-accept-result" 2>&1 && [ "$(cat "$HOME/.ssh/config.bak")" = 'older backup' ] && cmp "$T/accepted-original" "$HOME/.ssh/"config.bak.* && grep -q '^Host \*' "$HOME/.ssh/config" && ! grep -q '^Host original' "$HOME/.ssh/config"
); }
real_first_import_with_key(){ (
  case_setup first_key
  ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/key file" || exit 1
  cp "$HOME/.ssh/key file" "$T/first-original-key"
  printf 'Host first-host\n  HostName 192.0.2.20\n  IdentityFile "%s"\n' "$HOME/.ssh/key file" > "$HOME/.ssh/config"
  cp "$HOME/.ssh/config" "$T/first-original-config"
  ui_menu(){ case "$1" in "$(t first_how)") echo 1;; *'Key trong cấu hình SSH:'*) echo 0;; *) echo 2;; esac; }
  ui_confirm(){ case "$*" in *"$(t first_import_q)"*|*"$(t impf_ask)"*) return 0;; *) return 1;; esac; }
  ask_secret_name(){ echo first-key; }
  first_run > "$T/first-key-result" 2>&1 || exit 1
  cmp "$T/first-original-key" "$HOME/.ssh/key file" && cmp "$T/first-original-config" "$HOME/.ssh/"config.bak.* && [ -s "$SEC/first-key.age" ] && grep -q '^# secret: first-key' "$DIR/imported.conf" && ! grep -Fq "$HOME/.ssh/key file" "$DIR/imported.conf"
); }
global_precedence_preserved(){ (
  case_setup precedence
  printf 'User global-user\nHost target\n  HostName 192.0.2.1\n  User host-user\nHost *\n  ServerAliveInterval 30\n' > "$HOME/.ssh/config"
  before=$(ssh -F "$HOME/.ssh/config" -G target 2>/dev/null | awk '$1=="user" {print $2}')
  ui_confirm(){ case "$*" in *"$(t first_import_q)"*|*"$(t impf_ask)"*) return 0;; *) return 1;; esac; }
  ui_menu(){ case "$1" in "$(t first_how)") echo 1;; *) echo 2;; esac; }
  first_run > "$T/precedence-result" 2>&1 || exit 1
  awk -v d="$DIR" '$1=="Include" && $2=="~/ssh-hosts/*.conf" {printf "Include \"%s/*.conf\"\n",d; next} {print}' "$HOME/.ssh/config" > "$T/precedence-config"
  after=$(ssh -F "$T/precedence-config" -G target 2>/dev/null | awk '$1=="user" {print $2}')
  [ "$before" = global-user ] && [ "$after" = "$before" ] && grep -q '^Host \*' "$HOME/.ssh/config"
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
canceled_vault_entry_routes(){ (
  case_setup entry_routes
  mkdir -p "$DIR"; git -C "$DIR" init -q
  printf 'Host named\n  HostName 192.0.2.1\n' > "$DIR/hosts.conf"
  awk -v marker="$T/route-entered" '
    /^# ---------- entry point ----------/ {
      print "check_deps(){ :; }"
      print "ask_new_pw(){ return 1; }"
      print "home(){ : > \"" marker "\"; }"
      print "connect(){ : > \"" marker "\"; }"
      print "import_flow(){ ONLY_NEXT=$STATE/last-import; }"
    }
    {print}
  ' "$HERE/../hostbook" > "$T/route-hostbook"
  ! bash "$T/route-hostbook" named >/dev/null 2>&1 && [ ! -e "$T/route-entered" ] || exit 1
  ! bash "$T/route-hostbook" search >/dev/null 2>&1 && [ ! -e "$T/route-entered" ] || exit 1
  ! bash "$T/route-hostbook" import dummy >/dev/null 2>&1 && [ ! -e "$T/route-entered" ] || exit 1
  bash "$T/route-hostbook" _header >/dev/null 2>&1 && [ ! -e "$T/route-entered" ]
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
# A short destination list is a choice; a discovered list can still be searched.
machine_picker_controls(){ (
  case_setup picker
  local count=0 selected=1 result
  run_progress(){ local j; : > "$3"; for ((j=1;j<=count;j++)); do printf 'node%s.example|fixture\n' "$j" >> "$3"; done; }
  ui_choice(){ printf choice > "$T/picker-kind"; printf '%s' "$selected"; }
  ui_menu(){ printf menu > "$T/picker-kind"; printf '%s' "$selected"; }
  ui_field(){ if [ "$1" = "$(t kho_url_label)" ]; then printf 'https://example.invalid/hosts.git'; else printf '%s' "$2"; fi; }
  result=$(pick_machine Pick url) || exit 1
  [ "$result" = https://example.invalid/hosts.git ] && [ "$(cat "$T/picker-kind")" = choice ] || exit 1
  count=2
  result=$(pick_machine Pick url) || exit 1
  [ "$result" = "$ME@node2.example" ] && [ "$(cat "$T/picker-kind")" = choice ] || exit 1
  count=4; selected=2
  result=$(pick_machine Pick url) || exit 1
  [ "$result" = "$ME@node3.example" ] && [ "$(cat "$T/picker-kind")" = menu ] || exit 1
  count=0; ui_choice(){ return 1; }
  ! pick_machine Pick url
); }
canceled_start_choice(){ (
  case_setup canceled_choice
  ui_choice(){ return 1; }
  ! first_run >/dev/null 2>&1 && [ ! -d "$DIR/.git" ] && ! vault_started
); }
hub_start_choice(){ (
  case_setup hub_choice
  ui_choice(){ echo 1; }
  make_kho_here(){ : > "$T/hub-selected"; }
  first_run >/dev/null 2>&1 && vault_exists && [ -f "$T/hub-selected" ]
); }
guided_setup_choices(){ (
  case_setup guided
  ui_choice(){
    printf '%s\n' "$1" >> "$T/guided-steps"
    [ "$#" = 3 ] || return 1
    echo 0
  }
  first_run > "$T/guided-result" 2>&1 || exit 1
  [ "$(sed -n '1p' "$T/guided-steps")" = "$(t first_role)" ] &&
  [ "$(sed -n '2p' "$T/guided-steps")" = "$(t first_how)" ]
); }
password_prompt_policies(){ (
  case_setup password_policy
  . "$T/lib"
  clear(){ :; }; title(){ :; }; ui_keys(){ :; }
  gum(){
    printf '%s\n' "$*" >> "$T/password-headers"
    printf '%s' 'x'
  }
  [ "$(ask_new_pw "$(t pw_login_head)" 1 2>/dev/null)" = x ] || exit 1
  ! grep -q 'ít nhất' "$T/password-headers" || exit 1
  : > "$T/password-headers"
  gum(){
    printf '%s\n' "$*" >> "$T/password-headers"
    if [ "$(wc -l < "$T/password-headers")" -eq 1 ]; then printf x; else printf synthetic-password; fi
  }
  [ "$(ask_new_pw "$(t vault_master)" 10 2>/dev/null)" = synthetic-password ] &&
  [ "$(wc -l < "$T/password-headers" | tr -d ' ')" = 3 ] && grep -q 'ít nhất 10' "$T/password-headers"
); }
vault_login_sources(){ (
  case_setup login_sources
  secret_names(){ :; }
  local source result
  ui_menu(){
    printf '%s\n' "$@" > "$T/vault-login-menu"
    echo 0
  }
  ui_choice(){ printf '%s\n' "$@" > "$T/vault-login-sources"; echo "$source"; }
  pick_key_file(){ echo /synthetic/key; }
  secret_add_key(){ printf '%s\n' "$1" > "$T/vault-login-source"; echo saved-key; }
  secret_add_password(){ echo saved-password; }
  for source in 0 1 3; do
    result=$(pick_login) || exit 1
    case $source in
      0) [ "$result" = saved-key ] && [ "$(cat "$T/vault-login-source")" = /synthetic/key ] || exit 1;;
      1) [ "$result" = saved-key ] && [ "$(cat "$T/vault-login-source")" = clip ] || exit 1;;
      3) [ "$result" = saved-password ] || exit 1;;
    esac
    grep -Fq "$(t login_add_vault)" "$T/vault-login-menu" || exit 1
    ! grep -Fq "$(t act_key_file)" "$T/vault-login-menu" || exit 1
  done
  source=0
  secret_add_key(){ return 1; }
  ! pick_login >/dev/null 2>&1
); }
vault_login_cancel_returns_to_list(){ (
  case_setup login_cancel
  secret_names(){ echo saved-key; }
  secret_meta(){ echo key; }; secret_users(){ :; }; secret_label(){ echo ed25519; }
  ui_menu(){
    if [ -e "$T/login-add-opened" ]; then echo 0; else touch "$T/login-add-opened"; echo 1; fi
  }
  ui_choice(){ echo 0; }
  pick_key_file(){ return 1; }
  secret_add_key(){ echo unexpected > "$T/login-unexpected-import"; return 1; }
  [ "$(pick_login)" = saved-key ] && [ ! -e "$T/login-unexpected-import" ]
); }
vault_key_import_choices(){ (
  case_setup login_key_only
  secret_names(){ :; }
  ui_menu(){ echo 0; }
  ui_choice(){
    [ "$#" = 4 ] && ! printf '%s\n' "$@" | grep -Fq "$(t act_pw_new)" || return 1
    echo 1
  }
  secret_add_key(){ [ "$1" = clip ] && echo stored-key; }
  [ "$(pick_login '' '' key)" = stored-key ]
); }
secrets_manager_vault_first(){ (
  case_setup manager
  secret_names(){ echo saved-key; }; secret_users(){ :; }; secret_meta(){ echo key; }; secret_label(){ echo ed25519; }; legacy_keys(){ :; }
  ui_menu(){ printf '%s\n' "$@" > "$T/manager-menu"; echo 0; }
  secret_actions(){ [ "$1" = saved-key ]; }
  secret_manager >/dev/null 2>&1 || exit 1
  grep -Fq "$(t login_add_vault)" "$T/manager-menu" && ! grep -Fq "$(t act_key_file)" "$T/manager-menu" || exit 1
  rm -f "$T/manager-add"
  ui_menu(){ if [ -e "$T/manager-add" ]; then echo 0; else touch "$T/manager-add"; echo 1; fi; }
  ui_choice(){ printf '%s\n' "$@" > "$T/manager-sources"; return 1; }
  secret_manager >/dev/null 2>&1 && grep -Fq "$(t act_key_file)" "$T/manager-sources" || exit 1
  rm -f "$T/manager-add"
  ui_choice(){ echo 0; }; pick_key_file(){ return 1; }
  pause(){ touch "$T/manager-unexpected-pause"; }
  secret_manager >/dev/null 2>&1 && [ ! -e "$T/manager-unexpected-pause" ]
); }
check secrets_manager_vault_first
check vault_login_cancel_returns_to_list
check vault_key_import_choices
check vault_login_sources
check password_prompt_policies
check guided_setup_choices
check machine_picker_controls
check canceled_start_choice
check hub_start_choice
check new_install
check canceled_install
if [ -d "$T/new/hosts/secrets" ]; then check cloned_vault; else fail=$((fail+1)); echo 'FAIL  cloned_vault: new-install fixture has no vault'; fi
check partial_vault
check symlink_vault_rejected
check failed_vault_creation
check canceled_first_import
check accepted_first_import
check real_first_import_with_key
check global_precedence_preserved
check first_run_exec_rejected_before_ssh
check declined_import_with_include
check match_exec_rejected
check control_whitespace_rejected_before_ssh
check canceled_vault_entry_routes
check key_only_choices
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
