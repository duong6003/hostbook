term_cols(){ # [var]
  local _tc; _tc=$(stty size 2>/dev/null </dev/tty); _tc=${_tc#* }; _tc=${_tc:-${COLUMNS:-80}}
  if [ -n "${1:-}" ]; then printf -v "$1" '%s' "$_tc"; else echo "$_tc"; fi; }
box(){ # color line... · wrap lines wider than the screen within the box
  local c=$1 l v max=0 cols; shift; cols=$(term_cols)
  case $c in err) c=$TH_ERR;; warn) c=$TH_WARN;; ok) c=$TH_OK;; title) c=$TH_TITLE;; dim) c=$TH_DIM;; esac
  c=$(gum_color "$c")
  for l in "$@"; do v=$(printf '%s' "$l" | sed $'s/\033\\[[0-9;]*m//g'); [ ${#v} -gt $max ] && max=${#v}; done
  if [ $((max + 4)) -gt "$cols" ]; then gum style --border rounded --padding "0 1" --border-foreground "$c" --width $((cols - 2)) "$@"
  else gum style --border rounded --padding "0 1" --border-foreground "$c" "$@"; fi; }
title(){ at "$*" "$UI_HOST"; printf '%s\n%s\n\n' "$(ui_label)" "$CBORDER$(printf '%*s' "$(term_cols)" '' | tr ' ' '─')$C0"; }   # text screens: breadcrumb line, then a rule
shown(){ clear; title "$UI_AT"; }   # a text screen under the current breadcrumb (for output that follows a framed screen)
pause(){ # [text] → wait for enter, or esc on its own: an arrow key sends esc plus more bytes, which must not leak into the next screen
  local k r rc; printf '\n%s%s%s' "$CD" "${1:-$(t pause_back)}" "$C0" >&2
  while IFS= read -rsn1 k </dev/tty; do
    case $k in
      '') return 0;;
      $'\e') r=; read -rsn2 -t 0.05 r </dev/tty 2>/dev/null; rc=$?
        [ $rc -ne 1 ] || { read -rsn2 -t 1 r </dev/tty 2>/dev/null; rc=$?; }   # bash 3.2 takes whole seconds only
        [ $rc -le 128 ] || return 0;;
    esac
  done; return 0; }
# Not yet migrated flows print text and then ask: their screens open inline below that text (UI_INLINE) instead of
# covering it. Migrated screens call ui_* directly, pass their text as header lines, and get the full frame.
pick(){ UI_INLINE=1 ui_menu "$@"; }   # HEADER ITEM… → zero-based index of the chosen item
row(){ # label value [suggestion source] · align by character count, not bytes; indent the source on a separate line on narrow screens
  local cols; cols=$(term_cols)
  local src=${3:-} room=$(( cols - 6 - 19 - 2 ))
  [ ${#src} -gt $room ] && [ $room -gt 8 ] && src="${src:0:$((room - 1))}…"
  if [ -n "$src" ] && [ $(( 19 + ${#2} + 5 + ${#3} )) -gt $(( cols - 6 )) ]; then printf '%s%*s %s\n%19s%s← %s%s\n' "$1" $((18-${#1})) '' "$2" '' "$CD" "$src" "$C0"
  else printf '%s%*s %s%s\n' "$1" $((18-${#1})) '' "$2" "${3:+   ${CD}← $3${C0}}"; fi; }
confirm(){ UI_INLINE=1 ui_confirm "$@"; }
ask(){ # header [example] [value] → typed text; an example is shown as "e.g. …" under the field, never as a value
  local rule=''; [ -n "${2:-}" ] && [ -z "${3:-}" ] && rule=$(t example "$2")
  UI_INLINE=1 ui_field "$1" "${3:-}" any "$rule"; }

# ---------- screens (docs/design/screens.md) ----------
# Every screen besides the search list is one fzf frame: a header box (breadcrumb label + context card), an input or
# list box, and a footer box with key hints. A flow calls "at" once; the screens it opens then share that context.
UI_AT=""; UI_HOST=""; UI_TONE=""; UI_ENTER=""; UI_INLINE=""; UI_TAKEN=""; UI_HEAD=""; UI_POS=""; UI_EXPECT=""; UI_FOOT=""   # UI_TONE=danger: error-coloured frame; UI_ENTER: what enter does; UI_TAKEN: lang key for "name already used"
at(){ UI_AT=$1; UI_HOST=${2-}; }   # "part|part…" [server for the context card]
ui_label(){ # breadcrumb for the header label; the last part is what the screen does
  local rest=$UI_AT p o=" ${CP}${CB}hostbook${C0}"
  while [ -n "$rest" ]; do p=${rest%%|*}; if [ "$p" = "$rest" ]; then rest=; else rest=${rest#*|}; fi
    if [ -n "$rest" ]; then o="$o ${CD}›${C0} $p"; else o="$o ${CD}›${C0} ${CU}${CB}$p${C0}"; fi; done
  printf '%s ' "$o"; }
ui_keys(){ local k o=; for k in "$@"; do o="$o${o:+   }${CP}${k%%:*}${C0} ${CD}${k#*:}${C0}"; done; printf '%s' "$o"; }
ui_card(){ # [server] → context card: name and PROD; user@ip · project · login; description · groups
  local h=${1:-$UI_HOST}; [ -n "$h" ] && [ -f "$IDX" ] || return 0
  awk -F'\t' -v n="$h" -v b="$CB" -v z="$C0" -v d="$CD" -v r="$CR" -v ip="$ICON_PROD" -v grp="$GRP" \
    -v wp="$(t card_project)" -v wl="$(t card_login)" -v ws="$(t card_ssh_self)" -v wv="$(t card_via)" -v wg="$(t card_groups)" '
    BEGIN { while ((getline l < grp) > 0) { split(l, g, "\t"); if (index(" " g[3] " ", " " n " ")) gs = gs (gs != "" ? ", " : "") g[1] } }
    { sub(/\r$/, "") }
    $1 == n { prod = (" " $8 " " ~ / prod /)
      printf "%s%s%s%s\n", b, $1, z, (prod ? "  " r b ip " PROD" z : "")
      printf "%s%s%s%s · %s %s · %s %s%s%s\n", d, ($4 != "" ? $4 "@" : ""), $3, ($5 != "" && $5 != "22" ? ":" $5 : ""), wp, $2, wl, ($12 != "" ? $12 : ws), ($6 != "" ? " · " wv " " $6 : ""), z
      if ($9 != "" || gs != "") printf "%s%s%s%s%s\n", d, $9, ($9 != "" && gs != "" ? " · " : ""), (gs != "" ? wg " " gs : ""), z
      exit }' "$IDX"
}
ui_gutter(){ # The option is absent in fzf 0.63; without it newer fzf repeats the pointer on every row.
  local help; FZF_GUTTER=(); help=$(fzf --help)
  case $help in *--gutter*) FZF_GUTTER=(--gutter=' ');; esac
}
ui_fzf(){ # header-text fzf-args… (stdin: list) → the shared frame
  local head=$1 border style; shift; cfg border rounded border
  ui_gutter
  style=--style=full:$border; [ "$border" = none ] && style=--style=minimal
  [ -n "$head" ] || head=' '   # an empty header hides the box that carries the breadcrumb
  [ "$border" != none ] || head="$(ui_label)$NL$head"   # minimal style draws no box labels: the breadcrumb becomes the first header line
  local color; color=$(fzf_colors); [ "$UI_TONE" = danger ] && [ -z "${NO_COLOR:-}" ] && color="$color,border:$TH_ERR,label:$TH_ERR"
  fzf ${FZF_GUTTER[@]+"${FZF_GUTTER[@]}"} --ansi --layout=reverse --height="${UI_INLINE:+~}100%" "$style" --header-first --info=hidden --pointer="$ICON_PTR" \
    --no-scrollbar --color="$color" --header-label="$(ui_label)" --header-label-pos=2 --header="$head" "$@"
}
ui_head(){ # [line…] → header text: the context card, a blank line, then the screen's own lines
  local c l o=; c=$(ui_card)
  for l in "$@"; do if [ -n "$l" ] || [ -n "$o" ]; then o="${o:+$o$NL}$l"; fi; done   # an empty line in the middle stays a blank line
  while [ "${o%"$NL"}" != "$o" ]; do o=${o%"$NL"}; done
  local w; term_cols w; w=$((w - 7))   # fzf headers do not wrap: fold long lines here so nothing is cut off (S3)
  if [ -n "$c" ] && [ -n "$o" ]; then printf '%s\n \n%s' "$c" "$o"; else printf '%s' "$c$o"; fi | ui_wrap "$w"; }
ui_wrap(){ # width: fold stdin lines at spaces, counting characters (not bytes, not colour codes); continuations keep
  # the line's colour and indent
  LC_ALL=C awk -v W="$1" '
    function vis(s,  t) { t = s; gsub(/\033\[[0-9;]*m/, "", t); gsub(/[\200-\277]/, "", t); return length(t) }
    { if (vis($0) <= W) { print; next }
      lead = ""; rest = $0; if (match(rest, /^\033\[[0-9;]*m/)) { lead = substr(rest, 1, RLENGTH); rest = substr(rest, RLENGTH + 1) }
      ind = ""; if (match(rest, /^ +/)) ind = substr(rest, 1, RLENGTH)
      n = split(rest, w, " "); line = ""
      for (i = 1; i <= n; i++) { cand = (line == "" ? lead ind w[i] : line " " w[i])
        if (line != "" && vis(cand) > W) { print line (lead != "" ? "\033[0m" : ""); line = lead ind "  " w[i] } else line = cand }
      print line }'
}
# ui_field LABEL VALUE CHECK [RULE] [PREVIEW] [INTRO] → the value on enter (status 1 on esc). CHECK runs in fzf's shell on
# every key, builtins only: name:<file of taken names> | lname:<file> (lowercase only) | gname:<file> (group: lowercase, no ".") | addr | port | project | text | equals:<word> | any.
# PREVIEW is shown in the list box; every %s in it becomes the typed value.
ui_field(){
  local label=$1 value=$2 check=$3 rule=${4:-} prev=${5:-} intro=${6:-} d f keys v out tk rc prompt='› ' rl w
  [ "$(cfg border rounded)" != none ] || prompt="$label › "   # no input box label in the minimal style
  term_cols w; rl=$(printf '%s' "${rule:+$CD$rule$C0}" | ui_wrap "$((w - 7))")   # fzf footers do not wrap (S3)
  tk='m=; while IFS= read -r n; do [ "$n" = "$q" ] && m="${HB_E_TAKEN%%%s*}$q${HB_E_TAKEN#*%s}"; done < "$HB_ARG"'
  d=$(tmpd) || return 1; f=$d/field.sh
  keys=$(ui_keys "enter:${UI_ENTER:-$(t key_save)}" "esc:${UI_ESC:-$(t key_cancel)}")
  case $check in
    name:*) v='case $q in "") m=$HB_E_EMPTY;; *" "*) m=$HB_E_SPACE;; *[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0-9._-]*) m=$HB_E_CHARS;; *) '"$tk"';; esac';;
    lname:*) v='case $q in "") m=$HB_E_EMPTY;; *[!abcdefghijklmnopqrstuvwxyz0-9._-]*) m=$HB_E_LOWER;; *) '"$tk"';; esac';;
    # groups: no ".", tmux turns it into "-" in session names, so shop.prod and shop-prod would share one session
    gname:*) v='case $q in "") m=$HB_E_EMPTY;; *[!abcdefghijklmnopqrstuvwxyz0-9_-]*) m=$HB_E_GROUP;; *) '"$tk"';; esac';;
    port) v='case $q in ""|*[!0-9]*) m=$HB_E_PORT;; *) if [ "$q" -ge 1 ] && [ "$q" -le 65535 ]; then m=; else m=$HB_E_PORT; fi;; esac';;
    project) v='case $q in ""|*[!abcdefghijklmnopqrstuvwxyz0-9-]*) m=$HB_E_PROJECT;; *) m=;; esac';;
    addr) v='case $q in "") m=$HB_E_EMPTY;; *[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0-9._@:-]*|*@*@*|@*|*@|*@:*|:*|*:|*:*[!0-9]*) m=$HB_E_ADDR;; *) m=;; esac';;
    text) v='if [ -n "$q" ]; then m=; else m=$HB_E_EMPTY; fi';;
    equals:*) v='if [ "$q" = "$HB_ARG" ]; then m=; else m=; [ -n "$q" ] && m="${HB_E_EQUALS%%%s*}$HB_ARG${HB_E_EQUALS#*%s}"; [ -z "$q" ] && m=-; fi';;
    *) v='m=';;
  esac
  { printf '%s\n' 'q=$FZF_QUERY' "$v"
    cat <<'SH'
case $1 in
  prev) p=$HB_PREV; o=; [ -n "$q" ] || p=; while :; do case $p in *%s*) o=$o${p%%%s*}$q; p=${p#*%s};; *) break;; esac; done; printf '%s\n' "$o$p";;
  enter) if [ -z "$m" ]; then echo accept; fi;;
  *) [ -n "$HB_PREV" ] && printf 'reload(sh %s prev)+' "$HB_SELF"
     if [ -n "$m" ] && [ "$m" != - ] && [ -n "$q" ]; then printf 'change-footer:%s%s%s\n%s' "$HB_ERR" "$m" "$HB_C0" "$HB_KEYS"; else printf 'change-footer:%s' "$HB_FOOT"; fi;;
esac
SH
  } > "$f"
  out=$(export HB_SELF="$(printf %q "$f")" HB_PREV="$prev" HB_ARG="${check#*:}" HB_FOOT="${rl:+$rl$'\n'}$keys" HB_KEYS="$keys" \
      HB_ERR="$CR$ICON_ERR " HB_C0="$C0" HB_E_EMPTY="$(t err_empty)" HB_E_SPACE="$(t err_name_space)" HB_E_CHARS="$(t err_name_chars)" \
      HB_E_TAKEN="$(t "${UI_TAKEN:-err_name_taken}")" HB_E_LOWER="$(t err_name_lower)" HB_E_GROUP="$(t err_group_chars)" HB_E_PORT="$(t err_port)" HB_E_PROJECT="$(t err_project)" HB_E_EQUALS="$(t err_equals)" HB_E_ADDR="$(t err_addr)"
    : | ui_fzf "$(ui_head "$intro")" --disabled --print-query --query="$value" --prompt="$prompt" \
      --input-label=" $label " --list-label="${prev:+ $(t preview_label) }" --no-highlight-line --pointer=' ' \
      --footer="${rl:+$rl$'\n'}$keys" --bind "start:transform:sh $(printf %q "$f") change" \
      --bind "change:transform:sh $(printf %q "$f") change" --bind "enter:transform:sh $(printf %q "$f") enter" --bind 'esc:abort')
  rc=$?; [ $rc -le 1 ] || return 1   # enter on an empty list is "no match" (1) for fzf, esc is 130
  printf '%s\n' "${out%%$'\n'*}"
}
# ui_multiline LABEL VALUE → text on submit, nonzero on cancel.
ui_multiline(){
  local label=$1 value=$2 keys cols rows height header ga gt
  keys=$(ui_keys "ctrl+j:$(t multiline_newline)" "enter:$(t key_continue)" "ctrl+c:$(t key_cancel)")
  cols=$(term_cols); rows=$(term_rows); header="$(ui_label)$NL$(ui_head "$label")$NL$keys"
  height=$((rows - 7)); [ "$height" -le 10 ] || height=10; [ "$height" -ge 1 ] || height=1
  gum_color "$TH_ACCENT" ga; gum_color "$TH_TITLE" gt
  GUM_WRITE_VALUE="$value" GUM_WRITE_PROMPT_FOREGROUND=$ga GUM_WRITE_CURSOR_FOREGROUND=$ga GUM_WRITE_HEADER_FOREGROUND=$gt \
    gum write --width="$((cols > 4 ? cols - 4 : 1))" --height="$height" --char-limit=0 --header="$header" --placeholder=''
}
# ui_menu HEADER ITEM… → zero-based index of the chosen item. Items: "Label|description"; "#Name" starts a section
# (not an item); "!Label" marks a destructive item, shown in the error colour. Prefix a call with UI_HEAD (lines above the
# question, e.g. finished steps), UI_POS (row to start on), UI_EXPECT (extra keys that end it: the output is then the key,
# a newline, the index) or UI_FOOT (replaces the key hints). UI_MULTI=1: tab marks items, the output is one index per line
# (none when enter is pressed with nothing marked); UI_SELECT="1 3" marks those 1-based rows on start.
# UI_QUERY enables query input/output; include esc in UI_EXPECT when cancellation must preserve it.
ui_menu(){
  local header=$1; shift; local x lab desc sec= cols lw=0 sw=0 i=0 pad spad line list= danger
  local -a query_opts match_opts; query_opts=(); match_opts=()
  local esc_bind='esc:abort' foot
  if [ "${UI_QUERY+x}" ]; then query_opts=(--print-query --query="$UI_QUERY"); esc_bind=; fi
  cols=$(term_cols)
  for x in "$@"; do case $x in \#*) continue;; esac; x=${x#!}; x=${x%%|*}; [ ${#x} -gt $lw ] && lw=${#x}; done
  [ $lw -gt 34 ] && lw=34; for x in "$@"; do case $x in \#*) [ "$cols" -ge 70 ] && sw=12;; esac; done
  for x in "$@"; do
    case $x in \#*) sec=${x#\#}; continue;; esac
    danger=; case $x in !*) danger=$CR; x=${x#!};; esac
    lab=${x%%|*}; desc=; [ "$x" != "$lab" ] && desc=${x#*|}
    printf -v spad '%*s' $(( sw > ${#sec} ? sw - ${#sec} : 1 )) ''; printf -v pad '%*s' $(( lw + 2 - ${#lab} > 1 ? lw + 2 - ${#lab} : 2 )) ''
    line="$i"$'\t'; [ $sw -gt 0 ] && line="$line$CD$sec$C0$spad"
    line="$line$danger$lab$C0${desc:+$pad$CD$desc$C0}"; list="$list$line"$'\n'
    sec=; i=$((i+1))
  done
  foot=${UI_FOOT:-$(ui_keys "↑↓:$(t key_move)" "enter:$(t key_select)" "esc:${UI_ESC:-$(t key_back)}")}
  if [ -n "$list" ]; then
    foot="$foot$NL$(t menu_search_recovery)"
    match_opts=(--info=right --bind 'ctrl-u:clear-query')
  fi
  printf '%s' "$list" | ui_fzf "$(ui_head ${UI_HEAD:+"$UI_HEAD"} "${header:+$CB$header$C0}")" --delimiter="$TAB" --with-nth=2.. --accept-nth=1 \
    --prompt='› ' --input-label=" $(t menu_filter) " --ghost="$(t menu_ghost)" --highlight-line \
    --footer="$foot" ${esc_bind:+--bind "$esc_bind"} ${query_opts[@]+"${query_opts[@]}"} ${match_opts[@]+"${match_opts[@]}"} \
    ${UI_POS:+--bind "load:pos($UI_POS)"} ${UI_EXPECT:+--expect="$UI_EXPECT"} \
    ${UI_MULTI:+--multi --marker='✓' --bind 'tab:toggle+down' --bind 'enter:transform:[ "$FZF_SELECT_COUNT" = 0 ] && echo "become(true)" || echo accept'} \
    ${UI_SELECT:+--bind "load:$(local r=; for i in $UI_SELECT; do r="$r+pos($i)+select"; done; printf '%s' "${r#+}+first")"}
}
# Compact single-choice list. Values remain zero-based indices, like ui_menu.
ui_choice(){
  local header=$1 x i=0 list= height rows out head rest; shift
  for x in "$@"; do list="$list$i$TAB${x/|/  }$NL"; i=$((i+1)); done
  head=$(ui_head ${UI_HEAD:+"$UI_HEAD"} "${header:+$CB$header$C0}")
  rows=$(term_rows); height=$((i+9)); rest=$head
  while [[ $rest == *"$NL"* ]]; do height=$((height+1)); rest=${rest#*"$NL"}; done
  [ -z "${UI_PREVIEW:-}" ] || [ "$height" -ge 22 ] || height=22
  [ "$height" -le "$rows" ] || height=$rows
  out=$(printf '%s' "$list" | ui_fzf "$head" --height="$height" --no-input \
    --delimiter="$TAB" --with-nth=2.. --accept-nth=1 --highlight-line \
    --footer="${UI_FOOT:-$(ui_keys "↑↓:$(t key_move)" "enter:$(t key_select)" "esc:${UI_ESC:-$(t key_cancel)}")}" \
    --bind 'esc:abort,tab:down,btab:up,left:up,right:down' ${UI_POS:+--bind "load:pos($UI_POS)"} \
    ${UI_PREVIEW:+--preview="$UI_PREVIEW"} ${UI_PREVIEW_WINDOW:+--preview-window="$UI_PREVIEW_WINDOW"}) || return 1
  printf '%s\n' "$out"
}
ui_toggle(){ # header current → on|off; the focused option is shown in a single horizontal row
  local i pos=1 rows height on off base out
  on=$(t controls_on); off=$(t controls_off); [ "$2" != off ] || pos=2
  base=$(ui_head ${UI_HEAD:+"$UI_HEAD"} "$CB$1$C0" "$(t current): $([ "$2" = off ] && printf '%s' "$off" || printf '%s' "$on")")
  rows=$(term_rows); height=13; [ -z "${UI_PREVIEW:-}" ] || height=22
  [ "$height" -le "$rows" ] || height=$rows
  export HB_TOGGLE_BASE="$base" HB_TOGGLE_ON="$on" HB_TOGGLE_OFF="$off"
  out=$(printf '0\t \n1\t \n' | ui_fzf "$base" --height="$height" --no-input --pointer=' ' --no-highlight-line \
    --delimiter="$TAB" --with-nth=2 --accept-nth=1 --bind "load:pos($pos)" \
    --bind 'esc:abort,tab:down,btab:up,left:up,right:down' \
    --bind 'focus:transform-header:case {1} in 0) printf "%s\n[%s]    %s" "$HB_TOGGLE_BASE" "$HB_TOGGLE_ON" "$HB_TOGGLE_OFF";; 1) printf "%s\n %s    [%s]" "$HB_TOGGLE_BASE" "$HB_TOGGLE_ON" "$HB_TOGGLE_OFF";; esac' \
    --footer="${UI_FOOT:-$(ui_keys "←→:$(t key_move)" "enter:$(t key_save)" "esc:$(t key_cancel)")}" \
    ${UI_PREVIEW:+--preview="$UI_PREVIEW"} ${UI_PREVIEW_WINDOW:+--preview-window="$UI_PREVIEW_WINDOW"}) || return 1
  case $out in 0) printf 'on\n';; 1) printf 'off\n';; *) return 1;; esac
}
ui_checklist(){ # header items… → selected indices, possibly empty
  local header=$1 x i=0 list= height rows sel= r out foot; shift
  for x in "$@"; do list="$list$i$TAB$x$NL"; i=$((i+1)); done
  rows=$(term_rows); height=$((i+10)); [ "$height" -le "$rows" ] || height=$rows
  for r in ${UI_SELECT:-}; do sel="$sel+pos($r)+select"; done
  export HB_CHECK_KEYS="${UI_FOOT:-$(ui_keys "↑↓:$(t key_move)" "space:$(t key_mark)" "enter:$(t key_done)" "esc:$(t key_cancel)")}" HB_CHECK_COUNT="$(t controls_selected)" HB_CHECK_ACTION="${UI_CHECK_ACTION:--1}"
  foot='n=$FZF_SELECT_COUNT; if [ "$n" -gt 0 ]; then for x in {+1}; do [ "$x" != "$HB_CHECK_ACTION" ] || n=$((n-1)); done; fi; printf "%s %s\n%s" "$n" "$HB_CHECK_COUNT" "$HB_CHECK_KEYS"'
  out=$(printf '%s' "$list" | ui_fzf "$(ui_head ${UI_HEAD:+"$UI_HEAD"} "${header:+$CB$header$C0}")" --height="$height" --no-input --multi --marker='✓' \
    --delimiter="$TAB" --with-nth=2.. --accept-nth=1 --highlight-line \
    --footer="0 $HB_CHECK_COUNT$NL$HB_CHECK_KEYS" \
    --bind "load:${sel#+}${sel:++}first+transform-footer:$foot" \
    --bind 'esc:abort,tab:down,btab:up,enter:transform:[ "$FZF_SELECT_COUNT" = 0 ] && echo "become(true)" || echo accept' \
    --bind "space:toggle+transform-footer:$foot") || return 1
  [ -z "$out" ] || printf '%s\n' "$out"
}
# ui_confirm [--default=false] [--affirmative X] [--negative Y] [--danger] QUESTION [DETAIL…] → status 0 for yes, 1 for no,
# 2 for esc. A danger confirm has no "y" shortcut, so a stray key can never delete.
ui_confirm(){
  local yes no def=1 danger= q out ybind='y:pos(1)+accept,'
  yes=$(t yes); no=$(t no)
  while [ $# -gt 0 ]; do case $1 in --default=false) def=2;; --default=true) def=1;; --affirmative) yes=$2; shift;; --negative) no=$2; shift;;
    --affirmative=*) yes=${1#*=};; --negative=*) no=${1#*=};; --danger) danger=1;; *) break;; esac; shift; done
  q=$1; shift
  [ -n "$danger" ] && { yes="$CR$yes$C0"; ybind=; }
  out=$(printf '1\t%s\n0\t%s\n' "$yes" "$no" | UI_TONE=${danger:+danger} ui_fzf "$(ui_head ${UI_HEAD:+"$UI_HEAD"} "$CB$q$C0" "$@")" --no-input --delimiter="$TAB" --with-nth=2 \
    --accept-nth=1 --highlight-line --bind "load:pos($def)" --bind "${ybind}n:pos(2)+accept,esc:abort" \
    --footer="$(ui_keys "↑↓:$(t key_move)" "enter:$(t key_confirm)" "esc:${UI_ESC:-$(t key_cancel)}")")
  case $out in 1) return 0;; 0) return 1;; *) return 2;; esac
}

dimw(){ dim "$1" | ui_wrap "$(( $(term_cols) - 2 ))"; }   # a dim text line folded to the terminal width
term_rows(){ local r; r=$( { stty size </dev/tty; } 2>/dev/null | cut -d' ' -f1); echo "${r:-24}"; }

hsize(){ local b=$1; if [ "$b" -lt 1024 ]; then printf '%s B' "$b"; elif [ "$b" -lt 1048576 ]; then printf '%s.%s K' $((b/1024)) $((b%1024*10/1024))
  elif [ "$b" -lt 1073741824 ]; then printf '%s.%s M' $((b/1048576)) $((b%1048576*10/1048576)); else printf '%s.%s G' $((b/1073741824)) $((b%1073741824*10/1073741824)); fi; }
# File picker: one ui_menu per directory. Folders first, dot files hidden, sizes right-aligned; → opens a folder, ← goes up,
# enter opens a folder or picks a file. Prints the file (as ~/… under the home directory); esc → status 1.
file_pick(){ # start-dir header [enter-label] [dotfiles] [session-dir] [file-pattern]
  local dir=${1:-$HOME} head=$2 enter=${3:-$(t key_pick)} dots=${4:-} session=${5:-} base=$UI_AT H=${HOME%/} pattern=${6:-*}
  local lines kind size name items names isdir out rc key i lab shown h2 ls_flags query= path parent error= UI_AT=$UI_AT
  if [ -n "$session" ] && [ -f "$session/dir" ]; then IFS= read -r dir < "$session/dir" || :; fi
  if [ -n "$session" ] && [ -f "$session/query" ]; then IFS= read -r query < "$session/query" || :; fi
  H=$(CDPATH= cd "$HOME" 2>/dev/null && pwd -P) || return 1
  [ -d "$dir" ] || dir=$H
  dir=$(CDPATH= cd "$dir" 2>/dev/null && pwd -P) || return 1; dir=${dir%/}
  while :; do
    shown=$dir; case $dir in "$H"|"$H"/*) shown="~${dir#"$H"}";; esac
    shown=$(printf '%s' "$shown" | LC_ALL=C tr -d '\000-\037\177'); UI_AT="$base|${shown:-/}"
    items=(); names=(); isdir=(); ls_flags=-lnL; [ -z "$dots" ] || ls_flags=-lnLA
    lines=$(LC_ALL=C ls $ls_flags "$dir/" 2>/dev/null | awk '{ k = substr($1, 1, 1); if (k != "d" && k != "-") next; n = $0; for (i = 1; i <= 8; i++) sub(/^[ \t]*[^ \t]+/, "", n); sub(/^ /, "", n); printf "%s\t%s\t%s\n", k, $5, n }' | sort -t "$TAB" -k1,1r -k3,3f)
    while IFS="$TAB" read -r kind size name; do
      [ -n "$name" ] || continue
      if [ "$kind" != d ]; then case $name in $pattern) ;; *) continue;; esac; fi
      lab=$(printf '%s' "$name" | LC_ALL=C tr -d '\000-\037\177'); lab=${lab//|/¦}; case $lab in \#*|\!*) lab=" $lab";; esac
      if [ "$kind" = d ]; then items+=("$lab/"); isdir+=(1); else items+=("$lab|$(printf '%8s' "$(hsize "${size:-0}")")"); isdir+=(0); fi
      names+=("$name")
    done <<< "$lines"
    h2=$head; [ ${#items[@]} -gt 0 ] || h2="$head$NL$CD$(t file_empty)$C0"
    [ -z "$error" ] || h2="$h2$NL$CR$error$C0"; error=
    out=$(UI_QUERY="$query" UI_EXPECT=left,right,ctrl-o,ctrl-l,esc UI_FOOT="$(ui_keys "→:$(t key_open)" "←:$(t key_up)" "enter:$enter")$NL$(ui_keys "ctrl-o:$(t key_dots)" "ctrl-l:$(t file_path_key)" "esc:$(t key_cancel)")" \
      ui_menu "$h2" ${items[@]+"${items[@]}"}); rc=$?
    [ $rc -le 1 ] || return 1
    query=${out%%$NL*}; out=${out#*$NL}; key=${out%%$NL*}; i=${out#*$NL}
    if [ -n "$session" ]; then printf '%s\n' "${dir:-/}" > "$session/dir"; printf '%s\n' "$query" > "$session/query"; fi
    case $key in
      esc) return 1;;
      left) dir=${dir%/*}; query=; continue;;
      ctrl-o) if [ -n "$dots" ]; then dots=; else dots=1; fi; continue;;
      ctrl-l)
        path=$(ui_field "$(t file_path_label)" '' any) || continue
        [ -n "$path" ] || { error=$(t file_path_invalid); continue; }
        case $path in '~') path=$HOME;; '~/'*) path=$HOME/${path#\~/};; esac
        case $path in /*) ;; *) path=${dir:-/}/$path;; esac
        if [ -d "$path" ] && [ -r "$path" ] && [ -x "$path" ]; then
          dir=$(CDPATH= cd "$path" 2>/dev/null && pwd -P) || { error=$(t file_path_invalid); continue; }
          dir=${dir%/}; query=; continue
        fi
        if [ -f "$path" ] && [ -r "$path" ]; then
          parent=$(CDPATH= cd "$(dirname "$path")" 2>/dev/null && pwd -P) || { error=$(t file_path_invalid); continue; }
          path=${parent%/}/${path##*/}; case $path in "$H"/*) path="~${path#"$H"}";; esac
          printf '%s\n' "$path"; return 0
        fi
        error=$(t file_path_invalid); continue;;
    esac
    case $i in ''|*[!0-9]*) continue;; esac
    [ "$i" -lt "${#names[@]}" ] || continue
    if [ "${isdir[$i]}" = 1 ]; then dir=$dir/${names[$i]}; query=; continue; fi
    [ "$key" = right ] && continue
    [ -r "$dir/${names[$i]}" ] || { error=$(t file_path_invalid); continue; }
    case $dir in "$H"|"$H"/*) dir="~${dir#"$H"}";; esac; printf '%s/%s\n' "$dir" "${names[$i]}"; return 0
  done
}
pick_key_file(){ # → print a private-key path (as ~/… under the home directory)
  local start=$HOME; [ -d "$HOME/.ssh" ] && start=$HOME/.ssh
  file_pick "$start" "$(t pick_key_head)" "" 1   # keys are often in dot folders: show hidden files from the start
}
ui_done(){ # label value [source] → "✓ Label  value  ← source": a finished step of a multi-step flow
  printf '%s%s%s %s%*s %s%s\n' "$CG" "$ICON_OK" "$C0" "$1" $(( ${#1} < 10 ? 10 - ${#1} : 1 )) '' "$2" "${3:+  ${CD}← $3$C0}"; }
