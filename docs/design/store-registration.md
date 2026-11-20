# Device-local store registration and callback context

Status **proposed**, documentation only. This slice of
[#35](https://github.com/duong6003/hostbook/issues/35) defines how one device/account
selects a checkout and resolves the context for an action. It extends the
[identity sidecar](store-format.md), [mutation rules](store-mutations.md),
[publication barrier](store-transfer.md#publication-admission-and-transition-barrier)
and [transaction journal](store-journal.md). The implementation targets macOS
and Linux. The first UI exposes one private and at most one connected shared
store. The registry and isolation fixtures support multiple independent shared stores.

## Registration authority

Use a single UTF-8 JSON registry at `stores/v1/registry.json` beneath the verified
device/account state directory. Keep that directory and the separately registered
recovery root outside all checkouts, Git directories, synchronized folders and
export trees. Installation/account bootstrap must independently verify ownership
and bind these roots to this device/account. A path override, matching
`device_id`, or copied registry cannot establish that binding by itself.
Disposable tests select an explicitly isolated account root before resolution and
use it for every fixture dependency. They never fall back to live paths.

The registry owns device-local checkout selection and effective sync mode. The
tracked sidecar owns store and host identity. The recovery journal owns
transition intents, publication reservations, attempt outcomes and recovery
payloads, including [hostless control records](store-journal.md#publication-and-registration-control-records).
The remote decides actual access. None can stand in for another.

| Record | Exact v1 fields and constraints |
| --- | --- |
| Registry | `format: "hostbook-registrations"`, `version: 1`, `device_id`, `recovery_root_id`, `revision`, `stores`. The root ID must resolve to the independently verified recovery root. `revision` advances on each committed registry replacement. |
| Store entry | `store_id`, `kind` (`private` or `shared`), `registration_generation`, `checkout_generation`, `connection_generation`, `policy_generation`, `checkout`, `connection`, `effective_mode`, `transition_id`. One entry per store ID, including disconnected entries. At most one private entry. |
| Checkout | `checkout_id`, `root`, `binding_id`. `root` is the approved absolute canonical directory. `binding_id` references verified local evidence identifying that directory and Git worktree/common directory, where applicable. `checkout` may be null only for a disconnected shared entry whose local checkout was removed. |
| Connection | `target_id`, `ref`. The opaque local `target_id` resolves approved exact transport and target settings in protected local configuration. `ref` is a validated full branch ref. Connection is null for private and disconnected stores. A Git remote name alone cannot identify the target. |
| Mode and transition | Private and disconnected entries use null `effective_mode`. Connected entries use `receive-only` or `two-way`; new shared connections start in receive-only. Non-null `transition_id` references the exact outstanding journal intent. Any requested mode lives in that intent. There is no second requested-mode value in the registry. |

IDs follow the sidecar's canonical UUIDv4 rules. Revisions and generations are
positive integers that never wrap or repeat within their authority. Reject
duplicate JSON keys, unknown fields or versions, type errors and conflicting
bindings. Missing registry after a known installation is a recovery condition.
Do not silently recreate it or assign new store IDs. A new install needs
explicit initialization. Keep disconnected entries so reconnect cannot reuse an
old generation or adopt a copied checkout automatically. Reconnect always
requires verified selection and starts in receive-only. Restore uses fresh
generations and invalidates outstanding runtime contexts. It never makes a
stale callback current by reinstalling an old registry snapshot.

Exactly one checkout can be active for a logical store. Refuse a duplicate
store ID at a second checkout until the user explicitly reconciles and chooses
one. Two different stores cannot share or nest active data roots or Git common
directories. Compare resolved filesystem identity as well as paths, check the
sidecar store ID and revalidate the binding under the store gate before effects.
A moved or replaced directory, changed Git worktree binding, or unexpected
target/ref requires explicit reconciliation. A sidecar digest does not prove
directory ownership. Concrete filesystem primitives and old-writer exclusion
remain prerequisites to runtime use.

## Changes, generations and resources

- Advance `registration_generation` when the selected registration, role or
  application resource binding changes. Advance `checkout_generation` on every
  activation, relocation, replacement or removal of a checkout. Advance
  `connection_generation` on connect, disconnect, or changes to the effective
  target, ref or authentication binding. Advance `policy_generation` whenever
  effective mode, publication fence or scoped migration ownership changes,
  including cancellation or release. Unaffected store generations stay unchanged.
- Mode, checkout, target and disconnect changes follow the publication barrier.
  After durably recording the transition intent, install its fence while
  holding the affected store gates. Leave the old effective values in place
  until admitted attempts are quiescent and reconciled. Inspect nonterminal
  journal authority before admission even if the registry pointer is absent or
  stale. Disagreement blocks affected writes until reconciled. Old attempt
  records retain their original binding for settlement, even though new
  callbacks with the same generations may no longer act.
- For a registry replacement, acquire affected store gates in the existing
  canonical order, then a short device registry lock. Reread the complete
  registry, compare expected entry generations and recheck uniqueness and
  directory overlap across the resulting registry before replacing it
  atomically and durably. Preserve unrelated entries from the latest revision.
  No holder of the device registry lock may wait for a store gate.
  Release both before prompts, editor sessions or network waits. Recovery uses
  the journal for interruptions between directory, registry and remote effects.
- Keep the writer gate keyed by store ID across generations. Namespace the search
  index and derived settings by store ID and checkout generation, the unlock
  cache by device/account, store ID and vault generation, and sync status and
  rights observations by store ID and connection generation. Rights evidence
  also binds registration, checkout and policy generations, target/ref and the
  authenticated identity. Distinguish verified write access, explicit denial
  and unknown access. Network unavailability is an outcome of the current
  preflight, not a persisted write permission. Store only an opaque observation
  reference in the context.
  Denial supersedes earlier permission for new writes. The bounded offline
  exception for ordinary edits follows the [mutation rules](store-mutations.md#proposed-admission-rules).

## Callback context and resolution

Pass an opaque, scoped context ID to installed internal callbacks and background
children, and pass the same resolved record explicitly to sourced functions.
Resolve a context ID only from the verified account's private runtime state.
The runtime record binds the launching session or background job, operation and
permitted targets. Store `version: 1`, `context_id`, the launching owner ID,
an operation identifier from an application-defined list, its lifetime and
the target list. A writer context is short-lived and limited to one action.
Its targets contain store ID and expected registration,
checkout, connection and policy generations, plus selected host ID and relevant
record/settings/vault revisions where applicable. A transfer binds both stores.
For new records, bind the intended destination explicitly. A context may refer
to an already journaled intent, but its ID cannot resume or grant that intent.
No passwords, private keys, vault identities, SSH config text, remote credentials
or caller-provided checkout paths travel in the context. IDs are correlation
values. The resolver makes the authorization decision.

1. Resolve each store from the current registry and validate the operation and
   target type. Reject missing, malformed, expired or unknown IDs, wrong session
   or operation, and missing, stale or mismatched target generations. A raw alias,
   UI selection, current directory, environment override or copied helper cannot
   select a production mutation target. Old callbacks with no context fail
   closed on mutation. A newly initiated top-level Add or Import may resolve the
   private default once, then captures that exact destination before staging.
2. Resolve paths and resource namespaces from verified bindings. Recheck the
   store ID, checkout identity, relevant selected record and vault when the
   operation actually reads protected material or writes. Under the owning
   store gates, freshly evaluate effective mode, fences, exact target and rights
   under the mutation rules. Pending requested mode and cached results in a
   callback never grant rights. Context validity alone grants neither USER
   writes nor publication.
3. Before writing, repeat resolution after prompts and network waits. Bind
   duplicate execution to the existing intent or reject it. A stale callback
   gets a fresh selection/confirmation; it cannot retarget itself or authorize
   rollback. After restart, restore operations derive a fresh context from the
   journal and current registry under the gates, preserving original attempts.
   Derived search rendering must remain local and follow hot-path budgets.
   Retained rows carry store and host IDs, and confirmation acts on fresh resolution.

The resolver protects Hostbook's entrypoints from confused or stale routing.
External tools with the same operating-system access can still alter files or
invoke Git. The separate writer-isolation contract must address those writers
before cutover. The context is not a security boundary against the user's own OS account.

## Synthetic decision cases

| Case | Required decision |
| --- | --- |
| 1. Private, shared A and shared B, all named `db` | Resolve by store and host IDs. A's write, cache unlock and status cannot affect B or private. |
| 2. Worker launched for A, UI later selects B | Only A can resolve. A's original confirmation cannot authorize an operation in B. |
| 3. Invoked with a forged context ID, an alias or a directory override | Reject before touching production paths. An otherwise valid ID for another operation is also rejected. |
| 4. Confirmed action followed by atomic checkout replacement at the same path | Reject on filesystem binding or generation mismatch, even if the replacement sidecar uses the same store ID. |
| 5. Same shared ID appears in two clones, or two registrations share a Git common directory | Block new activation pending explicit reconciliation. Keep existing data. |
| 6. Requested two-way, effective receive-only, stale allowed rights evidence | Reject USER writes until the transition completes and fresh applicable rights are established. |
| 7. A sender is unsettled and the registry transition pointer was lost | Discover the journal fence and stop new admission. Keep the original attempt and settlement target. |
| 8. Two concurrent settings changes for independent stores | Preserve both changes without resetting either generation. Globally increasing registry revision alone does not invalidate the other store's context. |
| 9. A copied device registry or callback is opened after restart or under a fixture | Require the verified local authority and fresh context. No automatic adoption of the old cache, checkout or recovery owner. |
| 10. Disconnect, then reconnect the same URL and ref | Reject old callbacks and rights evidence, and keep historical attempts for recovery. The new connection starts receive-only. |
| 11. With effective two-way, remote denies an ordinary edit while earlier evidence allowed it | Reject the new write and retain existing pending work. Only network unavailability can qualify for the previously authorized ordinary-edit exception. |
| 12. Settlement finds the same remote in the registry with a different branch ref | Use the immutable ref from the admitted attempt, never the current registry ref. Keep the outcome unresolved if the original binding cannot be verified. |

These scenarios describe expected decisions for downstream fixtures in
[#36](https://github.com/duong6003/hostbook/issues/36),
[#37](https://github.com/duong6003/hostbook/issues/37),
[#38](https://github.com/duong6003/hostbook/issues/38) and
[#39](https://github.com/duong6003/hostbook/issues/39). This proposal does not test
a parser, filesystem isolation or remote permissions.
