# Delivery progress

Baseline: ef847ee. Main checkout: /Users/duong.vv/works/hostbook (reserved).
App worktree: /Users/duong.vv/works/hostbook-ui-next, feature/ui-modules.
CI worktree: /Users/duong.vv/works/hostbook-ci-next, feature/ci-efficiency.
Prototype: /Users/duong.vv/works/hostbook-ui-prototype (v3, synthetic UI fixtures).

## Current state

- User approved five-phase execution and model roles: Astra high for planning/architecture, Terra implementation, Sol independent review.
- Native worktree creation failed outside Git; using git worktree fallback at sibling directories as previously used in this project.
- Existing main has only our uncommitted AGENTS/module-loading documentation; copied into feature worktree without changing main.
- Delivery plan persisted. Task-specific plans and evidence pending.

## Decisions

- Keep app changes sequential. CI may proceed separately; synchronize changes to installer/test loading before integration.
- Explicit case/source loading only if measured useful. No generic lazy-load framework.
- Keep new work in feature branches until reviewable and tested; preserve main for the user.

## Baseline evidence

- Core: 150 passed; platform: 15 passed; UI: 111 passed; import: 13 passed, no failures (logs /tmp/hb-next-baseline-*.log).
- Added tests/performance.py: subprocess timing in isolated fixture HOME/config/state/SSH,20/500 hosts,9 samples,100 columns. No network connections. JSON performance-baseline.json records timings and sample spread.
- Baseline medians:20 hosts preview22.6/header33.7/footer19.3/list47.9/demo50.7 ms;500 hosts preview22.9/header34.1/footer19.2/list61.0/demo50.8 ms. Version about5ms.
- Existing500-host list exceeds50ms if used synchronously; record as baseline limitation, investigate under module comparison instead of claiming all budgets pass. Pointer response requires separate interactive measurement.

## Execution checkpoints

- CI commits:8ab924c efficiency;bd7c273 coverage;ed7d17e isolated HOSTBOOK_HOME/named fixture branch;2d6f88c existing fixture branch correction. Sol reviewed first two phases; review-driven fixtures verified with disposable sentinel and detached/named inputs.
- Draft PR: https://github.com/duong6003/hostbook/pull/13. First hosted run43697720583 oned7d17e; keep it finish for cold cache evidence before pushing2d6f88c. Main remains unchanged. GitHub connector cannot create PR (collaborator mismatch); authenticated gh used.
- CI local macOS suites all passed, including TUI/screens and15 Docker SSH checks. Native macOS CI SSH fixture remains a documented gap; do not modify system accounts or install a Docker VM merely to claim parity.
- Module task: Terra module_implementation implements Tasks1-2 of2026-11-15-modules.md; pending test/benchmark results.
- Import planning: Astra architecture_plan preparing2026-11-15-import.md; production code still untouched for that phase.
- Reviewer: Sol ci_review; no code edits.
- Ruling: existing user tmux/ssh-agent processes are left untouched; test cleanup is judged by owned fixture process groups, not killing every global matching process.

- Module eager extraction committed9732cb9, benchmark reportde95e78: core150/platform17/UI111/import13/TUI/screens all pass; initial eager timings roughly unchanged. Correcting benchmark TMUX isolation and remeasuring snapshots before final decision.
- Import task plan persisted2026-11-15-import.md; includes explicit dirty-data rollback, commit failure and unsafe source/archive handling checks.
- CI review accepted all changes through2d6f88c; hosted cold run currently macOS/Ubuntu green, Windows running.

- CI task1 acceptance complete:PR13 runs43697720583 and43698073522 allOSgreen. Windows cold/warm job208/170s, fresh install100.45/46.53s; see ci-hosted.md for exact limits and evidence. No main merge.
- Sol module review found Windows ln-s copy semantics and normal underscore search incorrectly skipping imports; Terra fixing both with regressions.
- UI task plan saved2026-11-15-ui.md. Independent Terra form_spike tests direct-edit feasibility only in/tmp/hostbook-form-spike.8ZW7de; no app writes until sequential integration phase.

## Module integration and form gate

- Independent review accepted module commits through c3b1d67; reviewed CI branch merged locally as 2346317. Main remains untouched.
- Explicit installed module assertions and callbacks at both installation/update stages added in a07d017. `bash tests/install.sh` passed; native Windows run remains pending.
- CI PR13 description now includes both hosted runs and observed cache times.
- Import implementation started against the committed module boundary, with separate behavior commits and an independent review planned.
- Direct-edit native Bash3.2 form spike failed Escape and signal cleanup. See form-feasibility.md; candidate stays scratch-only. User was asked whether to test modern Bash while preserving the approved interaction or retain Bash3.2 and adjust interaction to existing tools. No dependency change or failing form integration has been made.

- Module acceptance: Sol measured 12 individual Down keys after three warmups for each revision/size. Current medians 12.692ms (20 hosts) / 12.670ms (500); all warm samples 12.267–13.351ms, below20ms including tmux send/capture overhead. Initial first-key tooling outliers26–38ms retained separately. No universal latency claim. Task2 accepted; integrated native Windows remains Task5.
- Draft integration PR14: https://github.com/duong6003/hostbook/pull/14 at a07d017. CI trigger/check status is being inspected; no main merge.
- To keep independent UI work progressing while the direct-form compatibility decision is pending, Task3/4 compact controls use a separate worktree `/Users/duong.vv/works/hostbook-controls-next`, branch `feature/compact-controls`, base a07d017. Terra owns controls there; importer retains sole ownership of app-worktree changes. Integrate controls after import and resolve shared language/UI changes explicitly. This parallel preparation does not change the agreed sequential integration order.

- Integrated module CI passed all platforms in run43699265082 at a07d017. PR14 had its own cold cache (sibling PR13 cache is not shared); Windows fresh install135.67s/update5.50s. This is not a warm-cache regression comparison.
- Onboarding slice5796d9e added12-case suite (11 originally); independent review caught a leading-control-whitespace Match-exec guard bypass. Fix442d018 rejects unsafe control bytes and adds actual OpenSSH sentinel regressions for CR/VT/FF. Full import staging remains in progress; actual canceled-vault relaunch/direct-name/View tests still requested.
- CI now runs onboarding on all platforms via90ca8b1; this newer onboarding slice has not yet passed hosted CI.

- Fzf-only form alternatives were also tested in scratch: restart-per-field loses queued Tab; a persistent instance preserves queued Tab/basic cursor but measures44–49ms per switch and has unresolved marker/sanitization risks. See fzf-form-feasibility.md. Neither candidate was integrated. Corrected paste evidence: true bracketed paste retains printable suffixes, stripping tabs/newlines; the earlier suffix-loss observation was a harness error. Compatibility preference remains pending.
- Controls branch85beefb completed UI Tasks3/4 local checks and is under independent review. Home empty-state recovery is being prepared separately. Reviewer found stale selected-count footer after Escape clears selection; owner is fixing explicit event paths while retaining fzf0.63.
- Onboarding CI43699920128 passed native Windows, failed Ubuntu at a TUI fixture reset, and canceled macOS by matrix fail-fast. Root reproduced that real background sync commits temporary hosts, so restoring HEAD restores the wrong fixture. Fix8c60b50 pins the original revision and forces sync in the regression; full TUI passed in a fixed-HEAD snapshot. The fix is pushed for hosted verification.

## Integration checkpoint

- Reviewed controls, home recovery and verbatim host UI extraction merged as b31d450. Host extraction6d3400d/evidence911a980 passed local suites and Unix installation. The apparent add-screen slowdown did not reproduce in a clean alternating comparison (135.530→136.222ms median); contaminated samples are excluded and retained as notes.
- Reviewed shared picker/multiline helpers4aec80f + whitespace fixcd40031 merged as cf5dedd. Both additive language sections were preserved. Native gum uses Enter submit/Ctrl-J newline/Ctrl-C cancel across supported versions; oldgum ignores Escape. Current canceled edits are discarded, prior submitted draft remains; native textarea is an explicit framing exception. No new dependency.
- Import UI WIP was saved as stash ab6029aad55fcc022e5c9c0d28002cb8e9e71796 during that merge, then restored successfully. Root verified source/review tests and lib/import.sh byte-for-byte, and importer confirmed restoration. Keep this named backup until final code is committed, then remove only this stash.
- Backend c7c2571 review found key-write locking, managed-path, private-material and group-reference defects. Fix8a30165 closed these; reviewer withdrew an incorrect case-insensitivity premise after a real SSH probe. Commit93dae72 restores exact aliases and remaps ProxyJump references for package both decisions. Functional backend review accepted these fixes; one duplicate helper is being removed.
- Source routing1495835 passed focused tests and real helper integration. Review found literal --help/~literal paths and malformed v10/v1broken marker routing; fixes and compact source/key choices are in importer WIP. Read-only preflight/results remain in progress.
- Shared-menu native count/Ctrl-U recovery1f576f4 passed independent review, pending merge from feature/import-input. Its worktree is the reused /Users/duong.vv/works/hostbook-ci-next; feature/ci-efficiency is preserved separately.
- Root CI commits2f8b0c8/a4fdd0f add four-module Windows assertions and new backend/control/helper suites. Latest hosted green is run43700698508 at8c60b50 (onboarding + deterministic TUI fixture reset); newer integrated backend/UI has not yet been pushed or verified natively.
- Required user preference remains unanswered: modern Bash exploration versus retaining Bash3.2 and adjusting direct-form interaction. Native Bash3.2 and two fzf form spikes did not meet the combined gate; no failing form or runtime requirement change was integrated.

## Import integration and modern Bash evidence

- Import review/results checkpoint0202a20 is under independent review. The optional expected SHA passed into `undo_last` closes the gap between selecting import Undo and capturing HEAD; ordinary Tools undo retains its existing behavior. Ten focused review/result tests passed, including an injected background commit in that gap.
- Accepted shared-menu recovery1f576f4 merged cleanly as f2015ba. CI commit2a40799 adds menu recovery and import source/review/TUI suites on Unix, and source/review regressions on native Windows. Unix isolated install/update passed at2a40799; hosted verification of this integration is still pending.
- A scratch-only GNU Bash5.3.0 build resolves timed-read/signal blocking, but the form still fails wide/combining Unicode cursor positioning and delayed escape-sequence handling. See modern-bash-form-feasibility.md and modern-bash-form-latency.json. Ruling: retain the current runtime requirement and keep this candidate out of production; changing Bash alone does not deliver a correct form. The approved direct-edit form remains incomplete.
- Integrated checks: core150, platform22, UI111, import37, keys12, source12, review13 and onboarding16 passed; shared file/menu helpers, screen contract and general TUI passed. The 24-case import screen matrix and encrypted package44×24 passed; final package exact-block reuse had its own package TUI check. Docker SSH15 passed at887e76f. These local results do not replace hosted Windows validation.
- Review Task3 UI fix round1/5 (original preflight mismatch addressed, one new finding open; aa41c59..17f9bce): Ruling: review describes literal settings being saved, not effective values from arbitrary external SSH configuration. Missing directives are labeled inherited, and the screen discloses overrides. This avoids a new SSH Include simulator or rejection of otherwise valid user configurations. Package apply retains exact reviewed blocks after state/decision validation. Reviewer confirmed this contract, but found alias-based private filenames collide for `same`/`Same` on case-insensitive filesystems; numeric block IDs are being implemented in round2.
- The user was asked whether to keep the existing input form while accepting the remaining improvements, or expand technology scope for the nine-field editor. No answer has been received; that scope decision remains open.
- Task3 UI fix round2/5 (case-insensitive block-file collision addressed, zero open findings;17f9bce..ae904b5): numeric IDs preserve exact aliases and normalized decision order; independent spec/quality review passed. Import-review14/14 and the real package44×24 flow passed.
- First-preflight measurements at17f9bce are preserved in import-performance.md/json, with reproducible isolated harness tests/import-performance.py. Median launch-to-preview is922ms for30 hosts and8,924ms for500; staging6,620ms and review preparation2,159ms dominate the larger fixture. This is a one-off import cost, not per-key latency or a before/after regression claim. Large-import preparation remains a measured limitation; follow-up should profile those two stages and compare the same fixture without weakening validation, rather than adding a generic lazy loader.
- Independent integration review at60f912c found no actionable code issue across module/install, transaction, picker and undo boundaries. PR14 was pushed at60f912c and remains draft. The restored import WIP is committed, so the exact temporary stashab6029a was removed; task worktrees remain.
- Hosted run43706581334 failed Ubuntu at the home-recovery fixture's missing vault; macOS was canceled by matrix fail-fast. Fix36f3859 initializes both disposable fixture vaults and explicitly isolates keychain/agent state. Its seven recovery groups passed locally and scoped review accepted it.
- The same run passed Windows install/update/native SSH/fzf/uninstall and import36+one symlink skip, keys12, onboarding15+one symlink skip. Windows source routing failed its literal-relative-path case; import-review did not run after that suite failed. The shared Windows path conversion is being corrected, and full hosted acceptance remains pending. Windows cached install37.50s, update5.43s; expanded validation made the total job longer than earlier cache-only comparisons.
- Windows path fixffbb050 terminates cygpath options before literal paths. Scoped review accepted it; platform23 and source12 passed locally. Hosted run43707509087 passed the full Windows job, including source12, review14, native SSH/fzf and install/update/uninstall. Two symlink-creation cases were explicitly skipped; tar-link checks still ran. Cached install46.48s and update6.04s were measured in that run.
- That run failed macOS because tmux was absent; the app installer intentionally treats it as optional. Reviewed CI fixc7dfabf installs tmux on macOS and requires tmux/fzf/gum before Unix tests. Matrix fail-fast is disabled to collect both Unix outcomes; superseded-run cancellation remains. Run43708273567 is verifying this workflow and application tree. Main remains untouched.
- A disposable preview offfbb050 was smoke-tested with two synthetic hosts and opened in Terminal. Its isolated home contains try-import.conf (one new host, one existing alias). The fixture uses its own file-backed vault/keychain and SSH configuration; it does not use personal host data. Path pointer: /tmp/hostbook-preview-path.
- Hosted run43708273567 passed all three jobs atc7dfabf: Ubuntu4m48s, macOS7m3s and Windows8m18s. macOS now actually ran terminal helper/home/import/screen suites, with no missing-tmux skip. Windows retained the two explicit symlink-creation skips; native SSH/fzf/lifecycle and source/review suites passed. See ci-hosted.md. Task3 is accepted; Task4 direct editor and Task5 user acceptance remain open. No main merge.
- This final evidence update is a local documentation-only commit after the tested PR head. Keep the green application revision c7dfabf explicit; do not claim a later documentation commit itself ran CI.

## First-run wording cleanup

The user approved clarifying each existing setup screen without a progress indicator or a new wizard. Vietnamese and English now distinguish the required secure vault from the optional sync repository, name the local-start path directly, and make later/import/appearance actions explicit. Runtime flow and storage behavior are unchanged. Getting-started documentation and existing TUI text expectations were updated. `bash tests/screens.sh` and `bash tests/tui.sh` passed locally, including first-run completion at 60, 100 and 200 columns. Hosted CI has not been rerun for this wording-only change.

The subsequent user test identified that the three-way start screen still exposed a filter. It now uses the shared compact choice, with local start first/default and a focused explanation below the choices. Password, confirmation and appearance controls are retained. Short destination lists (up to five options) and hub setup actions also use compact choices; longer discovered lists retain search. The shared choice height accounts for wrapped context. Existing local/remote/hub branch semantics are covered after reordering; no vault or SSH storage rules changed.

Validation: onboarding 19/19; controls at 44 columns; full screens and TUI suites passed, including complete setup at 60/100/200 columns. Additional real first-run checks at 44×24 passed in vi/en: all three actions, selected explanation, no filter, and entry into vault setup. A quiet 100×24 pointer sample (nine measurements after three warmups, including tmux send/capture) measured old median10.756ms (10.141–11.534) and new10.971ms (10.526–11.924). The preview command uses only a precomputed case and builtin printf, not an app callback. Measurements preceded the final footer wording change; artifacts are in /tmp/hb-onboarding-ux-fglx_igy. Hosted CI has not run this interaction change yet.

Independent review found one fixture mismatch: the native Windows first-run test still stubbed the old menu. It now stubs the compact choice with the local-start index. Scoped review approved the fix; syntax checks passed, but native Windows execution remains pending. The final picker cancellation path and local/remote/hub mappings passed onboarding 19/19 again. This update is prepared for local user testing, not a main merge.


### Role-first setup correction

The initial menu mixed machine roles with data sources. Setup now explains and asks the role first (connect/manage servers or host a sync repository), then asks client machines whether to create a server list or retrieve existing synced data. Both use the existing compact choice control; no search or per-selection preview subprocess is needed. The encryption-password screen explicitly distinguishes the vault password (minimum ten characters) from existing server credentials (nonempty, no strength policy). Server password fields no longer advertise a length rule.

Validation: onboarding 21/21, full screen suite and TUI suite passed locally; fresh vi/en launches at 44 columns showed both choices and the password explanation. Updated clone and hub fixtures cover the new branch mapping. A short server password is accepted; a short vault password is rejected. No hosted Windows run for this revision.

The isolated preview launcher passed an explicit missing SSH config to OpenSSH. Reproduced the reported “Can't open user config file” failure; an empty config returns success. The launcher now creates that private file before launch. This is a preview-fixture repair, not a relaxation of application config validation. Existing personal config and vault data were untouched.


### Vault-first login selection

The server login form opens directly on saved vault credentials. “Add to vault…” is an inline action; its compact submenu contains private-key file import, clipboard import, key generation and password creation. A key-only caller excludes passwords and external SSH. Canceling the source submenu or file picker returns to the vault list. Successful additions select the secret name returned by storage (including deduplicated keys), display confirmation, and preserve original imported files. Existing generated-key public-key instructions remain available. Server-add context is retained above the vault list.

Validation: onboarding 24/24, import-keys 12/12, and the full screen suite passed locally. A private tmux smoke at 60 columns verified the vault list, add submenu, and Escape return. Preview snapshot updated. No hosted Windows check or main merge for this change. The generic file browser itself is unchanged.


### Package-only public import and vault Secrets

Public Import now opens a package picker directly, starts in Downloads when present, lists folders and `.hbshare` files, and supports literal paths. Contents must have the supported Hostbook package header and pass the existing decrypt/layout/config/credential validation before review/apply. Arbitrary text/SSH lists and paste/clipboard entry points are removed from public Import. Automatic clipboard offers on app entry are removed. Initial setup retains a separate internal `import_config_file` migration entry point; its validation/rollback tests remain active.

Export always writes a uniquely named encrypted `.hbshare` file with private permissions, including when clipboard copying succeeds. Tar/encryption failures propagate rather than producing a success result. Secrets lists saved entries directly, has one Add to vault action with file/paste/generate/password choices, retains migration/vault maintenance, and confirms stored credentials. Canceling the file picker returns directly to Secrets.

Validation (local macOS): package input 6/6, internal migration/transactions 37/37, key validation 12/12, review/undo 14/14, platform 23/23, onboarding 25/25, shared file input, complete screens and TUI suites. Converted the import TUI matrix (vi/en, 44/60/100 columns, both borders, 8/30 hosts) to actual encrypted packages; all matrix cases passed. Corrected an over-broad fixture edit in its conflict scenario and reran that scenario successfully at 44 columns. Round-trip tests export key/password credentials into a separate vault, preserve originals, reject wrong passwords, cancel without writes, prevent duplicate changes, retain distinct files on repeated exports, and clean transient plaintext. Added the round-trip test to hosted CI, but did not run hosted CI/native Windows for this revision.

Updated isolated preview snapshot and added a synthetic `Downloads/hostbook-demo.hbshare` (password `hostbook-demo-import`). Real Secrets → Add → key picker → Escape smoke passed at 60 columns. Personal data was not read or modified. No merge to main.
