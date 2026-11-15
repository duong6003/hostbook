# Interface settings; preview rendering remains in hostbook for _demo callbacks.
setting_options(){
  local k
  case $1 in
    preset) for k in cards compact minimal dracula nord light high-contrast; do printf '%s\t%s\n' "$k" "$(t "set_o_preset_${k//-/_}")"; done;;
    list) for k in cards compact minimal; do printf '%s\t%s\n' "$k" "$(t "set_o_list_$k")"; done;;
    border) printf '%s\n' rounded sharp bold double none;;
    preview) for k in right bottom hidden; do printf '%s\t%s\n' "$k" "$(t "set_o_preview_$k")"; done;;
    separator) printf 'on\t%s\noff\t%s\n' "$(t yes)" "$(t no)";;
    icons) for k in unicode ascii nerd; do printf '%s\t%s\n' "$k" "$(t "set_o_icons_$k")"; done;;
    theme) theme_names; local f n; for f in "$CONF"/themes/*.theme; do [ -f "$f" ] || continue; n=$(basename "$f" .theme); valid_theme_name "$n" && { theme_builtin "$n" >/dev/null || echo "$n"; }; done;;
  esac
}
setting_pick(){ # key [heading] [row number to start on]; its own fzf because the preview is the live _demo
  local key=$1 heading=${2:-} selected=${3:-} out
  [ -n "$heading" ] || heading=$(t "set_k_$key")
  at "$(t settings_title)|$(t "set_k_$key")"
  if [ -z "$selected" ] && [ "$key" != preset ]; then
    selected=$(setting_options "$key" | awk -F'\t' -v v="$(cfg_raw "$key")" '$1==v {print NR; exit}')
  fi
  # The picker always uses single-line rows; --read0 is only for the server card list.
  out=$(setting_options "$key" | awk -F'\t' '{if(NF==1) print $0 "\t" $0; else print}' |
    ui_fzf "$(ui_head "$CB$heading$C0")" --delimiter="$TAB" --with-nth=2 --accept-nth=1 --highlight-line \
      --prompt='› ' --input-label=" $(t menu_filter) " --ghost="$(t menu_ghost)" \
      --footer="$(ui_keys "↑↓:$(t key_move)" "enter:$(t key_save)" "esc:$(t key_cancel)")" --bind 'esc:abort' \
      --preview "$(self_cmd) _demo '$key='{1}" --preview-window='right,65%,wrap,<80(down,55%,wrap)' \
      ${selected:+--bind="load:pos($selected)"}) || return 1
  if [ "$key" = preset ]; then apply_preset "$out"; else cfg_set "$key" "$out"; fi
  apply_theme
}
set_language(){ # [breadcrumb] Language / Ngôn ngữ: save lang, then reload the texts so the next screen is already in it
  local i l; at "${1:-$(t settings_title)|$(t set_language)}"
  i=$(UI_POS=$([ "$UI_LANG" = vi ] && echo 1 || echo 2) ui_menu "$(t set_lang_head)" "$(t lang_vi)|$([ "$UI_LANG" = vi ] && t current)" "$(t lang_en)|$([ "$UI_LANG" = en ] && t current)") || return 1
  case $i in 0) l=vi;; *) l=en;; esac
  cfg_set lang "$l" || return 1
  HOSTBOOK_LANG=; lang_load   # a one-run HOSTBOOK_LANG would hide the choice just made, here and in child processes
}
custom_theme(){
  local n="" f current d e=""
  at "$(t settings_title)|$(t set_custom)"
  d=$(tmpd) || return 1; theme_names > "$d/taken"
  while :; do
    n=$(UI_TAKEN=err_theme_taken ui_field "$(t theme_label)" "$n" "name:$d/taken" "$(t theme_rule)" "" "$e") || return 1
    valid_theme_name "$n" && break; e="$CR$ICON_ERR $(t err_theme_name)$C0"
  done
  f=$CONF/themes/$n.theme; mkdir -p "$CONF/themes"
  if [ -f "$f" ]; then ui_confirm --default=false --affirmative "$(t theme_edit_yes)" --negative "$(t cancel)" "$(t theme_exists_q "$n")" || return 1
  else current=$(cfg theme hostbook)
    { printf '# Colors: 0–255, #rrggbb, or ANSI names. base: dark, light, 16.\n# accent: emphasis; title: headings; dim: secondary text; ok/warn/err: status.\n# border: borders; sel_bg: selection background; chip_fg/chips: project chip foreground/backgrounds.\n'
      theme_builtin "$current" || cat "$CONF/themes/$current.theme"; } > "$f"
  fi
  while :; do
    ${EDITOR:-vi} "$f"
    theme_check "$f" && break
    ui_confirm --affirmative "$(t theme_fix_yes)" --negative "$(t cancel)" "$(t theme_bad_q)" || return 1
  done
  "$SELF" _demo "theme=$n"
  confirm "$(t theme_use_q)" && cfg_set theme "$n"
  apply_theme
}
settings(){
  local i k
  while :; do
    at "$(t settings_title)"
    i=$(ui_menu "$(t settings_head)" \
      "#$(t sec_display)" "$(t set_k_preset)|$(t set_h_preset)" "$(t set_k_list)|$(cfg list cards)" "$(t set_k_border)|$(cfg border rounded)" \
      "$(t set_k_preview)|$(cfg preview right)" "$(t set_k_separator)|$(cfg separator on)" "$(t set_k_icons)|$(cfg icons unicode)" "$(t set_k_theme)|$(cfg theme hostbook)" \
      "#$(t sec_lang)" "$(t set_language)|$(t "lang_$UI_LANG")" \
      "#$(t sec_advanced)" "$(t set_custom)|$(t set_custom_hint)" "#$(t sec_reset)" "!$(t set_reset)|$(t set_reset_hint)") || return 0
    case $i in
      0) k=preset;; 1) k=list;; 2) k=border;; 3) k=preview;; 4) k=separator;; 5) k=icons;; 6) k=theme;;
      7) set_language || :; continue;;
      8) custom_theme; continue;;
      9) ui_confirm --default=false "$(t set_reset_q)" && apply_preset cards; apply_theme; continue;;
    esac
    setting_pick "$k" || :
  done
}
