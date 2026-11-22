# Device-local transaction journal and recovery retention

Status: **proposed**, documentation only; sixth bounded [#35](https://github.com/duong6003/hostbook/issues/35)
slice under [#33](https://github.com/duong6003/hostbook/issues/33). Extends [identity](store-identity.md),
[transfer](store-transfer.md), [mutation ownership](store-mutations.md), [migration](store-migration.md) and
[sidecar v1](store-format.md). This selects a representation and collection policy, not implementation or
completion of #35.

## One authority outside stores

Choose a private **device/account recovery root**, outside every checkout, Git directory, shared/synchronized
folder and export tree. Defaults are `~/.local/state/hostbook/recovery-v1` on Linux, `~/Library/Application
Support/Hostbook/Recovery/v1` on macOS. [Registration](store-registration.md) must
verify this boundary, ownership and confidentiality before use; an unsafe/unverifiable root blocks mutation, never
falls back inside a store. Do not reuse an arbitrary `HOSTBOOK_STATE` override without these checks. Nothing here
is tracked, synced, included in `.hbshare`, or copied by ordinary store export/checkout relocation.

```text
recovery-v1/
  root.json                         # version, device_id, root_id (UUIDs)
  transactions/<transaction_id>/
    current.json                    # sole authoritative metadata selector
    records/<sequence>.json         # immutable complete metadata revisions
    payloads/<payload_id>.age        # immutable encrypted generations
    recovery/<vault_generation>/    # vault.pub, vault.key.age, vault.recovery.age
  control/<record_id>/              # device-local publication/transition records
    current.json
    records/<sequence>.json
  stores/<store_id>/<transaction_id>.ref   # version + transaction_id only
```

One registered root per device/account is authoritative; registrations bind it to the device/account independently
of copied `root.json`. A copied root/transaction does not establish that binding. Duplicate roots/registrations
block automatic resumption until explicit owner/attempt reconciliation; no device silently adopts another device's
journals. Other devices see shared content, not these intents, reservations or recovery payloads; local gates are
no distributed exclusion.

Both stores reference **one** transaction, never independent source/destination journals. Under ordered store
gates, inspect authoritative nonterminal records for overlapping store/host keys before creating an intent; a
missing/stale `.ref` cannot permit another retry. Provisional migration resource
claims also bind all registration and connection admissions under the short
device registry lock, even if the competing store ID differs and the claim has
no registry row. Check verified checkout/Git common directory and repository/ref
identity in addition to store/host keys. Only the owning migration may advance
its exact claim under fresh admission checks. References are repairable lookup hints only. Resolve UUID
components beneath the verified root, reject links/path escapes and verify record store membership. Never follow a
path supplied by shared content. Scan authority as the fallback; malformed/unreadable authority blocks affected
work (the entire root if affected IDs cannot be determined), preserving unrelated data.

## Publication and registration control records

Ordinary publication and registration changes use the same verified recovery
root, but do not create a transfer or migration vault. The resolver scans both
`transactions/` and `control/` for unresolved attempts and fences before admission,
including when registry pointers or hints are missing. An unreadable control
record blocks the affected work, or the root if the affected IDs are unknown.

Control records use the same durable sequence and selector rules below. Their
selector uses `record_id` in place of `transaction_id`. Each revision has exactly
`format: "hostbook-control"`, `version: 1`, `record_id`, `device_id`, `root_id`,
`sequence`, `previous_sequence`, `store_id`, `owner`, `operation`, `detail`.
IDs, sequence and owner checks have the same meaning as in transaction records.
These records affect one store and have no required host IDs.

| Operation | Exact fields in `detail` and progression |
| --- | --- |
| `publish` | `attempt_id`, `connection_generation`, `connection_binding_id`, `base_oid`, `proposed_oid`, `sender_state`, `outcome`. The Git object IDs use the registered repository's object format. Only creation of a durable reserved attempt admits a send. Record uncertain outcomes, then settle accepted or not-accepted once both outcome and sender quiescence are established. Never reuse an attempt. |
| `registration-transition` | `old_binding_id`, `proposed_binding_id`, `phase` (`fenced`, `applied`, `cancelled`). The immutable local binding references identify the exact old and proposed registry tuples, including requested settings and generations. A durable `fenced` record is itself the fence, even before the registry points to it. Only a drained fence and a verified registry replacement allow `applied`. Only reconciliation confirming the old binding remains effective allows `cancelled`. |

These owner-only control records store opaque references and Git object IDs,
without checkout paths, remote URLs, settings text or credentials. Verified,
protected local binding records resolve exact targets and configurations by ID.
Before admission, persist an immutable connection binding that fixes the
transport and target, exact full branch ref, authentication binding and
connection generation. Settlement uses that binding independently of the
current registry. Keep original bindings while their attempts can require
recovery. Ordinary sync does not depend on unlocking a store
or transaction vault. Sensitive publication or recovery evidence still needs
protection appropriate to its content. Transfer and migration attempts keep
their original encrypted-manifest representation.

After an interruption, compare a fenced transition's old/proposed binding to the
actual registry entry and reconcile all admitted attempts before settlement.
An installed new binding without terminal evidence remains fenced until verified.
An unreferenced fenced record is not an abandoned request. Record its outcome
before clearing a registry pointer. Retain terminal records for reporting, with
no replay through a stale pointer.

## Exact metadata v1

JSON uses unique keys and exact fields/types below; reject unknown fields/version, duplicate IDs, missing fields,
non-JSON suffixes and invalid state transitions. All IDs are independently generated canonical lowercase UUIDv4 as
in sidecar v1; retries preserve transaction/store/host IDs. Positive integers never wrap/reuse. Journal version 1
does not alter `.hostbook/identity.json` version 1 or its source bindings, generation and exact non-secret
source-byte revalidation requirements.

| Object | Exact fields and meaning |
| --- | --- |
| Root | `version:1`, `device_id`, `root_id`. IDs identify local registration, not authority from possession. |
| Selector | `version:1`, `transaction_id`, `sequence` identifying one complete durable record. |
| Record | `format:"hostbook-transaction"`, `version:1`, `transaction_id`, `device_id`, `root_id`, `sequence`, `previous_sequence`, `operation`, `checkpoint`, `owner`, `stores`, `hosts`, `payload_id`, `recovery_generation`, `effects`, `attempts`, `collection`. |
| Sequence/operation | `sequence` starts at 1; `previous_sequence` is 0 initially, otherwise the previous sequence. `operation` is `transfer` or `migration`; compensation remains in that intent. |
| Checkpoint | Exactly the applicable transfer/migration checkpoint names; `blocked` is an outcome on an effect/attempt, not a success checkpoint. No generic workflow interpreter. |
| Owner | `owner_id` UUID, `process_id` positive integer, `process_start_id` UUID, `boot_id` UUID; registration maps these to independently verified live/dead process evidence. PID equality or age is insufficient. |
| Stores | Nonempty array of unique objects: `store_id`, `role` (`source`, `destination`, `affected`), `snapshot_id`, `checkout_generation`, `connection_generation`, `identity_generation`, `vault_generation`. Generations positive; identity/vault generation is 0 only before sidecar/vault exists. |
| Hosts | Unique objects with `store_id`, `host_id`; empty only for migration before host identity admission, whose mapping remains protected. |
| Effects | Ordered unique objects: `effect_id`, `store_id`, `intent_id`, `evidence_id`, `outcome`. IDs reference protected manifest entries; `evidence_id` is null before evidence. `outcome` is `intended`, `observed`, `blocked` or `reconciled`. |
| Attempts | Unique objects: `attempt_id`, `store_id`, `owner_id`, `connection_generation`, `reservation_id`, `target_id`, `intent_id`, `evidence_id`, `sender_state`, `outcome`. Manifest references bind the exact target/base/proposal; evidence nullable. Sender is `active`, `unknown` or `quiescent`; outcome is `reserved`, `uncertain`, `accepted` or `not-accepted`. |
| Collection | `decision_id` nullable protected reference, `payload_state` (`retained`, `collection-intended`, `collected`), `payload_ids` (unique UUID array), `recovery_generations` (unique positive integer array). Lists are empty until collection intent, then bind exact approved local deletions; retain them and decision UUID, not sensitive text. |
| Payload reference | `payload_id` UUID, null only when collected; `recovery_generation` positive integer selects this transaction's recovery envelopes independently of store vault generations. |
| Store reference | `version:1`, `transaction_id`; pathname must match both validated UUIDs. |

Actual paths, aliases, addresses, URLs/ref names, settings, working-tree/index/ref snapshots, exact revisions,
credential names/mappings, vault public identities and rights observations belong in the **encrypted manifest**,
addressed by the UUIDs above. No addresses/settings, keys, passwords, recovery material or secret-derived digests
in metadata, logs, argv, environment, UI or plaintext temporary files. Metadata includes no content fingerprints;
snapshot UUIDs are references, not proofs. UI may show opaque IDs and bounded outcomes; decrypt comparisons
securely.

## Protected payload and independent recovery access

Choose one separate **transaction recovery vault**: a new age identity protected by password and recovery envelopes
using the existing vault encryption primitives (`vault.pub`, `vault.key.age`, `vault.recovery.age`). This is not
the source or destination vault identity, their password, an unlock cache, or custom crypto. Each payload
generation is age-encrypted to exactly that recovery identity's public recipient; retain its complete envelope
generation beside the ciphertext. The existing credential transport wrappers are not approved by this design:
implementation must pass secret input through protected streams/descriptors, never environment/argv. Failure to
provide that path blocks implementation.

The encrypted manifest binds transaction/device/root IDs, sequence, payload ID, all snapshot/effect/reservation IDs
and the full confirmed plan. It contains exact original/proposed bytes, local durability observations (owned
generation, file and directory flush outcomes, exact post-write comparison), remote target/ref, expected
base/proposed revision, sender ownership/transport evidence and acceptance facts. It contains complete migration
recovery state required by the migration contract, not just commits or diffs. Transfer retains the original
host/settings, credential mapping/material and reference context. Sensitive comparisons stay inside this
protection; neither ordinary hashes nor secret-derived hashes replace access checks.

Preserve original source/destination vault ciphertext, public identities and password/recovery envelopes for
**every referenced old generation**, plus required credential material re-encrypted into this payload. Preserve an
old private vault identity inside the payload when needed to decrypt retained original/history ciphertext.
Capturing it requires that vault's authorized unlock; never substitute another store's identity. Independent
source/destination vaults remain independent. Recovery access therefore survives source disconnection/deleted
files/rekey and restart without assuming any unlock cache survives. Do not discard originals.

Before migration's first mutation, and before transfer's first effect, securely reopen the recovery vault from its
retained envelopes without relying on cache; decrypt/compare the complete payload and exercise migration's isolated
restore in encrypted staging. Require a usable operator-held password or recovery code independent of source
access. Before source removal also unlock the intended destination vault and verify its current credential
mapping/decryptability. Locked/missing required generation, failed encryption/access or incomplete backup stops
progress with original/recovery intact; never create replacement source identities or plaintext. Rekey of the
recovery vault requires a verified new envelope/payload generation, then the same durable selector protocol; retain
old generations until safe collection.

## Durable update and interruption rules

Create owner-only directories and files on macOS and Linux, reject symlink
substitutions and verify ownership on reopen. Serialize the transaction with an owner-checked lock
**after** common store gates in canonical store-ID order and subordinate vault/recovery locks in that order. Before
legacy IDs exist use migration's provisional ordering. Hold short gates only for validation/admission/local
replacement, never prompts or network waits. No age-based lock/reservation takeover; prove owner death and sender
quiescence separately, then reconcile exact effects. A dead process is not a rejected send.

For each intent/evidence update, stage only ciphertext and non-secret metadata in owner-only files on the same
filesystem. Flush encrypted payload and needed recovery envelopes to durable storage, then their directory entries;
write/flush immutable `records/N.json`, then its directory. Finally write/flush a new selector, replace
`current.json` atomically and flush its directory. Only then may an intent authorize its effect; save evidence in a
subsequent generation after the effect. Advance per-store references afterward; they never select a different
record. An implementation unable to establish these durability steps must stop before the effect. This is ordered
persistence, not atomic disk/vault/remote commit.

A flush failure before attempting replacement leaves the old authority selected.
Retain any staged state and stop. If replacement was attempted and acknowledgement
or directory sync fails, the outcome is uncertain. A readable new selector or
registry binding is an observation. Keep the durable intent, fences and protected
recovery, and block dependent writes, publication and cleanup. Under the owning
gates, reconcile the exact observed binding and retained payload, re-establish
durability and persist verified evidence before advancing. If that proof remains
unavailable, keep the operation blocked. File and directory synchronization are
separate steps. [Linux fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html).

On restart, validate selector/retained metadata chain, decrypt referenced payload and compare manifest IDs/sequence
and current snapshots. Valid unselected files are orphans, never automatically a newer authority. Torn/missing
selector, unexpectedly missing payload, unknown version, sequence gap, copied identity or mismatched manifest blocks
replay/deletion; preserve all candidates for explicit recovery. A durable intent without evidence requires
observation of the exact effect, not blind replay. Unselected recovery generations may contain the only post-crash
evidence: inspect them during reconciliation; do not delete by age or automatically promote them.

Publication admission/transition fences and sender settlement follow the [transfer
barrier](store-transfer.md#publication-admission-and-transition-barrier) unchanged. Persist the exact
reservation/target in this authority before send. Migration durably hands a fully drained fence to scoped migration
ownership; its sends reserve under that ownership after the fence is replaced, never through a pending fence.
Unknown/lost acknowledgement preserves reservation and ownership, blocks effective transition/replay/source
deletion/collection, and requires exact remote reconciliation plus current content checks. Rejection/offline is not
success. USER guards apply at every shared write, including restore/cleanup. New shared edits require successful live
preflight, and transfer/migration retain their stricter admission rules. A journal cannot grant rights or bypass a
changed sidecar/source snapshot.

## Retention and collection decision

Unresolved, blocked and uncertain-publication transactions **never auto-expire**. Terminal records retain
indefinitely a minimal tombstone: original transaction, store/host/attempt IDs, generations, sequences, terminal
checkpoint, settled outcome and collection decision ID. This recognizes retries without recreating effects; without
payload, only report the outcome, never replay from the tombstone.

Payload collection is an explicit per-transaction operator decision, not a timer: verify completed or fully
rolled-back/cancelled effects, quiescent settled senders, fresh source/destination/reference observations, and
absence of unresolved rollback, history-decryption or other recovery dependencies. Reconfirm material changes.
Retain payload/envelopes while any dependency remains; cancellation alone is no proof. Record collection intent
durably with its exact non-secret deletion lists, remove only listed intent-owned encrypted generations/envelopes,
flush directories, then record `collected`. Keep the current encrypted decision and its envelopes until last.
Crash reconciles present/absent files against those lists; no directory glob. If that decision is already gone,
only acknowledge all-listed-files-absent as collected; retain any leftovers pending reviewed recovery, no replay.
Revalidate references immediately before deletion under gates; keep credentials referenced by any source host.
Never erase newer/unrelated work to pass collection.

Local recovery-file collection is LOCAL; shared host/vault/metadata cleanup is a separate USER mutation requiring
current two-way mode/actual rights and publication evidence. Local collection does not revoke credentials, erase
remote history or promise secure physical erasure. Keep the small non-secret current tombstone; all metadata
revisions remain non-secret and retained. A collected terminal record has no payload; its recorded collection
decision explains removed older payloads, so report-only lookup remains valid without pretending those manifests
still exist.

## Ten independent synthetic decisions and limits

| Case | Required decision |
| --- | --- |
| 1. Source and destination references find the same UUID | Load one authority; no cross-store duplicate intent/retry. |
| 2. Missing `.ref`, active authority for that host | Scan authority, repair hint under gates; block a second transaction. |
| 3. Unknown version, malformed selector or sequence gap | Preserve candidates and block affected replay; never regenerate IDs. |
| 4. Journal copied to a second device/root | No inherited ownership/attempt authority; explicit reconciliation, no automatic resume. |
| 5. Payload flushed, selector still old after crash | Preserve orphan; observe effects and reconcile, never promote/replay by sequence alone. |
| 6. Selector points to missing/mismatched encrypted payload | Block effects/collection; recover verified candidate, no plaintext fallback. |
| 7. Source rekeyed/disconnected/files deleted after removal | Reopen separate recovery envelopes; recover retained old generations/material without cache. |
| 8. Destination locked or source snapshot changed before removal | Preserve source/recovery; unlock/revalidate/reconfirm before any removal. |
| 9. Lost acknowledgement while transition fence drains | Keep exact reservation/fence pending despite owner death/age; no resend/delete/collection. |
| 10. Completed move but credential referenced by another source host | Retain dependency; local payload decision cannot authorize shared credential deletion. |

Static documentation checks (repository root):

```bash
git diff --check -- docs/design/store-*.md
python3 - <<'PY'
import pathlib, re
root = pathlib.Path('docs/design')
for p in root.glob('store-*.md'):
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', p.read_text()):
        if '://' not in target:
            assert (p.parent / target.split('#')[0]).exists(), (p, target)
s = (root / 'store-journal.md').read_text()
assert len(re.findall(r'^\| \d+\.', s, re.M)) == 10
print('store journal documentation: PASS (links, 10 cases)')
PY
```

These checks do not exercise encryption, crash recovery, remote settlement or ACLs. macOS/Linux durability and permission
mechanics remain unverified. The [acceptance map](store-acceptance.md) tracks concrete
upgrade verification and restoration primitives. The first-transfer scope defers
different-ID merge.
Downstream #36–#46 own implementation and isolated runtime checks.
