# Screen system

Every hostbook screen besides the search list is built from the same frame, the same pieces and the same words.
This page is the contract: new or changed screens follow it, and `tests/screens.sh` checks the rules at the end.
The interactive prototype that this page was approved from lives outside the repo; this page is the source of truth.

## Layout: the full-screen frame

A screen is one fzf window (`ui_fzf`), drawn in the same boxes as the search screen. Compact choices, toggles and checklists use the same frame at a height capped by the terminal:

```
╭ hostbook › shop-api › Rename ────────────────────────────╮   header box: breadcrumb label (S1)
│ shop-api  ● PROD                                         │   context card (ui_card)
│ deploy@10.20.1.11 · project shop · login key-shop        │
│ API payments · groups shop-prod                          │
╰──────────────────────────────────────────────────────────╯
╭──────────────────────── New name ────────────────────────╮   input box: what the user types or filters
│ › shop-pay                                               │
╰──────────────────────────────────────────────────────────╯
╭─────────────────────── Will change ──────────────────────╮   list box: choices, or a preview of the change
│ shop-api → shop-pay                                      │
╰──────────────────────────────────────────────────────────╯
╭──────────────────────────────────────────────────────────╮   footer box: rule or error, then key hints (S2)
│ Letters, digits and . _ -                                │
│ enter save   esc cancel                                  │
╰──────────────────────────────────────────────────────────╯
```

The frame follows the user's `border` setting (`none` gives fzf's minimal style) and theme. Screens that only show
text (reports, progress) print a breadcrumb line and a rule with `title`, then their lines.

**Inline mode.** A flow that still prints text and then calls `pick`, `ask` or `confirm` gets an inline frame
(`UI_INLINE=1`, adaptive height) below that text, so nothing it printed is hidden. Migrating a flow means moving that
text into the screen's header lines and calling the `ui_*` helper directly, which gives the full-screen frame.

## Screen types

| Type | Helper | Use for | Must have |
| --- | --- | --- | --- |
| Action menu | `ui_menu` | → on a server or group, Tools, Secrets | sections (`#Name`), descriptions, destructive items last (`!Label`) |
| Compact choice | `ui_choice` | short choices and opening modes | no filter, visible current choice and explicit apply/cancel |
| Toggle | `ui_toggle` | binary settings | horizontal on/off, current value and explicit apply/cancel |
| Checklist | `ui_checklist` | tags | Space toggles, selected count and empty selection accepted |
| Single field | `ui_field` | rename, port, path, group name, secret name | rule shown before typing, inline error, preview of the change |
| Multiline input | `ui_multiline` | paste or edit multiline text | native gum editor, breadcrumb and localized key hints; Ctrl-C cancels (Escape also cancels on gum 2) |
| Multi-step | `ui_field` per step | add server, connect sync hub, vault setup | `step n/m` in the breadcrumb, finished steps as `✓` lines |
| Review | `ui_menu` for add; `ui_fzf` for import | last step of add/import | import review scrolls through saved directives, counts, skipped reasons and key actions; absent fields say SSH config, whose active settings can override saved values; Enter accepts, Escape cancels |
| Confirm | `ui_confirm` | move project, undo, group open mode | the object's name, what changes, a primary button that names the action |
| Danger | `ui_confirm --danger` or `UI_TONE=danger ui_field … equals:<name>` | delete server/group/secret, replace key | error-coloured frame, what is removed and what is kept, how to undo; PROD needs the name typed |
| Progress | `title` + lines | check connections, find the sync hub, sync | elapsed / limit, a way to cancel |
| Result | `ui_menu` | after check, share, import (only when there is a next step) | import bounds its host summary to keep actions visible; View opens the complete imported list and Undo is available only for the current import commit |
| Report | `title` + lines | doctor, help | grouped sections, `→ fix` on every problem line |
| File picker | `ui_menu` per directory | send a file, key from file, import package | starts in `~` (Downloads for import), folders first, dot files hidden (Ctrl-O toggles), sizes right-aligned |

Multiline input is a native `gum write` screen: gum does not expose a live outer-border or footer option. It uses the same breadcrumb, theme roles and key-hint words, with the hints above the editor. Ctrl-J inserts a newline and Enter submits in the supported gum versions.

Searchable menus with choices show the live match count and a persistent localized Ctrl-U clear-search hint. Ctrl-U clears the entire query; empty file-picker directories retain their distinct empty-folder message.

Public import opens a package picker directly, with format instructions, folders and `.hbshare` files. It starts in Downloads when available; literal paths remain available via Ctrl-L. Filename filtering is guidance only: package contents and version must validate before review/apply. No paste/clipboard source menu or automatic clipboard offer is shown. First-run SSH migration is a separate internal flow. Export always saves an encrypted package file, even when clipboard copying succeeds.

Secrets opens directly on saved vault entries and an inline “Add to vault…” action, with a compact submenu for file/paste/generate/password sources. Existing migration and vault maintenance actions remain available. Canceling a source returns to the vault list; successful storage is confirmed.

Server login selection opens directly on credentials saved in the vault, with an inline “Add to vault…” action. A vault with no matching credentials explains the empty state. File import, clipboard import, key generation and password creation belong to the compact add submenu; key-only import flows exclude passwords and external SSH login. Canceling the submenu or file picker returns to the vault list. Successful creation/import selects the returned vault secret name, including fingerprint deduplication; it never returns the source file path as a managed credential. External SSH remains an explicit separate option for hardware keys and independently managed agents.

First-run setup asks the machine's role before data setup. Each compact choice has at most two actions and explains their purpose above the options: connect/manage servers versus host a sync repository, then create a new server list versus retrieve existing synced data. Do not mix roles and data sources in one menu. Vault setup explicitly distinguishes its encryption password from server login credentials. Language and appearance keep their compact controls, passwords keep their input forms, and optional actions keep their confirmations. Machine destinations use compact choices for up to five options and search for larger lists; hub setup actions are also compact. There is no overall wizard progress indicator. Compact-choice height includes wrapped context lines so the explanation does not hide actions.

When a screen only reports that something finished, it does not stay open: it returns to the search screen and calls
`flash` (shown in the header there).

The home search distinguishes an empty host list from a query with no matches. An empty list points to Add and Tools/Import. A no-match message explains that Escape clears the query, keeps the normal shortcut footer visible, and says when selections remain. Escape clears query, then selection, then an imported-only view.

## Pieces

| Piece | Function | Notes |
| --- | --- | --- |
| Where am I | `at "server|Rename" server` | sets the breadcrumb (`UI_AT`) and the server for the context card (`UI_HOST`); call once per flow |
| Context card | `ui_card [server]` | name and PROD badge; user@ip · project · login; description · groups |
| Header lines | `ui_head line…` | card, a blank line, then the screen's own lines |
| Key hints | `ui_keys "enter:$(t key_save)" "esc:$(t key_cancel)"` | every screen offers esc (or ctrl-c while running) |
| Field checks | `ui_field LABEL VALUE CHECK [RULE] [PREVIEW] [INTRO]` | CHECK: `name:<taken-file>`, `port`, `project`, `text`, `equals:<word>`, `any`; runs inside fzf's shell with builtins only, so typing never boots hostbook |
| Tone | `UI_TONE=danger`, `UI_ENTER=<verb>` | prefix a single call; never set globally |
| Menu options | `UI_HEAD`, `UI_POS`, `UI_EXPECT`, `UI_FOOT` | prefix a `ui_menu`/`ui_confirm` call: lines above the question (finished steps), row to start on, extra ending keys (`←` `→` in the file picker), replacement key hints |
| Compact controls | `ui_choice`, `ui_toggle`, `ui_checklist` | no search input; `UI_POS`, `UI_HEAD`, `UI_FOOT` and preview options carry context |
| Step line | `ui_done label value [source]` | `✓ Label  value  ← source` for a finished step of a multi-step flow |
| File picker | `file_pick start-dir header [enter-label] [dotfiles] [session-dir] [file-pattern]` | `ui_menu` per directory; Ctrl-L enters a literal path, optional session stores folder and query; an optional filename pattern filters files, preserving directories |

## Colour roles

Colours come from the theme (`TH_*`, escapes `CP CU CD CG CY CR CBORDER`), never from literal codes.

| Role | Variable | Used for |
| --- | --- | --- |
| Accent | `CP` | keys, the `›` prompt, the selected item, a new value |
| Title | `CU` | the last breadcrumb part |
| Dim | `CD` | descriptions, rules, hints, sources |
| OK / warning / error | `CG` / `CY` / `CR` | `✓`, `!`, `✗`; error also for PROD and danger frames |
| Border | `CBORDER` | frames and rules |

## Language

All user-facing text comes from `lang/<code>.sh` through `t key [args…]` (or `tv var key [args…]` in hot paths).

- Every key exists in both `lang/vi.sh` and `lang/en.sh` (S11); only the selected language file is loaded.
- Keys are `snake_case` grouped by prefix: `key_` (key hints), `err_` (input errors), `act_` (menu items),
  `sec_` (menu sections), `done_` (flash messages), then one prefix per flow (`rename_`, `delete_`, …).
- A key that takes values is a printf format (`L_done_renamed='Renamed %s → %s'`); escape a literal `%` as `%%`.
- The language is `lang = vi|en` in the config, `HOSTBOOK_LANG` for one run, otherwise the locale (`vi*` → vi, else en).
  Installs from before languages were added are pinned to `vi`.
- Tests run with `HOSTBOOK_LANG=vi` so their Vietnamese assertions stay valid.
- Text from git, ssh and other tools is shown as it comes; it is not translated.

## Copy

- Menu items and buttons start with a verb: `Rename`, `Delete server`, never `Name`.
- Errors say what is wrong and how to fix it, in one line: `✗ Contains a space · use letters, digits and . _ - only`.
- Suggestions say where they came from: `Suggested shop- because 10.20.1.x matches shop-api`.
- Examples are labelled `e.g.` and live under the field; a password field never shows an example.
- Paths start with `~` when they are under the home directory.
- No git words on screen (`commit`, `push`, `HEAD`): say `3 changes not synced yet`.
- Flash messages use the past tense: `✓ Renamed shop-api → shop-pay`.

## Rules the tests check

`tests/screens.sh` opens each screen in a private tmux server at 60, 100 and 200 columns and checks:

| Rule | What |
| --- | --- |
| S1 | The first line carries the breadcrumb `hostbook ›` |
| S2 | A key-hint footer in the UI language with `esc` (or `ctrl-c`); no gum English (`navigate`, `submit`, `toggle`) |
| S3 | No line is wider than the terminal |
| S4 | A field shows its rule before anything is typed |
| S5 | A wrong value shows one error line in place and keeps the typed text |
| S6 | Destructive menu items are in the error colour and last |
| S7 | No absolute home paths (`/Users/…`, `/home/…`) |
| S8 | Deleting a PROD server needs the name typed; enter on an empty field deletes nothing |
| S9 | Progress screens show elapsed time and a way to cancel |
| S10 | No git words in screen text |
| S11 | Every key in `lang/vi.sh` exists in `lang/en.sh` and the other way round; no `‹L_` on any screen |
