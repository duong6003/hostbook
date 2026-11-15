# Compact controls progress

Base: a07d017 (`feature/compact-controls` worktree). Tasks 3 and 4 only; direct-edit form remains independent.

- [x] Mechanically extract settings UI; keep preset/demo rendering in core and skip settings module on internal callbacks. Verified `tests/settings-module.sh`, `tests/ui.sh` (111 checks) and `tests/platform.sh` (21 checks).
- [ ] Test and implement compact choice, toggle, checklist and setting/choice integrations.
- [ ] Test and implement opening-mode diagram and split fallback.
- [ ] Verify real terminals, supported fzf floor and performance; reconcile screen documentation.

Pre-flight: Task 4 consumes Task 3's `ui_choice` and preview interface. Tasks 1–2 are not prerequisites for these controls. Final integration must reconcile import-branch changes and re-run its suites.
