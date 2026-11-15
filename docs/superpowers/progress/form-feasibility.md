# Direct-edit Bash 3.2 form spike

**Result: FAIL; do not integrate this candidate.** The approved layout is feasible, but the input decoder and signal cleanup do not meet the gate. This is a throwaway experiment; no application files or host data were changed.

## Evidence

- macOS arm64, `/bin/bash` 3.2.57, private tmux server, synthetic draft only. `python3 tests/layout_test.py` passes at 44, 60 and 100 columns by 24 rows: all nine field names/values appear in one frame, and the cursor starts in the name field. Captures are in `captures/`.
- `python3 tests/form_test.py` **fails**: after edits and bare Escape the form never exits within 3 seconds. The test tears down its own private tmux server. Subsequent assertions do not run, so this does not establish editing, paste or dispatch correctness.
- `python3 tests/probe.py` independently reproduces the decoder issue on a fresh PTY. With `stty -icanon min 0 time 1`, Bash `read -r -n 1` changes the terminal to `VMIN=1 VTIME=0`. After the first Escape, the next character read remains blocked past 310 ms. `read -r -d ''` also switches to `1/0` and blocks. Thus the intended 100 ms kernel timeout is overridden.
- A plain `read -r` preserves the terminal timeout, but waits for the input to become idle: the probe returned after about 101 ms for bare Escape and 132 ms when an arrow suffix arrived 20 ms later. It can collect subsequent input until timeout/newline; it does not meet the <=20 ms navigation target. These figures are individual measurements, not a latency benchmark.
- `python3 tests/signal_test.py` **fails** at 44 columns: TERM sent to the pane's Bash process while blocked in `read -n 1` did not complete the cleanup trap or restore tty within 2 seconds. No wider signal claim is made. Private tmux server was terminated in the test teardown.
- `/bin/bash -n lib/form.sh` passed. No CPU-heavy tests or user tmux sessions were used. Test processes were checked after teardown and no scratch processes remained at that point.

## Candidate and proposed integration interface

`lib/form.sh` is an incomplete Bash 3.2 candidate. It defines `ui_form DRAFT_DIR`, reads the nine files `name address user port project login tags jump desc`, records `focus` and `cursor_<id>`, and on intended success writes an `action` of `review`, `project`, `login`, `tags` or `jump`. It renders directly to `/dev/tty` and uses shell builtins for per-key redraws. The candidate is **not safe to integrate**: the failures above prevent reliable cancellation and cleanup. The draft and cursor files are synthetic; they are not a tested production contract.

## Smallest viable next step

The cleanest route retaining direct editing is to prove the same form on an already-supported newer Bash with fractional `read -t`, if the project accepts changing its macOS Bash 3.2 requirement. That only supplies a possible timed read; it does **not** prove split-sequence decoding, signal delivery, cleanup, Unicode, paste, cross-platform behavior or latency. Windows reportedly already bundles newer Bash, but that has not been independently verified in this spike. Otherwise, retaining Bash 3.2 likely requires a separately distributed native terminal input component (and platform builds), which changes the deployment/runtime shape and needs an explicit decision. Neither alternative has been implemented or approved here. Existing fzf/gum single-query interfaces have not been shown to satisfy the approved all-visible direct-edit form.

## Untested and known limits

The failing test stops before assertions for text edits, literal shell metacharacters, accented input, paste, split sequences in the full form, choice/review re-entry and draft retention; none is certified. Ctrl-C, INT/HUP, WINCH/resize, terminal close, process failure, short terminals, large paste, screen/mode restoration on all paths, trap preservation, actual key response latency and terminal safety for all C0/invalid Unicode are unverified. Linux, Windows, physical terminals and older/alternative terminal emulators were not tested. The candidate currently has no real theme or localization integration, and does not satisfy all short-terminal or safe-cleanup requirements. Do not rely on it to handle arbitrary input or real data.
