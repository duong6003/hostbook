# Hosted CI verification

Date: 2026-11-15. Draft PR https://github.com/duong6003/hostbook/pull/13.

| Run | Revision | Cache | Windows job | Fresh installation | Update |
| --- | --- | --- | --- | --- | --- |
| [43697720583](https://github.com/duong6003/hostbook/actions/runs/43697720583) | ed7d17e | miss, saved | 208 s | 100.45 s | 4.78 s |
| [43698073522](https://github.com/duong6003/hostbook/actions/runs/43698073522) | 2d6f88c | restored | 170 s | 46.53 s | 5.93 s |

All macOS, Ubuntu and Windows jobs passed in both runs. The revision difference
only corrects an existing fixture branch edge case in the Unix install test; the
Windows installer/cache/test code is unchanged between these runs. One pair of
hosted runs is evidence of improvement, not a stable performance guarantee.

The restored cache was about84MB and took about2 seconds to restore. The cold
run saved it in about3 seconds. Both installs deliberately corrupt the age
archive first to verify rejection/replacement; both still perform fresh runtime
extraction and full install. Update is a different workload (runtime already
installed), not a substitute measurement for warm fresh install.

The Windows suite retained launcher/path/DPAPI/clipboard, real key/password SSH,
interactive fzf, update and uninstall checks. The cache stores only archives and
validates expected SHA256 on every hit. Main remains unchanged.

Independent Sol review accepted efficiency and coverage after fixing fixture
HOSTBOOK_HOME isolation, detached Git checkout and already-existing branch cases.
Local macOS tests also passed core150/platform15/UI111/import13/TUI/screens/
Docker SSH15 plus the new Unix install/update fixture.

Limits: macOS hosted real-SSH and Windows10/11 physical terminal rendering remain
explicit gaps in the coverage matrix. No claim of complete platform parity.
