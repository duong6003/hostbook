# Cross-store transfer transaction

Status: **proposed**, documentation only; second bounded design slice of
[#35](https://github.com/duong6003/hostbook/issues/35) under
[#33](https://github.com/duong6003/hostbook/issues/33). It extends
[Store and host identity](store-identity.md), whose identity rules still apply.
This is a logical protocol, not an on-disk journal format or implemented behavior.
There is no atomic transaction across disks, vaults or remote repositories.
[Store mutation and resource ownership](store-mutations.md) maps ordinary writers,
callbacks and sync to the same proposed guards and publication barrier.
[Legacy store migration and recovery](store-migration.md) applies these barriers
to installation upgrades, protected backups and conditional rollback.

## Outcome and authorization

Transfer moves one host between distinct logical stores. It preserves the stable
host ID and all supported connection settings, including the existing ProxyJump
value and resolution behavior. It neither copies a host as a new identity nor
moves jump hosts automatically. References to the old (store ID, host ID) need
explicit reconciliation; do not redirect them by alias. Two checkouts with the
same store ID are not a cross-store transfer.

A successful move requires a durable destination host and usable destination
credentials, destination publication when shared, durable source removal, and
source-removal publication when shared. Only then may it report **completed**.
Private and each independent shared store have separate vaults, locks, modes and
sync status. Both affected stores are write targets: every shared write requires
current two-way mode **and** actual remote write rights, checked at its write
boundary. Receive-only is the default, not merely a disabled UI action. A cached
rights check or confirmation never grants remote rights. Rejection, offline state
or changed mode pauses the transfer; do not queue an apparently successful move.
The same checks apply to retries, compensation and credential cleanup.

Before mutation, show an explicit preview and confirmation: source/destination
store labels and keys, host ID, alias/name decisions, connection-setting changes
(normally none), required publications, credential mapping decisions, reference
impacts and any unresolved duplicate. Never show secrets, secret-derived hashes
or decrypted key material. A changed plan requires renewed confirmation.

Same-ID destination records require comparison against this intent and its saved
payload, even on retry. Equal ID or equal content alone is not commit evidence.
A changed same-ID record blocks automatic overwrite and source deletion. A
different ID at the same effective endpoint offers compare, update-existing,
keep-both with valid names, or cancel. Alias collisions also need an explicit
choice. **Update-existing is blocked in this slice**: defining lineage and the
surviving ID is a remaining #35 dependency, not permission to discard the moving
ID. Never mint another ID to make retry or collision handling succeed.

Credentials are decrypted only as needed and re-encrypted for the destination
vault; copying source ciphertext is insufficient. Resolve destination credential
name/identity conflicts explicitly, without silent overwrite or presumed
equivalence. Verify the saved destination mapping and decryptability with the
intended vault before source removal. Keep source credentials while any source
host references them. Reference scans and any later credential deletion use the
same revision and lock checks as host writes. The source recovery payload retains
what is needed to reconstruct the original host and its credential mapping until
completion or explicit recovery resolution; unreferenced credential collection is
not a prerequisite for completing the host move.

## Durable evidence and observed state

Allocate one opaque transfer ID and retain it, the host ID and both store IDs on
retry. Before the first write, durably save the confirmed intent, non-secret
snapshot identifiers, selected collision/credential decisions and the next step.
Before deleting source data, durably retain its recoverable original payload and
credential references/material under vault protection. If that cannot be done,
stop while the source remains intact. Protected recovery must survive process
exit and restart, including loss of the source working-tree file.

The journal contains metadata only (IDs, revisions, step attempts and outcomes),
or references to vault-protected recovery payloads. Complete SSH settings or
credential material that might contain secrets belong in the protected payload,
not an unprotected journal. No secrets in logs, argv, environment, clipboard or
unprotected temporary files. Encryption failure must not fall back to plaintext.
Recovery must block safely when its vault cannot be unlocked.

Distinguish three kinds of fact:

- **Intent** records what was confirmed, including expected preconditions and
  proposed local/remote changes. Persist intent *before* each effect.
- **Evidence** records durable local writes and exact remote acceptance proofs:
  logical store, configured remote/ref, proposed revision, expected parent/revision,
  affected host ID and payload/recovery reference. Persist evidence after the
  effect; a crash between effect and evidence is an uncertain outcome.
- **Observations** are newly read local/remote revisions, content, vault generation,
  mode and rights. An old acknowledgement proves acceptance at that revision; it
  does not prove that the host still exists or is unchanged now.

A successful local command or push exit alone is insufficient without matching
publication evidence. If a publication response is lost, keep its attempt
uncertain. On restart query the intended remote/ref and reconcile the exact
proposed revision and affected content against current state. An accepted revision
in current history proves historical acceptance, but later changes still need
comparison. If acceptance cannot be established (offline, rewritten history,
conflict or insufficient read rights), remain blocked and retain recovery data.
Do not infer success from the absence of an error or repeat a destructive write
blindly. A retry may publish only against freshly verified preconditions.

## Serialization and revalidation

Use one common per-store mutation gate for transfer, ordinary edits, vault changes,
sync application/publication, store registration changes and mode transitions.
Acquire both gates in canonical store-ID order, then subordinate vault/journal
locks in that same order; never acquire a lower-ordered store while holding a
higher one. Locks have owners; only the owner releases them, and stale recovery
must establish owner death. A gate holder does not wait for another store in a
different order. Do not hold gates over user prompts or network waits: persist
intent and any publication admission described below, release, then reacquire
both and revalidate before applying a result. Remote actors are not serialized
by these local locks.

### Publication admission and transition barrier

Rechecking mode after a network response cannot prevent a previously started
send from taking effect. Before **any** remote write, every publisher (including
ordinary/background sync, destination addition, source removal and compensation)
must acquire that store's gate, check the effective mode/connection and transition
fence, and durably reserve one publication attempt before releasing the gate or
starting network effects. The reservation binds its owner/attempt ID, store and
connection generation, remote/ref, expected base and proposed revision. Actual
remote rights remain enforced by the remote. No sender may start without this
admission, reuse a settled reservation or change its admitted target/payload.

A request to change mode, remote/connection or active checkout acquires the same
gate and durably installs a **transition fence** against new admissions. Display
the request as pending; keep the old effective mode, connection and checkout
until every admitted attempt is drained. Existing admitted senders may finish,
but transfer progress pauses at subsequent write boundaries while the fence is
pending. Release the short gate while waiting; the durable reservations and fence,
not a held process lock, serialize this interval. Concurrent settings requests
must reconcile the existing transition rather than bypass its fence.

An attempt is drained only when its sender and transport children are known
unable to issue further network effects and its exact remote outcome is durably
reconciled as
accepted or definitively not accepted. Mere absence from the currently observed
ref is insufficient if delivery may still be pending. Record settlement under
the gate.
Transport exit, owner death, timeout, cancellation and age alone do not prove
remote rejection; a request already delivered may still be accepted. On crash or
lost response retain the reservation and fence, establish sender quiescence,
and reconcile against the admitted remote/ref. If that outcome cannot be proven,
remain visibly pending and block new admissions/effective transition. Do not
silently clear reservations, expire them by age or retarget them on restart.
Historical acceptance plus fresh content revalidation are still required for
transfer progress; draining does not waive conflict checks.

Only under the gate, after successful draining/reconciliation of all admitted
attempts, may the requested settings become effective and be reported effective.
A definitive failed send leaves the old mode/connection unchanged and reports
the transition failed, consistent with #33; uncertainty leaves it pending. Once
all attempts are settled, an explicitly cancelled/failed transition may release
its fence while retaining outcome evidence; another settings attempt needs new
admission checks. A receive-only mode can therefore never be reported effective
while a local two-way send remains in flight or uncertain. This barrier governs
local publishers; independent remote actors can still write.

The confirmation snapshot binds both store identities/active checkouts, source
record/settings and credential references, destination identity/alias/endpoint
candidates, credential mappings/vault generations, local revisions and remote
ref revisions, and modes. Under both gates, compare these inputs before each
local mutation. Any material change invalidates the plan; present a new preview
or an explicit conflict decision. Never apply a network result to a checkout or
mode that changed while gates were released.

Publication must be conditional on the verified remote base, preserving unrelated
changes; no force overwrite or automatic conflict resolution. If the remote base
moves, fetch/reconcile, revalidate the relevant records and reconfirm material
changes before proposing a new publication. Permissions can still be rejected
at publication, and that rejection is authoritative.

After destination acknowledgement, refresh **both** stores before source removal.
Require the destination's current host, credentials and settings to match the
confirmed intent; require the source to match the version being moved. After
local source removal, concurrent source reappearance or remote source edits must
block removal publication rather than delete the newer data. Revalidate the
destination again before source publication and final completion, and recheck
both current states before destructive recovery/credential cleanup. A remote
edit after an acknowledgement is never ignored merely because the old revision
was accepted. A matching destination from unrelated work is not owned by this
transfer and cannot be removed during rollback.

These are conditional per-store writes and fresh observations, not a distributed
lock or an atomic cross-store snapshot. A remote actor can edit immediately after
an observation. Completion records the exact accepted revisions and final
observations; it promises successful required steps, not immunity to subsequent
edits. A detected divergence before completion blocks the operation and retains
the source recovery payload, even if a prior step was acknowledged. No cleanup
may erase newer or unrelated work to manufacture the intended final state.

## States and crash boundaries

States are durable checkpoints, not proof of current external state. Every restart
reconciles intent/evidence with current observations before advancing. A failure
or cancellation can leave a checkpoint with a blocked/uncertain outcome.

| Checkpoint | Required evidence and next action |
| --- | --- |
| `prepared` | Confirmed intent and protected source recovery are durable; source remains present. Encrypt and save destination conditionally. |
| `destination-saved` | Destination host, credentials and mapping are durably saved and verified locally. Publish if shared; otherwise establish local destination confirmation. |
| `destination-confirmed` | Required destination publication is evidenced (or private save verified). Refresh both stores, then conditionally remove source locally. |
| `source-removed-local` | Source host is locally absent; protected original remains recoverable. Publish removal if shared; otherwise establish local source confirmation. |
| `source-confirmed` | Required source removal publication is evidenced (or private deletion verified). Revalidate current destination and source, then durably mark completion. |
| `completed` | All required steps succeeded and final observations agree. Retain outcome evidence for retry recognition; recovery-payload retention/collection policy is deferred. |
| `cancelled` / `rolled-back` | Cancellation before effects, or explicitly reconciled compensation, respectively. Neither implies a completed move. |

A crash during destination save may leave partial local files/credentials; inspect
and repair only this intent's changes using protected recovery, preserving source
and unrelated vault entries. A crash after destination publication but before its
acknowledgement is recorded follows uncertain-publication recovery above; never
create a second destination record. A crash before or during source deletion
reconciles whether the exact expected source is present, absent, or changed.
Changed source content blocks advancement and must not be replaced with the old
snapshot.

A crash after local source deletion but before source publication leaves the
original available in protected recovery. Offline or rejected source publication
is **not completion**: expose the pending removal, keep recovery material, and
retry conditionally or restore locally through reconciliation. Remote readers may
still see the original. A crash during source publication has the same uncertain
acknowledgement treatment as destination publication. A crash after source
acceptance but before completion revalidates both stores and records completion
only if the required effects remain consistent; it must not repeat deletion of a
newly reintroduced source record. A crash after completion recognizes the same
transfer ID, verifies observations, and reports its outcome without replaying it.

## Cancellation, retry and compensation

Before destination effects, cancellation preserves the source and ends the intent.
After local destination effects, cancellation stops forward progress and offers
review of intent-owned cleanup. After remote publication, cancellation cannot
promise a simple undo: it may require a separately confirmed compensating change
to remove an unchanged destination, restore the source, or publish that restore.
Obtain current two-way mode/rights and compare current revisions first. Do not
silently erase others' edits, remove a credential now referenced elsewhere, or
rewrite remote history. If compensation is blocked, report a recoverable partial
transfer and retain the protected original; cancellation itself is not rollback.

Retry uses the original intent and host ID, rechecks authorization and current
content, and resumes only missing effects whose preconditions still hold. It
never assumes a same-ID record is its prior write. Rollback likewise records its
own intent/evidence and can be interrupted or rejected; report `rolled-back`
only when required local and shared compensations have succeeded. Otherwise
retain a blocked, recoverable state with the next decision clearly identified.

## Documentation examples and remaining scope

[transfer-recovery.json](fixtures/transfer-recovery.json) contains independent
synthetic failure/restart checkpoints, publication-barrier examples and deliberate
invalid barrier counterexamples. Run from the repository root:

```bash
python3 docs/design/fixtures/validate-transfer.py
```

The static validator checks conceptual fixture consistency and selected safety
invariants, including rejection of the invalid barrier counterexamples. It
performs no storage, encryption, Git, network or runtime tests and does not prove a production implementation satisfies this protocol. It reads
only the adjacent documentation fixture.

Remaining #35 work includes durable encoding/journal representation, reference
reconciliation, update-existing lineage, implementation-level audit beyond the
bounded mutation inventory, vault and lock implementation contracts, recovery
retention/collection policy, concrete migration backup/restore and writer-upgrade
mechanisms, legacy admission and store-specific SSH resolution. Broader #35 fixtures and
downstream #36–#46 runtime implementation/regressions remain separate. This slice
neither selects those mechanisms nor completes #35.
