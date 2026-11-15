# Compact controls progress

Base: a07d017 (`feature/compact-controls` worktree). Tasks 3 and 4 only; direct-edit form remains independent.

- [x] Mechanically extract settings UI; keep preset/demo rendering in core and skip settings module on internal callbacks. Verified `tests/settings-module.sh`, `tests/ui.sh` (111 checks) and `tests/platform.sh` (21 checks).
- [x] Implement compact choice, toggle, checklist, settings and tags. Private tmux checks pass against verified fzf 0.63 at 44/60/100; screen suite and 0.63 TUI suite pass. Performance verification remains pending.
- [x] Implement opening-mode diagram and split fallback. Private tmux at 44/60/100 and stubbed mode, single-host, saved-group, cancellation and PROD guard checks pass.
- [x] Verify private real-terminal fixtures, fzf 0.63 and pointer performance; update screen documentation. Platform and integration gaps are recorded below.

Pre-flight: Task 4 consumes Task 3's `ui_choice` and preview interface. Tasks 1–2 are not prerequisites for these controls. Final integration must reconcile import-branch changes and re-run its suites.

Ruling: the installed minimum fzf 0.63 lacks `--gutter` and the `multi` event used by the shared frame/home. Detect gutter support once on screen entry and use the blank gutter on versions that support it; update the footer from the Tab action, preserving the existing selection restriction. Real 0.63 and 0.75 screens now both have one pointer.

## Verification and measurements

- `tests/settings-module.sh`, `tests/ui.sh` (111 checks), `tests/platform.sh` (21 checks), `tests/open-mode.sh` and Bash 3.2 syntax checks pass.
- Real private-tmux `tests/controls.sh` passes with the verified fzf 0.63.0 at 44/60/100×24, including settings preview, horizontal toggle, empty checklist, Other cancel, immediate language reload, mode diagram and cancel. It also passes with local fzf 0.75.0 at 60×24. Captures are under `controls-captures/`.
- Full `tests/tui.sh` passes against fzf 0.63.0; `tests/screens.sh` passes with local fzf 0.75.0. An earlier TUI run exposed a fixture race: background sync can commit the temporary search data before the test restores HEAD. The baseline fixture commit is now pinned; this is a test fix, not an index change. An earlier test assumed an untruncated preview name at width 44; it was corrected to assert the visible prefix.
- Pointer benchmark: macOS arm64, 100×24, rounded, compact, right preview, Vietnamese; nine samples after three warmups, `a07d017` → `d482121`, private synthetic hosts, one Down including tmux send/capture overhead. At 20 hosts median 13.684 → 13.425 ms, range 13.508–14.455 → 12.922–15.485 ms; at 500 hosts median 13.747 → 13.282 ms, range 13.227–14.382 → 12.997–14.831 ms. Full samples and method are in `controls-pointer.json`. Command: `python3 /Users/duong.vv/works/hostbook-ui-next/tests/pointer-performance.py --repo /Users/duong.vv/works/hostbook-controls-next --revisions a07d017 d482121 --samples 9 --output /tmp/hostbook-controls-pointer.json`.
- Screen entry including the capability check: private tmux 100×24 `hostbook settings`, five warm samples, median 98.79 → 100.97 ms, range 98.30–99.61 → 100.57–105.19 ms. The first observations (152.44 → 100.69 ms) are not comparable: the first populated a shared dependency cache. This is launch-to-visible, not a per-key measurement. fzf capability is checked on screen entry and is therefore refreshed if the executable changes; it is never checked on a key press.
- Linux, Windows, physical terminals and Task 1 form tests were not run here. Import branch integration and independent review remain outstanding; no merge or push was performed.

## Task 3 checklist copy

- [x] Test no-search choice, preselection, apply/cancel, Space checklist, count and empty selection in private tmux.
- [x] Implement compact shared controls with theme/frame, bounded height and actual fzf 0.63 checks.
- [x] Extract settings UI and use compact controls; preserve preview command and immediate language reload.
- [x] Use checklist for tags; retain custom tags, cancel/back and empty selections, normalize and deduplicate on apply.
- [x] Run UI, TUI and screens tests and save captures. Direct-form test is in the independent form spike, which failed its feasibility gate; no integrated form test exists here.

## Task 4 checklist copy

- [x] Exercise missing/invalid/saved modes, cancel, group preference, single host and PROD rejection with isolated stubs.
- [x] Use compact choice and precomputed diagrams; default to Split without changing valid saved modes.
- [x] Verify local private TUI and related regressions. Linux/Windows verification and branch integration remain for final acceptance.

## Home recovery (Task 5 subset)

- [x] Preserve and test Escape's query → selection → imported-only view hierarchy; selected hosts remain actionable on zero matches.
- [x] Distinguish a query with no matches from an empty host list. Show the existing Add and Tools/Import instructions for no data; keep shortcut footer visible and explain when selections remain during a no-match query.
- [x] Real private-tmux tests cover rapid edits, long queries, recovery, Enter on selected hosts, empty data and 44/60/100×24 in vi/en and rounded/none with actual fzf 0.63.0. fzf 0.75 and regression results are recorded at integration.
- [x] Compare query response before/after in a quiet window; a builtins-only fzf result callback updates the footer, without starting the app per key.
- [ ] File picker and other searchable lists, integration with import and form, independent review and platform coverage remain outside this slice.

Query latency: macOS arm64, 100×24, rounded/compact, vi, private 20/500-host fixtures, fzf 0.75; 9 samples after 3 warmups, revisions `9598b22` → `309f356`. A single `z` produces no matches. Baseline waits for the 0/N count; changed revision waits for the recovery message, so timings include tmux send/capture and polling rather than only application time. At 20 hosts the median was 15.831 → 15.637 ms (range 15.197–16.904 → 15.170–16.329); at 500 hosts it was 15.754 → 15.683 ms (15.179–17.886 → 15.255–16.364). Raw samples are in `home-query-performance.json`; the isolated measurement script was `/tmp/hostbook-home-query-perf.py` and reused `tests/pointer-performance.py` from the companion worktree.

## Host UI extraction

The named host interaction functions moved verbatim to `lib/hosts.sh`; suggestion, parsing, rendering and raw mutations remain in core. Human flows, unknown underscore searches and sourced fixtures load it explicitly; `_connect`/`_explain` use pane-mode error handling and do not reach Edit. The loader regression checks human and unknown-search paths with an intentionally failing module and confirms unrelated callbacks and `--version` skip it. Unix install/update module assertions include `settings` and `hosts`; Windows module assertions remain for integration.

Verification before the extraction commit: Bash 3.2 syntax checks; function text comparison against the previous revision; hosts-module; core (150), UI (111), platform (22), import (13), TUI and screens passed. The first UI run under concurrent suites exceeded its 200 ms demo test limit (261 ms); it passed when rerun alone (58 ms). Unix install/update and isolated before/after performance remain pending.
