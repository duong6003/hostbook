#!/usr/bin/env bash
# Import results and undo use only disposable repositories and test data.
set -u
unset SSH_AUTH_SOCK SSH_AGENT_PID
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/config" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh-config"
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang" HOSTBOOK_LANG=en HOSTBOOK_KEYCHAIN="file:$T/keychain/id"
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR" "$HOSTBOOK_STATE"
printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
git -C "$DIR" init -q; git -C "$DIR" commit --allow-empty -qm initial
for i in $(seq 1 30); do printf 'Host very-long-server-name-%02d\n  HostName 192.0.2.%s\n  User fixture\n' "$i" "$i"; done > "$DIR/very-long-project-name.conf"
git -C "$DIR" add .; git -C "$DIR" commit -qm import
IMPORT_COMMIT=$(g rev-parse HEAD); build_index full; cut -f1 "$IDX" > "$STATE/last-import"
term_rows(){ echo 24; }; term_cols(){ if [ -n "${1:-}" ]; then printf -v "$1" '%s' 44; else echo 44; fi; }
pause(){ :; }; clear(){ :; }; shown(){ :; }; flash(){ : > "$T/flashed"; }
ui_menu(){
  printf '%s\n' "${UI_HEAD:-}" > "$T/header"
  printf '%s\n' "$@" > "$T/actions"
  local i; i=$(cat "$T/count" 2>/dev/null || echo 0); echo $((i+1)) > "$T/count"
  sed -n "$((i+1))p" "$T/picks"
}
eval "$(declare -f undo_last | sed '1s/^undo_last/real_undo/')"
undo_last(){ : > "$T/undo-called"; return 1; }
check_hosts(){ : > "$T/check-called"; }
reset(){ echo 0 > "$T/count"; rm -f "$T/undo-called" "$T/flashed"; }
pass=0; fail=0
check(){ if "$@"; then pass=$((pass+1)); echo "ok    $1"; else fail=$((fail+1)); echo "FAIL  $1"; fi; }
review_is_read_only_and_shows_saved_fields(){ (
  local stage=$T/review-stage before
  mkdir -p "$stage"
  printf '# tags: prod api\n# desc: Long service description\n# secret: login-key\nHost new-service\n  HostName 192.0.2.88\n  User deploy\n  Port 2222\n  ProxyJump jump-host\n' > "$stage/project.conf"
  printf 'new-service\n' > "$stage/added"; printf 'previous-result\n' > "$STATE/last-import"
  printf 'old\texisting\nnew-service\tduplicate\n' > "$stage/skipped"; printf 'bad line\n' > "$stage/invalid"
  printf 'new-service\t/path/key\n' > "$stage/key-refs"; printf 'new-service\tlogin-key\t/path/managed.pub\n' > "$stage/bindings"
  before=$(g rev-parse HEAD)
  ui_fzf(){ printf '%s\n' "$@" > "$T/review-args"; cat > "$T/review-rows"; return 130; }
  declare -F import_review >/dev/null || exit 1
  ! import_review "$stage" 'input file' 1 0 2 1 >/dev/null 2>&1 || exit 1
  grep -q 'new-service' "$T/review-rows" && grep -q '192.0.2.88' "$T/review-rows" && grep -q 'deploy' "$T/review-rows" && grep -q '2222' "$T/review-rows" && grep -q 'prod' "$T/review-rows" && grep -q 'login-key' "$T/review-rows" && grep -q 'jump-host' "$T/review-rows" && grep -q 'Long service description' "$T/review-rows" && grep -q '/path/key' "$T/review-rows" && grep -q 'already exists' "$T/review-rows" && grep -q 'repeated in input' "$T/review-rows" && grep -q 'bad line' "$T/review-rows" && grep -q 'input file' "$T/review-args" && [ "$(g rev-parse HEAD)" = "$before" ] && [ "$(cat "$STATE/last-import")" = previous-result ]
); }
package_review_shows_final_remapped_values(){ (
  local pkg=$T/package-review
  mkdir -p "$pkg"
  printf 'same\tproj\t192.0.2.20\tdev\t22\t\t\tprod\tmain\tfile\t1\tssh-key\nclient\tproj\t192.0.2.21\tdev\t22\tops@same:2222,external\t\tapi\tclient desc\tfile\t2\t\n' > "$pkg/index"
  printf 'same\tboth\nclient\tadd\n' > "$pkg/decisions"
  printf 'ssh-key\tlocal-key\tnew\n' > "$pkg/smap"
  ui_fzf(){ printf '%s\n' "$@" > "$T/package-review-args"; cat > "$T/package-review-rows"; return 130; }
  declare -F import_review_package >/dev/null || exit 1
  ! import_review_package "$pkg" "$pkg/decisions" 'package source' >/dev/null 2>&1 || exit 1
  grep -q 'same-2' "$T/package-review-rows" && grep -q 'ops@same-2:2222,external' "$T/package-review-rows" && grep -q 'local-key' "$T/package-review-rows" && grep -q 'package source' "$T/package-review-args" && grep -Fq '2 new' "$T/package-review-args"
); }
prod_update_requires_explicit_confirmation(){ (
  printf 'protected\tupdate\n' > "$T/prod-decisions"
  is_prod(){ [ "$1" = protected ]; }; ui_field(){ : > "$T/prod-prompt"; return 1; }
  ! pack_confirm_prod "$T/prod-decisions" && [ -e "$T/prod-prompt" ]
); }
package_choice_cancel_aborts(){ (
  mkdir -p "$T/cancel-package"
  printf 'changed\tdiff\tdetails\n' > "$T/cancel-package/classes"
  ui_menu(){ return 130; }
  ! pack_decide "$T/cancel-package" ask > "$T/cancel-decisions" && [ ! -s "$T/cancel-decisions" ]
); }
result_actions_stay_visible(){
  reset; printf '2\n' > "$T/picks"
  import_done 30 0 0 >/dev/null 2>&1 || return 1
  [ "$(wc -l < "$T/header" | tr -d ' ')" -le 9 ] && grep -Fq "$(t act_imp_view)" "$T/actions" && grep -Fq "$(t act_imp_undo)" "$T/actions"
}
canceled_undo_returns_to_result(){
  reset; printf '3\n2\n' > "$T/picks"
  import_done 30 0 0 >/dev/null 2>&1 && [ -e "$T/undo-called" ] && [ "$(cat "$T/count")" = 2 ]
}
stale_undo_does_not_revert_newer_head(){
  reset; git -C "$DIR" commit --allow-empty -qm unrelated; printf '3\n2\n' > "$T/picks"
  import_done 30 0 0 >/dev/null 2>&1 && [ ! -e "$T/undo-called" ] && [ "$(cat "$T/count")" = 2 ] && [ "$(git -C "$DIR" log -1 --format=%s)" = unrelated ]
}
successful_real_undo(){ (
  reset
  printf 'Host undo-target\n  HostName 192.0.2.99\n' > "$DIR/undo.conf"
  git -C "$DIR" add undo.conf; git -C "$DIR" commit -qm 'import for undo'
  IMPORT_COMMIT=$(g rev-parse HEAD); build_index full; printf 'undo-target\n' > "$STATE/last-import"; printf '3\n' > "$T/picks"
  ui_confirm(){ return 0; }; undo_last(){ real_undo "$@"; }
  import_done 1 0 0 >/dev/null 2>&1 && [ ! -e "$DIR/undo.conf" ] && git -C "$DIR" log -1 --format=%s | grep -q '^Revert '
); }
undo_race_preserves_newer_commit(){ (
  reset
  IMPORT_COMMIT=$(g rev-parse HEAD); printf '3\n2\n' > "$T/picks"
  undo_last(){ git -C "$DIR" commit --allow-empty -qm race-newer; real_undo "$@"; }
  ui_confirm(){ : > "$T/unexpected-confirm"; return 0; }
  import_done 1 0 0 >/dev/null 2>&1 && [ ! -e "$T/unexpected-confirm" ] && [ "$(g log -1 --format=%s)" = race-newer ] && [ "$(cat "$T/count")" = 2 ]
); }
zero_change_has_no_undo(){
  reset; : > "$STATE/last-import"; printf '3\n' > "$T/picks"
  import_done 0 0 30 >/dev/null 2>&1 && [ -e "$T/flashed" ] && [ ! -e "$T/undo-called" ] && [ "$(cat "$T/count")" = 0 ]
}
check review_is_read_only_and_shows_saved_fields
check package_review_shows_final_remapped_values
check prod_update_requires_explicit_confirmation
check package_choice_cancel_aborts
check result_actions_stay_visible
check canceled_undo_returns_to_result
check stale_undo_does_not_revert_newer_head
check successful_real_undo
check undo_race_preserves_newer_commit
check zero_change_has_no_undo
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
