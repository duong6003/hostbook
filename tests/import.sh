#!/usr/bin/env bash
# Exercise real import writes and result navigation using temporary hosts only.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG=vi HOSTBOOK_LANG_DIR="$HERE/../lang"   # the library is sourced from a temporary copy: tell it where the texts are
export HOME=$T/home XDG_CONFIG_HOME=$T/config HOSTBOOK_DIR=$T/hosts HOSTBOOK_STATE=$T/state HOSTBOOK_SSH_CONFIG=$T/ssh-config
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
mkdir -p "$HOME" "$HOSTBOOK_DIR"
printf 'Include "%s/*.conf"\n' "$HOSTBOOK_DIR" > "$HOSTBOOK_SSH_CONFIG"
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
trap 'cleanup; rm -rf "$T"' EXIT
# Replace terminal input only; parsing, writes, validation, commits and indexing remain real.
clear(){ :; }; pause(){ :; }; confirm(){ return 0; }; pick(){ echo 0; }
# Screens: the result menu prints its header lines (summary and list) to stderr and picks the first item; a flash prints its text there too.
# ui_menu answers from $T/picks, one choice per call (default 0); it counts in a file because it runs inside $(...).
ui_menu(){ local n; printf '%s\n' "${UI_HEAD:-}" >&2; n=$(cat "$T/pn" 2>/dev/null || echo 0); echo $((n+1)) > "$T/pn"; sed -n "$((n+1))p" "$T/picks" 2>/dev/null | grep . || echo 0; }; flash(){ printf '%s\n' "$*" >&2; }
gum(){ if [ "$1" = choose ]; then echo dev; else command gum "$@"; fi; }
printf 'Host existing\n  HostName 192.0.2.1\n  User original\n' > "$DIR/old.conf"
g init -q; g add -A; g commit -qm fixture
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
check(){ if eval "$2"; then pass=$((pass+1)); printf 'ok    %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL  %s\n' "$1"; fi; }
import_flow "$T/incoming.conf" > "$T/result" 2>&1
check 'SSH config result lists imported names, addresses and project' 'grep -q dev-api "$T/result" && grep -q 192.0.2.10 "$T/result" && grep -q dev-db "$T/result"'
check 'SSH config result counts this import, excluding existing and repeated names' 'grep -q "2 mới · 0 cập nhật · 2 bỏ qua" "$T/result"'
check 'SSH config import records only accepted hosts' '[ -f "$STATE/last-import" ] && [ "$(cat "$STATE/last-import")" = "$(printf "dev-api\ndev-db")" ]'
check 'view imported results filters out pre-existing hosts' '[ "${ONLY_NEXT:-}" = "$STATE/last-import" ] && [ "$(HOSTBOOK_ONLY=$ONLY_NEXT list_data | cut -f2 | sort)" = "$(printf "dev-api\ndev-db")" ]'
check 'existing hosts remain untouched and repeated input does not append duplicates' '[ "$(grep -ih "^Host " "$DIR"/*.conf | wc -l | tr -d " ")" = 3 ] && [ "$(host_field existing 3)" = 192.0.2.1 ] && [ "$(host_field dev-api 3)" = 192.0.2.10 ]'
check 'import retains tags, descriptions and explicit SSH options' '[ "$(host_field dev-api 8)" = "dev api" ] && [ "$(host_field dev-api 9)" = "Imported API" ] && grep -q "IdentityFile ~/.ssh/fixture" "$DIR/dev.conf"'
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
