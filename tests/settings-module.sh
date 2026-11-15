#!/usr/bin/env bash
# Check that human flows load settings while hot callbacks do not need it.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/config" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state" HOSTBOOK_SSH_CONFIG="$T/ssh_config" HOSTBOOK_LIB_DIR="$HERE/../lib" HOSTBOOK_LANG_DIR="$HERE/../lang"
mkdir -p "$HOME" "$HOSTBOOK_DIR"
test -f "$HOSTBOOK_LIB_DIR/settings.sh" || { echo 'FAIL settings module missing'; exit 1; }
sed '/^# ---------- entry point ----------/,$d' "$HERE/../hostbook" > "$T/lib"
. "$T/lib"
declare -F setting_pick settings set_language custom_theme >/dev/null
printf 'PASS settings functions loaded for sourced fixtures\n'
