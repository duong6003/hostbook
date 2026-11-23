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
git -C "$T/source" checkout -q -B ci-install
export HOSTBOOK_REPO="$T/source"
(
  export HOME="$T/registered home" HOSTBOOK_DIR="$T/registered home/ssh-hosts"
  export HOSTBOOK_HOME="$HOME/.local/share/hostbook" HOSTBOOK_STATE="$T/registered state"
  export XDG_CONFIG_HOME="$T/registered config" HOSTBOOK_SSH_CONFIG="$HOME/.ssh/config"
  mkdir -p "$HOSTBOOK_DIR/.hostbook" "$HOME/.ssh"
  printf 'preserve fixture SSH config\n' > "$HOSTBOOK_SSH_CONFIG"
  printf 'preserve fixture metadata\n' > "$HOSTBOOK_DIR/.hostbook/identity.json"
  bash "$HERE/install.sh" </dev/null
  [ -x "$HOME/.local/bin/hostbook" ] && [ "$HOME/.local/bin/hostbook" -ef "$HOSTBOOK_HOME/hostbook" ]
  git -C "$T/source" commit --allow-empty -qm 'Registered install update fixture'
  bash "$HERE/install.sh" </dev/null
  [ "$(git -C "$HOSTBOOK_HOME" rev-parse HEAD)" = "$(git -C "$T/source" rev-parse HEAD)" ]
  [ "$HOME/.local/bin/hostbook" -ef "$HOSTBOOK_HOME/hostbook" ]
  [ "$(cat "$HOSTBOOK_SSH_CONFIG")" = 'preserve fixture SSH config' ]
  [ "$(cat "$HOSTBOOK_DIR/.hostbook/identity.json")" = 'preserve fixture metadata' ]
  [ ! -e "$HOSTBOOK_STATE" ] && [ ! -e "$XDG_CONFIG_HOME" ]
)
bash "$HERE/install.sh" </dev/null
app="$HOME/.local/share/hostbook"
export HOSTBOOK_TEST_INSTALLED="$HOME/.local/bin/hostbook"
[ -x "$HOME/.local/bin/hostbook" ] && [ -d "$app/.git" ]
for module in ui import settings hosts; do [ -s "$app/lib/$module.sh" ]; done
"$HOME/.local/bin/hostbook" --version | grep -q '^hostbook '
(cd "$T" && "$HOME/.local/bin/hostbook" _header > "$T/header")
[ -s "$T/header" ]
python3 -B "$HERE/tests/store-context.py" -k installed_callback
printf 'update fixture\n' > "$T/source/ci-update-probe"
git -C "$T/source" add ci-update-probe
git -C "$T/source" commit -qm 'CI update fixture'
"$HOME/.local/bin/hostbook" update </dev/null
[ -f "$app/ci-update-probe" ]
for module in ui import settings hosts; do [ -s "$app/lib/$module.sh" ]; done
(cd "$T" && "$HOME/.local/bin/hostbook" _header > "$T/header")
[ -s "$T/header" ]
python3 -B "$HERE/tests/store-context.py" -k installed_callback
[ "$(cat "$HOSTBOOK_DIR/sentinel")" = 'keep host data' ]
printf 'Unix install and update passed; host data preserved.\n'
