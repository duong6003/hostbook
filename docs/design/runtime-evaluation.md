# Runtime evaluation and stabilization plan

Date: 2026-11-17. Status: proposal; no production runtime migration is authorized by this document.
Baseline: main `9f98c7d`; scoped rollback PR #30 `c8b139b`.

## Decision

Stabilize the existing Bash release first. Then evaluate a gradual move to Go with a read-only prototype.
Bubble Tea is the preferred candidate for a later Go UI, conditional on measured behavior and terminal coverage.
Rust remains a viable alternative if the maintainers prefer it or a prototype demonstrates a concrete advantage.
Do not rewrite the vault, SSH stack, Git implementation and UI together.

This recommendation is engineering judgment for this repository, not a benchmark result. The codebase has
about 4,400 lines across the main script and shared libraries, plus installers, platform helpers and tests.
Most expensive operations delegate to external programs; changing languages alone will not remove `ssh -G`
per-host indexing or make an incorrect rollback/sync algorithm safe.

## Stabilization order

| Order | Problem | Completion evidence |
| --- | --- | --- |
| 1 | Incomplete rollback in #29/#30; ignored filesystem and commit failures | Real raw operations with injected failing `mv`/`rm`/commit restore working files, staged index and HEAD; unrelated local work survives |
| 2 | Live writer eviction and unowned lock release | Old live locks remain held; dead locks recover; concurrent recovery permits one writer; busy sync preserves prior status |
| 3 | Sync branch selection and invalid incoming configuration | Differently named branches exchange data on the selected remote branch; invalid pulls return failure and restore the previous valid HEAD; rejected pushes remain pending |
| 4 | Tailscale address selection | VPN IP is retained, machine name is visible, SSH user can be edited and cancellation performs no connection |
| 5 | Cross-platform release validation | Ubuntu and macOS CI on the final SHA, with import, TUI and terminal-close checks and explicit reporting for skipped capabilities |

The macOS TUI import check in #30 reported a busy sync. Verify that scenario on macOS.

## Options

| Option | Benefit here | Cost and limitations | Recommendation |
| --- | --- | --- | --- |
| Keep Bash + fzf/gum | Small immediate fixes, familiar installation, preserves current interactions | Implicit shared state and exit-status propagation remain fragile, and callbacks restart the script | Maintain the supported release while stabilizing and measuring |
| Go core, existing UI initially | Typed host/sync models, explicit errors, structured JSON, direct argument passing to subprocesses; binary release artifacts | Packaging per platform, process-tree cancellation and OS credential adapters still need implementation; a helper booted per key still has startup cost | Preferred next experiment |
| Go + Bubble Tea | Persistent application state and event loop can replace shell callback boots and make transitions directly testable | Rebuild keyboard/search/preview behavior, Unicode layout, terminal handoff, resize and accessibility; fuzzy-search parity needs tests | Adopt only after a read-only UI prototype passes the gates |
| Rust + a Rust TUI such as Ratatui | Strong ownership and explicit error handling; native application is feasible | Adds a separate UI stack and toolchain learning if the team does not already use Rust; most current defects concern transaction design and external tools | Choose for demonstrated team or technical fit, not memory safety alone |

Go's [`os/exec`](https://pkg.go.dev/os/exec) passes arguments without invoking a shell by default, removing a class
of quoting mistakes when used correctly. It does not eliminate subprocess lifecycle work: cancellation must stop
descendants, restore the terminal, wait for exit and release owned locks on each supported OS.
[Bubble Tea](https://github.com/charmbracelet/bubbletea) models state using Init, Update and View; it is a Go UI
framework, not an alternative language. Rust supports multiple native desktop targets with different support
tiers; verify the actual release targets against the [official support table](https://doc.rust-lang.org/rustc/platform-support.html).
Check release versions and module paths when starting a prototype; do not base the migration on old tutorials.

## Incremental migration gates

1. **Preserve behavior as a contract.** Keep ordinary SSH `.conf`, `groups.tsv`, vault ciphertext and share-package
   formats. Record the current CLI, environment isolation variables, vi/en strings, SSH inheritance, ProxyJump,
   key handling, PROD confirmations, conflict choices and undo semantics. Convert new regression scenarios into
   implementation-independent fixtures. Continue using OpenSSH to interpret effective SSH configuration.
2. **Build a read-only Go experiment.** Start with listing/info, Tailscale JSON decoding and a minimal searchable
   list. Use only synthetic/copied data. Separate data access, external commands and UI state. Keep Git/OpenSSH/age
   as subprocesses initially; this phase does not remove those dependencies. Do not add network work to previews.
3. **Measure before selecting a UI.** Compare Bash and the experiment on the same macOS machine at 100×24 with
   20/50/500 hosts: cold first frame, warm first frame, typing, pointer, preview, RSS and binary/package size.
   Report repeated samples and spread, not a single startup number. Gates: cold first frame at most 200 ms
   (the target in #23), pointer at most 20 ms, preview at most 100 ms and version at most 30 ms. A partial/stale
   first frame must not permit connecting with stale credentials or outdated host settings.
4. **Move writes only after transactional parity.** Use a single writer with explicit ownership, staged writes,
   validation before publish and rollback including the index. Test every filesystem and Git failure boundary,
   cancellation, lock-owner death, offline/rejected push, different branches, conflicting edits and manual edits
   during sync. Preserve Unix permissions. Define compatible locking for a
   transition; never let old Bash and new Go writers mutate the same data concurrently without it.
5. **Migrate screens and release deliberately.** Port screen contracts and keyboard semantics, keep bounded
   background work and reject stale results by request/selection generation. Verify real terminal handoff to
   SSH, terminal-close cleanup, keyboard input, Unicode and narrow screens. Provide a reversible binary
   selection mechanism and a data backup; avoid a one-way data conversion. Replace the supported runtime only
   after final-SHA cross-platform CI and independent review.

Suggested Go package boundaries are `model`, `store` (transactions/locking), `sync`, `discovery`, `ssh`, `vault`,
platform adapters and `ui`. Do not introduce a generic plugin framework or a long-lived daemon just to perform
this migration. Prototype results, maintainer familiarity and release maintenance cost decide whether to proceed;
the current bug count alone does not establish that a full rewrite is worthwhile.
