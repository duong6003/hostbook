# ---------- bulk server import ----------
# Each line: "name address [#tag …]" or just "address" (derive the name from the hostname prefix). Commas and tabs are accepted.
# Print "name<TAB>user<TAB>host<TAB>port<TAB>tags"; report invalid lines to stderr with line numbers.
import_parse(){
  local no=0 line a b rest name addr tags p u h pt
  while IFS= read -r line || [ -n "$line" ]; do no=$((no+1)); line=${line%$'\r'}
    line=$(printf '%s' "$line" | tr ',\t' '  ' | sed 's/^ *//; s/ *$//')
    case $line in ""|\#*) continue;; esac
    tags=$(printf '%s\n' "$line" | tr ' ' '\n' | sed -n 's/^#//p' | tr '\n' ' ' | sed 's/ $//')
    rest=$(printf '%s\n' "$line" | tr ' ' '\n' | grep -v '^#' | grep . | tr '\n' ' ')
    set -- $rest
    if [ $# -ge 2 ]; then name=$1; addr=$2
    elif [ $# -eq 1 ]; then addr=$1; h=${addr#*@}; h=${h%%:*}
      case $h in *[A-Za-z]*) name=${h%%.*};; *) { t impf_line_missing "$no" "$addr"; echo; } >&2; continue;; esac
    else continue; fi
    valid_name "$name" || { { t impf_line_name "$no" "$name"; echo; } >&2; continue; }
    p=$(parse_addr "$addr") || { { t impf_line_addr "$no" "$addr"; echo; } >&2; continue; }
    u=$(echo "$p" | sed -n 1p); h=$(echo "$p" | sed -n 2p); pt=$(echo "$p" | sed -n 3p)
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$u" "$h" "$pt" "$(printf '%s' "$tags" | tr -cd 'A-Za-z0-9._ -')" | sed 's/\t\t/\t-\t/g; s/\t\t/\t-\t/g; s/\t$/\t-/'   # write "-" for empty columns: read with IFS=tab collapses adjacent tabs
  done
}
prepare_import_list(){ # parsed TSV and optional project → private stage
  local list=$1 to=$2 d n u h pt tg blk
  local group src_group user src_user port tags src_tags jump src_jump secret src_secret
  d=$(import_tmpd) || return 1; : > "$d/added"; : > "$d/skipped"; blk=$d/block
  awk -F '\t' -v skipped="$d/skipped" 'FILENAME==ARGV[1] {seen[$1]="existing"; next} {if($1 in seen) {print $1 "\t" seen[$1] >> skipped; next} seen[$1]="duplicate"; print}' "$IDX" "$list" > "$d/accepted" || return 1
  while IFS="$TAB" read -r n u h pt tg; do
    [ "$u" = - ] && u=""; [ "$pt" = - ] && pt=""; [ "$tg" = - ] && tg=""
    valid_name "$n" && parse_addr "${u:+$u@}$h${pt:+:$pt}" >/dev/null || return 1
    group=""; user=""; port=""; tags=""; jump=""; secret=""
    read_suggest "$h" "$n" "$u" "$pt" "$to" || return 1
    case $group in ''|.|..|*/*|*$'\n'*) return 1;; esac
    [ -n "$tg" ] && tags=$tg
    host_block "$n" "$h" "$user" "$port" "$jump" "$secret" "$tags" "" > "$blk" || return 1
    DIR="$d" write_host "$group" "$blk" || return 1
    printf '%s\n' "$n" >> "$d/added" || return 1
  done < "$d/accepted"
  import_snapshot_targets "$d" || return 1
  printf '%s\n' "$d"
}
import_write(){ # tsv-file project → apply staged entries and print count
  local stage; stage=$(prepare_import_list "$1" "$2") || return 1
  apply_import_config "$stage" || return 1
  grep -c . "$STATE/last-import" || :
}
# Reject includes, executable Match clauses and unsafe inherited credentials before OpenSSH.
import_config_guard(){
  awk -v strict="${2:-}" '
    {line=$0; sub(/\015$/, "", line); if (line ~ /[\001-\010\013-\037\177]/) bad=1
     sub(/^[ \t]*/, "", line); if (line ~ /^#/ || line=="") next
     key=line; sub(/[ \t=].*$/, "", key); key=tolower(key)
     if (key=="include") bad=1
     if (key=="match") {
       args=line; sub(/^[^ \t=]+[ \t=]+/, "", args); args=tolower(args)
       gsub(/"/, "", args); gsub(/\047/, "", args); gsub(/\\/, "", args)
       if (strict || args ~ /(^|[ \t])!?exec([ \t=]|$)/) bad=1
       scope="match"
     }
     if (key=="host") {
       rest=line; sub(/^[^ \t=]+[ \t=]+/, "", rest); n=split(rest,a,/[ \t]+/); scope="host"; count=0
       for (i=1;i<=n;i++) {if (a[i] ~ /^#/) break; if (a[i]=="") continue; count++; if (a[i] !~ /^[A-Za-z0-9._*?!-]+$/) bad=1; if (a[i] ~ /[*?!]/) {scope="wild"; if(strict) bad=1}}
       if (!count) bad=1
     }
     if (key ~ /^(certificatefile|identityagent|addkeystoagent|pkcs11provider|securitykeyprovider)$/) bad=1
     if (scope!="host" && key ~ /^(identityfile|identitiesonly)$/) bad=1
    }
    END {exit bad}
  ' "$1" || { err "$(t err_import_unsafe)" >&2; return 1; }
}
# A retained wildcard/Match must follow all imported Host blocks to preserve SSH precedence.
first_import_order_guard(){
  awk '{line=$0; sub(/^[ \t]*/,"",line); key=line; sub(/[ \t=].*/,"",key); key=tolower(key)
    if(key=="match") tail=1
    if(key=="host") {rest=line; sub(/^[^ \t=]+[ \t=]+/,"",rest); n=split(rest,a,/[ \t]+/); concrete=0; wild=0
      for(i=1;i<=n;i++) {if(a[i] ~ /^#/) break; if(a[i] ~ /[*?!]/) wild=1; else if(a[i]!="") concrete=1}
      if((wild && concrete) || (tail && concrete)) bad=1; if(wild) tail=1
    }} END {exit bad}' "$1" || { err "$(t err_import_order)" >&2; return 1; }
}
# All transient input and decrypted package files belong to this import flow.
import_tmpd(){ if [ -n "${IMPORT_WORK:-}" ]; then mktemp -d "$IMPORT_WORK/item.XXXXXX"; else tmpd; fi; }
import_flow(){ # [package-file]
  local IMPORT_WORK rc
  IMPORT_WORK=$(tmpd) || return 1
  import_source "$@"; rc=$?
  rm -rf "$IMPORT_WORK"
  return "$rc"
}
import_source(){ # [package-file]; validate content even when the picker filters names
  local src=${1:-} label input=$IMPORT_WORK/input rc session=$IMPORT_WORK/picker start=$HOME
  mkdir -p "$session" || return 1
  [ ! -d "$HOME/Downloads" ] || start=$HOME/Downloads
  while :; do
    at "$(t import_title)"
    if [ -z "$src" ]; then
      src=$(UI_HEAD="$(t import_package_intro)" file_pick "$start" "$(t import_package_pick)" "" "" "$session" '*.hbshare') || return 1
    fi
    label=$src; case $src in '~') src=$HOME;; '~/'*) src=$HOME/${src#\~/};; esac
    if ! { [ -f "$src" ] && [ -r "$src" ] && cp -- "$src" "$input"; }; then
      err "$(t err_import_read)" >&2; rc=1
    elif ! is_pack "$input"; then
      err "$(t err_import_package_only)" >&2; rc=2
    else
      import_pack "$input" "$label"; rc=$?
    fi
    [ "$rc" -ne 0 ] || return 0
    [ -z "${1:-}" ] || return "$rc"
    pause; src=
  done
}
import_config_file(){ # internal first-run migration; not a public import format
  local IMPORT_WORK rc
  IMPORT_WORK=$(tmpd) || return 1
  cp -- "$1" "$IMPORT_WORK/input" && import_content "$IMPORT_WORK/input" "$1"; rc=$?
  rm -rf "$IMPORT_WORK"
  return "$rc"
}
import_content(){ # private input file, after choosing a source
  local src=$1 label=${2:-$(t import_title)} raw list errs n to cnt to_dir tmp stage f invalid
  title "$(t impf_title)"
  [ -s "$src" ] || { err "$(t err_import_empty)" >&2; return 2; }
  LC_ALL=C tr -d '\011\012\015\040-\176\200-\377' < "$src" > "$IMPORT_WORK/control-bytes" || return 1
  [ ! -s "$IMPORT_WORK/control-bytes" ] || { err "$(t err_import_binary)" >&2; return 2; }
  if grep -qiE 'BEGIN [A-Z0-9 ]*PRIVATE KEY' "$src"; then err "$(t err_import_private)" >&2; return 2; fi
  if is_pack "$src"; then import_pack "$src" "$label"; return $?; fi
  if grep -qiE '^#[[:space:]]*hostbook-share|BEGIN AGE ENCRYPTED FILE' "$src"; then err "$(t err_pack_bad)" >&2; return 2; fi
  raw=$(cat "$src") || return 1
  [ -n "$(printf '%s' "$raw" | tr -d '[:space:]')" ] || { err "$(t err_import_empty)" >&2; return 1; }
  tmp=$(import_tmpd) || return 1; list=$tmp/list; errs=$tmp/errs; : > "$list"; : > "$errs"
  if printf '%s\n' "$raw" | grep -qiE '^[[:space:]]*(host|match|include)([[:space:]]|=)'; then   # a complete SSH config was pasted: preserve it and split by name
    ensure_index
    printf '%s\n' "$raw" > "$list"; import_config_guard "$list" || { rm -f "$list" "$errs"; return 1; }; to_dir=$(import_tmpd) || return 1
    : > "$to_dir/ordered"
    split_config "$list" "$to_dir" "$to_dir/global" "$to_dir/ordered" >/dev/null || return 1
    n=$(awk '{line=$0; sub(/^[ \t]*/,"",line); key=line; sub(/[ \t=].*/,"",key); if(tolower(key)!="host") next; sub(/^[^ \t=]+[ \t=]+/,"",line); count=split(line,a,/[ \t]+/); for(i=1;i<=count;i++) {if(a[i] ~ /^#/) break; if(a[i]!="") print a[i]}}' "$to_dir/ordered" | wc -l | tr -d ' ')
    if [ -z "${IMPORT_KEEP_GLOBAL:-}" ] && grep -qvE '^[[:space:]]*(#|$)' "$to_dir/global"; then err "$(t err_import_global)" >&2; return 1; fi
    mkdir -p "$to_dir/unified"; cp "$to_dir/ordered" "$to_dir/unified/source.conf" || return 1
    stage=$(prepare_import_config "$to_dir/unified") || return 1
    mkdir -p "$stage/grouped" || return 1
    split_config "$stage/source.conf" "$stage/grouped" "$stage/global" >/dev/null || return 1
    rm -f "$stage/source.conf"
    for f in "$stage/grouped"/*.conf; do [ ! -f "$f" ] || mv "$f" "$stage/${f##*/}" || return 1; done
    import_snapshot_targets "$stage" || return 1
    import_config_keys "$stage" || return 1
    cnt=$(grep -c . "$stage/added")
    import_review "$stage" "$label" "$cnt" 0 "$((n-cnt))" 0 || return 1
    apply_import_config "$stage" || return 1
    cnt=$(grep -c . "$STATE/last-import"); rm -rf "$list" "$errs" "$to_dir"
    import_done "$cnt" 0 "$((n-cnt))"; return
  fi
  printf '%s\n' "$raw" | import_parse > "$list" 2> "$errs"
  n=$(grep -c . "$list"); [ -s "$errs" ] && { warn "$(t impf_skipped "$(grep -c . "$errs")")"; sed 's/^/  /' "$errs" | head -10; }
  [ "$n" -gt 0 ] || { err "$(t impf_none)"; pause; rm -f "$list" "$errs"; return 1; }
  ensure_index
  local dupe; dupe=$(cut -f1 "$list" | while read -r x; do host_exists "$x" && printf '%s ' "$x"; done)
  [ -n "$dupe" ] && warn "$(t impf_dupes "$dupe")"
  local projs pi; projs=$(awk -F'\t' '{print $2}' "$IDX" | sort -u)
  pi=$(pick "$(t impf_which "$n")" "$(t impf_guess)" $projs) || { rm -f "$list" "$errs"; return 1; }
  if [ "$pi" = 0 ]; then to=""; else to=$(printf '%s\n' "$projs" | sed -n "${pi}p"); fi
  stage=$(prepare_import_list "$list" "$to") || return 1
  cp "$errs" "$stage/invalid" || return 1
  invalid=$(grep -c . "$errs")
  cnt=$(grep -c . "$stage/added")
  import_review "$stage" "$label" "$cnt" 0 "$((n-cnt))" "$invalid" || return 1
  apply_import_config "$stage" || return 1
  cnt=$(grep -c . "$STATE/last-import"); rm -f "$list" "$errs"
  import_done "$cnt" 0 "$((n-cnt))"
}
# Split SSH config files by name prefix (a-web1 → a.conf) when at least two servers share the prefix;
# put the rest in imported.conf. Keep "Host *" and "Match" blocks in the global file (restored to ~/.ssh/config).
split_config(){ # source-file destination-directory global-file
  awk -v out="$2" -v glob="$3" -v ordered="${4:-}" -v headout="${5:-}" '
    {sub(/\r$/,"")}
    function pfx(p,  q){ q=tolower(p); if (q !~ /-/) return ""; sub(/-.*/,"",q); gsub(/[^a-z0-9]/,"-",q); return q }
    { key=$0; sub(/^[ \t]*/, "", key); sub(/[ \t=].*$/, "", key); key=tolower(key) }
    key=="host" || key=="match" { nb++; kind[nb]=key; args=$0; sub(/^[ \t]*[^ \t=]+[ \t=]+/, "", args); count=split(args,a,/[ \t]+/); pat[nb]=a[1]; wild[nb]=0; for(j=1;j<=count;j++) {if(a[j] ~ /^#/) break; if(a[j] ~ /[*?!]/) wild[nb]=1}; txt[nb]=pend $0 "\n"; pend=""; next }
    /^[ \t]*#/ { pend=pend $0 "\n"; next }
    !nb { head=head pend $0 "\n"; pend=""; next }
    { if (pend!="") { txt[nb]=txt[nb] pend; pend="" } txt[nb]=txt[nb] $0 "\n" }
    END {
      if (pend!="") txt[nb]=txt[nb] pend
      for (i=1;i<=nb;i++) if (kind[i]=="host" && (ordered=="" || !wild[i]) && pat[i] !~ /[*?]/) { k=pfx(pat[i]); if (k!="") c[k]++ }
      if (headout!="") {printf "%s", head > headout; printf "%s", "" > glob}
      else printf "%s", head > glob
      for (i=1;i<=nb;i++) {
        if (kind[i]=="match" || pat[i]=="*" || (ordered!="" && wild[i])) { printf "%s", txt[i] >> glob; continue }
        if (ordered!="") printf "%s", txt[i] >> ordered
        k=pfx(pat[i]); f=(k!="" && c[k]>=2) ? k : "imported"
        printf "%s", txt[i] >> (out "/" f ".conf"); n++
      }
      print n
    }' "$1"
}
prepare_import_config(){ # split directory → private stage with accepted names
  local f d; d=$(import_tmpd) || return 1
  : > "$d/added"; : > "$d/skipped"
  for f in "$1"/*.conf; do [ -f "$f" ] || continue
    awk -v idx="$IDX" -v added="$d/added" -v skipped="$d/skipped" '
      BEGIN {
        while ((getline line < idx)>0) {split(line,a,"\t"); seen[a[1]]="existing"} close(idx)
        while ((getline line < added)>0) seen[line]="duplicate"; close(added)
      }
      function emit(i) {
        if (!started || !nn) return
        for(i=1;i<=nn;i++) {printf "%sHost %s\n%s", prefix, names[i], body; print names[i] >> added}
      }
      {
        line=$0; sub(/\r$/, "", line); key=line; sub(/^[ \t]*/, "", key); sub(/[ \t=].*$/, "", key); key=tolower(key)
        if (key=="host") {
          emit(); rest=line; sub(/^[ \t]*[^ \t=]+[ \t=]+/, "", rest); count=split(rest,a,/[ \t]+/)
          nn=0; started=1
          for(i=1;i<=count;i++) {n=a[i]; if(n ~ /^#/) break; if(n=="") continue; if(n in seen) {print n "\t" seen[n] >> skipped; continue} seen[n]="duplicate"; names[++nn]=n}
          prefix=pending; body=""; pending=""; next
        }
        if (line ~ /^[ \t]*#/) {pending=pending line "\n"; next}
        body=body pending line "\n"; pending=""
      }
      END {body=body pending; emit()}
    ' "$f" > "$d/$(basename "$f")" || return 1
  done
  printf '%s\n' "$d"
}
import_discard_secret(){
  legacy_destinations "$SEC"/* || return 65
  rm -f "$SEC/$1.pub" "$SEC/$1.meta" "$SEC/$1.age" || { err "$(t err_import_secret_cleanup "$SEC/$1")" >&2; return 1; }
}
import_commit_secret(){ # commit only a newly confirmed import key, preserving unrelated staged changes
  legacy_destinations "$SEC"/* || return 65
  local name=$1 rollback idxfile failed=0 restore=0
  revert_in_progress && return 1
  validate >/dev/null || return 1
  rollback=$(mktemp -d "$STATE/import-key-rollback.XXXXXX") || return 1
  idxfile=$(g rev-parse --git-path index) || return 1; case $idxfile in /*) ;; *) idxfile=$DIR/$idxfile;; esac
  [ ! -L "$idxfile" ] || return 1
  [ ! -e "$idxfile" ] || cp -p "$idxfile" "$rollback/index" || return 1
  g add -- "secrets/$name.meta" "secrets/$name.pub" "secrets/$name.age" && g commit -qm "$(t done_secret_added "$name")" --only -- "secrets/$name.meta" "secrets/$name.pub" "secrets/$name.age" || failed=1
  if [ "$failed" = 1 ]; then
    if [ -f "$rollback/index" ]; then cp -p "$rollback/index" "$idxfile" || restore=1; else rm -f "$idxfile" || restore=1; fi
    if [ "$restore" = 0 ]; then rm -rf "$rollback"; else err "$(t err_import_restore "$rollback")" >&2; fi
    return 1
  fi
  : > "$ST.force"; rm -rf "$rollback"
}
import_config_keys(){ # staged config → encrypted key bindings before host writes
  legacy_destinations "$SEC"/* || return 65
  local d=$1 f h effective k full nm choice path secret fp IMPORT_KEY_MODE=1
  for f in "$d"/*.conf; do [ ! -f "$f" ] || import_config_guard "$f" || return 1; done
  printf 'Include "%s/*.conf"\n' "$d" > "$d/ssh-config" || return 1
  : > "$d/key-refs"; : > "$d/bindings"
  while IFS= read -r h; do
    valid_name "$h" || { err "$(t err_import_alias "$h")" >&2; return 1; }
    effective=$(SSHCFG="$d/ssh-config" sshx -G -o IdentityFile=none -- "$h" 2>/dev/null) || return 1
    if ! k=$(printf '%s\n' "$effective" | awk '$1=="identityfile" && tolower($2)!="none" {sub(/^[^ ]+ /,""); if(!seen[$0]++) {n++; path=$0}} END {if(n>1) exit 1; if(n) print path}'); then
      err "$(t err_import_many_keys "$h")" >&2; return 1
    fi
    [ -z "$k" ] || printf '%s\t%s\n' "$h" "$k" >> "$d/key-refs"
  done < "$d/added"
  cut -f2- "$d/key-refs" | sort -u > "$d/key-paths"
  while IFS= read -r k; do [ -n "$k" ] || continue
    full=${k/#\~/$HOME}; nm=
    case $full in "$SEC/"*.pub)
      nm=${full#"$SEC/"}; case $nm in */*) nm=;; *) nm=${nm%.pub};; esac
      if [ -n "$nm" ]; then
        fp=$(ssh-keygen -lf "$SEC/$nm.pub" 2>/dev/null | awk '{print $2}')
        valid_secret_name "$nm" && [ "$(secret_meta "$nm" type)" = key ] && [ -s "$SEC/$nm.pub" ] && [ -s "$SEC/$nm.age" ] && [ -n "$fp" ] && [ "$(secret_meta "$nm" fp)" = "$fp" ] || { err "$(t err_import_managed "$nm")" >&2; return 1; }
      fi;;
    esac
    if [ -z "$nm" ]; then
      if [ -f "$full" ]; then
        choice=$(ui_choice "$(t import_key_question "$k")" "$(t import_key_encrypt)" "$(t import_key_other)" "$(t cancel)") || return 1
        case $choice in 0) nm=$(secret_add_key "$full") || return 1;; 1) nm=$(pick_login "" "" key) || return 1;; *) return 1;; esac
      else
        warn "$(t import_key_missing "$k")" >&2
        nm=$(pick_login "" "" key) || return 1
      fi
    fi
    [ -n "$nm" ] && [ "$(secret_meta "$nm" type)" = key ] && [ -s "$SEC/$nm.pub" ] && [ -s "$SEC/$nm.age" ] || return 1
    while IFS="$TAB" read -r h path; do
      [ "$path" != "$k" ] || printf '%s\t%s\t%s\n' "$h" "$nm" "$(ssh_path "${SEC/#$HOME/\~}/$nm.pub")" >> "$d/bindings"
    done < "$d/key-refs"
  done < "$d/key-paths"
  for f in "$d"/*.conf; do [ -f "$f" ] || continue
    awk -v bindings="$d/bindings" '
      BEGIN {while((getline line < bindings)>0) {split(line,a,"\t"); secret[a[1]]=a[2]; pub[a[1]]=a[3]} close(bindings)}
      function comments(strip,i) {for(i=1;i<=np;i++) if(!strip || pending[i] !~ /^[ \t]*#[ \t]*secret:/) print pending[i]; np=0}
      {line=$0; sub(/^[ \t]+/,"",line); k=line; sub(/[ \t=].*/,"",k); k=tolower(k)}
      /^[ \t]*#/ {pending[++np]=$0; next}
      k=="host" {
        selected=""; for(i=2;i<=NF;i++) if($i in secret) {selected=$i; break}
        comments(selected!="")
        if(selected!="") printf "# secret: %s\n%s\n  IdentityFile %s\n  IdentitiesOnly yes\n",secret[selected],$0,pub[selected]
        else print
        next
      }
      {comments(selected!="")}
      selected!="" && (k=="identityfile" || k=="identitiesonly") {next}
      {print}
      END {comments(0)}
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f" || return 1
  done
}
import_snapshot_targets(){ # stage → record destination state for final revalidation
  local stage=$1 f name sig mode
  : > "$stage/targets" || return 1
  for f in "$stage"/*.conf; do [ -s "$f" ] || continue
    name=${f##*/}; case $name in ''|.|..|*/*|*$'\n'*|*$'\t'*) return 1;; esac
    [ ! -L "$DIR/$name" ] || return 1
    if [ -e "$DIR/$name" ]; then
      [ -f "$DIR/$name" ] || return 1
      sig=$(sha256 < "$DIR/$name") && mode=$(perm "$DIR/$name") && [ -n "$sig" ] && [ -n "$mode" ] || return 1
      printf '%s\tfile\t%s\t%s\n' "$name" "$sig" "$mode" >> "$stage/targets" || return 1
    else printf '%s\tabsent\t-\t-\n' "$name" >> "$stage/targets" || return 1; fi
  done
}
import_targets_unchanged(){
  local stage=$1 name kind sig mode current
  [ -f "$stage/targets" ] || return 1
  while IFS="$TAB" read -r name kind sig mode; do
    [ ! -L "$DIR/$name" ] || return 1
    case $kind in
      file) [ -f "$DIR/$name" ] || return 1; current=$(sha256 < "$DIR/$name") && [ -n "$current" ] || return 1; [ "$current" = "$sig" ] && [ "$(perm "$DIR/$name")" = "$mode" ] || return 1;;
      absent) [ ! -e "$DIR/$name" ] || return 1;;
      *) return 1;;
    esac
  done < "$stage/targets"
}
apply_import_config(){ # staged config and names; restore only these destinations on failure
  legacy_destinations "$DIR"/*.conf || return 65
  local stage=$1 rollback f name idxfile before failed=0 restore=0 n
  lock 10 || { err "$(t sync_busy)"; return 1; }
  if [ ! -s "$stage/added" ]; then : > "$STATE/last-import"; ONLY_NEXT=; IMPORT_COMMIT=; unlock; return 0; fi
  validate >/dev/null || { unlock; return 1; }
  import_targets_unchanged "$stage" || { unlock; err "$(t err_import_changed)" >&2; return 1; }
  ensure_index
  if ! awk -F '\t' 'FILENAME==ARGV[1] {seen[$1]=1; next} $0 in seen {exit 1}' "$IDX" "$stage/added"; then unlock; return 1; fi
  before=$(g rev-parse -q --verify HEAD 2>/dev/null) || before=
  rollback=$(mktemp -d "$STATE/import-rollback.XXXXXX") || { unlock; return 1; }
  mkdir "$rollback/work" && : > "$rollback/touched" || { unlock; return 1; }
  idxfile=$(g rev-parse --git-path index) || { unlock; return 1; }
  case $idxfile in /*) ;; *) idxfile=$DIR/$idxfile;; esac
  if [ -L "$idxfile" ]; then unlock; return 1; fi
  if [ -e "$idxfile" ]; then cp -p "$idxfile" "$rollback/index" || { unlock; return 1; }; fi
  for f in "$stage"/*.conf; do [ -s "$f" ] || continue
    name=${f##*/}; case $name in ''|.|..|*/*|*$'\n'*|*$'\t'*) unlock; return 1;; esac
    if [ -L "$DIR/$name" ] || { [ -e "$DIR/$name" ] && [ ! -f "$DIR/$name" ]; }; then unlock; return 1; fi
    if [ -e "$DIR/$name" ]; then
      cp -p "$DIR/$name" "$rollback/$name" && cp -p "$DIR/$name" "$rollback/work/$name" || { unlock; return 1; }
    fi
  done
  for f in "$stage"/*.conf; do [ -s "$f" ] || continue
    name=${f##*/}
    DIR="$rollback/work" write_host "${name%.conf}" "$f" || { failed=1; break; }
    if [ -L "$DIR/$name" ] || { [ -f "$rollback/$name" ] && ! cmp -s "$rollback/$name" "$DIR/$name"; } || { [ ! -e "$rollback/$name" ] && [ -e "$DIR/$name" ]; }; then failed=1; break; fi
    printf '%s\n' "$name" >> "$rollback/touched" || { failed=1; break; }
    cp -p "$rollback/work/$name" "$DIR/$name" || { failed=1; break; }
  done
  if [ "$failed" = 0 ]; then validate >/dev/null && build_index && commit_all "$(t done_imported_config)" || failed=1; fi
  if [ "$failed" = 1 ]; then
    while IFS= read -r name; do
      if [ -L "$DIR/$name" ]; then restore=1
      elif [ -f "$rollback/$name" ]; then cp -p "$rollback/$name" "$DIR/$name" || restore=1
      else rm -f "$DIR/$name" || restore=1; fi
    done < "$rollback/touched"
    if [ -f "$rollback/index" ]; then cp -p "$rollback/index" "$idxfile" || restore=1
    else rm -f "$idxfile" || restore=1; fi
    build_index || restore=1
    unlock
    if [ "$restore" = 0 ]; then rm -rf "$rollback"; else err "$(t err_import_restore "$rollback")" >&2; fi
    return 1
  fi
  cp "$stage/added" "$STATE/last-import" || { unlock; return 1; }
  IMPORT_COMMIT=$(g rev-parse -q --verify HEAD 2>/dev/null) || IMPORT_COMMIT=
  [ "$IMPORT_COMMIT" != "$before" ] || IMPORT_COMMIT=
  ONLY_NEXT=; : > "$ST.force"; flash "${CG}${ICON_OK} $(t done_imported_config)${C0}"
  rm -rf "$rollback"; unlock
}
# ---------- share package import ----------
is_pack(){ head -c 300 "$1" 2>/dev/null | grep -qE '^# hostbook-share v1([[:space:]]|$)'; }
pack_open(){ # package-file → print the extracted directory (inside tmpd); prompt for the package password
  local f=$1 d pw i bad=""
  d=$(import_tmpd) || return 1; at "$(t import_title)"
  for i in 1 2 3; do
    pw=${HB_PACK_PW:-}; [ -n "$pw" ] || pw=$(ask_pw "$(t pack_pw_head)" "${bad:+$bad}") || return 1
    if sed -n '/BEGIN AGE ENCRYPTED FILE/,$p' "$f" > "$d/pack.age" && age_pw_dec "$pw" "$d/pack.age" > "$d/pack.tgz" && [ -s "$d/pack.tgz" ]; then break; fi
    bad="$(t err_pack_pw)"; [ -n "${HB_PACK_PW:-}" ] && { err "$bad" >&2; return 1; }; [ $i = 3 ] && { err "$bad" >&2; return 1; }
  done
  # accept only the expected package layout; reject absolute paths and paths containing ".."
  tar -tzf "$d/pack.tgz" > "$d/names" 2>/dev/null && tar -tvzf "$d/pack.tgz" > "$d/types" 2>/dev/null || { err "$(t err_pack_bad)" >&2; return 1; }
  bad=$(grep -vE '^(\./)?(manifest|groups\.tsv|conf/|secrets/|conf/[A-Za-z0-9._-]+\.conf|secrets/[a-z0-9._-]+\.(key|pass|meta|pub))?$' "$d/names")
  [ -z "$bad" ] && ! grep -q '\.\.' "$d/names" && ! grep -q '^[^d-]' "$d/types" || { err "$(t err_pack_bad)" >&2; return 1; }
  mkdir -p "$d/x" && tar -xzf "$d/pack.tgz" -C "$d/x" || return 1
  rm -f "$d/pack.age" "$d/pack.tgz"; echo "$d/x"
}
pack_validate_secrets(){ # validate decrypted package credentials before they can affect a vault
  local x=$1 f n kind expected private_fp public_fp derived public
  for f in "$x"/secrets/*; do [ -e "$f" ] || continue
    case $f in *.meta|*.key|*.pub|*.pass) n=${f##*/}; n=${n%.*};; *) return 1;; esac
    [ -f "$x/secrets/$n.meta" ] || return 1
  done
  for f in "$x"/secrets/*.meta; do [ -f "$f" ] || continue
    n=${f##*/}; n=${n%.meta}
    kind=$(awk -F= '$1=="type" {n++; v=$2} END {if(n==1) print v}' "$f")
    case $kind in
      password) [ -s "$x/secrets/$n.pass" ] && [ ! -e "$x/secrets/$n.key" ] && [ ! -e "$x/secrets/$n.pub" ] || return 1;;
      key)
        [ -s "$x/secrets/$n.key" ] && [ -s "$x/secrets/$n.pub" ] && [ ! -e "$x/secrets/$n.pass" ] || return 1
        grep -qE '^-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----$' "$x/secrets/$n.key" || return 1
        chmod 600 "$x/secrets/$n.key" || return 1
        derived=$(ssh-keygen -y -P "" -f "$x/secrets/$n.key" 2>/dev/null) || return 1
        derived=$(printf '%s\n' "$derived" | awk 'NR==1 {print $1 " " $2}')
        public=$(awk 'NR==1 {print $1 " " $2}' "$x/secrets/$n.pub")
        [ -n "$derived" ] && [ "$derived" = "$public" ] || return 1
        private_fp=$(ssh-keygen -lf "$x/secrets/$n.key" 2>/dev/null | awk '{print $2}')
        public_fp=$(ssh-keygen -lf "$x/secrets/$n.pub" 2>/dev/null | awk '{print $2}')
        expected=$(awk -F= '$1=="fp" {n++; v=$2} END {if(n==1) print v}' "$f")
        [ -n "$expected" ] && [ "$expected" = "$private_fp" ] && [ "$expected" = "$public_fp" ] || return 1;;
      *) return 1;;
    esac
  done
}
pack_index(){ # package-directory → package index (same format as index.tsv)
  local x=$1 f
  : > "$x/names"
  for f in "$x"/conf/*.conf; do [ -f "$f" ] || continue
    import_config_guard "$f" strict || return 1
    awk '{line=$0; sub(/^[ \t]*/,"",line); key=line; sub(/[ \t=].*/,"",key); if(tolower(key)!="host") next; sub(/^[^ \t=]+[ \t=]+/,"",line); n=split(line,a,/[ \t]+/); for(i=1;i<=n;i++) {if(a[i] ~ /^#/) break; if(a[i]!="") print a[i]}}' "$f" >> "$x/names" || return 1
  done
  printf 'Include "%s/conf/*.conf"\n' "$x" > "$x/cfg"
  ( SSHCFG=$x/cfg; for f in "$x"/conf/*.conf; do [ -f "$f" ] && index_file "$f"; done ) > "$x/index.tmp" 2>/dev/null || return 1
  LC_ALL=C sort "$x/names" > "$x/names.sorted"
  cut -f1 "$x/index.tmp" | LC_ALL=C sort > "$x/index.names"
  cmp -s "$x/names.sorted" "$x/index.names" || return 1
  cat "$x/index.tmp"
}
pack_validate_index(){ # validate package names, groups and references
  local x=$1
  awk ' /[\001-\010\013-\037\177]/ {bad=1} /^version=/ {versions++; if($0!="version=1") bad=1} /^from=/ {senders++; if($0=="from=") bad=1} END {exit (bad || versions!=1 || senders!=1)}' "$x/manifest" || return 1
  awk -F '\t' '
    FILENAME==ARGV[1] {n=$1; if($1 !~ /^[A-Za-z0-9._-]+$/ || n in hosts) bad=1; hosts[n]=1; host_count++; next}
    NF {if(NF!=3 || $1 !~ /^[A-Za-z0-9._-]+$/ || ($2!="" && $2!="tabs" && $2!="split" && $2!="sync") || ($1 in groups)) bad=1; groups[$1]=1
        count=split($3,a,/ +/); for(i=1;i<=count;i++) if(a[i]!="" && !(a[i] in hosts)) bad=1}
    END {exit (bad || !host_count)}
  ' "$x/index" "$x/groups.tsv"
}
# Map package secrets to the local vault. Print "package-name<TAB>local-name<TAB>new|existing|renamed".
pack_secret_map(){ # package-directory
  local x=$1 m n base loc fp found used=" " suffix
  for m in "$x"/secrets/*.meta; do [ -f "$m" ] || continue; n=${m##*/}; n=${n%.meta}
    base=$n; valid_secret_name "$base" || base=imported
    if [ "$(awk -F= '$1=="type" {print $2}' "$m")" = password ]; then
      if secret_exists "$base" && [ "$(secret_meta "$base" type)" = password ] && [ "$(secret_get "$base")" = "$(cat "$x/secrets/$n.pass")" ]; then
        printf '%s\t%s\texisting\n' "$n" "$base"; continue
      fi
    else
      fp=$(sed -n 's/^fp=//p' "$m"); found=$(grep -lxF "fp=$fp" "$SEC"/*.meta 2>/dev/null | head -1)
      if [ -n "$found" ]; then
        loc=$(basename "$found" .meta)
        [ "$(secret_meta "$loc" type)" = key ] && [ -s "$SEC/$loc.age" ] && [ -s "$SEC/$loc.pub" ] && [ "$(ssh-keygen -lf "$SEC/$loc.pub" 2>/dev/null | awk '{print $2}')" = "$fp" ] || return 1
        printf '%s\t%s\texisting\n' "$n" "$loc"; continue
      fi
    fi
    loc=$base; suffix=2
    while secret_exists "$loc" || case $used in *" $loc "*) true;; *) false;; esac; do
      loc="${base:0:35}-$suffix"; suffix=$((suffix+1))
    done
    used="$used$loc "
    if [ "$loc" = "$n" ]; then printf '%s\t%s\tnew\n' "$n" "$loc"; else printf '%s\t%s\trenamed\n' "$n" "$loc"; fi
  done
}
# Classify package servers as new | same | diff | ipdup. Print "name<TAB>type<TAB>details".
pack_classify(){ # package-directory
  local x=$1
  pack_validate_secrets "$x" || { err "$(t err_pack_bad)" >&2; return 1; }
  ensure_index; pack_index "$x" > "$x/index" || return 1; pack_validate_index "$x" || { err "$(t err_pack_bad)" >&2; return 1; }
  pack_secret_map "$x" > "$x/smap" || return 1
  awk -F '\t' 'FILENAME==ARGV[1] {seen[$1]=1; next} $12!="" && !($12 in seen) {bad=1} END {exit bad}' "$x/smap" "$x/index" || { err "$(t err_pack_bad)" >&2; return 1; }
  awk -F'\t' -v smap="$x/smap" -v wj="$(t imp_f_jump)" -v wn="$(t imp_f_none)" -v wt="$(t imp_f_tags)" -v wd="$(t imp_f_desc)" -v wl="$(t imp_f_login)" -v ws="$(t card_ssh_self)" '
    BEGIN { while ((getline l < smap) > 0) { split(l,a,"\t"); M[a[1]]=a[2] } }
    FILENAME==ARGV[1] { L[$1]=1; for (i=3;i<=12;i++) V[$1,i]=$i; ADDR[$3 ":" $5]=$1; next }
    { n=$1; sc=($12 in M) ? M[$12] : $12
      if (n in L) { d=""
        if (V[n,3]!=$3) d=d "HostName " V[n,3] " → " $3 "; "
        if (V[n,4]!=$4) d=d "User " V[n,4] " → " $4 "; "
        if (V[n,5]!=$5) d=d "Port " V[n,5] " → " $5 "; "
        if (V[n,6]!=$6) d=d wj " " (V[n,6]==""?wn:V[n,6]) " → " ($6==""?wn:$6) "; "
        if (V[n,8]!=$8) d=d wt " [" V[n,8] "] → [" $8 "]; "
        if (V[n,9]!=$9) d=d wd "; "
        if (V[n,12]!=sc && $12!="") d=d wl " " (V[n,12]==""?ws:V[n,12]) " → " sc "; "
        sub(/; $/,"",d); print n "\t" (d==""?"same":"diff") "\t" d }
      else if (($3 ":" $5) in ADDR) print n "\tipdup\t" ADDR[$3 ":" $5]
      else print n "\tnew\t" }' "$IDX" "$x/index"
}
pack_jump_map(){ # ProxyJump list and normalized package decisions
  awk -F '\t' -v jumps="$1" '
    $2 ~ /^both:/ {map[$1]=substr($2,6)}
    END {
      n=split(jumps,a,","); out=""
      for(i=1;i<=n;i++) {
        part=a[i]; prefix=""; suffix=""
        if(index(part,"ssh://")==1) {prefix="ssh://"; part=substr(part,7)}
        if(index(part,"@")) {at=index(part,"@"); prefix=prefix substr(part,1,at); part=substr(part,at+1)}
        if(substr(part,1,1)!="[" && index(part,":")) {at=index(part,":"); suffix=substr(part,at); part=substr(part,1,at-1)}
        if(part in map) part=map[part]
        out=out (i>1?",":"") prefix part suffix
      }
      printf "%s", out
    }' "$2"
}
pack_block(){ # package-directory name [new-name] [normalized decisions]
  local x=$1 n=$2 as=${3:-$2} decisions=${4:-} r sc jump
  r=$(awk -F'\t' -v n="$n" '$1==n{print $0; exit}' "$x/index"); sc=$(printf '%s' "$r" | cut -f12)
  [ -n "$sc" ] && sc=$(awk -F'\t' -v s="$sc" '$1==s{print $2}' "$x/smap")
  jump=$(printf '%s' "$r" | cut -f6)
  [ -z "$decisions" ] || jump=$(pack_jump_map "$jump" "$decisions") || return 1
  host_block "$as" "$(printf '%s' "$r" | cut -f3)" "$(printf '%s' "$r" | cut -f4)" "$(printf '%s' "$r" | cut -f5)" "$jump" "$sc" "$(printf '%s' "$r" | cut -f8)" "$(printf '%s' "$r" | cut -f9)"
  block_extras "$(printf '%s' "$r" | cut -f10)" "$(printf '%s' "$r" | cut -f11)"
}
pack_state_signature(){ # detect changes to files that influence package choices
  local f sig mode idxfile d link rc
  [ ! -L "$SEC" ] || return 1
  idxfile=$(g rev-parse --git-path index) || return 1; case $idxfile in /*) ;; *) idxfile=$DIR/$idxfile;; esac
  d=$(import_tmpd) || return 1
  for f in "$DIR"/*.conf "$GRP" "$SEC"/*.meta "$SEC"/*.pub "$SEC"/*.age "$idxfile"; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    case $f in *$'\n'*|*$'\t'*) return 1;; esac
    if [ -L "$f" ]; then
      link=$(readlink "$f") || return 1; case $link in *$'\n'*|*$'\t'*) return 1;; esac
      printf 'link\t%s\t%s\n' "$f" "$link"
    elif [ -f "$f" ]; then
      sig=$(sha256 < "$f") && mode=$(perm "$f") && [ -n "$sig" ] && [ -n "$mode" ] || return 1
      printf 'file\t%s\t%s\t%s\n' "$f" "$sig" "$mode"
    else printf 'other\t%s\n' "$f"; fi
  done > "$d/signature" || return 1
  LC_ALL=C sort "$d/signature"; rc=$?; rm -rf "$d"; return "$rc"
}
# The package transaction snapshots a fixed manifest and records each path before mutation.
import_touch(){
  legacy_destinations "$DIR/$1" || return 65
  local rel=$1 file=$DIR/$1 saved=$IMPORT_ROLLBACK/files/$1
  grep -Fxq "$rel" "$IMPORT_ROLLBACK/manifest" || return 1
  grep -Fxq "$rel" "$IMPORT_ROLLBACK/touched" && return 0
  [ ! -L "$file" ] || return 1
  if [ -f "$saved" ]; then cmp -s "$saved" "$file" || return 1
  else [ ! -e "$file" ] || return 1; fi
  printf '%s\n' "$rel" >> "$IMPORT_ROLLBACK/touched"
}
pack_write_host(){
  legacy_destinations "$DIR/$1.conf" || return 65
  local project=$1 block=$2 rel=$1.conf work=$IMPORT_ROLLBACK/work/$1.conf
  rm -f "$work" || return 1
  if [ -e "$DIR/$rel" ]; then cp -p "$DIR/$rel" "$work" || return 1; fi
  DIR="$IMPORT_ROLLBACK/work" write_host "$project" "$block" || return 1
  import_touch "$rel" && cp -p "$work" "$DIR/$rel"
}
pack_delete_host(){
  legacy_destinations "$DIR"/*.conf || return 65
  local n=$1 keep=${2:-} f l r rel work=$IMPORT_ROLLBACK/work
  f=$(host_field "$n" 10); l=$(host_field "$n" 11)
  case $f in "$DIR/"*) rel=${f#"$DIR/"};; *) return 1;; esac
  [ -n "$l" ] && [ -f "$f" ] || return 1
  if host_shared "$n"; then
    awk -v l="$l" -v n="$n" 'NR==l {out=$1; for(i=2;i<=NF;i++) if($i!=n) out=out " " $i; print out; next} {print}' "$f" > "$work/data" || return 1
  else
    r=$(block_range "$n")
    awk -v s="${r% *}" -v e="${r#* }" 'NR>=s && NR<=e {next} {if($0 ~ /^[ \t]*$/ && prevblank) next; prevblank=($0 ~ /^[ \t]*$/); print}' "$f" > "$work/data" || return 1
  fi
  cp -p "$f" "$work/deleted" && cat "$work/data" > "$work/deleted" || return 1
  import_touch "$rel" || return 1
  if [ -s "$work/deleted" ] && grep -q '[^[:space:]]' "$work/deleted"; then cp -p "$work/deleted" "$f" || return 1
  else rm -f "$f" || return 1; fi
  if [ -z "$keep" ] && [ -f "$GRP" ]; then
    cp -p "$GRP" "$work/groups" || return 1
    GRP="$work/groups" groups_rename "$n" || return 1
    import_touch groups.tsv && cp -p "$work/groups" "$GRP" || return 1
  fi
}
pack_normalize_decisions(){ # resolve "both" names while reserving every incoming alias
  awk -F '\t' 'FILENAME==ARGV[1] {seen[$1]=1; next} {name[++n]=$1; act[n]=$2; if($2!="skip") seen[$1]=1} END {for(i=1;i<=n;i++) {a=act[i]; if(a=="both") {v=name[i] "-2"; while(v in seen) v=v "-2"; seen[v]=1; a="both:" v} print name[i] "\t" a}}' "$IDX" "$1"
}
apply_import_package(){ # package and decisions; preserve unrelated and staged files on failure
  legacy_destinations "$DIR"/*.conf "$SEC"/* || return 65
  local x=$1 decisions=$2 rollback idxfile f rel n act p target loc status needed before failed=0 restore=0
  if ! awk -F '\t' '$2!="skip" {found=1} END {exit !found}' "$decisions"; then : > "$STATE/last-import"; ONLY_NEXT=; IMPORT_COMMIT=; return 0; fi
  lock 10 || { err "$(t sync_busy)"; return 1; }
  validate >/dev/null || { unlock; return 1; }
  ensure_index
  [ -f "$x/base-index" ] && cmp -s "$x/base-index" "$IDX" || { unlock; return 1; }
  pack_state_signature > "$x/current-state" && cmp -s "$x/base-state" "$x/current-state" || { unlock; err "$(t err_import_changed)" >&2; return 1; }
  before=$(g rev-parse -q --verify HEAD 2>/dev/null) || before=
  rollback=$(mktemp -d "$STATE/import-rollback.XXXXXX") || { unlock; return 1; }
  mkdir -p "$rollback/files" "$rollback/work" || { unlock; return 1; }
  : > "$rollback/manifest"; : > "$rollback/touched"
  idxfile=$(g rev-parse --git-path index) || { unlock; return 1; }; case $idxfile in /*) ;; *) idxfile=$DIR/$idxfile;; esac
  [ ! -L "$idxfile" ] || { unlock; return 1; }
  [ ! -e "$idxfile" ] || cp -p "$idxfile" "$rollback/index" || { unlock; return 1; }
  pack_normalize_decisions "$decisions" > "$rollback/decisions" || { unlock; return 1; }
  if [ -f "$x/preview-decisions" ] && ! cmp -s "$x/preview-decisions" "$rollback/decisions"; then unlock; err "$(t err_import_changed)" >&2; return 1; fi
  while IFS="$TAB" read -r n act; do
    [ "$act" != skip ] || continue
    valid_name "$n" || { unlock; return 1; }
    p=$(awk -F '\t' -v n="$n" '$1==n {print $2; exit}' "$x/index")
    case $p in ''|.|..|*/*|*$'\n'*) unlock; return 1;; esac
    printf '%s.conf\n' "$p" >> "$rollback/manifest"
    case $act in
      update) target=$n;; replace:*) target=${act#replace:}; printf 'groups.tsv\n' >> "$rollback/manifest";;
      add|both:*) target=;; *) unlock; return 1;;
    esac
    if [ -n "$target" ]; then
      f=$(host_field "$target" 10); case $f in "$DIR/"*) rel=${f#"$DIR/"};; *) unlock; return 1;; esac
      case $rel in */*|*$'\n'*) unlock; return 1;; esac; printf '%s\n' "$rel" >> "$rollback/manifest"
    fi
  done < "$rollback/decisions"
  needed=$(awk -F '\t' 'FILENAME==ARGV[1] {if($2!="skip") act[$1]=1; next} ($1 in act) && $12!="" {print $12}' "$rollback/decisions" "$x/index" | sort -u)
  [ ! -L "$SEC" ] || { unlock; return 1; }
  while IFS="$TAB" read -r n loc status; do
    [ "$status" != existing ] || continue
    printf '%s\n' "$needed" | grep -Fxq "$n" || continue
    valid_secret_name "$loc" || { unlock; return 1; }
    for rel in "secrets/$loc.meta" "secrets/$loc.age"; do [ ! -e "$DIR/$rel" ] && [ ! -L "$DIR/$rel" ] || { unlock; return 1; }; printf '%s\n' "$rel" >> "$rollback/manifest"; done
    if [ ! -f "$x/secrets/$n.pass" ]; then rel=secrets/$loc.pub; [ ! -e "$DIR/$rel" ] && [ ! -L "$DIR/$rel" ] || { unlock; return 1; }; printf '%s\n' "$rel" >> "$rollback/manifest"; fi
  done < "$x/smap"
  [ ! -s "$x/groups.tsv" ] || printf 'groups.tsv\n' >> "$rollback/manifest"
  sort -u "$rollback/manifest" > "$rollback/manifest.sorted" && mv "$rollback/manifest.sorted" "$rollback/manifest" || { unlock; return 1; }
  while IFS= read -r rel; do [ -n "$rel" ] || continue
    f=$DIR/$rel; [ ! -L "$f" ] && { [ ! -e "$f" ] || [ -f "$f" ]; } || { unlock; return 1; }
    if [ -e "$f" ]; then case $rel in */*) mkdir -p "$rollback/files/${rel%/*}" || { unlock; return 1; };; esac; cp -p "$f" "$rollback/files/$rel" || { unlock; return 1; }; fi
  done < "$rollback/manifest"
  IMPORT_ROLLBACK=$rollback pack_apply_raw "$x" "$rollback/decisions" || failed=1
  if [ "$failed" = 0 ]; then validate >/dev/null && build_index && commit_all "$(t done_pack_imported "$(sed -n 's/^from=//p' "$x/manifest")" 0 0)" || failed=1; fi
  if [ "$failed" = 1 ]; then
    while IFS= read -r rel; do [ -n "$rel" ] || continue; f=$DIR/$rel
      if [ -L "$f" ]; then restore=1
      elif [ -f "$rollback/files/$rel" ]; then cp -p "$rollback/files/$rel" "$f" || restore=1
      else rm -f "$f" || restore=1; fi
    done < "$rollback/touched"
    if [ -f "$rollback/index" ]; then cp -p "$rollback/index" "$idxfile" || restore=1; else rm -f "$idxfile" || restore=1; fi
    build_index || restore=1; unlock
    if [ "$restore" = 0 ]; then rm -rf "$rollback"; else err "$(t err_import_restore "$rollback")" >&2; fi
    return 1
  fi
  cp "$x/applied" "$STATE/last-import" || { unlock; return 1; }
  IMPORT_COMMIT=$(g rev-parse -q --verify HEAD 2>/dev/null) || IMPORT_COMMIT=
  [ "$IMPORT_COMMIT" != "$before" ] || IMPORT_COMMIT=
  ONLY_NEXT=; : > "$ST.force"; rm -rf "$rollback"; unlock
}
# Apply a decisions file: "name<TAB>add|skip|update|both|replace:<existing name>". Record added/updated names in last-import.
pack_apply_raw(){ # package-directory decisions-file
  legacy_destinations "$DIR"/*.conf "$SEC"/* || return 65
  local x=$1 dec=$2 n act p blk line t st new loc m needed i=0 work=$IMPORT_ROLLBACK/work
  : > "$x/applied"; blk=$work/blk
  needed=$(awk -F '\t' 'FILENAME==ARGV[1] {if($2!="skip") act[$1]=1; next} ($1 in act) && $12!="" {print $12}' "$dec" "$x/index" | sort -u | tr '\n' ' ')
  while IFS="$TAB" read -r n loc st; do
    [ "$st" = existing ] && continue
    case " $needed " in *" $n "*) ;; *) continue;; esac
    vault_exists || return 1
    import_touch "secrets/$loc.meta" && cp "$x/secrets/$n.meta" "$SEC/$loc.meta" || return 1
    if [ -f "$x/secrets/$n.pass" ]; then
      import_touch "secrets/$loc.age" && secret_enc < "$x/secrets/$n.pass" > "$SEC/$loc.age" || return 1
    else
      import_touch "secrets/$loc.pub" && cp "$x/secrets/$n.pub" "$SEC/$loc.pub" && chmod 600 "$SEC/$loc.pub" || return 1
      import_touch "secrets/$loc.age" && secret_enc < "$x/secrets/$n.key" > "$SEC/$loc.age" || return 1
    fi
  done < "$x/smap"
  while IFS="$TAB" read -r n act; do
    p=$(awk -F '\t' -v n="$n" '$1==n{print $2; exit}' "$x/index"); t=$n
    case $act in
      skip) continue;;
      update) build_index && pack_delete_host "$n" keep || return 1;;
      replace:*) build_index && pack_delete_host "${act#replace:}" || return 1;;
      both:*) t=${act#both:};;
    esac
    i=$((i+1))
    if [ -f "$x/preview-decisions" ]; then
      [ -f "$x/review-blocks.d/$i.conf" ] && cp "$x/review-blocks.d/$i.conf" "$blk" || return 1
    else pack_block "$x" "$n" "$t" "$dec" > "$blk" || return 1; fi
    pack_write_host "$p" "$blk" || return 1
    printf '%s\n' "$t" >> "$x/applied" || return 1
  done < "$dec"
  build_index || return 1
  [ ! -s "$x/groups.tsv" ] || while IFS= read -r line; do group_line "$line"
    new=$(for m in $GH; do
      t=$(awk -F '\t' -v n="$m" '$1==n {if($2 ~ /^both:/) print substr($2,6); else print $1; exit}' "$dec")
      [ -n "$t" ] && host_exists "$t" && printf '%s ' "$t"
    done)
    if [ -n "$(group_field "$GN" 1)" ]; then
      new=$(printf '%s\n' $(group_field "$GN" 3) $new | awk '!s[$0]++' | tr '\n' ' ')
      cp -p "$GRP" "$work/group" || return 1
      awk -F '\t' -v OFS='\t' -v n="$GN" -v h="${new% }" '$1==n{$3=h} {print}' "$GRP" > "$work/group-data" && cat "$work/group-data" > "$work/group" || return 1
      import_touch groups.tsv && cp -p "$work/group" "$GRP" || return 1
    elif [ "$(printf '%s\n' $new | grep -c .)" -ge 2 ]; then
      import_touch groups.tsv && printf '%s\t%s\t%s\n' "$GN" "$GM" "${new% }" >> "$GRP" || return 1
    fi
  done < "$x/groups.tsv"
  return 0
}
pack_decide(){ # package-directory mode(pack|mine|ask) → print the decisions file
  local x=$1 mode=$2 n c det all="" i
  while IFS="$TAB" read -r -u 3 n c det; do
    case $c in
      new) printf '%s\tadd\n' "$n";;
      same) printf '%s\tskip\n' "$n";;
      diff) if [ "$mode" = pack ] || [ "$all" = update ]; then printf '%s\tupdate\n' "$n"; elif [ "$mode" = mine ] || [ "$all" = skip ]; then printf '%s\tskip\n' "$n"
            else at "$(t import_title)|$n"
              i=$(UI_HEAD="$CB$(t imp_differs "$n")$C0$NL$(printf '%s\n' "$det" | tr ';' '\n' | sed 's/^ */  /')" ui_menu "" "$(t act_dec_update)" "$(t act_dec_keep)" "$(t act_dec_both)|$(t act_dec_both_hint "$n")" "$(t act_dec_update_all)" "$(t act_dec_keep_all)") || return 1
              case $i in
                0) printf '%s\tupdate\n' "$n";; 2) printf '%s\tboth\n' "$n";; 3) all=update; printf '%s\tupdate\n' "$n";; 4) all=skip; printf '%s\tskip\n' "$n";; *) printf '%s\tskip\n' "$n";; esac; fi;;
      ipdup) if [ "$mode" = pack ]; then printf '%s\tadd\n' "$n"; elif [ "$mode" = mine ]; then printf '%s\tskip\n' "$n"
             else at "$(t import_title)|$n"
               i=$(ui_menu "$(t imp_dupip "$n" "$det")" "$(t act_dup_add)" "$(t act_dup_skip)" "!$(t act_dup_replace "$det")") || return 1
               case $i in 0) printf '%s\tadd\n' "$n";; 2) printf '%s\treplace:%s\n' "$n" "$det";; *) printf '%s\tskip\n' "$n";; esac; fi;;
    esac
  done 3< "$x/classes"
}
import_pack(){ # file
  local f=$1 source=${2:-$(t import_title)} x from nh nnew nsame ndiff ndup dec mode np n sum new_count updated_count skip_count
  x=$(pack_open "$f") || { pause; return 1; }
  pack_validate_secrets "$x" || { err "$(t err_pack_bad)" >&2; return 1; }
  if ls "$x"/secrets/*.meta >/dev/null 2>&1; then vault_init || { pause; return 1; }; fi
  pack_classify "$x" > "$x/classes" || return 1
  cp "$IDX" "$x/base-index" && pack_state_signature > "$x/base-state" || return 1
  from=$(sed -n 's/^from=//p' "$x/manifest"); nh=$(grep -c . "$x/classes")
  nnew=$(grep -c "${TAB}new${TAB}" "$x/classes"); nsame=$(grep -c "${TAB}same${TAB}" "$x/classes"); ndiff=$(grep -c "${TAB}diff${TAB}" "$x/classes"); ndup=$(grep -c "${TAB}ipdup${TAB}" "$x/classes")
  np=$(awk -F'\t' '(" " $8 " ") ~ / prod /' "$x/index" | grep -c .)
  at "$(t import_title)"
  if [ $((nnew + ndiff + ndup)) -eq 0 ]; then : > "$STATE/last-import"; ONLY_NEXT=; IMPORT_COMMIT=; flash "${CG}${ICON_OK} $(t imp_nothing)${C0}"; return 0; fi
  # Keep this choice visible in a short terminal; per-host details follow in
  # the conflict decisions and the scrollable final review.
  from=${from:0:18}
  sum="$(t imp_from "$from" "$nh" "$(grep -c . "$x/groups.tsv" 2>/dev/null)" "$(grep -c . "$x/smap")")$NL$(t imp_counts "$nnew" "$nsame" "$ndiff" "$ndup" "$np")"
  case $(UI_HEAD=$sum$NL ui_menu "$(t imp_head)" "$(t act_imp_suggest)|$(t act_imp_suggest_hint)$( [ $((ndiff + ndup)) -gt 0 ] && t act_imp_suggest_ask)" "$(t act_imp_all)" "$(t act_imp_mine)|$(t act_imp_mine_hint)" "$(t cancel)") in
    0) mode=ask;; 1) mode=pack;; 2) mode=mine;; *) return 1;; esac
  dec=$x/decisions; pack_decide "$x" "$mode" > "$dec" || return 1
  import_review_package "$x" "$dec" "$source" || return 1
  pack_confirm_prod "$x/preview-decisions" || return 1
  new_count=$(grep -cE "${TAB}(add|both)$" "$dec"); updated_count=$(grep -cE "${TAB}(update|replace:.*)$" "$dec"); skip_count=$(grep -c "${TAB}skip$" "$dec")
  apply_import_package "$x" "$dec" || { pause; return 1; }
  import_done "$new_count" "$updated_count" "$skip_count"
}
pack_confirm_prod(){ # require the existing PROD alias before update or replacement
  local decisions=$1 n act target
  while IFS="$TAB" read -r n act; do
    case $act in update) target=$n;; replace:*) target=${act#replace:};; *) continue;; esac
    if is_prod "$target"; then
      UI_TONE=danger UI_ENTER=$(t key_confirm) ui_field "$(t delete_type_label "$target")" "" "equals:$target" "$(t delete_type_rule "$target")" >/dev/null || return 1
    fi
  done < "$decisions"
}
# Extract raw saved directives, without interpreting SSH inheritance or quoting.
import_review_values(){ # concatenated concrete Host blocks → name, hostname, user, port, jump
  awk 'BEGIN {OFS="\t"}
    function emit() {if (name!="") print name, (h==""?"-":h), (u==""?"-":u), (p==""?"-":p), (j==""?"-":j)}
    {line=$0; sub(/^[ \t]*/,"",line); if(line ~ /^#/ || line=="") next
     key=line; sub(/[ \t=].*/,"",key); key=tolower(key)
     val=line; sub(/^[^ \t=]+[ \t=]+/,"",val); sub(/[ \t]+$/,"",val)
     if(key=="host") {emit(); name=val; sub(/[ \t].*/,"",name); h=u=p=j=""; next}
     if(key=="hostname" && h=="") h=val
     if(key=="user" && u=="") u=val
     if(key=="port" && p=="") p=val
     if(key=="proxyjump" && j=="") j=val
    } END {emit()}' "$@"
}
import_review_package(){ # package directory decisions source → final read-only review
  local x=$1 decisions=$2 source=$3 new updated skipped n act target i=0
  pack_normalize_decisions "$decisions" > "$x/preview-decisions" || return 1
  new=$(grep -cE "${TAB}(add|both:.*)$" "$x/preview-decisions")
  updated=$(grep -cE "${TAB}(update|replace:.*)$" "$x/preview-decisions")
  skipped=$(grep -c "${TAB}skip$" "$x/preview-decisions")
  mkdir -p "$x/review-blocks.d" && : > "$x/review-blocks" || return 1
  while IFS="$TAB" read -r n act; do
    case $act in skip) continue;; both:*) target=${act#both:};; *) target=$n;; esac
    i=$((i+1))
    pack_block "$x" "$n" "$target" "$x/preview-decisions" > "$x/review-blocks.d/$i.conf" && cat "$x/review-blocks.d/$i.conf" >> "$x/review-blocks" || return 1
  done < "$x/preview-decisions"
  import_review_values "$x/review-blocks" > "$x/review-values" || return 1
  awk -F '\t' -v h="$(t import_review_host '%s' '%s' '%s' '%s' '%s')" -v d="$(t import_review_details '%s' '%s' '%s')" -v desc="$(t import_review_description '%s')" -v sk="$(t import_review_skipped '%s' '%s')" -v keep="$(t import_skip_kept)" -v keyfmt="$(t import_review_secret '%s' '%s' '%s')" -v newlabel="$(t imp_sc_new)" -v oldlabel="$(t imp_sc_existing)" -v renamedlabel="$(t imp_sc_renamed)" -v prod="$ICON_PROD" -v self="$(t card_ssh_self)" -v inherited="$(t import_review_inherited)" '
    FILENAME==ARGV[1] {action[$1]=$2; if($2 ~ /^both:/) renamed[$1]=substr($2,6); next}
    FILENAME==ARGV[2] {secret[$1]=$2; status[$1]=$3; order[++k]=$1; next}
    FILENAME==ARGV[3] {host[$1]=$2; userval[$1]=$3; portval[$1]=$4; jumpval[$1]=$5; next}
    {n=$1; act=action[n]; if(act=="skip") {printf sk,n,keep; print ""; next}
     if(act=="") next; name=(n in renamed?renamed[n]:n); mark=(" " $8 " " ~ / prod / ? prod " " : "")
     user=(userval[name]=="-"?inherited:userval[name]); port=(portval[name]=="-"?inherited:portval[name]); address=(host[name]=="-"?inherited:host[name]); tags=($8==""?"-":$8); login=($12==""?self:secret[$12]); jump=(jumpval[name]=="-"?inherited:jumpval[name])
     if($12!="") needed[$12]=1
     printf "%s", mark; printf h,name,user,address,port,$2; printf "  "; printf d,tags,login,jump; if($9!="") {printf "  "; printf desc,$9} printf "\n"
    }
    END {for(i=1;i<=k;i++) {n=order[i]; if(n in needed) {label=(status[n]=="new"?newlabel:status[n]=="existing"?oldlabel:renamedlabel); printf keyfmt,n,secret[n],label; print ""}}}
  ' "$x/preview-decisions" "$x/smap" "$x/review-values" "$x/index" > "$x/review-rows" || return 1
  import_review_screen "$x/review-rows" "$source" "$new" "$updated" "$skipped" 0
}
import_review_screen(){ # rows source new updated skipped invalid
  local rows=$1 source=$2 new=$3 updated=$4 skipped=$5 invalid=$6 header cols max
  source=$(printf '%s' "$source" | LC_ALL=C tr -d '\000-\037\177')
  term_cols cols; max=$((cols - 18)); [ "$max" -ge 8 ] || max=8
  if [ ${#source} -gt "$max" ]; then source="${source:0:$((max-1))}…"; fi
  at "$(t import_title)|$(t import_review_title)"
  header=$(ui_head "$CB$(t import_review_title)$C0" "$(t import_review_source "$source")" "$(t import_review_counts "$new" "$updated" "$skipped" "$invalid")" "$(t import_review_retained)")
  [ -s "$rows" ] || printf '%s\n' "$(t import_review_nothing)" > "$rows"
  cat "$rows" | ui_fzf "$header" --no-input --wrap --prompt='› ' --footer="$(ui_keys "↑↓:$(t key_move)" "enter:$(t import_review_apply)" "esc:$(t key_cancel)")" --bind 'esc:abort' >/dev/null
}
import_review(){ # stage source new updated skipped invalid → read-only review
  local stage=$1 source=$2 new=$3 updated=$4 skipped=$5 invalid=$6 f
  mkdir -p "$stage/review-state" || return 1
  case $stage in *'"'*|*'\'*|*$'\n'*) err "$(t err_import_config_path)" >&2; return 1;; esac
  {
    [ -z "${IMPORT_HEADER:-}" ] || cat "$IMPORT_HEADER"
    printf 'Include %s\n' "$(ssh_path "$stage/*.conf")"
    [ -z "${IMPORT_TAIL:-}" ] || cat "$IMPORT_TAIL"
  } > "$stage/review-ssh" || return 1
  : > "$stage/review-index"
  for f in "$stage"/*.conf; do [ -f "$f" ] || continue
    import_config_guard "$f" || return 1
    (SSHCFG="$stage/review-ssh"; STATE="$stage/review-state"; index_file "$f") >> "$stage/review-index" || return 1
  done
  LC_ALL=C sort "$stage/added" > "$stage/review-expected"
  cut -f1 "$stage/review-index" | LC_ALL=C sort > "$stage/review-actual"
  cmp -s "$stage/review-expected" "$stage/review-actual" || return 1
  : > "$stage/review-values" || return 1
  for f in "$stage"/*.conf; do [ ! -f "$f" ] || import_review_values "$f" >> "$stage/review-values" || return 1; done
  { awk -F '\t' -v h="$(t import_review_host '%s' '%s' '%s' '%s' '%s')" -v d="$(t import_review_details '%s' '%s' '%s')" -v desc="$(t import_review_description '%s')" -v prod="$ICON_PROD" -v self="$(t card_ssh_self)" -v inherited="$(t import_review_inherited)" '
      FILENAME==ARGV[1] {host[$1]=$2; userval[$1]=$3; portval[$1]=$4; jumpval[$1]=$5; next}
      {mark=(" " $8 " " ~ / prod / ? prod " " : ""); user=(userval[$1]=="-"?inherited:userval[$1]); port=(portval[$1]=="-"?inherited:portval[$1]); address=(host[$1]=="-"?inherited:host[$1]); tags=($8==""?"-":$8); secret=($12==""?self:$12); jump=(jumpval[$1]=="-"?inherited:jumpval[$1])
       printf "%s", mark; printf h,$1,user,address,port,$2; printf "  "; printf d,tags,secret,jump; if($9!="") {printf "  "; printf desc,$9} printf "\n"}
    ' "$stage/review-values" "$stage/review-index"
    if [ -f "$stage/key-refs" ] && [ -f "$stage/bindings" ]; then
      awk -F '\t' -v f="$(t import_review_key '%s' '%s')" 'FILENAME==ARGV[1] {path[$1]=$2; next} ($1 in path) {key=path[$1] SUBSEP $2; if(!seen[key]++) {printf f,path[$1],$2; print ""}}' "$stage/key-refs" "$stage/bindings"
    fi
    [ ! -f "$stage/skipped" ] || awk -F '\t' -v f="$(t import_review_skipped '%s' '%s')" -v e="$(t import_skip_existing)" -v d="$(t import_skip_duplicate)" '{printf f,$1,($2=="existing"?e:d); print ""}' "$stage/skipped"
    [ ! -s "$stage/invalid" ] || awk -v f="$(t import_review_invalid '%s')" '{printf f,$0; print ""}' "$stage/invalid"
  } > "$stage/review-rows" || return 1
  import_review_screen "$stage/review-rows" "$source" "$new" "$updated" "$skipped" "$invalid"
}
import_done(){ # new-count updated-count skipped-count
  local n sum list rows cols limit more choice lines allowed actions=()
  n=$(grep -c . "$STATE/last-import")
  sum="$CG${ICON_OK} $(t imp_done "$1" "$2" "$3")$C0"
  if [ "$n" -eq 0 ]; then flash "$sum"; return 0; fi
  at "$(t import_title)"
  # Reserve two extra rows for the search-recovery footer when it wraps at 44 columns.
  rows=$(term_rows); term_cols cols; limit=$((rows - 19)); [ "$limit" -ge 0 ] || limit=0
  allowed=$((rows - 16)); [ "$allowed" -ge 1 ] || allowed=1
  cols=$((cols - 9)); [ "$cols" -ge 8 ] || cols=8
  while :; do
    list=$(LC_ALL=C awk -F '\t' -v f="$(t imp_done_line '%s' '%s' '%s')" -v max="$limit" -v width="$cols" '
      function clip(s,w, i,ch,o,c) {if(length(s)<=w) return s; o=""; c=0; for(i=1;i<=length(s);i++) {ch=substr(s,i,1); if(ch !~ /[\200-\277]/) {if(c>=w-1) break; c++} o=o ch} return o "…"}
      FILENAME==ARGV[1] {wanted[$1]=1; next}
      ($1 in wanted) && ++k<=max {printf "  %s\n", clip(sprintf(f,$1,$3,$2),width)}' "$STATE/last-import" "$IDX")
    more=$((n-limit)); [ "$more" -gt 0 ] || more=0
    [ "$more" -eq 0 ] || list="$list${list:+$NL}$CD$(t imp_done_more "$more")$C0"
    lines=$(ui_head "$sum" " " "$list" "$CB$(t imp_next)$C0" | awk 'END {print NR}')
    [ "$lines" -gt "$allowed" ] && [ "$limit" -gt 0 ] || break
    limit=$((limit-1))
  done
  actions=("$(t act_imp_view)" "$(t act_imp_check)|$(t act_imp_check_hint)" "$(t act_imp_later)")
  [ -z "${IMPORT_COMMIT:-}" ] || actions+=("$(t act_imp_undo)")
  while :; do
    at "$(t import_title)"
    choice=$(UI_HEAD="$sum$NL $NL$list" ui_menu "$(t imp_next)" "${actions[@]}") || return 0
    case $choice in
      0) ONLY_NEXT=$STATE/last-import; return 0;;
      1) check_hosts $(cat "$STATE/last-import");;
      3) if [ -z "${IMPORT_COMMIT:-}" ] || [ "$(g rev-parse HEAD 2>/dev/null)" != "$IMPORT_COMMIT" ]; then
           err "$(t err_import_undo_stale)" >&2; pause; continue
         fi
         undo_last "$IMPORT_COMMIT" && return 0;;
      *) return 0;;
    esac
  done
}
