# Legacy store migration and recovery

Status: **proposed**, documentation only; fourth bounded design slice of
[#35](https://github.com/duong6003/hostbook/issues/35) under
[#33](https://github.com/duong6003/hostbook/issues/33). Extends
[identity](store-identity.md), [transfer](store-transfer.md) and
[mutation ownership](store-mutations.md). These are logical requirements, not an
implemented migration or assertion that #35 is complete. The subsequent
[format/admission proposal](store-format.md) selects identity encoding and legacy mapping.

## Classification before creation

Inventory the existing installation without initializing a vault, changing its
remote, committing, syncing or assigning identities. A configured remote makes a
legacy installation **shared**, even while unreachable; no configured remote
makes it **private by default**, not proof that its content was never published.
An explicit later change of role never erases publication history. Separate the
private store and every independent shared store, including their vaults, caches,
locks, connections and pending work. New hosts/imports still default to private;
shared registration defaults to receive-only. Shared migration writes require
successful live preflight and current remote write rights.

The following rows are cumulative gates, not a first-match decision algorithm.
An observation must identify its checkout, remote/ref and revision; an unknown
result never means absent. Remote inspection is an explicit setup action, not a
preview or startup background connection. Fetch into protected staging without
applying fetched content or running repository-supplied hooks/configuration.

| Observed state | Classification and required next decision |
| --- | --- |
| No remote; complete local vault or no vault | Default private candidate. Check prior publication/history evidence and identity mapping; create a private vault only after proving no local partial vault exists and passing all gates below. |
| Configured, reachable, unambiguous remote/ref | Shared candidate. Inspect its current content, vault and identity/history **before any new shared vault creation**, even if the local checkout has none. |
| Configured remote unreachable, unreadable or missing required refs | Shared but unresolved. STOP; restore access/select the intended ref and repeat discovery. Do not treat as empty or bootstrap locally. |
| Conflicting remote/upstream/ref, duplicate registration or uncertain prior role | Ambiguous. STOP; ask for an explicit target/active-checkout decision, preserve alternatives, then rediscover. A URL/path/name is not a store ID. |
| Remote shared vault exists and local vault is absent | Reuse the existing shared identity; unlock with its password or recovery code. Never create a replacement because unlock failed. Private gets its own separate vault. |
| No vault at either verified shared endpoint | Creation is only a candidate after current remote absence is established and shared USER authorization permits it; conditional publication must reject a concurrently created vault. Receive-only cannot bootstrap. |
| Independent local and remote vaults, even with equal secret names | STOP automatic merge. Retain both; explicitly choose separate stores or a reviewed reconciliation/re-encryption plan, unlocking every participating vault. Never overwrite/discard/combine identities silently. |
| Locked complete vault | Read-only inventory may continue; STOP before secret migration/re-encryption or decryptability verification until each required vault is unlocked. No cached identity from another store substitutes. |
| Partial, malformed or mismatched vault/public key/envelopes | STOP; preserve all pieces, restore a verified complete backup or use explicit recovery. Missing pieces are not absence; do not call creation to repair them. |
| Pending/staged/unstaged/untracked work, merge/rebase, uncertain publication | Preserve all work and classify it. STOP cutover until reviewed reconciliation and publication drain establish a stable snapshot; never auto-commit, stash away or reset it to pass. |
| Same store ID with divergent history; different IDs with shared ancestry; unknown metadata version or ambiguous host mapping | STOP identity-dependent writes; compare recorded lineage/history and reconcile explicitly. Equal aliases, URLs, ciphertext names or content do not establish identity; do not merge unrelated histories automatically. |

Historical publication remains shared exposure: moving files or hosts to private
cannot make copies, old clones or remote Git history private. Explain this before
confirmation. Preserve encrypted records and recoverable history; never promise
to erase old remote history or use history rewriting as migration/rollback.

## Preflight, snapshot and protected backup

Show the reviewed classification, original and proposed registrations/roles,
identity mappings, vault choices, pending work, required remote effects and SSH
configuration changes. Unresolved multi-alias mapping or stripped metadata stops
identity assignment; preserve ordinary SSH directives, wildcard precedence and
ProxyJump behavior. Existing v1 share-package validation/compatibility still
applies; migration does not reinterpret a legacy import as a transfer retry.
No automatic jump-host transfer or new store-specific jump restriction is added.

Capture an intent ID and snapshots of all affected store/host identities,
registration/checkout/connection generations, metadata versions, vault generations,
working-tree and index state, refs/history, remote refs, mode/rights and relevant
configuration dependencies. Inventory symlinks and external dependencies without
following untrusted paths blindly. A backup must cover the **whole affected state**:

- All working-tree files, including staged and unstaged versions, untracked and
  ignored relevant data; Git index, refs, objects/history and in-progress operation
  state. A commit, patch or clean-worktree copy alone is insufficient.
- Vault ciphertext, public identity, password/recovery envelopes and credential
  mappings, including original generations needed for history/recovery. Retain
  referenced external key/config inputs when migration would alter or depend on
  them; otherwise record their protected location and verify continued access.
- Local registration/connection settings, Git configuration, SSH Include/config
  dependencies and permissions/ACLs relevant to restoring the prior installation.
  Record original locations and restore ownership; do not export unrelated device
  credentials or copy an unlocked cache as a substitute for recovery envelopes.

Before **any migration mutation**, verify backup confidentiality (protected
storage, permissions/ACLs and encryption appropriate to content), integrity
(complete manifest and exact state comparison), and access (operator can reopen
and decrypt required recovery material). Exercise a restore into isolated protected
staging: compare working-tree/index distinctions, refs/history, identity/vault
bindings and dependency resolution; verify necessary secret decryptability without
displaying plaintext. A backup that exists but cannot be restored fails preflight.
Keep original encrypted data and this verified recovery set until explicit safe
retention resolution under the [journal/retention policy](store-journal.md#retention-and-collection-decision).

No secrets, recovery codes, secret-derived hashes or complete sensitive SSH
settings in journals, logs, argv, environment, UI or unprotected temporary files.
Use vault-protected recovery payloads and non-secret references as in transfer;
encryption/access failures STOP, with no plaintext fallback. Prompts may accept
password/recovery input securely but must not echo or log it. A protected backup
is not permission to proceed without unlocking all vaults whose secrets will be
migrated/re-encrypted, including destination verification.

## Writers, ordered gates and upgrade boundary

Quiesce local app instances, callbacks, editors, sync and transport children for
every affected checkout; inventory other devices/automation that may publish.
Install transition fences and drain admitted publications using the
[transfer barrier](store-transfer.md#publication-admission-and-transition-barrier).
An uncertain send blocks cutover; owner death or transport exit alone is no proof
of rejection. Acquire common per-store gates in canonical store-ID order (use an
explicit provisional registration ordering before legacy IDs exist), then their
subordinate vault/recovery locks in the same order. Owner-checked release and
stale-owner proof follow transfer. Never hold gates during prompts/network waits.

After every old attempt is quiescent and durably reconciled, revalidate the plan
under the ordered gates and **durably replace** the drain fence with migration
ownership before cutover. This is a recorded handoff, not an exemption allowing
publication through a pending transition fence. Ownership binds the exact intent,
store, remote/ref, expected base and proposed checkout/connection generation;
record the resulting generation at cutover. It excludes ordinary writers,
publishers and settings/registration changes. Only that intent's reviewed writes
and exact generation/target publication reservations may be admitted, with fresh
snapshots, effective two-way mode and actual rights. Receive-only grants none.
No process gate is held across network waits; durable ownership persists instead.
Restart must reconcile its owner, generations and attempts; uncertainty retains
ownership and recovery, never clears them by age. Release ownership durably under
the gates only after a reconciled terminal completion/cancellation/rollback and
all sends are quiescent/settled; blocked recovery cannot reopen ordinary writers.

Future identity-aware writers must reject unknown metadata versions and missing,
malformed or ambiguous associations rather than downgrade or regenerate IDs.
A legacy binary cannot be made to honor a marker or lock it never understood.
Require enforceable isolation of its writable paths/credentials and suspension
of old jobs, plus compatible upgrades or removal of old writers from all affected
checkouts/devices before exposing migrated content. A warning or new lockfile is
insufficient. Prevent legacy publication from bypassing remote preconditions,
using access restrictions or a separately isolated destination when required.
If safe isolation/compatibility cannot be established, STOP and explain which
writer/access must be upgraded, stopped or isolated; keep the original intact.
Do not claim an atomic global lock over independent remote actors.

Compare the captured full snapshot under gates after each wait and before every
mutation, restore and publication admission. Gates serialize cooperating writers;
manual editors and independent remote actors can still change data. Any changed
index/file/ref/vault/configuration or generation invalidates the plan/backup:
retain it, recapture/reverify and renew confirmation for material changes. Never
clobber newer work to make the snapshot match. Fresh actual rights and effective
two-way mode are required for **every** shared USER write, including metadata,
vault creation, cleanup and rollback; confirmation is not a rights grant.

## Durable boundaries and recovery

Each checkpoint records intent before effects and evidence after effects, using
protected recovery references. Restart reconciles current observations with both;
a checkpoint name alone never authorizes replay. Partial multi-store cutover is
possible and must be visible; there is no atomic disk/vault/remote transaction.

| Boundary | Preconditions, durable evidence and interruption behavior |
| --- | --- |
| `prepared` | Classification, compatible writer isolation, confirmed plan, stable snapshot and verified complete recovery set. Original remains authoritative and intact; cancel without migration effects. |
| `staged` | Build isolated candidate from the snapshot; retain stable identities, SSH behavior/history and mappings. Re-encrypt required credentials only with all participating vaults unlocked; no promotion/publication. Failed encryption leaves original untouched. |
| `verified` | Check candidate completeness, unique identities, encrypted vault bindings/decryptability, preserved SSH settings/references and restore access. Revalidate original and remote snapshots under gates. Failed validation retains evidence; never promote partial output. |
| `cutover-local` | After durable drain-fence → migration-ownership handoff, under ordered gates and fresh USER guards, record a recoverable intent then conditionally switch the exact registration/checkout/metadata generation. Retain original snapshot/history; record each effect and resulting owned generation. Crash between effect and evidence requires comparison, not blind replay. |
| `publication-pending` | Under migration ownership (no pending transition fence), each exact target/base/proposed revision and owned generation receives its own authorized admission reservation. Publish conditionally; retain ownership/recovery until sender quiescence and acceptance/rejection are reconciled. Offline/rejected/uncertain is blocked, never completed. |
| `completed` | Required local effects and remote acknowledgements are durable; fresh observations match the plan and identity/vault/SSH checks still pass. Record exact revisions and report boundaries; later external edits remain possible. |
| `rollback-pending` / `rolled-back` | Revalidate intent-owned changes and newer work, restore conditionally from verified recovery and verify full restore. Required shared compensations need fresh authorization and acknowledgement. Only all successful required effects justify rolled-back. |

A lost publication acknowledgement follows transfer exactly: preserve the admitted
attempt and original target, establish publisher/children quiescence, query the
intended remote/ref for the proposed revision and affected content, then compare
current state. Historical acceptance is not proof of unchanged current content.
Offline, rewritten history, insufficient read rights or changed content leaves a
blocked recoverable migration, with no blind resend, new identity or forced reset.

Before cutover, cancellation discards only intent-owned staging after checking
ownership. After cutover, stop forward progress and offer a reviewed rollback;
never automatically replace a changed original/destination or unrelated vault
entry. Restore working-tree/index distinctions, refs/history, registrations and
configuration dependencies, then rerun integrity/access/SSH checks. If restoration
would clobber newer work, STOP with a protected recovery location and explicit
compare/reconcile guidance. Remote effects need separately confirmed conditional
compensating commits with actual rights and acknowledged outcomes; never rewrite
history or assume local restoration undid publication. Blocked rollback retains
original/recovery material and precise next steps, not a success message.

## Synthetic decisions and remaining #35 scope

[migration-decisions.json](fixtures/migration-decisions.json) contains independent
classification/interruption examples. Run from the repository root:

```bash
python3 docs/design/fixtures/validate-migration.py
```

The retained static validator reads only adjacent synthetic JSON and checks
selected conceptual decisions/invariants; it does not call the app, Git, vault,
SSH or network and does not prove enforcement, encryption or crash safety.
Its four handoff checkpoints cover drain, cutover, authorized reservation and lost
acknowledgement; premature handoff and ordinary-publisher admissions are rejected.
The [registration contract](store-registration.md) defines the active binding.
Remaining before implementation: concrete writer
isolation/upgrade negotiation,
platform ACL/lock/restore mechanics, identity
reconciliation/update-existing lineage, store SSH resolution and complete parent
acceptance fixtures. [Journal/retention](store-journal.md) selects device-local
authority, protected payload layout and collection policy. Remaining representation and compatibility choices must satisfy
this contract and be reviewed; this slice does not choose them or complete #35.
Downstream #36–#46 still own runtime migration and isolated regression coverage.
