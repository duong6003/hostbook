# fzf direct-edit feasibility spike (scratch only)

2026-11-15, macOS arm64. No application files changed. fzf 0.63.0 verified release binary `/tmp/hostbook-fzf063/fzf`; installed Homebrew fzf 0.75.0 used for bracketed-paste comparison. Run prototypes under a private tmux server; scripts are experiments, not production-safe code.

## Findings

- `form.sh` arranges nine fields in one border by placing prior fields in `--header-first`, the active field in the query prompt, and later fields in the list. `capture-44.txt`, `capture-60.txt`, `capture-100.txt` show all labels at 24 rows. Captures only show short values. Cursor is on the active row. Escape exits the finder in this basic test.
- Exiting and restarting fzf for each Tab lost rapid queued Tabs. Six measured Tab/Shift-Tab samples at each width were 23.6–33.4 ms, measured from completion of tmux send-keys to the first captured expected frame. This includes tmux/process overhead and is not a pure renderer benchmark. Switching back reset the cursor to the end (`alphaX` after left-left, Tab, Shift-Tab, X).
- `persistent.sh` and `persistent-callback.sh` maintain one fzf instance and use an inserted marker, a Bash callback, raw transform-header/prompt/query output, reload, and fixed backward-char actions. Three queued Tabs advanced from name to port, and left-left, Tab, Shift-Tab, X produced `alpXha`. Six sequential samples at each width were 44.3–49.4 ms under the same measurement method. Each switch uses a callback plus multiple synchronous child processes; this is over the 20 ms goal and creates substantial form-specific machinery.
- The marker is a fixed scratch string and can collide with user input. It is not a safe production encoding. Display sanitization, arbitrarily long fields, terminal cleanup under signals, safe choice actions, complete literal metacharacter handling, Windows/Linux behavior, and sustained typing latency remain unproven. The scratch version must not be integrated.
- Correction: a first tmux test was not a valid bracketed-paste test; its reported suffix loss was an artifact of unbracketed key delivery. `pty4.py` explicitly sends ESC [ 200 ~ / ESC [ 201 ~ around UTF-8 paste bytes and confirms fzf enables bracketed mode. On both fzf 0.63 and 0.75, `Việt<TAB>NEXT` and `Việt<NEWLINE>NEXT` retained the printable suffix as `ViệtNEXT`, without submitting or navigating in this isolated fzf test. Tab and newline are removed rather than turned into spaces. One 0.63 rerun did not return a result within the harness timing; an earlier direct PTY run captured the tab result. Raising the fzf floor would not change the observed separator behavior.
- A 2048-character paste test did not produce a result within the scratch harness timing; long-paste preservation remains unverified. Earlier simple literal `$(touch …)` input did not execute, but this does not establish full injection safety.

## Recommendation

Keep the current form until the Bash compatibility/interaction decision is settled or a modern Bash implementation is proven. Neither scratch fzf approach meets the combined latency, simplicity and safety gate. Do not treat this as evidence that every possible fzf design is impossible.

## Reproduction

`python3 /tmp/hb-fzf-spike/check.py` and `python3 /tmp/hb-fzf-spike/check-persistent.py` use a private tmux server; `python3 /tmp/hb-fzf-spike/pty4.py` exercises bracketed paste directly. Scripts require the two fzf paths above and are disposable, with synthetic field values only.
