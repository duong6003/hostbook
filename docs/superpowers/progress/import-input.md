# Import input helper progress

Base: `9598b22`, branch `feature/import-input`. Scope: shared `file_pick`, `ui_multiline`, language and isolated helper tests; importer-owned classification/results untouched.

Ruling: `file_pick START HEADER [ENTER] [DOTFILES] [SESSION_DIR]` retains existing output/status behavior; optional session files `dir` and `query` contain literal state. Ctrl-L takes a literal file or folder path, resolving relative names from the browsed directory. The picker uses its existing menu and numeric identifiers; display labels remove control characters. Session files are private staging owned by the caller.

Ruling: `ui_multiline LABEL VALUE` uses native `gum write`, with themed breadcrumb and localized key hints above the editor. Gum has no live outer-border/footer option, so this screen is explicitly documented as an exception. Enter submits, Ctrl-J adds a line and Ctrl-C cancels. The caller retains the previously supplied VALUE on cancellation; gum returns no canceled edits. Ctrl-C returns to the surrounding shell. Escape also cancels on gum 2 but is ignored by gum 0.14, so it is not promised. No editor is invoked implicitly.

Ruling: official SHA256-verified gum 0.14.0 and 0.14.5 and installed 2.0.2 all use Ctrl-J / Enter, contrary to the original plan's major-version assumption; no version test or new dependency is needed. Captures at 44, 60 and 100 columns are in `import-input-captures/`.

Checks: `tests/file-input.sh` passed using verified fzf 0.63 at 44, 60, and 100 columns with gum 0.14.0, 0.14.5, and 2.0.2 respectively. Real private-tmux checks cover literal `$()`/apostrophe/Unicode paths, query restoration including cancellation, missing/unreadable path, relative folder selection, hidden toggle, original key-picker caller, multiline newline/submit/ctrl-c, and no implicit editor. Gum 2 Escape passes; gum 0.14 Escape is an explicit skip. `bash tests/test.sh` (150 checks), `bash tests/ui.sh` (111 checks), `bash tests/tui.sh`, and `bash tests/screens.sh` passed. `bash -n lib/ui.sh tests/file-input.sh` and `git diff --check` passed. Actual Windows path conversion and Windows terminal behavior require CI; no Windows claim is made.
