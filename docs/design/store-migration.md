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
snapshots and actual rights. Legacy shared targets use the scoped admission
below to satisfy the migration-only mode check. Established v1 shared stores
retain their regular effective two-way requirement.
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

### First supported upgrade

- **Fresh shared store.** Verify a separate checkout and Git common directory
  with no existing legacy writer route or imported private history. Verify the remote
  binding and confirm that any legacy publisher is excluded from the intended
  ref. A newly connected store with valid tracked identity starts receive-only.
  Initialization of a genuinely empty repository is a separate explicit,
  authorized operation. Connection cannot initialize metadata or a vault.
- **Migration of legacy data.** Keep the original checkout and stage a
  separate target on macOS or Linux. The supported local boundary is a
  distinct OS writer identity plus filesystem permissions. Check ownership,
  mode, ACLs and ancestor replacement rights for the target worktree, Git
  common directory and writable state. The inventoried legacy identities
  must be unable to modify or replace those resources, including through
  inherited groups or delegated services. A new directory under the same
  writable account, a marker file or a source checkout lock provides no
  evidence of that exclusion. If a legacy writer still shares the target
  writer identity or its access cannot be established, leave cutover blocked.
- **Shared migration target.** Use server-enforced rights for the exact repo
  and full branch ref. A dedicated repo is the simplest destination when it
  separates legacy credentials. A separate ref in the same repo qualifies
  only when its server policy also excludes every inventoried legacy Git
  identity, including device credentials and automation. No reliance on
  naming, read access or a dry-run push. Require evidence of the enforced
  server rule and reconciliation of prior attempts before cutover. Keep
  provider implementations outside this contract. If a host cannot supply
  reliable evidence, leave the original checkout and remote unchanged.

#### Evidence of local writer exclusion

Bind the check to the actual staged worktree, Git common directory and
writable state. Resolve each path from trusted directory anchors and record
the filesystem identity of each resource and the directories that bind it to
its name. Follow the checks up to a verified boundary beyond which legacy
writers cannot replace the path. Unresolved links, mounts or ownership stop
the check.

Evaluate effective permissions for each inventoried legacy OS identity and
its groups, including relevant native ACLs, inherited rights and authority
through a service. Check both edits inside each resource and rename, unlink
or replacement of the resource or any directory in its path. Evaluate a
replacement at the containing directory, including the rules that affect
that operation. Owner and mode on the target alone cannot establish that
its name is protected. Use a read-only check of the actual target.
[Disposable fixture operations](store-writer-validation.md) demonstrate
the kernel decisions on synthetic paths.

Resolve any identity or service information that requires waiting outside
the short migration gates. Before handoff, compare the current path bindings
and access metadata under the gates against the verified evidence, including
the scope and freshness of the identity information. A writable path,
changed binding or permission the platform cannot establish stops cutover
and identifies the affected resource or ancestor. Preserve the original and
recovery set.

#### Evidence of remote writer exclusion

Obtain effective ref-update rules through an authenticated, supported
server-policy read for the exact target. Account for inherited access and
bypass rules when determining whether each legacy device key, user and
automation identity can update the ref. A missing identity result or a
remaining update right blocks handoff. A failed Git command can reflect
unrelated conditions and supplies no policy evidence.

Bind the result to the server and its stable repository identity, the full
branch ref, the current connection generation, the authenticated migration
identity, the normalized legacy identity inventory and the observed effective
policy revision. Use non-secret identity references. The revision is a
server-provided version or a comparison of freshly read, complete effective
policy data. If the server cannot establish current rules for this exact
binding, stop and report the missing evidence. An actual private target has
no remote policy requirement.

Read the policy outside the short migration gates. Under the gates, compare
the latest authenticated observation with the reviewed evidence and the
current local binding immediately before ownership handoff or publication
reservation. After a wait or restart, obtain a fresh observation. A changed
repo, ref, identity, connection or policy invalidates the old evidence. If
freshness cannot be established, stop the handoff or reservation and release
the gates before refreshing. Remote exclusion is separate from confirming
the migration writer's own authority and from reconciling previous sends.
A local gate cannot prevent a later policy change. The server's active rule
governs each remote update.

Capture writer inventory and evidence for both boundaries under the reviewed
plan. Recheck destination filesystem identity, Git common directory, the exact
connection/ref and prior publication outcomes before cutover. Filesystem and
remote observations must remain current after waits and through the ownership
handoff. The legacy original is quiesced for the snapshot and retained with the
tested recovery set. A confirmed backup and a restore exercise authorize
neither cutover nor publication by themselves. Unknown or changed evidence,
an untested restore, or any surviving legacy write route requires a stop
with the missing condition named.

Compare the captured full snapshot under gates after each wait and before every
mutation, restore and publication admission. Gates serialize cooperating writers;
manual editors and independent remote actors can still change data. Any changed
index/file/ref/vault/configuration or generation invalidates the plan/backup:
retain it, recapture/reverify and renew confirmation for material changes. Never
clobber newer work to make the snapshot match. Fresh actual rights and effective
two-way mode are required for **every** shared USER write, including metadata,
vault creation, cleanup and rollback. For a legacy shared migration, effective
eligibility comes only from the scoped authority below. Confirmation alone
grants no remote rights.

### Legacy shared admission

A legacy candidate without v1 identity is resolved only by an explicit migration
request from the verified local account owner. Inspect the exact repository and
full ref and prepare the proposed v1 identity and vault changes in protected
staging. Allocate planned store IDs once in the reviewed plan. An already
identified logical store retains its ID. Connect alone opens no migration
authority and can register a shared checkout only after v1 is valid.

1. Capture the verified device/account, original checkout, isolated candidate
   and their filesystem/Git common-directory identities. Bind the planned store
   and checkout/connection generations, exact remote/ref, authenticated Git
   identity, expected remote base and permitted effects. Require explicit plan
   confirmation, verified backup/restore, vault access, legacy-writer exclusion
   and fresh remote write-rights evidence for that identity and ref. Missing,
   denied or ambiguous evidence stops before any target mutation.
2. Under the ordered gates and short device registry lock, reject overlapping
   active bindings or unresolved intents by verified filesystem and repository/ref
   identity as well as planned store ID. Durably claim those resources in the
   intent and install the drain fence before releasing the registry lock so
   another admission cannot claim them concurrently. Reconcile earlier attempts
   outside that lock. After drain and fresh
   revalidation, durably record the exact scoped authority in this migration
   transaction's protected manifest and replace the fence with migration
   ownership. Reserve the destination's planned store ID for the gate at and
   after registration. Candidate authority is resolved only from the verified
   device-local journal.
3. Resolve migration writes using that authority as their effective two-way
   eligibility. Under the same gates, recheck the intent, planned operation,
   owner, exact target/base, generations and current rights before each effect.
   An ordinary writer never inherits the authority. Publication also requires
   a durable reservation and a conditional update of the bound ref. A changed
   repo, ref, credential, checkout or base requires reconciliation and a new
   reviewed plan. Before the first effect, preserve the original unchanged.
4. At local cutover install the validated v1 binding with registry mode
   receive-only. Keep ordinary access fenced until the migration is reconciled.
   On an uncertain send or incomplete recovery retain ownership and scoped
   evidence and block new effects. Terminal settlement releases the scoped
   authority under the gates. Later two-way use requires its ordinary explicit
   mode transition.

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
The scoped admission checks exercise the verified intent and changed or missing
rights, binding and operation evidence. The three upgrade decisions cover a
fresh shared registration, an eligible legacy migration and a legacy writer
that still has access. The remote-policy decisions admit matching evidence
and stop on missing, incomplete or stale evidence for a shared legacy target.
Counterexamples
include reused paths, untested recovery, uncertain publication and unresolved
remote rights. Fixture observations are synthetic.

### Required dependencies kept outside the backup

When the recovery contract leaves a required input at its original location,
record the approved path, file identity, owner/access metadata and expected
contents in the protected manifest. Resolve the path from a trusted directory
and check the approved binding before parsing. Verify the effective SSH
settings from approved inputs only, following the
[store-scoped SSH resolution rules](store-identity.md#ssh-resolution-for-a-selected-host).
Recheck before selection while the migration gates are held. A missing, changed,
redirected or unusable input stops selection, identifies the affected
dependency and preserves the original. Independent writers can still change
external files after selection.

### Staged restore checkpoint

Run the [isolated restore fixture](fixtures/verify-migration-restore.py) with
Python 3, Git, `age`, `age-keygen` and OpenSSH available:

```bash
python3 -B docs/design/fixtures/verify-migration-restore.py
```

All seven cases passed on native arm64 macOS (Darwin 27.2.0, Git 2.42.1,
OpenSSH 10.5p1) and on arm64 Linux in Docker with networking disabled
(6.4.16-linuxkit, Git 2.54.1, OpenSSH 10.3p1), both with age 1.3.2.

- A complete age-encrypted archive restored two Git commits, a tag, local and
  remote-tracking refs and remote configuration, distinct HEAD/index/working
  tree contents, ignored and untracked files, app and SSH Include configuration,
  descendant file and directory modes, and a decryptable synthetic vault. The
  staged root was created with mode `0700`. Git observations used a scratch
  index so stat refreshes could not rewrite the restored index.
- An archive missing the Git index, damaged ciphertext, and an unavailable
  vault recovery identity each stopped before promoting the staged copy. The
  original tree's descendant inventory, bytes and modes matched the initial
  snapshot in every case.

- One required direct SSH `Include` remained outside the archive, inside the
  disposable workspace. The fixture verified the path and checked that
  OpenSSH resolved the synthetic endpoint from a private copy of the approved
  bytes. Removing the file, changing its contents or redirecting its path to
  a symlink with identical contents each blocked selection and preserved the
  original. With the file missing, the fixture also observed `ssh -G` exit
  successfully and return the alias as the hostname.
  [OpenSSH ssh(1)](https://man.openbsd.org/ssh)
  [OpenSSH ssh_config(5)](https://man.openbsd.org/ssh_config).

The manifest stayed in fixture memory. Nested/globbed includes, other
external dependency types, Hostbook's vault format, crash durability and
production cutover remain open.

### Restore permissions and ownership checkpoint

Capture mode, UID/GID and native ACLs in the protected recovery manifest for
the root and every restored entry. Preserve ACL order, effective mask and
inheritance where the platform uses them. Validate principal bindings on the
target. A changed owner/group or an ACL translation needs explicit review.
Apply the approved metadata in private staging, with the required ownership
authority, and read it back alongside the restored bytes before selecting
the candidate. A missing ACL facility, unresolved principal, failed
application or mismatch blocks promotion and retains the original and
recovery material. The writer-isolation checks still establish whether old
writers can replace or modify an otherwise matching candidate.

Run the [isolated metadata fixture](fixtures/verify-migration-metadata.py):

```bash
python3 -B docs/design/fixtures/verify-migration-metadata.py
```

The fixture copies a disposable directory and file with `cp -pR`, reads
mode and UID/GID back with `stat`, and compares native ACL output. macOS
uses `ls -led` for the test. Linux uses numeric `getfacl` output including
default directory entries. These tool invocations are fixture evidence.
[Apple filesystem ACL documentation](https://developer.apple.com/library/archive/documentation/OpenSource/Conceptual/ShellScripting/ShellScriptSecurity/ShellScriptSecurity.html)
[Linux getfacl(1)](https://man7.org/linux/man-pages/man1/getfacl.1.html).

Five cases passed on native macOS arm64 (Darwin 27.2.0, APFS) and Linux
arm64 Docker (6.4.16-linuxkit, ext4, GNU coreutils 9.11 and acl 2.3.2)
with networking disabled. A complete copy preserved bytes, mode, UID/GID
and native directory and file ACLs. Missing ACL, mismatched UID, mismatched
GID and unavailable ACL inspection each blocked selection and preserved
the original. Linux used a disposable ext4 volume with CHOWN and FOWNER,
and exercised a real nondefault UID/GID in the success case and real
ownership changes in the rejection cases. macOS compared actual same-owner
metadata against simulated differing UID/GID values in the rejection cases.

The fixture covers one directory and one child file on each platform.
Native macOS cross-account ownership restoration, protected archive
integration and production cutover remain open.

### Local cutover interruption checkpoint

Run the [isolated cutover fixture](fixtures/verify-migration-cutover.py):

```bash
python3 -B docs/design/fixtures/verify-migration-cutover.py
```

The fixture uses the [local replacement sequence](store-journal.md#local-selector-and-registry-replacement)
on native macOS arm64 (Darwin 27.2.0, APFS) and a writable ext4 volume in
network-disabled Linux arm64 Docker (6.4.16-linuxkit). On macOS it calls
`F_FULLFSYNC` on the file and parent directory. On Linux it uses `fsync`.
The candidate file is synced before replacement, and the parent directory
is synced afterward.

- SIGKILL before replacement leaves the original binding selected and the
  synced pending file unselected.
- SIGKILL after replacement and directory sync leaves the new binding
  selected before any completion record is written.
- Injected `EIO` at pending-file sync leaves the original binding selected.
  Injected `EIO` at the attempted replacement or during the final directory
  sync leaves the result uncertain. The old or new binding may be visible.
- Injected unsupported-sync errors before and after replacement block
  completion at both boundaries. Original and recovery fixtures are retained.

The SIGKILL cases start two fresh readers and verify original and candidate
bytes, the recovery marker and file metadata. Error cases check returned
status and immediately visible files. The Linux volume is backed by the
Docker VM's virtual disk. These checks leave Hostbook's actual recovery
journal, vault, remote publication and physical power-loss durability open.

The [registration contract](store-registration.md) defines the active binding.
The [acceptance map](store-acceptance.md) records current coverage and open work.
The [writer exclusion checkpoint](store-writer-validation.md) records the Linux
permission and local Git server experiments.
Runtime backup/restore, writer exclusion and system-crash durability remain open. [Journal/retention](store-journal.md) selects device-local authority,
protected payload layout and collection policy.
Downstream #36–#46 still own runtime migration and isolated regression coverage.
