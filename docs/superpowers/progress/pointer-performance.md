# Isolated real pointer measurement

Fixed snapshots: `ef847ee` and `c3b1d67`, materialized with `git archive` in disposable fixtures. No production files changed.

Reproduction commands, from the repository root:

```sh
python3 tests/pointer-performance.py --repo . --revisions ef847ee c3b1d67 --samples 12 --warmups 0 --output pointer-first-input.json
python3 tests/pointer-performance.py --repo . --revisions ef847ee c3b1d67 --samples 12 --warmups 3 --output pointer-warm.json
```

The committed raw reports preserve the original measured samples. The reusable script was packaged afterward without another benchmark run; its timing and reset loop is unchanged. It accepts another repository and two revision names, materializes committed snapshots in a disposable directory, and stores no inherited environment in reports. Its fixture starts from an explicit environment rather than forwarding personal variables. Use `--self-check` for small parser/statistics/isolation checks without launching the app.

Real application in a private tmux server. Each sample sends one Down, then captures the pane immediately and after 5 ms sleeps until the visible ▌ selection changes. Three unmeasured Down/Up transitions precede samples. Up resets selection outside the interval; 80 ms settling between samples. Time starts before tmux send-keys and ends after the detecting capture returns; includes command delivery, capture subprocesses and polling overhead. Compact list, preview right, rounded border, Vietnamese, 100x24; index built before launch. No pure render-time claim.

The second pass separates warm navigation from the first-input effects observed in the initial pass. Both raw reports are retained as [pointer-first-input.json](pointer-first-input.json) and [pointer-warm.json](pointer-warm.json). p95 uses the nearest-rank definition; with twelve samples it equals the maximum.

| Hosts | Revision | Median ms | p95 ms | Min–max ms | Send median ms |
| --- | --- | --- | --- | --- | --- |
| 20 | ef847ee | 12.511 | 13.865 | 12.093–13.865 | 6.375 |
| 20 | c3b1d67 | 12.692 | 13.004 | 12.369–13.004 | 6.413 |
| 500 | ef847ee | 12.605 | 14.549 | 12.160–14.549 | 6.421 |
| 500 | c3b1d67 | 12.670 | 13.351 | 12.267–13.351 | 6.501 |

All forty-eight warm samples detected the visible pointer moving from `sample-0000` to `sample-0001` in the first capture. The loop captures immediately, and sleeps 5 ms before subsequent captures when needed; no second capture was necessary in these samples. Values include command-delivery and capture-process overhead and are an instrumented upper bound, not pure application rendering time.

Warm medians changed by +0.181 ms at 20 hosts and +0.065 ms at 500 hosts. All warm observations were below 20 ms in this workload. This does not guarantee every key under arbitrary machine load.

Initial pass retained first-Down outliers: current/20 hosts 38.328 ms (send 25.712, capture 12.524); baseline/500 26.411 ms (send 15.156, capture 11.185); current/500 28.351 ms (send 16.641, capture 11.640). Do not fold these into a claim of universal <=20 ms behavior.

Isolation: each run used a unique absolute tmux socket, disposable HOME/config/host/state/SSH paths, file-backed test keychain, no SSH agent, and a failing clipboard stub. The host repository had no remote. Cleanup signalled only pane-owned process groups and the private server; no owned processes remained.

Lifecycle check: current merged-worktree changes add `ui`/`import` module existence checks and other-directory `_header` callbacks after both install and update on Unix and Windows. Unix execution was reported passing by root; native Windows execution remains for hosted CI.
