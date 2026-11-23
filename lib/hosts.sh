# Host interaction flows. Shared parsing, mutation and suggestion functions remain in core.
pick_project(){ # suggestions-file → print the project name (call with UI_HEAD for the finished steps)
  local f=$1 items=() keys=() p sc dt why i
  while IFS="$TAB" read -r _ p sc dt why; do items+=("$p|$dt $why"); keys+=("$p"); done < <(grep '^project' "$f")
  for p in $(cut -f2 "$IDX" | sort -u); do case " ${keys[*]:-} " in *" $p "*) ;; *) items+=("$p"); keys+=("$p");; esac; done
  [ ${#keys[@]} -eq 0 ] || items=("#$(t sec_projects)" "${items[@]}")
  items+=("#$(t sec_new)" "$(t move_new)"); keys+=("+")
  i=$(ui_menu "$(t proj_head)" "${items[@]}") || return 1
  if [ "${keys[$i]}" = "+" ]; then ui_field "$(t move_new_label)" "" project "$(t move_rule)"$'\n'"$(t example project-d)" "" "$UI_HEAD"
  else echo "${keys[$i]}"; fi
}
pick_tags(){ # project current-tags → print new tags
  local g=$1 cur=$2 all sel other extra idx t i=0 action on= more
  local items=()
  other=$(t tags_other)
  all=$( { printf '%s\n' $cur; awk -F'\t' -v g="$g" '$2==g{print $8}' "$IDX" | tr ' ' '\n'; } | grep . | awk '!s[$0]++')
  while IFS= read -r t; do [ -n "$t" ] || continue; items+=("$t"); i=$((i+1)); case " $cur " in *" $t "*) on="$on $i";; esac; done <<< "$all"
  action=$i; items+=("$other")
  while :; do
    idx=$(UI_SELECT="$on" UI_CHECK_ACTION=$action ui_checklist "$(t tags_head)" "${items[@]}") || return 1
    sel=; on=; more=
    for i in $idx; do
      if [ "$i" -eq "$action" ]; then more=1
      else sel="$sel${items[$i]}"$'\n'; on="$on $((i+1))"; fi
    done
    [ -n "$more" ] || break
    extra=$(ui_field "$(t tags_add_label)" "" any "$(t tags_add_rule)"$'\n'"$(t example 'prod web')") || continue
    sel="$sel"$(printf '%s' "$extra" | tr ' ' '\n')$'\n'
    break
  done
  printf '%s\n' "$sel" | tr -cd 'A-Za-z0-9._\n-' | awk 'NF && !seen[$0]++' | tr '\n' ' ' | sed 's/ $//'
}
add_addr_ask(){ # [value] [intro] → print the address once parse_addr reads it
  local v=${1:-} intro=${2:-} e= raw
  while :; do
    raw=$(ui_field "$(t add_addr_label)" "$v" addr "$(t add_addr_rule)"$'\n'"$(t example '10.0.1.13, ubuntu@10.0.1.13, 10.0.1.13:2222')" "" "$intro${e:+${intro:+$NL}$e}") || return 1
    if parse_addr "$raw" >/dev/null; then printf '%s\n' "$raw"; return 0; fi
    v=$raw; e="$CR$ICON_ERR $(t err_addr_read "$raw")$C0"
  done
}
add_name_ask(){ # value suggestion-reason intro own-name → print a complete, unused server name
  local v=${1:-} why=${2:-} intro=${3:-} d n rule e=
  d=$(tmpd) || return 1; { [ -f "$IDX" ] && cut -f1 "$IDX"; } | awk -v x="${4:-}" '$0!=x' > "$d/taken"
  while :; do
    rule=$(t rename_rule); if [ -n "$why" ]; then rule=$rule$'\n'$(t add_name_filled "$v" "$why"); elif [ -z "$v" ]; then rule=$rule$'\n'$(t example web3); fi
    n=$(ui_field "$(t f_name)" "$v" "name:$d/taken" "$rule" "$(t add_name_prev "$CP$CB%s$C0")" "$intro${e:+${intro:+$NL}$e}") || return 1
    case $n in *-) e="$CR$ICON_ERR $(t err_name_unfinished "$n")$C0"; v=$n; why=;; *) printf '%s\n' "$n"; return 0;; esac
  done
}
# Steps of add_host: addr, name, peer, proj, login, review. A step that shows a screen is pushed on "hist"; esc goes back to the
# previous one (keeping what was typed), and leaves the flow only from the first screen. The functions below see add_host's locals.
add_push(){ case $hist in *"$st ") ;; *) hist="$hist$st ";; esac; escl=$(t key_back); [ "$hist" != "$st " ] || escl=$(t key_cancel); }
add_back(){ local l; hist=${hist%"$st "}; [ -n "$hist" ] || return 1; l=${hist% }; st=${l##* }
  case $st in addr) x=$raw; raw=""; pre=$name; prewhy=""; name="";; name) pre=$name; prewhy=""; name="";; peer) cloned="";; login) secret="";; esac; }
add_fin(){ # [withproj] → finished steps as check lines
  fin=""; [ -z "$raw" ] || fin=$(ui_done "$(t f_addr)" "$raw"); [ -z "$name" ] || fin="${fin:+$fin$NL}$(ui_done "$(t f_name)" "$name")"
  [ -z "${1:-}" ] || fin="${fin:+$fin$NL}$(ui_done "$(t f_project)" "$group" "$src_group")"; }
add_host(){ # [address] [name]: ask only what is needed, infer the rest, allow review, then save
  local raw=${1:-} name=${2:-} u h p pre="" prewhy="" line i blk f dup fin="" e="" sug peer peerwhy cloned="" x="" via login has tbl rc st=addr hist="" escl
  local group="" src_group="" user="" src_user="" port="" tags="" src_tags="" jump="" src_jump="" secret="" src_secret="" desc="" lines=()
  ensure_index; sug=$(tmpd) || return 1; sug=$sug/sug
  if [ -n "$raw" ] && ! parse_addr "$raw" >/dev/null; then e="$CR$ICON_ERR $(t err_addr_read "$raw")$C0"; x=$raw; raw=""; fi
  while :; do
    case $st in
    addr)
      at "$(t add_title)|$(t add_step 1 4)"   # address, name, project, login: a step that is already known still counts
      if [ -z "$raw" ]; then add_push; raw=$(UI_ESC=$escl add_addr_ask "$x" "$e") || { add_back || return 1; continue; }; fi
      e=""; x=""; line=$(parse_addr "$raw")
      u=$(echo "$line" | sed -n 1p); h=$(echo "$line" | sed -n 2p); p=$(echo "$line" | sed -n 3p)
      suggest "$h" "" "$u" "$p" > "$sug"
      if [ -z "$pre" ]; then pre=$(sug_val "$sug" prefix); prewhy=$(sug_why "$sug" prefix); fi
      st=name;;
    name)
      at "$(t add_title)|$(t add_step 2 4)"
      if [ -n "$name" ]; then   # given on the command line
        case $name in *-) e="$CR$ICON_ERR $(t err_name_unfinished "$name")$C0"; pre=$name; prewhy=""; name="";;
          *) if ! valid_name "$name"; then e="$CR$ICON_ERR $(t err_name_chars)$C0"; name=""
             elif host_exists "$name"; then e="$CR$ICON_ERR $(t err_name_taken "$name")$C0"; name=""; fi;; esac
      fi
      if [ -z "$name" ]; then add_push; add_fin; name=$(UI_ESC=$escl add_name_ask "$pre" "$prewhy" "$fin${e:+$NL$e}") || { add_back || return 1; continue; }; fi
      e=""; st=peer;;
    peer)
      at "$(t add_title)|$(t add_step 3 4)"; cloned=""
      suggest "$h" "$name" "$u" "$p" > "$sug"
      peer=$(awk -F'\t' '$1=="peer"{print $2}' "$sug"); peerwhy=$(awk -F'\t' '$1=="peer"{print $4}' "$sug")
      if [ -n "$peer" ]; then   # a very similar server exists: offer to copy its settings
        add_push; add_fin
        login=$(host_field "$peer" 12); via=$(host_field "$peer" 6)
        has="$(t add_peer_user "$(host_field "$peer" 4)")${login:+ · $login}${via:+ · $(t add_peer_via "$via")} · $(t add_peer_proj "$(host_field "$peer" 2)")"
        lines=("$CD$(t add_peer_why)$C0")
        while IFS= read -r x; do [ -z "$x" ] || lines+=("  • $x"); done <<< "${peerwhy// · /$'\n'}"
        lines+=(" " "$(t add_peer_has "$peer" "$has")")
        UI_ESC=$escl UI_HEAD=$fin$NL ui_confirm --affirmative "$(t add_peer_yes "$peer")" --negative "$(t add_peer_no)" "$(t add_peer_q "$peer")" "${lines[@]}"; rc=$?
        case $rc in 0) cloned=1;; 1) ;; *) add_back || return 1; continue;; esac
      fi
      st=proj;;
    proj)
      at "$(t add_title)|$(t add_step 3 4)"
      if [ -n "$cloned" ]; then group=$(host_field "$peer" 2)
      elif [ "$(awk -F'\t' '$1=="auto"{print $2}' "$sug")" = 1 ]; then group=$(awk -F'\t' '$1=="group"{print $2}' "$sug")
      elif [ ! -s "$IDX" ]; then group=servers
      else add_push; add_fin; group=$(UI_ESC=$escl UI_HEAD=$fin$NL pick_project "$sug") || { add_back || return 1; continue; }; fi
      read_suggest "$h" "$name" "$u" "$p" "$group"
      if [ -n "$cloned" ]; then
        [ -n "$u" ] || { user=$(host_field "$peer" 4); src_user=$(t add_same_as "$peer"); }
        jump=$(host_field "$peer" 6); src_jump=${jump:+$(t add_same_as "$peer")}; secret=$(host_field "$peer" 12); src_secret=${secret:+$(t add_same_as "$peer")}; src_group=$(t add_same_as "$peer")
      fi
      st=login;;
    login)
      at "$(t add_title)|$(t add_step 4 4)"
      if [ -z "$secret" ]; then   # nothing to suggest: ask for the login method now
        add_push; add_fin withproj; secret=$(UI_ESC=$escl UI_HEAD=$fin$NL pick_login) || { add_back || return 1; continue; }
      fi
      st=review;;
    review)
      at "$(t add_title)|$(t add_review)"; add_push
      dup=$(awk -F'\t' -v ip="$h" '$3==ip{print $1; exit}' "$IDX")
      tbl=$(row "$(t f_name)" "$name"
        row "$(t f_addr)" "$user@$h$( [ "$port" != 22 ] && echo ":$port")" "$src_user"
        row "$(t f_project)" "$group" "$src_group"
        row "$(t f_tags)" "${tags:-—}" "$src_tags"
        row "$(t f_jump)" "${jump:-$(t f_no_jump)}" "$src_jump"
        row "$(t f_login)" "$( [ -n "$secret" ] && echo "$secret ($(secret_label "$secret"))" || t card_ssh_self)" "$src_secret"
        row "$(t f_desc)" "${desc:-$(t add_empty)}"
        [ -n "$dup" ] && printf '%s%s%s %s\n' "$CY" "$ICON_WARN" "$C0" "$(t add_dup "$dup")")
      i=$(UI_ESC=$escl UI_HEAD=$tbl ui_menu "" "$(t act_add_save)|$(t act_add_save_hint)" "$(t act_add_edit)" "$(t cancel)") || { add_back || return 1; continue; }
      case $i in
        0) break;;
        1) case $(ui_menu "$(t add_edit_head)" "$(t f_name)|$name" "$(t f_addr)|$user@$h:$port" "$(t f_project)|$group" "$(t f_tags)|${tags:-—}" "$(t f_jump)|${jump:-$(t f_no_jump)}" "$(t f_login)|${secret:-$(t card_ssh_self)}" "$(t f_desc)|${desc:-$(t add_empty)}") in
             0) i=$(add_name_ask "$name" "" "" "$name") && name=$i;;
             1) i=$(add_addr_ask "${user:+$user@}$h:$port") && line=$(parse_addr "$i") && { u=$(echo "$line" | sed -n 1p); h=$(echo "$line" | sed -n 2p); p=$(echo "$line" | sed -n 3p); user=${u:-$user}; port=${p:-22}; src_user=""; };;
             2) i=$(pick_project "$sug") && { group=$i; src_group=""; };;
             3) i=$(pick_tags "$group" "$tags") && { tags=$i; src_tags=""; };;
             4) x=(); while IFS= read -r i; do x+=("$i"); done < <(awk -F'\t' '$8 ~ /(^| )bastion( |$)/ {print $1}' "$IDX")
                i=$(ui_menu "$(t add_jump_head)" "$(t add_jump_none)" ${x[@]+"${x[@]}"}) && { if [ "$i" = 0 ]; then jump=""; else jump=${x[$((i-1))]}; fi; src_jump=""; };;
             5) i=$(pick_login "$secret" "$src_secret") && { secret=$i; src_secret=""; };;
             6) i=$(ui_field "$(t f_desc)" "$desc" any "$(t add_desc_rule)"$'\n'"$(t example "$(t add_desc_example)")") && desc=$(printf '%s' "$i" | tr -d '\t\n\r');;
           esac;;
        *) return 1;;
      esac;;
    esac
  done
  blk=$(mktemp); host_block "$name" "$h" "$user" "$port" "$jump" "$secret" "$tags" "$desc" > "$blk"
  f=$DIR/$group.conf; [ -f "$f" ] && cp "$f" "$blk.bak"
  write_host "$group" "$blk" || return 65
  if ! i=$(validate); then
    if [ -f "$blk.bak" ]; then mv "$blk.bak" "$f"; else rm -f "$f"; fi
    at "$(t add_title)"; shown; err "$(t add_err_invalid)"; dim "$i"; rm -f "$blk"; pause; return 1
  fi
  rm -f "$blk" "$blk.bak"; build_index
  save_and_sync "$(t done_added "$name")"
  ADDED=$name
}
edit_host(){ # open the file at the correct line, validate on close, and prompt again if invalid
  local n=$1 f l ed e bak
  f=$(host_field "$n" 10); l=$(host_field "$n" 11); [ -n "$f" ] || return 1
  ed=${VISUAL:-${EDITOR:-$(have nano && echo nano || echo vi)}}
  bak=$(mktemp); cp "$f" "$bak"
  while :; do
    legacy_destinations "$f" || return 65
    case $ed in code*|cursor*) $ed -g "$f:$l" --wait;; *) $ed "+$l" "$f";; esac
    cmp -s "$f" "$bak" && { rm -f "$bak"; return 0; }
    if e=$(validate); then rm -f "$bak"; build_index; save_and_sync "$(t done_edited "$n")"; return 0; fi
    echo; box err "${CR}$(t eh_unsynced)${C0}" "$(printf '%s' "$e" | sed "s|$DIR/||" | head -3)"
    l=$(printf '%s' "$e" | sed -n 's/.*line \([0-9][0-9]*\).*/\1/p' | head -1); l=${l:-1}
    case $(pick "$(t eh_next)" "$(t eh_reopen)|$(t eh_reopen_h "$l")" "$(t eh_revert)|$(t eh_revert_h)") in
      1) legacy_destinations "$f" || return 65; cp "$bak" "$f"; rm -f "$bak"; build_index; flash "${CG}↩ $(t done_restored "$(basename "$f")")${C0}"; return 0;;
      *) ;;   # the first item and esc both go back to the editor: esc never drops the edit
    esac
  done
}


delete_server(){
  local n=$1 users gs lines=() d
  at "$n|$(t act_delete)" "$n"; users=$(jump_users "$n")
  gs=$(awk -F'\t' -v n="$n" 'index(" " $3 " ", " " n " ") {printf "%s%s", (c++ ? ", " : ""), $1}' "$GRP" 2>/dev/null)
  lines+=("$CB$(t delete_removes)$C0" "  • $(t delete_block "$(t where_line "$(basename "$(host_field "$n" 10)")" "$(host_field "$n" 11)")")")
  [ -n "$gs" ] && lines+=("  • $(t delete_groups "$gs")")
  [ -n "$users" ] && lines+=("  ${CR}• $(t delete_jump_users "$users")$C0")
  lines+=("" "$CD$(t delete_undo)$C0")
  if is_prod "$n"; then   # PROD: type the name, so enter alone can never delete it
    UI_TONE=danger UI_ENTER=$(t key_delete) ui_field "$(t delete_type_label "$n")" "" "equals:$n" "$(t delete_type_rule "$n")" "" "$(printf '%s\n' "${lines[@]}")" >/dev/null || return 1
  else
    confirm --danger --default=false --affirmative "$(t delete_yes "$n")" --negative "$(t cancel)" "$(t delete_question "$n")" "${lines[@]}" || return 1
  fi
  apply_change "$(t done_deleted "$n")" delete_host_raw "$n"
}
rename_server(){
  local n=$1 m d
  at "$n|$(t act_rename)" "$n"
  d=$(tmpd) || return 1; awk -F'\t' -v n="$n" '$1!=n {print $1}' "$IDX" > "$d/taken"
  m=$(ui_field "$(t rename_label)" "$n" "name:$d/taken" "$(t rename_rule)" \
      "$n ${CD}→$C0 $CP$CB%s$C0"$'\n'"$CD$(host_where "$n") · ssh %s$C0") || return 1
  [ "$m" = "$n" ] && return 0
  apply_change "$(t done_renamed "$n" "$m")" rename_host_raw "$n" "$m"
}
move_server(){
  local n=$1 cur to p c d i items=() projs=() lines=() f nf
  at "$n|$(t act_move)" "$n"; cur=$(host_field "$n" 2)
  host_shared "$n" && { shown; err "$(t err_shared "$n")"; pause; return 1; }
  while IFS="$TAB" read -r p c; do projs+=("$p"); items+=("$p|$(t sc_users "$c")"); done < <(awk -F'\t' -v c="$cur" '$2!=c {k[$2]++} END {for (p in k) print p "\t" k[p]}' "$IDX" | sort)
  [ ${#projs[@]} -eq 0 ] || items=("#$(t sec_projects)" "${items[@]}")
  items+=("#$(t sec_new)" "$(t move_new)")
  i=$(ui_menu "$(t move_head "$n")" "${items[@]}") || return 1
  if [ "$i" -ge ${#projs[@]} ]; then
    to=$(ui_field "$(t move_new_label)" "" project "$(t move_rule)"$'\n'"$(t example project-d)" "$(t move_to "$n" "$CP$CB%s$C0")") || return 1
  else to=${projs[$i]}; fi
  f=$(host_field "$n" 10); nf=$DIR/$to.conf
  lines=("$CB$(t move_will)$C0" "  • $(t move_line_project "$cur" "$to")" "  • $(t move_line_file "$(t where_line "${f##*/}" "$(host_field "$n" 11)")" "$to.conf")$([ -f "$nf" ] || t move_file_new)" "" "$CD$(t move_same)$C0")
  ui_confirm --affirmative "$(t move_yes "$n")" --negative "$(t cancel)" "$(t move_question "$n" "$to")" "${lines[@]}" || return 1
  apply_change "$(t done_moved "$n" "$to")" move_host_raw "$n" "$to"
}

