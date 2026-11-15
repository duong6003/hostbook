# Modern Bash direct-edit feasibility spike

**Outcome: the timed-read and signal-blocking problems are resolved in this macOS scratch prototype, but the approved form is not proven feasible yet.** Wide and combining Unicode place the cursor incorrectly, and a sufficiently delayed escape-sequence suffix leaks into the next terminal input. Do not integrate this candidate or change the supported Bash version based on this result alone.

## Provenance and scope

- Scratch directory: `/tmp/hostbook-modern-form.NZ8Avz`. No app, configuration, installed tools or host files were changed.
- The machine had only `/bin/bash` 3.2.57 in the checked standard locations. The scratch binary `bash-5.3/bash` is GNU Bash **5.3.0**, built serially from unpatched `https://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz`, SHA-256 `0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba`. The checksum was checked against Homebrew's Bash formula metadata. The formula's current 5.3.20 includes patches that were not applied here. Nothing was installed system-wide.
- `lib/form.sh` is a disposable extension of the Bash 3.2 candidate; Python is used by tests only. It is not connected to the application and does not implement domain validation, theme, localization, or complete production cleanup.

## Evidence

Run with `BASH_FORM="$PWD/bash-5.3/bash"` from this directory:

- `python3 tests/read_probe.py`: real PTY, fractional timed read returned after bare Escape in about 55 ms; TERM trap ran promptly. The original untimed candidate still blocked, including under Bash 5.3.
- `python3 tests/form_test.py`: passed with a private tmux server and synthetic drafts: all nine fields in one outer frame and cursor in the focused field at 44/60/100×24; direct edits, Tab/Shift-Tab, retained cursor, left/right, Home/End, Backspace/Delete, Ctrl-U/K, Ctrl-C, review and project dispatch/re-entry; literal quotes, backslashes and `$()` without execution; precomposed accented text and bracketed paste; TERM, HUP and resize; 44×16 then 100×24. A split arrow sent with 20 ms between bytes passed. Bare Escape observations in the integrated test were 84–121 ms including the subsequent terminal-input check.
- Terminal input and echo worked after Review, cancel, TERM and HUP, and original TERM trap was restored in those tests. Exact macOS `stty -g` comparison differs only in PENDIN (`0x20000000`); the same PENDIN difference occurs with a minimal `stty` noncanonical→restore control. The harness compares other flags and separately tests input/echo. This does not establish exact restoration of every terminal state, alternate screen or bracketed-paste state.
- `python3 tests/unicode_probe.py`: **FAIL** for display cells. Precomposed `é` positions correctly; wide `你` advances one cell instead of two; combining `e` + accent advances two instead of one. Cursor movement and clipping use Bash character counts, not display-cell/grapheme widths.
- `python3 tests/escape_delay_probe.py`: **FAIL** for a delayed split sequence. With its suffix delivered 90 ms after Escape, the 50 ms timeout cancels and the next terminal read receives literal `[A`. The deadline cannot safely distinguish all slow sequences from bare Escape.
- `python3 tests/latency.py`: private tmux, nine samples after three warmups, includes send/capture subprocess overhead and 5 ms polling. At 44 columns, character median 17.762 ms (14.354–19.920), Tab 16.039 ms (14.633–18.551); at 100 columns, character 17.604 ms (15.078–17.876), Tab 17.574 ms (15.898–19.612). Full samples are in `latency.json`. These small samples are close to the 20 ms target and are not a universal latency bound. Inspection of `lib/form.sh` shows ordinary key handling and redraw use Bash builtins; `stty` runs at entry/exit and on resize, not per ordinary key.

## Limits and integration risks

The prototype uses a fixed 50 ms escape decision; malformed or slower sequences can cancel or leak. It returns status 130 on TERM/HUP after restoring state rather than re-raising the signal, so application signal semantics are unresolved. `read -n 1` temporarily enables ISIG even after `stty -isig`; Ctrl-C must be handled via the INT trap. The tests did not prove terminal-close cleanup, previous alternate-screen/bracketed-paste mode preservation, oversized paste, short terminals below 16 rows, arbitrary control/invalid UTF-8 handling, theme/i18n, or real application validation. The tests do not cover Linux, Windows, physical terminals, remote latency, grapheme editing or accessibility. A production design needs to solve these gaps and remeasure before adoption.
