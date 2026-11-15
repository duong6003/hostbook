# Package import and vault screens implementation plan

> Execute inline in the existing feature/ui-modules worktree; keep main untouched.

**Goal:** Import exported Hostbook packages through a clear file flow and show saved vault credentials directly in Secrets.

**Architecture:** Reuse the existing package validator, transactional apply/review and shared file/choice controls. Keep first-run SSH configuration migration as a separate internal entry point. No new dependencies or hot callbacks.

- [x] Restrict public import to validated Hostbook packages; replace source selection with a package picker that shows directories and .hbshare files, supports literal paths, and retries invalid files. Keep initial SSH migration internal. Update source, CLI and TUI fixtures.
- [x] Ensure sharing always writes an encrypted .hbshare file, including small packages. Test an actual export/decrypt/import round trip, wrong password, damaged package, cancellation and duplicates using isolated data.
- [x] Show saved Secrets immediately with Add to vault; put import/create actions in a compact submenu. Preserve migration, vault settings, generated public-key guidance and cancellation. Cover menu mapping and successful storage feedback.
- [x] Run import/input/key/review/onboarding and screen/TUI checks as appropriate, update English docs and isolated preview. Record limits and commit locally.

Files: lib/import.sh (public import and internal migration), lib/ui.sh (package filter), hostbook (Secrets/export/onboarding), lang/{vi,en}.sh (copy), tests/{import-input,import,onboarding,tui,file-input}.sh and package tests (behavior), docs/design/screens.md and wiki guides (user contract).
