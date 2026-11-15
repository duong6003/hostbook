#!/usr/bin/env bash
# Selective host UI loading, without using real hosts or SSH config.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/config" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh" HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang"
mkdir -p "$HOME" "$HOSTBOOK_DIR" "$T/modules"
test -f "$HOSTBOOK_LIB_DIR/hosts.sh" || { echo 'FAIL hosts module missing'; exit 1; }
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
declare -F pick_project pick_tags add_addr_ask add_name_ask add_push add_back add_fin add_host edit_host delete_server rename_server move_server >/dev/null
cp "$HERE/../lib/ui.sh" "$HERE/../lib/import.sh" "$HERE/../lib/settings.sh" "$T/modules/"
printf 'echo unexpected-hosts-module >&2; return 1\n' > "$T/modules/hosts.sh"
for cmd in _header '_preview h:example' _footer _list _demo; do
  HOSTBOOK_LIB_DIR="$T/modules" bash "$HERE/../hostbook" $cmd >/dev/null || { echo "FAIL $cmd loaded host UI"; exit 1; }
done
for cmd in settings _unknown_search; do
  if HOSTBOOK_LIB_DIR="$T/modules" bash "$HERE/../hostbook" "$cmd" </dev/null >/dev/null 2>"$T/error"; then echo "FAIL $cmd skipped host UI"; exit 1; fi
  grep -q unexpected-hosts-module "$T/error" || { cat "$T/error"; echo "FAIL $cmd failed before host UI"; exit 1; }
done
HOSTBOOK_LIB_DIR="$T/modules" bash "$HERE/../hostbook" --version >/dev/null
printf 'PASS sourced host UI, human/unknown-search loading and unrelated callbacks\n' 
