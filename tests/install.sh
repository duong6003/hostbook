#!/usr/bin/env bash
# Exercise install and update using only a disposable home and local repository.
set -eu
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
HERE=$(cd "$(dirname "$0")/.." && pwd)
export HOME="$T/home" HOSTBOOK_DIR="$T/hosts" HOSTBOOK_STATE="$T/state"
export HOSTBOOK_HOME="$HOME/.local/share/hostbook"
export HOSTBOOK_SSH_CONFIG="$T/ssh_config" XDG_CONFIG_HOME="$T/config"
export HOSTBOOK_KEYCHAIN="file:$T/keychain/id" SHELL=/bin/bash
unset SSH_AUTH_SOCK SSH_AGENT_PID
export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@example.com GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@example.com
mkdir -p "$HOME" "$HOSTBOOK_DIR"
printf 'keep host data\n' > "$HOSTBOOK_DIR/sentinel"
git clone -q "$HERE" "$T/source"
git -C "$T/source" checkout -q -b ci-install
export HOSTBOOK_REPO="$T/source"
bash "$HERE/install.sh" </dev/null
app="$HOME/.local/share/hostbook"
[ -x "$HOME/.local/bin/hostbook" ] && [ -d "$app/.git" ]
"$HOME/.local/bin/hostbook" --version | grep -q '^hostbook '
printf 'update fixture\n' > "$T/source/ci-update-probe"
git -C "$T/source" add ci-update-probe
git -C "$T/source" commit -qm 'CI update fixture'
"$HOME/.local/bin/hostbook" update </dev/null
[ -f "$app/ci-update-probe" ]
(cd "$T" && "$HOME/.local/bin/hostbook" _header > "$T/header")
[ -s "$T/header" ]
[ "$(cat "$HOSTBOOK_DIR/sentinel")" = 'keep host data' ]
printf 'Unix install and update passed; host data preserved.\n'
