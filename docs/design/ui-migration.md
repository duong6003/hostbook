# Delivery checklist: UI, modules and CI

Updated 2026-11-15. Prototype v3 is a standalone set of synthetic interaction
samples. Launch `bash hostbook-components.command`. It does not source either
app checkout. Historical comparisons and the v2 checklist are in `archive/v2/`.

This is the current delivery checklist, including the subsequent module and CI
decisions. The filename is retained so existing links keep working. Prototype
completion does not mean the corresponding production change is implemented.

## Status and execution order

- [x] Clean the prototype to the remaining UI proposals and synthetic fixtures.
- [x] Review main ef847ee and verify its existing screen suite on macOS.
- [x] Measure recent CI runs: Windows 237–320 s; latest install about 100 s,
  update 31 s and real SSH 64 s. These are observed timings, not savings estimates.
- [ ] **1. CI visibility and efficiency:** expose meaningful step timings, avoid
  duplicate branch-push/PR runs and cancel superseded runs. Keep main push checks
  and required PR checks working.
- [ ] **2. Shared UI and import module extraction:** move existing behavior into
  modules in an isolated worktree. Update packaging, callback loading and tests.
- [ ] **3. Import UX first:** file selection, preflight, duplicate/key handling,
  bounded results and returning to results after canceling undo.
- [ ] **4. Remaining UI:** multi-field input, compact choices/toggles, checklist,
  opening modes and empty/long-data states. Extract related modules as touched.
- [ ] **5. Coverage parity and release verification:** fill important platform
  gaps, verify installers/update/uninstall and run the appropriate platform suites.

Keep CI, mechanical extraction and UI behavior changes in separate commits.
CI optimization is useful groundwork; it does not require completing all
cross-platform coverage additions before beginning UI work.

## Module boundaries and agent guidance

Use these initial responsibilities; avoid extracting unrelated code merely to
make the entry file shorter:

| File | Responsibility |
| --- | --- |
| `hostbook` | Bootstrap, lightweight internal callbacks and command dispatch |
| `lib/ui.sh` | Shared frame, menu, field, confirmation and selection helpers |
| `lib/import.sh` | Import orchestration, preflight and result flow |
| `lib/hosts.sh` | Add/manage hosts; extract when changing those flows |
| `lib/settings.sh` | App settings and appearance; extract when changing these flows |
| `lang/vi.sh`, `lang/en.sh` | Existing runtime translations |

- [ ] Inventory callers and shared variables before each move. Preserve return
  codes, quoting, validation and error handling; do not mix refactoring with UI changes.
- [ ] Resolve module paths from the installed app location, not the working
  directory. Verify that installation and update on macOS and Linux include all modules.
- [ ] Avoid eagerly sourcing every feature on hot callbacks. Measure before/after
  against AGENTS.md budgets; retain Bash 3.2 compatibility and lifecycle cleanup.
- [ ] Update repository **`AGENTS.md`** with the actual module map, where to add
  features, dependency/loading rules, UI/i18n rules and checks for each change.
  Update Development documentation and any single-file test fixtures as needed.
- [ ] Update `docs/design/screens.md` alongside adopted UI differences. Preserve
  the current safety rules and useful existing behavior.

## CI scope and cache policy

- [ ] Cache downloaded fzf, gum and age archives using OS,
  architecture, dependency version and integrity identity in cache keys. Validate
  SHA256 on reuse; do not accept an archive solely because a cache hit occurred.
- [ ] Keep the install directory fresh for install lifecycle tests. Reuse downloads
  for update tests, while still exercising extraction/install/update/uninstall.
- [ ] Do not cache host/vault data, SSH keys, credentials, agent state or test results.
- [ ] Measure cache restore/save overhead and cold/warm timings. Consider macOS
  Homebrew downloads or Linux dependencies only if measurements justify them.
- [ ] Create a coverage matrix for shared logic, UI, install lifecycle, real SSH
  key/password login and platform-specific boundaries. Mark gaps explicitly.
- [ ] Add missing coverage with platform-appropriate mechanisms. Preserve SSH,
  path and installer lifecycle checks.
- [ ] Verify event/concurrency rules do not suppress required merge checks or
  cancel an unrelated branch's runs. Report results for at least cold and warm runs.

Cache saves repeated network transfer; it does not remove extraction or real SSH
test time. Comparable coverage may increase macOS/Linux time. No speedup target
has been measured or promised yet.

## Keep the implementation already on main

Reuse `ui_fzf`, `ui_menu`, `ui_field`, `ui_confirm`, `ui_head`, `ui_card`, `ui_keys`
and `file_pick`. Keep grouped tools, breadcrumbs, inline validation, language
selection, theme previews, progress/cancellation, PROD protection and contextual
undo with its HEAD-change guard. Do not port the old vault branch UI wholesale.

The classic host search remains the reference. Existing editor/password flows,
backend behavior and terminal-background support are outside this prototype.

## Remaining UI changes

| Sample | App target | Remaining change |
| --- | --- | --- |
| Multi-field input | `add_host` and review | Show/edit fields together, retain values when returning, wrap/scroll long review values. Reuse current validation, suggestions and save behavior. The five-field sample is a layout study: retain user, tags, jump host and description in the real flow. |
| Compact toggle | Binary `setting_pick` options | No search; show current value and explicit apply/cancel. Keep the app's language setting and useful theme previews. |
| Checklist | `pick_tags` | Space toggles, visible selected count, compact frame and cancel without applying. Keep custom tags and existing data. |
| Opening modes | `open_many` | Useful layout diagram, compact choice and Split fallback when no preference exists. Preserve saved modes, single-server shortcut and PROD broadcast confirmation. |
| Import preflight | `import_flow`, package review | Show source, new/skipped records and retained values before applying. Port needed staging/duplicate/key behavior from the vault branch separately, through main's helpers. The fixture does not implement that backend. |
| Import result, 2/30 hosts | `import_done` | Keep existing counts/actions; reserve viewport space for actions and footer, report remaining hosts, provide full-list access and separate Undo. Preserve main's real contextual undo, including updates/removals/merge behavior. Cancel should return to the result. |
| Empty search | Shared searchable lists | Show no-match recovery, distinguish no data from no matches and avoid expensive callbacks. Keep each screen's existing shortcuts. |

The short confirmation used inside the synthetic import demo is support for that
flow, not a proposal to replace main's already implemented undo screen.

## Integration details still needed, without a new demo

- Compact choices should extend existing `ui_*` helpers. Main's full-screen boxes
  and multi-step form contract differ from these approved samples; update
  `docs/design/screens.md` and tests with the adopted design rather than adding
  per-screen exceptions.
- File picker: reuse main's `file_pick`; selectively bring direct path entry and
  import navigation/query restoration from the old branch. Main uses Ctrl-O for
  hidden files, whereas the branch used it for Documents. Reconcile shortcuts.
- All production UI strings go into both `lang/vi.sh` and `lang/en.sh` via `t`/`tv`.
  Prototype Vietnamese copy and fixed sample palette are not an i18n/theme layer.
- Preserve no-network hot paths, action return codes and cleanup. No Python
  dependency or separate UI framework is chosen by this prototype.

## Validation

Main baseline: `bash tests/screens.sh` passed on macOS at ef847ee, including the
suite's Vietnamese/English, width/border variants and process cleanup checks.
This is not a claim of prototype parity.

For implementation: run the existing screen suite plus targeted checks for
44/60/100-column, 24-row layouts; empty/long values; 8/30-host results; draft and
cancel behavior; real import/undo in isolated data; and theme behavior on macOS and Linux.

The checklist above is pending production work. Prototype v3 fixture import is simulated and resets on reopening. Its check
button never connects to a server. Session-mode demos use disposable local mock
terminals. No app integration, merge or push is part of this cleanup.
