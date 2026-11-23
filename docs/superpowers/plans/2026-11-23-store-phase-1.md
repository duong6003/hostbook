# Store-aware reads and write controls

> Agentic workers use subagent-driven-development or executing-plans. One person or agent writes code at a time. Check off work only after verification and independent review.

**Goal** — Integrate store-scoped host reads, shared write admission and explicit sync-mode transitions for milestone 1 of #33.

**Architecture** — Use the approved OS account, the v1 registry and the tracked sidecar for identity. Store-local writer gates serialize effects. Durable publication reservations and transition fences stay outside checkouts. The first shared save modifies only one existing host stanza.

**Tech stack** — Existing Bash 3.2-compatible launcher and Python 3 registered-store modules, OpenSSH and Git.

**Spec** — [Acceptance map](../../design/store-acceptance.md), [identity](../../design/store-identity.md), [registration](../../design/store-registration.md), [mutations](../../design/store-mutations.md), [journal](../../design/store-journal.md), and [transfer publication barrier](../../design/store-transfer.md#publication-admission-and-transition-barrier).

## Global constraints

- Base for the milestone is merged #36 at `e177011`. Review and test the exact final SHA before presenting the merge decision.
- Use isolated account, SSH, agent, credential, Git remote and terminal fixtures. The real account and vault are outside the test boundary.
- Preserve legacy paths and the Bash launcher. No network, process waits or added shell boot on hot paths. Record like-for-like macOS timings for affected hot paths.
- Bind every managed operation to stable store and host IDs, expected generations and its exact effect. No alias, environment value, UI default or remote name can retarget it.
- First release UI integration is tracked in #45. Multiple shared store management and aggregated search are tracked in #34.

## Review focus

- Duplicate aliases and secret names across stores must select the same approved store for preview, SSH and each credential lookup.
- Connection or ref changes during the edit must block a new request and leave any admitted request bound to its original target.
- A concurrent change to a different host must survive, while a changed revision of the selected host requires a decision.
- Interrupted publication must preserve enough evidence to settle the exact attempt. Unknown acknowledgement does not authorize replay or a mode change.
- Discard must preserve unrelated files and independent staged edits. Re-enabling two-way must not republish discarded work.

## #37 Store-aware reads

- [ ] Add red tests using duplicate aliases in the private, A and B fixtures. Cover stale generation, rename by stable ID, invalid sidecar, approved wildcard/ProxyJump behavior and installed callbacks from outside the installation directory.
- [ ] Use the registered-store resolution and index in `lib/store_context.py` as the authority. Build effective derived host rows from validated scoped SSH inputs outside preview. Recheck identity and configuration before Connect and use the same approved configuration view for its jump children.
- [ ] Audit history, groups and undo callers in `hostbook` and module entrypoints. Preserve legacy alias semantics for legacy data and keep registered-store actions scoped.
- [ ] Verify the focused tests and relevant install, platform, UI and SSH coverage. Record affected macOS hot-path measurements and review the exact commit.

## #38 Shared write boundary

- [ ] Use the file-level inventory in the mutation contract to test receive-only denial at the data boundary. Default new data to private. Test two shared stores independently, clean receive without publication, and offline and denied shared starts.
- [ ] Resolve target and full ref through protected registered bindings. Recheck effective mode, captured generations and the selected host version under the local writer gate. Keep the approved exact transport binding and revisioned publication reservation in the verified recovery root.
- [ ] For the first ordinary shared save, stage one existing host edit in a private Git index and reserve one exact conditional request. Test same-host conflict, stale remote/ref, unrelated edits and uncertain outcomes. Preserve input on failure and leave stricter operations subject to their own rights requirements.
- [ ] Cover supported internal and installed entrypoints for admission and safe rejection. Verify the focused and legacy sync suites and review the exact commit.

## #39 Effective mode transitions

- [ ] Write red tests for clean switch, cancel, send failure, confirmed discard, unrelated staged/unstaged work, an admitted concurrent send, and restart during the transition.
- [ ] Reuse the publication authority and device registry lock. Persist a transition fence under the store gate, release locks for network waits, and complete the registry replacement after admitted attempts are settled and the affected data is verified.
- [ ] Test cancellation and recovery before and after registry replacement and check that discarded edits cannot replay. Record exact commit review, run combined macOS and Ubuntu CI, and prepare the milestone PR for the user's merge decision.
