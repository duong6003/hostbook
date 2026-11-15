#!/usr/bin/env bash
# Exercise real import writes and result navigation using temporary hosts only.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR="$HERE/../lang"   # the library is sourced from a temporary copy: tell it where the texts are
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh-config
export HOSTBOOK_KEYCHAIN="file:$T/keychain/id" HOSTBOOK_MASTER=synthetic-master-password
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
mkdir -p "$HOME/.ssh" "$HOSTBOOK_DIR"
printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
# Replace terminal input only; parsing, writes, validation, commits and indexing remain real.
clear(){ :; }; pause(){ :; }; confirm(){ return 0; }; pick(){ echo 0; }
# Screens: the result menu prints its header lines (summary and list) to stderr and picks the first item; a flash prints its text there too.
# ui_menu answers from $T/picks, one choice per call (default 0); it counts in a file because it runs inside $(...).
ui_menu(){ case "$1" in "$(t import_key_question "~/.ssh/fixture")") echo 0; return;; esac; local n; printf '%s\n' "${UI_HEAD:-}" >&2; n=$(cat "$T/pn" 2>/dev/null || echo 0); echo $((n+1)) > "$T/pn"; sed -n "$((n+1))p" "$T/picks" 2>/dev/null | grep . || echo 0; }; flash(){ printf '%s\n' "$*" >&2; }
gum(){ if [ "$1" = choose ]; then echo dev; else command gum "$@"; fi; }
ask_secret_name(){ echo fixture; }
printf 'Host existing\n  HostName 192.0.2.1\n  User original\n' > "$DIR/old.conf"
g init -q; g add -A; g commit -qm fixture
vault_create "$HOSTBOOK_MASTER" >/dev/null || exit 1
g add -A; g commit -qm vault
ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/fixture" || exit 1
build_index full
cat > "$T/incoming.conf" <<'CONF'
Host existing
  HostName 192.0.2.99
# tags: dev api
# desc: Imported API
Host dev-api
  HostName 192.0.2.10
  User ubuntu
  IdentityFile ~/.ssh/fixture
Host dev-db
  HostName 192.0.2.11
  User ubuntu
Host dev-api
  HostName 192.0.2.100
CONF
pass=0; fail=0
check(){ local rc; if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else rc=$?; if [ "$rc" = 77 ]; then printf 'SKIP  %s (symlinks unavailable)\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; fi; }
# Archive entry types are checked before extraction, including otherwise valid names.
python3 - "$T" <<'PYTEST'
import io, sys, tarfile
from pathlib import Path
root = Path(sys.argv[1])
for kind in ('symlink', 'hardlink'):
    with tarfile.open(root / (kind + '.tgz'), 'w:gz') as archive:
        manifest = tarfile.TarInfo('manifest'); manifest.size = 1
        archive.addfile(manifest, io.BytesIO(b'x'))
        entry = tarfile.TarInfo('conf/evil.conf')
        entry.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
        entry.linkname = '../../escape' if kind == 'symlink' else 'manifest'
        archive.addfile(entry)
PYTEST
for kind in symlink hardlink; do
  cp "$T/$kind.tgz" "$T/archive"
  check "package rejects $kind before extraction" '(! (age_pw_dec(){ cat "$T/archive"; }; HB_PACK_PW=fixture pack_open "$T/incoming.conf") > "$T/archive-result" 2>&1) && [ ! -e "$T/escape" ]'
done
mkdir -p "$T/unsafe-package/conf"
printf 'Host safe\n  HostName 192.0.2.1\nMatch "exec" "touch %s"\n  User bad\n' "$T/executed" > "$T/unsafe-package/conf/unsafe.conf"
check 'package config is rejected before OpenSSH inspection' '(! (sshx(){ : > "$T/ssh-called"; return 1; }; pack_index "$T/unsafe-package") > "$T/pack-index" 2>&1) && [ ! -e "$T/ssh-called" ] && [ ! -e "$T/executed" ]'
printf 'IdentityFile ~/.ssh/private\nHost safe\n  HostName 192.0.2.1\n' > "$T/global-key.conf"
check 'global credentials are rejected before inspection' '! import_config_guard "$T/global-key.conf" >/dev/null 2>&1'
printf 'Host *.example\n  IdentityFile ~/.ssh/private\n' > "$T/wild-key.conf"
check 'wildcard credentials are rejected before inspection' '! import_config_guard "$T/wild-key.conf" >/dev/null 2>&1'
printf 'Host "quoted alias"\n  HostName 192.0.2.1\n' > "$T/quoted-alias.conf"
check 'quoted SSH aliases are rejected before inspection' '! import_config_guard "$T/quoted-alias.conf" >/dev/null 2>&1'
mkdir -p "$T/bad-package"
printf 'version=1\nfrom=fixture\n' > "$T/bad-package/manifest"
printf 'same\ta\t192.0.2.1\nsame\tb\t192.0.2.2\n' > "$T/bad-package/index"
: > "$T/bad-package/groups.tsv"
check 'package rejects duplicate aliases before decisions' 'declare -F pack_validate_index >/dev/null && ! pack_validate_index "$T/bad-package" >/dev/null 2>&1'
printf 'same\ta\t192.0.2.1\n' > "$T/bad-package/index"
printf 'group\tsplit\tsame absent\n' > "$T/bad-package/groups.tsv"
check 'package rejects groups referencing absent hosts' 'declare -F pack_validate_index >/dev/null && ! pack_validate_index "$T/bad-package" >/dev/null 2>&1'
mkdir -p "$T/source-split"
printf '# tags: one two\n# desc: retained\nHost existing fresh fresh\n  HostName 192.0.2.19\nHost fresh\n  HostName 192.0.2.20\n' > "$T/source-split/incoming.conf"
printf 'prior-result\n' > "$STATE/last-import"
check 'staging accepts each new alias once without writing live data' '(stage=$(prepare_import_config "$T/source-split") && [ "$(cat "$stage/added")" = fresh ] && [ "$(grep -c "^Host fresh$" "$stage/incoming.conf")" = 1 ] && grep -q "# desc: retained" "$stage/incoming.conf" && [ "$(cat "$STATE/last-import")" = prior-result ] && ! grep -q fresh "$DIR/old.conf")'
mkdir -p "$T/case-split"
printf 'Host existing Existing\n  HostName 192.0.2.50\n' > "$T/case-split/source.conf"
check 'SSH staging preserves distinct case-sensitive aliases' '(stage=$(prepare_import_config "$T/case-split") && [ "$(cat "$stage/added")" = Existing ])'
printf 'Existing\t-\t192.0.2.51\t-\t-\n' > "$T/case-list"
check 'list staging preserves distinct case-sensitive aliases' '(stage=$(prepare_import_list "$T/case-list" imported) && [ "$(cat "$stage/added")" = Existing ])'
# Failure after one write must preserve touched, staged, and unrelated local data.
transaction_failure(){ (
  local mode=$1 tx=$T/transaction-$1
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/stage"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host old\n  HostName 192.0.2.1\nHost *\n  User fixture\n' > "$DIR/a.conf"
  printf 'unrelated tracked\n' > "$DIR/note"; git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture
  printf 'staged change\n' >> "$DIR/note"; git -C "$DIR" add note
  printf '# local dirty change\n' >> "$DIR/a.conf"; printf 'Host unrelated\n  HostName 192.0.2.7\n' > "$DIR/untracked.conf"; printf 'user temp file\n' > "$DIR/a.conf.tmp"
  cp -p "$DIR/a.conf" "$tx/original"; git -C "$DIR" diff --cached --binary > "$tx/index-before"
  printf 'previous\n' > "$STATE/last-import"; build_index full
  printf 'Host new-a\n  HostName 192.0.2.2\n' > "$tx/stage/a.conf"
  printf 'Host new-b\n  HostName 192.0.2.3\n' > "$tx/stage/b.conf"
  printf 'new-a\nnew-b\n' > "$tx/stage/added"
  eval "$(declare -f write_host | sed '1s/^write_host/original_write_host/')"
  write_host(){ [ "$mode" != write ] || [ "$1" != b ] || return 1; : > "$tx/wrote"; original_write_host "$@"; }
  if [ "$mode" = commit ]; then commit_all(){ g add -A && return 1; }; fi
  if [ "$mode" = validation ]; then validcalls=0; validate(){ validcalls=$((validcalls+1)); [ "$validcalls" -lt 2 ]; }; fi
  declare -F apply_import_config >/dev/null && declare -F import_snapshot_targets >/dev/null || exit 1
  import_snapshot_targets "$tx/stage" || exit 1
  ! apply_import_config "$tx/stage" >/dev/null 2>&1 || exit 1
  git -C "$DIR" diff --cached --binary > "$tx/index-after"
  [ -e "$tx/wrote" ] && cmp "$tx/original" "$DIR/a.conf" && cmp "$tx/index-before" "$tx/index-after" && grep -q '^Host unrelated' "$DIR/untracked.conf" && [ "$(cat "$DIR/a.conf.tmp")" = 'user temp file' ] && [ ! -e "$DIR/b.conf" ] && [ "$(cat "$STATE/last-import")" = previous ] && [ ! -d "$STATE/sync.lock" ]
); }
check 'partial import write restores only touched data and Git index' 'transaction_failure write'
check 'failed commit restores only touched data and Git index' 'transaction_failure commit'
check 'failed validation restores only touched data and Git index' 'transaction_failure validation'
stale_import_target_rejected(){ (
  local tx=$T/stale-target
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/stage"; : > "$HIST"; printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host old\n  HostName 192.0.2.1\n' > "$DIR/a.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture; build_index full
  printf 'Host new\n  HostName 192.0.2.2\n' > "$tx/stage/a.conf"; printf 'new\n' > "$tx/stage/added"; printf 'previous\n' > "$STATE/last-import"
  declare -F import_snapshot_targets >/dev/null || exit 1
  import_snapshot_targets "$tx/stage" || exit 1
  printf '# changed after preview\n' >> "$DIR/a.conf"; cp "$DIR/a.conf" "$tx/changed"
  ! apply_import_config "$tx/stage" >/dev/null 2>&1 && cmp "$tx/changed" "$DIR/a.conf" && [ "$(cat "$STATE/last-import")" = previous ] && [ ! -d "$STATE/sync.lock" ]
); }
check 'import rejects a destination changed after staging' 'stale_import_target_rejected'
package_failure_preserves_data(){ (
  local mode=${1:-write} tx=$T/package-failure-${1:-write}
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/pack/secrets"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host old\n  HostName 192.0.2.1\nHost *\n  User fixture\n' > "$DIR/a.conf"
  printf 'tracked\n' > "$DIR/note"; git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture
  printf 'staged\n' >> "$DIR/note"; git -C "$DIR" add note
  printf '# dirty\n' >> "$DIR/a.conf"; printf 'user temp\n' > "$DIR/a.conf.tmp"; printf 'Host user-file\n  HostName 192.0.2.9\n' > "$DIR/untracked.conf"
  cp -p "$DIR/a.conf" "$tx/original"; git -C "$DIR" diff --cached --binary > "$tx/index-before"
  printf 'previous\n' > "$STATE/last-import"; build_index full; mkdir -p "$SEC"; cp "$IDX" "$tx/pack/base-index"; pack_state_signature > "$tx/pack/base-state"
  printf 'new-a\ta\t192.0.2.2\tfixture\t22\t\t\t\t\t\t\tcredential\nnew-b\tb\t192.0.2.3\tfixture\t22\t\t\t\t\t\t\t\n' > "$tx/pack/index"
  printf 'credential\tcredential\tnew\n' > "$tx/pack/smap"; printf 'type=password\n' > "$tx/pack/secrets/credential.meta"; printf 'test password\n' > "$tx/pack/secrets/credential.pass"
  printf 'new-a\tadd\nnew-b\tadd\n' > "$tx/pack/decisions"
  vault_exists(){ return 0; }; secret_enc(){ cat; }
  pack_block(){ : > "$tx/seen-$2"; [ "$mode" != write ] || [ "$2" != new-b ] || return 1; printf 'Host %s\n  HostName 192.0.2.2\n' "$2"; }
  if [ "$mode" = commit ]; then commit_all(){ g add -A && return 1; }; fi
  declare -F apply_import_package >/dev/null || exit 1
  ! apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 || exit 1
  git -C "$DIR" diff --cached --binary > "$tx/index-after"
  [ -e "$tx/seen-new-a" ] && [ -e "$tx/seen-new-b" ] && cmp "$tx/original" "$DIR/a.conf" && cmp "$tx/index-before" "$tx/index-after" && [ "$(cat "$DIR/a.conf.tmp")" = 'user temp' ] && grep -q '^Host user-file' "$DIR/untracked.conf" && [ ! -e "$SEC/credential.meta" ] && [ ! -e "$SEC/credential.age" ] && [ "$(cat "$STATE/last-import")" = previous ] && [ ! -d "$STATE/sync.lock" ]
); }
check 'failed package apply restores hosts, secrets and staged index' 'package_failure_preserves_data'
check 'failed package commit restores hosts, secrets and staged index' 'package_failure_preserves_data commit'
package_symlink_parent_rejected(){ (
  local tx=$T/package-symlink
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/pack" "$tx/outside" "$SEC"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  git -C "$DIR" init -q; git -C "$DIR" commit --allow-empty -qm fixture; build_index full
  cp "$IDX" "$tx/pack/base-index"; pack_state_signature > "$tx/pack/base-state"
  printf 'new\tproject\t192.0.2.2\tfixture\t22\t\t\t\t\t\t\tcredential\n' > "$tx/pack/index"
  printf 'credential\tcredential\tnew\n' > "$tx/pack/smap"; printf 'new\tadd\n' > "$tx/pack/decisions"
  rmdir "$SEC"; ln -s "$tx/outside" "$SEC" 2>/dev/null || exit 77
  [ -L "$SEC" ] || exit 77
  ! apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 && [ -z "$(ls -A "$tx/outside")" ] && [ ! -d "$STATE/sync.lock" ]
); }
check 'package refuses a symlinked secrets directory' 'package_symlink_parent_rejected'
package_both_names_do_not_collide(){ (
  local mode=${1:-ok} tx=$T/package-both-${1:-ok}
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/pack"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host same\n  HostName 192.0.2.1\n' > "$DIR/base.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture; build_index full; cp "$IDX" "$tx/pack/base-index"; pack_state_signature > "$tx/pack/base-state"
  printf 'same\tnew\t192.0.2.2\tfixture\t22\t\t\t\t\t\t\t\nsame-2\tnew\t192.0.2.3\tfixture\t22\t\t\t\t\t\t\t\n' > "$tx/pack/index"
  : > "$tx/pack/smap"; printf 'same\tboth\nsame-2\tadd\n' > "$tx/pack/decisions"
  printf 'pack-group\tsplit\tsame same-2\n' > "$tx/pack/groups.tsv"
  pack_block(){ printf 'Host %s\n  HostName 192.0.2.2\n' "$3"; }
  if [ "$mode" = stale ]; then
    printf '# edited after preview\n' >> "$DIR/base.conf"; cp "$DIR/base.conf" "$tx/changed"
    ! apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 && cmp "$tx/changed" "$DIR/base.conf" && [ ! -e "$DIR/new.conf" ]
    exit $?
  fi
  apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 || exit 1
  [ "$(sort "$STATE/last-import")" = "$(printf 'same-2\nsame-2-2\n')" ] && [ "$(grep -h '^Host ' "$DIR"/*.conf | sort -u | wc -l | tr -d ' ')" = 3 ] && [ "$(cut -f3 "$GRP")" = "same-2-2 same-2" ]
); }
check 'package both avoids names reserved by other decisions' 'package_both_names_do_not_collide'
package_alias_case_sensitive(){ (
  local tx=$T/package-case-sensitive
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/pack/conf"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host same\n  HostName 192.0.2.1\n  User fixture\n' > "$DIR/base.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture; build_index full
  printf 'Host Same\n  HostName 192.0.2.99\n  User fixture\n' > "$tx/pack/conf/base.conf"
  printf 'version=1\nfrom=fixture\n' > "$tx/pack/manifest"; : > "$tx/pack/groups.tsv"
  pack_classify "$tx/pack" > "$tx/pack/classes" || exit 1
  awk -F '\t' '$1=="Same" && $2=="new" {ok=1} END {exit !ok}' "$tx/pack/classes" || exit 1
  cp "$IDX" "$tx/pack/base-index"; pack_state_signature > "$tx/pack/base-state"
  pack_decide "$tx/pack" pack > "$tx/pack/decisions"
  apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 || exit 1
  build_index full
  [ "$(host_field same 3)" = 192.0.2.1 ] && [ "$(host_field Same 3)" = 192.0.2.99 ] && [ "$(ssh -F "$SSHCFG" -G same 2>/dev/null | awk '$1=="hostname" {print $2}')" = 192.0.2.1 ] && [ "$(ssh -F "$SSHCFG" -G Same 2>/dev/null | awk '$1=="hostname" {print $2}')" = 192.0.2.99 ]
); }
check 'package preserves distinct case-sensitive SSH aliases' 'package_alias_case_sensitive'
package_both_remaps_jump(){ (
  local tx=$T/package-jump
  DIR=$tx/hosts; STATE=$tx/state; SSHCFG=$tx/ssh; IDX=$STATE/index.tsv; IDXD=$STATE/idx2; HIST=$STATE/history; SEC=$DIR/secrets; GRP=$DIR/groups.tsv
  mkdir -p "$DIR" "$STATE" "$tx/pack/conf"; : > "$HIST"
  printf 'Include "%s/*.conf"\n' "$DIR" > "$SSHCFG"
  printf 'Host same\n  HostName 192.0.2.1\n' > "$DIR/base.conf"
  git -C "$DIR" init -q; git -C "$DIR" add .; git -C "$DIR" commit -qm fixture; build_index full
  printf 'Host same\n  HostName 192.0.2.2\nHost client\n  HostName 192.0.2.3\n  ProxyJump ops@same:2222,external\n' > "$tx/pack/conf/base.conf"
  printf 'version=1\nfrom=fixture\n' > "$tx/pack/manifest"; printf 'team\tsplit\tsame client\n' > "$tx/pack/groups.tsv"
  pack_classify "$tx/pack" > "$tx/pack/classes" || exit 1
  cp "$IDX" "$tx/pack/base-index"; pack_state_signature > "$tx/pack/base-state"
  printf 'same\tboth\nclient\tadd\n' > "$tx/pack/decisions"
  apply_import_package "$tx/pack" "$tx/pack/decisions" >/dev/null 2>&1 || exit 1
  build_index full
  [ "$(host_field same 3)" = 192.0.2.1 ] && [ "$(host_field same-2 3)" = 192.0.2.2 ] && [ "$(host_field client 6)" = ops@same-2:2222,external ] && [ "$(ssh -F "$SSHCFG" -G client 2>/dev/null | awk '$1=="proxyjump" {print $2}')" = ops@same-2:2222,external ] && [ "$(cut -f3 "$GRP")" = 'same-2 client' ]
); }
check 'package both remaps imported ProxyJump and group members' 'package_both_remaps_jump'
check 'package rejects unrelated target edits after preview' 'package_both_names_do_not_collide stale'
import_flow "$T/incoming.conf" > "$T/result" 2>&1
check 'SSH config result lists imported names, addresses and project' 'grep -q dev-api "$T/result" && grep -q 192.0.2.10 "$T/result" && grep -q dev-db "$T/result"'
check 'SSH config result counts this import, excluding existing and repeated names' 'grep -q "2 mới · 0 cập nhật · 2 bỏ qua" "$T/result"'
check 'SSH config import records only accepted hosts' '[ -f "$STATE/last-import" ] && [ "$(cat "$STATE/last-import")" = "$(printf "dev-api\ndev-db")" ]'
check 'view imported results filters out pre-existing hosts' '[ "${ONLY_NEXT:-}" = "$STATE/last-import" ] && [ "$(HOSTBOOK_ONLY=$ONLY_NEXT list_data | cut -f2 | sort)" = "$(printf "dev-api\ndev-db")" ]'
check 'existing hosts remain untouched and repeated input does not append duplicates' '[ "$(grep -ih "^Host " "$DIR"/*.conf | wc -l | tr -d " ")" = 3 ] && [ "$(host_field existing 3)" = 192.0.2.1 ] && [ "$(host_field dev-api 3)" = 192.0.2.10 ]'
check 'import retains tags, descriptions and explicit SSH options' '[ "$(host_field dev-api 8)" = "dev api" ] && [ "$(host_field dev-api 9)" = "Imported API" ] && grep -q "# secret: fixture" "$DIR/dev.conf" && [ -s "$SEC/fixture.age" ] && [ -s "$HOME/.ssh/fixture" ]'
unset ONLY_NEXT
before=$(g rev-parse HEAD)
import_flow "$T/incoming.conf" > "$T/repeat" 2>&1
check 'reimport reports zero new hosts and leaves repository unchanged' 'grep -q "0 mới · 0 cập nhật · 4 bỏ qua" "$T/repeat" && [ "$(g rev-parse HEAD)" = "$before" ] && [ -z "$(g status --porcelain)" ] && [ ! -s "$STATE/last-import" ] && [ -z "${ONLY_NEXT:-}" ]'
printf 'dev-cache ubuntu@192.0.2.12 #dev\nexisting ubuntu@192.0.2.99\ndev-cache ubuntu@192.0.2.100\n' > "$T/list"
import_flow "$T/list" > "$T/list-result" 2>&1
check 'plain-list import reports only the new host and accurate skipped count' 'grep -q "1 mới · 0 cập nhật · 2 bỏ qua" "$T/list-result" && grep -q dev-cache "$T/list-result" && [ "$(cat "$STATE/last-import")" = dev-cache ] && [ "${ONLY_NEXT:-}" = "$STATE/last-import" ]'
printf '# tags: dev first\n# desc: First entry\nHost dev-first\n  HostName 192.0.2.13\n' > "$T/first.conf"
import_flow "$T/first.conf" > "$T/first-result" 2>&1
check 'metadata before the first SSH Host survives import' '[ "$(host_field dev-first 8)" = "dev first" ] && [ "$(host_field dev-first 9)" = "First entry" ]'
printf 'Host=z-first a-shared\n  HostName 192.0.2.21\nHost a-shared\n  HostName 192.0.2.99\n' > "$T/order.conf"
import_flow "$T/order.conf" > "$T/order-result" 2>&1
check 'Host equals and first shared alias preserve source order' '[ "$(host_field z-first 3)" = 192.0.2.21 ] && [ "$(host_field a-shared 3)" = 192.0.2.21 ] && grep -q "2 mới · 0 cập nhật · 1 bỏ qua" "$T/order-result"'
printf 'Host dev-invalid\n  Port invalid\n' > "$T/invalid.conf"
before=$(g rev-parse HEAD)
check 'invalid config rolls back without showing a success result' '! import_flow "$T/invalid.conf" > "$T/invalid-result" 2>&1 && ! grep -q "Đã nhập:" "$T/invalid-result" && [ "$(g rev-parse HEAD)" = "$before" ] && [ -z "$(g status --porcelain)" ]'
printf 'Host existing dev-shared dev-shared\n  HostName 192.0.2.14\n  User shared\n' > "$T/shared.conf"
import_flow "$T/shared.conf" > "$T/shared-result" 2>&1
check 'shared Host blocks retain fresh aliases without reimporting existing aliases' '[ "$(host_field dev-shared 3)" = 192.0.2.14 ] && [ "$(host_field existing 3)" = 192.0.2.1 ] && [ "$(cat "$STATE/last-import")" = dev-shared ] && grep -q "1 mới · 0 cập nhật · 2 bỏ qua" "$T/shared-result"'
# import_done's other next steps: "check connections" (1) and "undo" (3) run the matching action
check_hosts(){ echo "$*" > "$T/chk"; }; undo_last(){ : > "$T/undone"; }
printf 'dev-shared\n' > "$STATE/last-import"
printf '1\n' > "$T/picks"; echo 0 > "$T/pn"; import_done 1 0 0 >/dev/null 2>&1
check 'import result: "check connections" checks the imported servers' '[ "$(cat "$T/chk")" = dev-shared ]'
printf '3\n' > "$T/picks"; echo 0 > "$T/pn"; import_done 1 0 0 >/dev/null 2>&1
check 'import result: "undo" undoes the import' '[ -f "$T/undone" ]'
printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
