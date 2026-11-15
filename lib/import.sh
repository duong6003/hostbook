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
import_write(){ # tsv-file project(empty = infer from IP range) → write all entries and print the server count
  local list=$1 to=$2 n u h pt tg blk cnt=0 seen=" "
  local group src_group user src_user port tags src_tags jump src_jump secret src_secret
  blk=$(mktemp); : > "$STATE/last-import"
  while IFS="$TAB" read -r n u h pt tg; do
    [ "$u" = - ] && u=""; [ "$pt" = - ] && pt=""; [ "$tg" = - ] && tg=""
    case $seen in *" $n "*) continue;; esac; host_exists "$n" && continue; seen="$seen$n "
    group=""; user=""; port=""; tags=""; jump=""; secret=""
    read_suggest "$h" "$n" "$u" "$pt" "$to"
    [ -n "$tg" ] && tags=$tg
    host_block "$n" "$h" "$user" "$port" "$jump" "$secret" "$tags" "" > "$blk"
    write_host "$group" "$blk" || { rm -f "$blk"; return 1; }
    echo "$n" >> "$STATE/last-import"; cnt=$((cnt+1))
  done < "$list"
  rm -f "$blk"; echo "$cnt"
}
# Reject Include and Match before imported config reaches OpenSSH.
import_config_guard(){
  awk '{line=tolower($0); sub(/\015$/, "", line); if (line ~ /[\001-\010\013-\037\177]/) bad=1; sub(/^[ \t]*/, "", line); if (line ~ /^(include|match)([ \t]|=)/) bad=1} END {exit bad}' "$1" || { err "$(t err_import_unsafe)" >&2; return 1; }
}
import_flow(){ # [file]
  local src=${1:-} raw list errs n to cnt to_dir
  [ -z "$src" ] || src=$(unix_path "$src")
  title "$(t impf_title)"
  list=$(mktemp); errs=$(mktemp)
  if [ -n "$src" ] && is_pack "$src"; then import_pack "$src"; return; fi
  if [ -z "$src" ] && clip_get 2>/dev/null | head -c 300 | grep -q '^# hostbook-share v1'; then   # a share package is already in the clipboard
    to_dir=$(tmpd) || return 1; to_dir=$to_dir/clip.hbshare; clip_get > "$to_dir"; import_pack "$to_dir"; return; fi
  if [ -n "$src" ]; then raw=$(cat "$src") || return 1
  else
    # use an editor to allow unlimited pasted lines (gum 2 input treats Enter as submit)
    local tf ed; tf=$(mktemp); ed=${VISUAL:-${EDITOR:-$(have nano && echo nano || echo vi)}}
    printf '%s\n' "$(t impf_tpl1)" "$(t impf_tpl2)" "$(t impf_tpl3)" "" > "$tf"
    $ed "$tf"
    if grep -q 'BEGIN AGE ENCRYPTED FILE' "$tf"; then { echo "# hostbook-share v1"; sed -n '/BEGIN AGE ENCRYPTED FILE/,$p' "$tf"; } > "$tf.pack"; rm -f "$tf"; import_pack "$tf.pack"; rm -f "$tf.pack"; return; fi
    raw=$(grep -v '^# ' "$tf" | sed '/./,$!d'); rm -f "$tf"   # strip leading blank lines so error line numbers match the pasted content
    [ -n "$(printf '%s' "$raw" | tr -d '[:space:]')" ] || return 1
  fi
  if printf '%s\n' "$raw" | grep -qiE '^[[:space:]]*host[[:space:]]'; then   # a complete SSH config was pasted: preserve it and split by name
    ensure_index
    printf '%s\n' "$raw" > "$list"; import_config_guard "$list" || { rm -f "$list" "$errs"; return 1; }; to_dir=$(mktemp -d)
    split_config "$list" "$to_dir" "$to_dir/global" >/dev/null
    n=$(awk 'tolower($1)=="host" {for(i=2;i<=NF;i++) if($i !~ /[*?!]/) print $i}' "$to_dir"/*.conf | wc -l | tr -d ' ')
    dim "$(t impf_ssh "$n")"
    confirm "$(t impf_ask)" || { rm -rf "$list" "$errs" "$to_dir"; return 1; }
    apply_change "Nhập server từ SSH config" merge_split "$to_dir" || { rm -rf "$list" "$errs" "$to_dir"; return 1; }
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
  apply_change "Đã nhập server từ danh sách" import_write "$list" "$to" >/dev/null || { rm -f "$list" "$errs"; return 1; }
  cnt=$(grep -c . "$STATE/last-import"); rm -f "$list" "$errs"
  import_done "$cnt" 0 "$((n-cnt))"
}
# Split SSH config files by name prefix (a-web1 → a.conf) when at least two servers share the prefix;
# put the rest in imported.conf. Keep "Host *" and "Match" blocks in the global file (restored to ~/.ssh/config).
split_config(){ # source-file destination-directory global-file
  awk -v out="$2" -v glob="$3" '
    {sub(/\r$/,"")}
    function pfx(p,  q){ q=tolower(p); if (q !~ /-/) return ""; sub(/-.*/,"",q); gsub(/[^a-z0-9]/,"-",q); return q }
    tolower($1)=="host" || tolower($1)=="match" { nb++; kind[nb]=tolower($1); pat[nb]=$2; txt[nb]=pend $0 "\n"; pend=""; next }
    /^[ \t]*#/ { pend=pend $0 "\n"; next }
    !nb { head=head pend $0 "\n"; pend=""; next }
    { if (pend!="") { txt[nb]=txt[nb] pend; pend="" } txt[nb]=txt[nb] $0 "\n" }
    END {
      if (pend!="") txt[nb]=txt[nb] pend
      for (i=1;i<=nb;i++) if (kind[i]=="host" && pat[i] !~ /[*?]/) { k=pfx(pat[i]); if (k!="") c[k]++ }
      printf "%s", head > glob
      for (i=1;i<=nb;i++) {
        if (kind[i]=="match" || pat[i]=="*") { printf "%s", txt[i] >> glob; continue }
        k=pfx(pat[i]); f=(k!="" && c[k]>=2) ? k : "imported"
        printf "%s", txt[i] >> (out "/" f ".conf"); n++
      }
      print n
    }' "$1"
}
merge_split(){ # Preserve complete blocks while skipping aliases already present or repeated in the input.
  local f d; d=$(tmpd) || return 1
  : > "$STATE/last-import"
  for f in "$1"/*.conf; do [ -f "$f" ] || continue
    awk -v idx="$IDX" -v added="$STATE/last-import" '
      BEGIN {
        while ((getline line < idx)>0) {split(line,a,"\t"); seen[a[1]]=1} close(idx)
        while ((getline line < added)>0) seen[line]=1; close(added)
      }
      function emit(i) {
        if (!started || skip) return
        printf "%s", body
        for(i=1;i<=nn;i++) {print names[i] >> added; seen[names[i]]=1}
      }
      tolower($1)=="host" {
        emit(); heading=$1; nn=0; pattern=0; started=1
        for(i=2;i<=NF;i++) {
          if($i ~ /[*?!]/) {heading=heading " " $i; pattern=1}
          else if(!($i in seen)) {names[++nn]=$i; seen[$i]=1; heading=heading " " $i}
        }
        skip=(nn==0 && !pattern); body=pending heading "\n"; pending=""
        next
      }
      /^[ \t]*#/ {pending=pending $0 "\n"; next}
      {body=body pending $0 "\n"; pending=""}
      END {body=body pending; emit()}
    ' "$f" > "$d/$(basename "$f")" || return 1
  done
  [ -s "$STATE/last-import" ] || return 0
  for f in "$d"/*.conf; do [ -s "$f" ] || continue
    write_host "$(basename "$f" .conf)" "$f" || return 1
  done
}

# ---------- share package import ----------
is_pack(){ head -c 300 "$1" 2>/dev/null | grep -q '^# hostbook-share v1'; }
pack_open(){ # package-file → print the extracted directory (inside tmpd); prompt for the package password
  local f=$1 d pw i bad=""
  d=$(tmpd) || return 1; at "$(t import_title)"
  for i in 1 2 3; do
    pw=${HB_PACK_PW:-}; [ -n "$pw" ] || pw=$(ask_pw "$(t pack_pw_head)" "${bad:+$bad}") || return 1
    if sed -n '/BEGIN AGE ENCRYPTED FILE/,$p' "$f" > "$d/pack.age" && age_pw_dec "$pw" "$d/pack.age" > "$d/pack.tgz" && [ -s "$d/pack.tgz" ]; then break; fi
    bad="$(t err_pack_pw)"; [ -n "${HB_PACK_PW:-}" ] && { err "$bad" >&2; return 1; }; [ $i = 3 ] && { err "$bad" >&2; return 1; }
  done
  # accept only the expected package layout; reject absolute paths and paths containing ".."
  bad=$(tar -tzf "$d/pack.tgz" 2>/dev/null | grep -vE '^(\./)?(manifest|groups\.tsv|conf/|secrets/|conf/[A-Za-z0-9._-]+\.conf|secrets/[a-z0-9._-]+\.(key|pass|meta|pub))?$')
  [ -z "$bad" ] && ! tar -tzf "$d/pack.tgz" | grep -q '\.\.' || { err "$(t err_pack_bad)" >&2; return 1; }
  mkdir -p "$d/x" && tar -xzf "$d/pack.tgz" -C "$d/x" || return 1
  rm -f "$d/pack.age" "$d/pack.tgz"; echo "$d/x"
}
pack_index(){ # package-directory → package index (same format as index.tsv)
  local x=$1 f
  printf 'Include "%s/conf/*.conf"\n' "$x" > "$x/cfg"
  ( SSHCFG=$x/cfg; for f in "$x"/conf/*.conf; do [ -f "$f" ] && index_file "$f"; done ) 2>/dev/null
}
# Map package secrets to the local vault. Print "package-name<TAB>local-name<TAB>new|existing|renamed".
pack_secret_map(){ # package-directory
  local x=$1 m n s fp loc t
  for m in "$x"/secrets/*.meta; do [ -f "$m" ] || continue; n=$(basename "$m" .meta)
    s=$n; valid_secret_name "$s" || s=imported   # a package must never name a secret after the vault's own files
    if grep -q '^type=password' "$m"; then
      if secret_exists "$s"; then if [ "$(secret_meta "$s" type)" = password ] && [ "$(secret_get "$s")" = "$(cat "$x/secrets/$n.pass")" ]; then printf '%s\t%s\texisting\n' "$n" "$s"; continue; fi
        t=$s-2; while secret_exists "$t"; do t=$t-2; done; printf '%s\t%s\trenamed\n' "$n" "$t"
      else printf '%s\t%s\tnew\n' "$n" "$s"; fi
    else fp=$(sed -n 's/^fp=//p' "$m"); loc=$(grep -lxF "fp=$fp" "$SEC"/*.meta 2>/dev/null | head -1)
      if [ -n "$loc" ]; then printf '%s\t%s\texisting\n' "$n" "$(basename "$loc" .meta)"
      elif secret_exists "$s"; then t=$s-2; while secret_exists "$t"; do t=$t-2; done; printf '%s\t%s\trenamed\n' "$n" "$t"
      else printf '%s\t%s\tnew\n' "$n" "$s"; fi; fi
  done
}
# Classify package servers as new | same | diff | ipdup. Print "name<TAB>type<TAB>details".
pack_classify(){ # package-directory
  local x=$1
  ensure_index; pack_index "$x" > "$x/index"; pack_secret_map "$x" > "$x/smap"
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
pack_block(){ # package-directory name [new-name] → Host block to write locally (with mapped secrets)
  local x=$1 n=$2 as=${3:-$2} r sc
  r=$(awk -F'\t' -v n="$n" '$1==n' "$x/index"); sc=$(printf '%s' "$r" | cut -f12)
  [ -n "$sc" ] && sc=$(awk -F'\t' -v s="$sc" '$1==s{print $2}' "$x/smap")
  host_block "$as" "$(printf '%s' "$r" | cut -f3)" "$(printf '%s' "$r" | cut -f4)" "$(printf '%s' "$r" | cut -f5)" "$(printf '%s' "$r" | cut -f6)" "$sc" "$(printf '%s' "$r" | cut -f8)" "$(printf '%s' "$r" | cut -f9)"
  block_extras "$(printf '%s' "$r" | cut -f10)" "$(printf '%s' "$r" | cut -f11)"
}
# Apply a decisions file: "name<TAB>add|skip|update|both|replace:<existing name>". Record added/updated names in last-import.
pack_apply_raw(){ # package-directory decisions-file
  local x=$1 dec=$2 n act p blk line t st new loc m needed
  : > "$STATE/last-import"; blk=$(tmpd) || return 1; blk=$blk/blk
  # import only secrets actually used by imported servers
  needed=$(awk -F'\t' 'NR==FNR { if ($2!="skip") act[$1]=1; next } ($1 in act) && $12!="" {print $12}' "$dec" "$x/index" | sort -u | tr '\n' ' ')
  while IFS="$TAB" read -r n loc st; do   # import secrets first because Host blocks reference them
    [ "$st" = existing ] && continue
    case " $needed " in *" $n "*) ;; *) continue;; esac
    vault_exists || return 1
    if [ -f "$x/secrets/$n.pass" ]; then cp "$x/secrets/$n.meta" "$SEC/$loc.meta"; secret_enc < "$x/secrets/$n.pass" > "$SEC/$loc.age"
    else cp "$x/secrets/$n.meta" "$SEC/$loc.meta"; cp "$x/secrets/$n.pub" "$SEC/$loc.pub"; chmod 600 "$SEC/$loc.pub"; secret_enc < "$x/secrets/$n.key" > "$SEC/$loc.age"; fi
  done < "$x/smap"
  while IFS="$TAB" read -r n act; do
    p=$(awk -F'\t' -v n="$n" '$1==n{print $2}' "$x/index"); t=$n
    case $act in
      skip) continue;;
      update) build_index; delete_host_raw "$n" keep;;
      replace:*) build_index; delete_host_raw "${act#replace:}";;
      both) t=$n-2; build_index; while host_exists "$t"; do t=$t-2; done;;
    esac
    pack_block "$x" "$n" "$t" > "$blk"; write_host "$p" "$blk"; echo "$t" >> "$STATE/last-import"
  done < "$dec"
  build_index
  [ -s "$x/groups.tsv" ] && while IFS= read -r line; do group_line "$line"
    new=$(for m in $GH; do host_exists "$m" && printf '%s ' "$m"; done)
    if [ -n "$(group_field "$GN" 1)" ]; then new=$(printf '%s\n' $(group_field "$GN" 3) $new | awk '!s[$0]++' | tr '\n' ' ')
      awk -F'\t' -v OFS='\t' -v n="$GN" -v h="${new% }" '$1==n{$3=h} {print}' "$GRP" > "$GRP.tmp" && mv "$GRP.tmp" "$GRP"
    elif [ "$(printf '%s\n' $new | grep -c .)" -ge 2 ]; then printf '%s\t%s\t%s\n' "$GN" "$GM" "${new% }" >> "$GRP"; fi
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
              i=$(UI_HEAD="$CB$(t imp_differs "$n")$C0$NL$(printf '%s\n' "$det" | tr ';' '\n' | sed 's/^ */  /')" ui_menu "" "$(t act_dec_update)" "$(t act_dec_keep)" "$(t act_dec_both)|$(t act_dec_both_hint "$n")" "$(t act_dec_update_all)" "$(t act_dec_keep_all)") || i=1
              case $i in
                0) printf '%s\tupdate\n' "$n";; 2) printf '%s\tboth\n' "$n";; 3) all=update; printf '%s\tupdate\n' "$n";; 4) all=skip; printf '%s\tskip\n' "$n";; *) printf '%s\tskip\n' "$n";; esac; fi;;
      ipdup) if [ "$mode" = pack ]; then printf '%s\tadd\n' "$n"; elif [ "$mode" = mine ]; then printf '%s\tskip\n' "$n"
             else at "$(t import_title)|$n"
               case $(ui_menu "$(t imp_dupip "$n" "$det")" "$(t act_dup_add)" "$(t act_dup_skip)" "!$(t act_dup_replace "$det")") in 0) printf '%s\tadd\n' "$n";; 2) printf '%s\treplace:%s\n' "$n" "$det";; *) printf '%s\tskip\n' "$n";; esac; fi;;
    esac
  done 3< "$x/classes"
}
import_pack(){ # file
  local f=$1 x from nh nnew nsame ndiff ndup dec mode np n sum
  x=$(pack_open "$f") || { pause; return 1; }
  if ls "$x"/secrets/*.meta >/dev/null 2>&1; then vault_init || { pause; return 1; }; fi
  pack_classify "$x" > "$x/classes"
  from=$(sed -n 's/^from=//p' "$x/manifest"); nh=$(grep -c . "$x/classes")
  nnew=$(grep -c "${TAB}new${TAB}" "$x/classes"); nsame=$(grep -c "${TAB}same${TAB}" "$x/classes"); ndiff=$(grep -c "${TAB}diff${TAB}" "$x/classes"); ndup=$(grep -c "${TAB}ipdup${TAB}" "$x/classes")
  np=$(awk -F'\t' '(" " $8 " ") ~ / prod /' "$x/index" | grep -c .)
  at "$(t import_title)"
  if [ $((nnew + ndiff + ndup)) -eq 0 ]; then flash "${CG}${ICON_OK} $(t imp_nothing)${C0}"; return 0; fi
  sum=$(printf '%s\n' "$(t imp_from "$from" "$nh" "$(grep -c . "$x/groups.tsv" 2>/dev/null)" "$(grep -c . "$x/smap")")$( [ "$np" -gt 0 ] && printf "   %s${ICON_PROD} %s%s" "$CR" "$(t imp_prod "$np")" "$C0")" ""
    [ "$nnew" -gt 0 ] && printf '  %s%s%s            %s\n' "$CG" "$(t imp_new "$nnew")" "$C0" "$(awk -F'\t' '$2=="new"{printf "%s ", $1}' "$x/classes" | cut -c1-60)"
    [ "$nsame" -gt 0 ] && printf '  %s%s%s\n' "$CD" "$(t imp_same "$nsame")" "$C0"
    [ "$ndiff" -gt 0 ] && { printf '  %s%s%s\n' "$CY" "$(t imp_diff "$ndiff")" "$C0"; awk -F'\t' '$2=="diff"{printf "      %-14s %s\n", $1, $3}' "$x/classes" | head -5 | cut -c1-100; }
    [ "$ndup" -gt 0 ] && { printf '  %s%s%s\n' "$CY" "$(t imp_dup "$ndup")" "$C0"; awk -F'\t' '$2=="ipdup"{print $1 "\t" $3}' "$x/classes" | head -5 | while IFS="$TAB" read -r n dec; do printf '      %s\n' "$(t imp_dup_line "$n" "$dec")"; done; }
    [ -s "$x/smap" ] && printf '\n  %s\n' "$(t imp_secrets "$(awk -F'\t' -v n="$(t imp_sc_new)" -v e="$(t imp_sc_existing)" -v r="$(t imp_sc_renamed)" '{printf "%s (%s%s) · ", $1, ($3=="new" ? n : $3=="existing" ? e : r), ($1!=$2 ? " → " $2 : "")}' "$x/smap" | sed 's/ · $//')")")
  case $(UI_HEAD=$sum$NL ui_menu "$(t imp_head)" "$(t act_imp_suggest)|$(t act_imp_suggest_hint)$( [ $((ndiff + ndup)) -gt 0 ] && t act_imp_suggest_ask)" "$(t act_imp_all)" "$(t act_imp_mine)|$(t act_imp_mine_hint)" "$(t cancel)") in
    0) mode=ask;; 1) mode=pack;; 2) mode=mine;; *) return 1;; esac
  dec=$x/decisions; pack_decide "$x" "$mode" > "$dec"
  apply_change "$(t done_pack_imported "$from" "$(grep -c "${TAB}add$" "$dec")" "$(grep -cE "${TAB}(update|replace:.*|both)$" "$dec")")" pack_apply_raw "$x" "$dec" || { pause; return 1; }
  import_done "$(grep -c "${TAB}add$" "$dec")" "$(grep -cE "${TAB}(update|replace:.*)$" "$dec")" "$(grep -c "${TAB}skip$" "$dec")"
}
import_done(){ # new-count updated-count skipped-count
  local n sum list; n=$(grep -c . "$STATE/last-import")
  sum="$CG${ICON_OK} $(t imp_done "$1" "$2" "$3")$C0"
  if [ "$n" -eq 0 ]; then flash "$sum"; return 0; fi
  list=$(awk -F'\t' -v f="$(t imp_done_line '%s' '%s' '%s')" 'FILENAME==ARGV[1] {wanted[$1]=1; next} ($1 in wanted) && ++k<=8 {printf "  "; printf f, $1, $3, $2; printf "\n"}' "$STATE/last-import" "$IDX")
  [ "$n" -le 8 ] || list="$list$NL$CD  $(t imp_done_more $((n-8)))$C0"
  at "$(t import_title)"
  case $(UI_HEAD="$sum$NL $NL$list" ui_menu "$(t imp_next)" "$(t act_imp_view)" "$(t act_imp_check)|$(t act_imp_check_hint)" "$(t act_imp_later)" "$(t act_imp_undo)") in
    0) ONLY_NEXT=$STATE/last-import;;
    1) check_hosts $(cat "$STATE/last-import");;
    3) undo_last;;
  esac
}
clip_offer(){ # offer to import a share package from the clipboard (only once per package)
  local c h f line cnt from
  c=$(clip_get 2>/dev/null | head -c 200000) || return 0
  printf '%s' "$c" | head -c 300 | grep -q '^# hostbook-share v1' || return 0
  h=$(printf '%s' "$c" | sha256); [ "$h" = "$(cat "$STATE/last-offer" 2>/dev/null)" ] && return 0
  echo "$h" > "$STATE/last-offer"
  line=$(printf '%s' "$c" | head -1)   # "# hostbook-share v1 · N server · từ <machine>": the file format, not screen text
  cnt=$(printf '%s' "$line" | sed -n 's/^# hostbook-share v1 · \([0-9]*\) server.*/\1/p'); from=$(printf '%s' "$line" | sed -n 's/.* từ //p')
  at "$(t import_title)"
  ui_confirm --affirmative "$(t clip_yes)" --negative "$(t clip_no)" "$(t clip_q)" ${from:+"$(t clip_from "$from")"} ${cnt:+"$(t clip_count "$cnt")"} "$CD$(t clip_note)$C0" || return 0
  f=$(tmpd) || return 1; f=$f/clip.hbshare; printf '%s\n' "$c" > "$f"; import_pack "$f"
}
