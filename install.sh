#!/usr/bin/env bash
# Install hostbook (macOS / Linux). Run again at any time to update.
#   git clone git@github.com:duong6003/hostbook.git ~/.local/share/hostbook && bash ~/.local/share/hostbook/install.sh
#   (the repository is private; the curl | bash one-liner works only while it is public)
# No sudo required: everything goes into ~/.local. Download official fzf and gum releases (with checksum verification)
# when missing or too old (Ubuntu apt only provides fzf 0.44 and has no gum package).
set -eu
case $(uname -s) in MINGW*|MSYS*) echo 'Windows: run install.ps1 from PowerShell; it sets up the runtime and dependencies.' >&2; exit 1;; esac
umask 022

REPO=${HOSTBOOK_REPO:-https://github.com/duong6003/hostbook.git}
APP=${HOSTBOOK_HOME:-$HOME/.local/share/hostbook}
BIN=$HOME/.local/bin
FZF_VER=0.75.0; FZF_MIN=0.63
GUM_VER=2.0.2;  GUM_MIN=0.14
AGE_VER=1.3.2;  AGE_MIN=1.3.0   # requires the batchpass plugin (available since 1.3) for the secret vault and sharing packages

say(){ printf '\033[38;5;114m✓\033[0m %s\n' "$*"; }
die(){ printf '\033[38;5;204m✗\033[0m %s\n' "$*" >&2; exit 1; }
have(){ command -v "$1" >/dev/null 2>&1; }
ver_ge(){ [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]; }
sha256(){ if have sha256sum; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }

for t in git curl tar ssh; do have "$t" || die "Cần cài $t trước"; done
mkdir -p "$BIN"; export PATH="$BIN:$PATH"

os=$(uname -s); arch=$(uname -m)
case $arch in x86_64|amd64) a_fzf=amd64; a_gum=x86_64;; arm64|aarch64) a_fzf=arm64; a_gum=arm64;; *) die "Chưa hỗ trợ kiến trúc $arch";; esac
case $os in Darwin) o_fzf=darwin; o_gum=Darwin;; Linux) o_fzf=linux; o_gum=Linux;; *) die "Chưa hỗ trợ $os";; esac

# Download NAME TARBALL_URL CHECKSUMS_URL PATH_IN_TARBALL
fetch_bin(){
  local name=$1 url=$2 sums=$3 inner=$4 tmp file want got
  tmp=$(mktemp -d); file=$tmp/$(basename "$url")
  curl -fsSL -o "$file" "$url" || die "Không tải được $name"
  want=$(curl -fsSL "$sums" | awk -v f="$(basename "$url")" '$2==f || $2=="*" f {print $1}')
  got=$(sha256 "$file")
  [ -n "$want" ] && [ "$want" = "$got" ] || die "Checksum của $name không khớp, dừng cài"
  tar -xzf "$file" -C "$tmp" && install -m 755 "$tmp/$inner" "$BIN/$name"
  rm -rf "$tmp"; say "$name $("$BIN/$name" --version 2>/dev/null | head -1)"
}
need(){ # NAME MIN_VERSION → 0 if the installed version is new enough
  have "$1" || return 1
  local v; case $1 in fzf) v=$(fzf --version | awk '{print $1}');; gum) v=$(gum --version | awk '{print $3}' | sed 's/^v//');;
    age) have age-plugin-batchpass && have age-keygen || return 1; v=$(age --version | sed 's/^v//');; esac
  ver_ge "$v" "$2"
}

if ! need fzf "$FZF_MIN"; then
  if [ "$os" = Darwin ] && have brew; then { brew upgrade fzf >/dev/null 2>&1 || brew install fzf >/dev/null; } && say "fzf (brew)"
  else fetch_bin fzf "https://github.com/junegunn/fzf/releases/download/v$FZF_VER/fzf-$FZF_VER-${o_fzf}_$a_fzf.tar.gz" \
    "https://github.com/junegunn/fzf/releases/download/v$FZF_VER/fzf_${FZF_VER}_checksums.txt" fzf; fi
else say "fzf $(fzf --version | awk '{print $1}') đã có"; fi
if ! need gum "$GUM_MIN"; then
  if [ "$os" = Darwin ] && have brew; then { brew upgrade gum >/dev/null 2>&1 || brew install gum >/dev/null; } && say "gum (brew)"
  else fetch_bin gum "https://github.com/charmbracelet/gum/releases/download/v$GUM_VER/gum_${GUM_VER}_${o_gum}_$a_gum.tar.gz" \
    "https://github.com/charmbracelet/gum/releases/download/v$GUM_VER/checksums.txt" "gum_${GUM_VER}_${o_gum}_$a_gum/gum"; fi
else say "gum $(gum --version | awk '{print $3}') đã có"; fi

# age does not publish checksum files, so the pinned release SHA-256 values are recorded here
age_sha(){ case $1 in
  linux-amd64) echo cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10;;
  linux-arm64) echo 6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4;;
  darwin-arm64) echo e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5;;
  darwin-amd64) echo 1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539;;
esac; }
if ! need age "$AGE_MIN"; then
  if [ "$os" = Darwin ] && have brew; then { brew upgrade age >/dev/null 2>&1 || brew install age >/dev/null; } && say "age (brew)"
  else plat="${o_fzf}-$a_fzf"; tmp=$(mktemp -d); file=$tmp/age.tgz
    curl -fsSL -o "$file" "https://github.com/FiloSottile/age/releases/download/v$AGE_VER/age-v$AGE_VER-$plat.tar.gz" || die "Không tải được age"
    [ "$(sha256 "$file")" = "$(age_sha "$plat")" ] || die "Checksum của age không khớp, dừng cài"
    tar -xzf "$file" -C "$tmp" && for b in age age-keygen age-plugin-batchpass; do install -m 755 "$tmp/age/$b" "$BIN/$b"; done
    rm -rf "$tmp"; say "age $("$BIN/age" --version)"; fi
else say "age $(age --version) đã có"; fi

if [ -d "$APP/.git" ]; then git -C "$APP" pull -q --ff-only && say "Đã cập nhật hostbook"
else rm -rf "$APP"; mkdir -p "$(dirname "$APP")"; git clone -q --depth 1 "$REPO" "$APP" && say "Đã tải hostbook"; fi

"$APP/hostbook" setup < /dev/null
printf '\nXong. Mở terminal mới rồi gõ \033[1mhostbook\033[0m (hoặc \033[1mhb\033[0m).\n'
command -v tmux >/dev/null 2>&1 || printf 'tmux chưa có: chỉ cần khi mở nhiều server cùng lúc, app sẽ nhắc lúc đó.\n'
