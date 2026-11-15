# Compact controls progress

Base: a07d017 (`feature/compact-controls` worktree). Tasks 3 and 4 only; direct-edit form remains independent.

- [x] Mechanically extract settings UI; keep preset/demo rendering in core and skip settings module on internal callbacks. Verified `tests/settings-module.sh`, `tests/ui.sh` (111 checks) and `tests/platform.sh` (21 checks).
- [x] Implement compact choice, toggle, checklist, settings and tags. Private tmux checks pass against verified fzf 0.63 at 44/60/100; screen suite and 0.63 TUI suite pass. Performance verification remains pending.
- [x] Implement opening-mode diagram and split fallback. Private tmux at 44/60/100 and stubbed mode, single-host, saved-group, cancellation and PROD guard checks pass.
- [ ] Verify real terminals, supported fzf floor and performance; reconcile screen documentation.

Pre-flight: Task 4 consumes Task 3's `ui_choice` and preview interface. Tasks 1–2 are not prerequisites for these controls. Final integration must reconcile import-branch changes and re-run its suites.

Ruling: the installed minimum fzf 0.63 lacks `--gutter` and the `multi` event used by the shared frame/home. Remove the option and update the footer from the Tab action, preserving the existing selection restriction. Risk: default gutter rendering can differ between versions; compare captures.
