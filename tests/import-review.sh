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
review_shows_saved_directives_not_effective_defaults(){ (
  local stage="$T/default-stage" before
  DIR="$T/default-repo"; STATE="$T/default-state"; SSHCFG="$T/default-ssh"; IDX="$STATE/index.tsv"; IDXD="$STATE/idx2"; ST="$STATE/status"; GRP="$DIR/groups.tsv"
  mkdir -p "$stage" "$DIR" "$STATE"
  printf 'User deploy\nPort 2222\nInclude "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host *\n  User fallback\n  Port 2223\n' > "$DIR/project.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm initial; build_index full
  printf 'Host preview-explicit\n  HostName=192.0.2.55\n  User "fixture"\n  User ignored\n  Port=2200\nHost preview-inherited\n  HostName 192.0.2.56\n' > "$stage/project.conf"
  printf 'preview-explicit\npreview-inherited\n' > "$stage/added"
  import_snapshot_targets "$stage" || exit 1
  before=$(g rev-parse HEAD)
  ui_fzf(){ cat > "$T/default-review"; return 0; }
  import_review "$stage" defaults 2 0 0 0 || exit 1
  [ "$(g rev-parse HEAD)" = "$before" ] && ! grep -q preview-explicit "$DIR/project.conf" || exit 1
  grep -Fq '"fixture"@192.0.2.55:2200' "$T/default-review" && grep -Fq 'SSH config@192.0.2.56:SSH config' "$T/default-review" || exit 1
  apply_import_config "$stage" >/dev/null 2>&1 || exit 1
  grep -Fq 'User "fixture"' "$DIR/project.conf" && grep -Fq 'Port=2200' "$DIR/project.conf" || exit 1
  sshx -G preview-explicit 2>/dev/null | grep -q '^user deploy$' && sshx -G preview-explicit 2>/dev/null | grep -q '^port 2222$'
); }
all_skipped_review_accepts_no_stage_files(){ (
  local stage="$T/empty-review"; mkdir -p "$stage"; : > "$stage/added"; printf 'old\texisting\n' > "$stage/skipped"
  ui_fzf(){ cat > "$T/empty-review-rows"; return 0; }
  import_review "$stage" empty 0 0 1 0 && grep -q 'old' "$T/empty-review-rows"
); }
package_review_shows_final_remapped_values(){ (
  local pkg=$T/package-review
  mkdir -p "$pkg"
  printf 'Host same\n  HostName 192.0.2.20\nHost client\n  HostName 192.0.2.21\n' > "$pkg/fixture.conf"
  printf 'same\tproj\t192.0.2.20\tdev\t22\t\t\tprod\tmain\t%s\t1\tssh-key\nclient\tproj\t192.0.2.21\tdev\t22\tops@same:2222,external\t\tapi\tclient desc\t%s\t3\t\n' "$pkg/fixture.conf" "$pkg/fixture.conf" > "$pkg/index"
  printf 'same\tboth\nclient\tadd\n' > "$pkg/decisions"
  printf 'ssh-key\tlocal-key\tnew\n' > "$pkg/smap"
  ui_fzf(){ printf '%s\n' "$@" > "$T/package-review-args"; cat > "$T/package-review-rows"; return 130; }
  declare -F import_review_package >/dev/null || exit 1
  ! import_review_package "$pkg" "$pkg/decisions" 'package source' >/dev/null 2>&1 || exit 1
  grep -q 'same-2' "$T/package-review-rows" && grep -q 'ops@same-2:2222,external' "$T/package-review-rows" && grep -q 'local-key' "$T/package-review-rows" && grep -q 'package source' "$T/package-review-args" && grep -Fq '2 new' "$T/package-review-args"
); }
package_applies_exact_reviewed_block(){ (
  local pkg="$T/exact-package" range
  DIR="$T/exact-repo"; STATE="$T/exact-state"; SSHCFG="$T/exact-ssh"; IDX="$STATE/index.tsv"; IDXD="$STATE/idx2"; ST="$STATE/status"; GRP="$DIR/groups.tsv"; SEC="$DIR/secrets"
  mkdir -p "$DIR" "$STATE" "$pkg/conf" "$pkg/secrets"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host same\n  HostName 192.0.2.1\n  Port 2200\n' > "$DIR/proj.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm initial; build_index full
  printf 'version=1\nfrom=fixture\n' > "$pkg/manifest"; : > "$pkg/groups.tsv"
  printf 'Host same\n  HostName 192.0.2.2\n  User dev\n  Port 22\n' > "$pkg/conf/proj.conf"
  pack_classify "$pkg" > "$pkg/classes" || exit 1
  cp "$IDX" "$pkg/base-index"; pack_state_signature > "$pkg/base-state" || exit 1
  printf 'same\tupdate\n' > "$pkg/decisions"
  ui_fzf(){ cat > /dev/null; return 0; }
  import_review_package "$pkg" "$pkg/decisions" fixture || exit 1
  grep -q '^  Port 22$' "$pkg/review-blocks.d/same.conf" || exit 1
  apply_import_package "$pkg" "$pkg/decisions" >/dev/null 2>&1 || exit 1
  range=$(block_range same); sed -n "${range% *},${range#* }p" "$DIR/proj.conf" > "$T/exact-actual"
  cmp -s "$pkg/review-blocks.d/same.conf" "$T/exact-actual"
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
check review_shows_saved_directives_not_effective_defaults
check all_skipped_review_accepts_no_stage_files
check package_review_shows_final_remapped_values
check package_applies_exact_reviewed_block
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
