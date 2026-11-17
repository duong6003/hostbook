# Sync/VPN integrity verification

Date: 2026-11-17. Base: PR #30 `c8b139bd6c7c3019c9a171d8da484faf86fa221a`.
Changed main-script blob: `4c43a4e4c5027e59d6465d4b4db7e631168d4e90`.

## Functional checks

All host data, SSH configuration, credentials, agent sockets and homes used for testing were disposable.
No real VPN host was contacted. Tailscale checks use synthetic CLI output.

- `bash tests/test.sh`: 151 passed.
- `bash tests/platform.sh`: 52 passed, zero failed/skipped on Linux. Added raw filesystem failures, partial
  secret/history failure, failed commit and notification failure after a successful commit.
- `bash tests/sync.sh`: 12 passed. Covers live/dead/ownerless locks, foreign release, concurrent stale recovery,
  busy foreground/background status, different branch names, invalid pull, clean-but-invalid local configuration,
  rejected push/CLI failure, failed link and invalid incoming configuration during link.
- `bash tests/discovery.sh`: 3 passed. Preserves VPN IP and display name, filters offline/mobile peers, permits
  editing the login on a discovered destination, and cancels without network activity.
- Module suites, `ui`, `file-input`, `menu-empty`, `controls`, `open-mode`, `home-recovery`, `import`, `import-keys`,
  `import-input`, `import-package`, `import-review`, `import-tui`, `onboarding`, `tui` and `screens`: passed on Linux.
  Core/platform and targeted sync/discovery were repeated after the relevant final changes. Screens cover vi/en,
  narrow/wide terminals, real private tmux sessions and teardown.
- `bash tests/e2e.sh`: 15 SSH behavior assertions passed through the local Docker-compatible Podman runtime.
  Podman's user-namespace image storage required manual unmount/removal of the disposable fixture after ordinary
  teardown encountered permission errors. This is separate from the SSH assertions.
- `bash -n hostbook install.sh tests/platform.sh tests/sync.sh tests/discovery.sh tests/onboarding.sh` and
  `git diff --check`: passed.

With the new sync/discovery suites copied onto the unchanged PR #30 script, sync had 6 failures and discovery
had 3 failures. The original isolated raw-secret reproduction also returned success with an absent destination
ciphertext. These are behavior regressions covered by the patch, not assertions that merely inspect source text.

Native macOS/Bash 3.2 and Windows execution, physical Windows terminal behavior, real Tailscale/MagicDNS behavior
and independent final-head review remain unverified locally. This patch must remain draft if published until
those release checks are complete. The existing macOS CI TUI-import busy-lock failure needs final-SHA CI confirmation.
The Windows symlink case now reports a capability skip when MSYS cannot actually create a symlink; a skip does
not establish the safety of native Windows symlink handling.

## Callback measurements

Host: Linux x86_64, glibc 2.44; dimensions 100×24, C.UTF-8, compact list, synthetic 20/500-host fixtures.
Base and changed applications shared exactly the same fixture/index for each size. Each callback was warmed,
then sampled 15 times per revision with alternating order. No tracing or concurrent test suites were running.
The initial command was `python3 tests/performance.py APP --samples 9 --sizes 20 500 --output REPORT`, using a
temporary copy with C.UTF-8 for this machine. A same-fixture alternating follow-up investigated an initial
preview timing difference. Raw alternating samples and their metadata are in
[sync-vpn-linux-warm.json](sync-vpn-linux-warm.json).

| Hosts | Callback | Base median (min–max), ms | Changed median (min–max), ms |
| --- | --- | --- | --- |
| 20 | version | 3.554 (2.706–5.339) | 3.279 (2.711–5.192) |
| 20 | preview | 26.331 (25.060–29.096) | 26.763 (25.425–27.326) |
| 20 | header | 28.505 (28.168–28.668) | 28.764 (28.533–29.130) |
| 20 | footer | 21.746 (21.542–22.112) | 22.076 (21.863–22.451) |
| 20 | list | 33.109 (32.818–36.307) | 33.510 (33.256–35.607) |
| 20 | demo | 49.507 (47.724–52.986) | 49.484 (48.352–51.056) |
| 500 | version | 2.825 (2.707–3.219) | 2.927 (2.747–3.214) |
| 500 | preview | 25.349 (25.100–25.641) | 25.586 (25.421–26.061) |
| 500 | header | 28.458 (28.165–28.955) | 28.927 (28.540–29.351) |
| 500 | footer | 21.696 (21.462–22.216) | 22.115 (21.898–22.577) |
| 500 | list | 42.282 (41.668–42.386) | 42.788 (42.055–43.603) |
| 500 | demo | 48.383 (47.819–51.752) | 48.628 (48.243–49.232) |

The initial separate runs had a 500-host preview median of 25.329 → 29.941 ms; the alternating follow-up did not
reproduce that larger increase. Small warm increases (roughly 0.2–0.5 ms) remain consistent with the additional
script parsing; this attribution is an inference, not a source-line profile. These warm callbacks are below
their applicable budgets on this Linux machine. Pointer latency and macOS budget acceptance were not measured.

Single cold samples include initial indexing and are not navigation measurements: 500-host cold `_list` was
2155.555 → 2236.611 ms in the initial runs; 20-host cold `_list` was 115.565 → 114.731 ms. Cold 500-host startup
remains existing debt tracked in #23; these single samples do not establish a statistically reliable regression.
The patch introduces no additional work per host in indexing or rendering and does not claim to fix cold startup.
