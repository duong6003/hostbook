#!/usr/bin/env bash
# Cài hostbook (macOS / Linux). Chạy lại bất cứ lúc nào để cập nhật.
#   curl -fsSL https://raw.githubusercontent.com/duong6003/hostbook/main/install.sh | bash
# Không cần sudo: mọi thứ vào ~/.local. fzf và gum lấy bản phát hành chính thức (có kiểm tra checksum)
# khi máy chưa có hoặc bản có sẵn quá cũ (apt của Ubuntu chỉ có fzf 0.44 và không có gum).
set -eu
umask 022

REPO=${HOSTBOOK_REPO:-https://github.com/duong6003/hostbook.git}
APP=${HOSTBOOK_HOME:-$HOME/.local/share/hostbook}
BIN=$HOME/.local/bin
FZF_VER=0.75.0; FZF_MIN=0.63
GUM_VER=2.0.2;  GUM_MIN=0.14

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

# tải TÊN URL_TARBALL URL_CHECKSUMS ĐƯỜNG_DẪN_TRONG_TARBALL
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
need(){ # TÊN BẢN_TỐI_THIỂU → 0 nếu đã đủ mới
  have "$1" || return 1
  local v; case $1 in fzf) v=$(fzf --version | awk '{print $1}');; gum) v=$(gum --version | awk '{print $3}' | sed 's/^v//');; esac
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

if [ -d "$APP/.git" ]; then git -C "$APP" pull -q --ff-only && say "Đã cập nhật hostbook"
else rm -rf "$APP"; mkdir -p "$(dirname "$APP")"; git clone -q --depth 1 "$REPO" "$APP" && say "Đã tải hostbook"; fi

"$APP/hostbook" setup < /dev/null
printf '\nXong. Mở terminal mới rồi gõ \033[1mhostbook\033[0m (hoặc \033[1ms\033[0m).\n'
command -v tmux >/dev/null 2>&1 || printf 'tmux chưa có: chỉ cần khi mở nhiều server cùng lúc, app sẽ nhắc lúc đó.\n'
