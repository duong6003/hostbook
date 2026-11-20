# Store and host identity

Status: **proposed**, documentation only; first design slice of [#35](https://github.com/duong6003/hostbook/issues/35)
under [#33](https://github.com/duong6003/hostbook/issues/33). Baseline: `718890f697f0460999f1a8ae2d6a684f505a3d66`.
This does not implement the contract or complete #35. Current behavior remains
described in [Sync and sharing](../wiki/Sync-and-sharing.md).

## Scope and terms

The model supports one private store and multiple independent shared stores. The
first UI may expose private plus one shared store; that limit must not become an
identity assumption. New hosts and imports default to private. Each store owns
its vault, cache, locks, synchronization status and mode. Shared stores default
to receive-only; identifying a host never grants permission to modify its store.

A **store ID** identifies the logical store, independent of its display name,
checkout path, Git remote URL or private/shared role. Another checkout of the
same shared store retains its ID. Creating an independent store, including a
fork intended to diverge independently, requires a new store ID through an
explicit operation; changing its path or remote alone does not create one.

A **stable host ID** identifies a logical host record, independent of its alias,
project/file location, address, port, login or credentials. New stores and host
records receive independently generated opaque UUIDs, using canonical lowercase
UUID text in this proposed contract. Never derive IDs from those mutable fields,
file order or content hashes. IDs are identifiers, not secrets or authorization.

The lookup key is **(store ID, stable host ID)**. Host IDs should be generated
with global uniqueness, but equality across stores can intentionally record
transfer lineage. It does not make those stores interchangeable or authorize
merging them. A host ID must be unique within a store. A store ID registered at
two paths represents one logical store, not two independent choices: surface the
duplicate registration and require explicit selection of the active checkout.
Never silently pick a path or merge divergent checkouts.

## Names and resolution

An alias is a mutable SSH-facing name, not an identity. Concrete managed aliases
use the existing `A-Za-z0-9._-` character set. Names must be unique within each
store using ASCII case-insensitive comparison, consistent with SSH host matching;
equal aliases in different stores are valid. Preserve the display spelling.
Wildcard and negated `Host` patterns and `Match` rules are SSH configuration,
not additional managed host IDs.

Search results carry the complete lookup key. Preview, connect, groups, history
and later mutation requests use that key; aliases and row positions are labels.
A rename preserves the key, as does moving between projects/files in one store.
Before acting, revalidate that the selected key still exists in the selected
store. A stale selection must not fall back to another record with the old alias.
Bare alias lookup requires a store context or an explicit choice when multiple
stores match. Store labels may help the user choose but are not lookup keys.

Connection planning must resolve the selected record in its store and preserve
its effective SSH settings. Aggregating all stores into one SSH `Include` and
taking the first matching alias is invalid: selecting shared B's `web` must not
connect to private's `web`. The mechanism for isolating resolution is deferred;
it must preserve supported SSH directives, wildcard precedence and existing
`ProxyJump` behavior. This proposal adds no store-specific restriction on jump
hosts, rewrites no jump chain and never transfers dependencies automatically.
SSH's normal resolution of an existing `ProxyJump` value must not be confused
with choosing a Hostbook search result by the first matching alias.

## Versioning and compatibility

Call these logical semantics **identity contract version 1**. The JSON examples
below use `contract_version` to label that proposal; they are documentation
fixtures, not a new production file format, parser specification or share-package
version. The subsequent [format and admission proposal](store-format.md) selects a
versioned sidecar; it is proposed separately from these original logical fixtures.

Persistent identity must survive restart, rename, file moves, sync and transfer.
A chosen representation will need a separately versioned encoding with an
explicit upgrade path. Missing metadata is legacy/unresolved identity, not a
license to infer IDs from aliases on each load. Unknown versions, malformed IDs
or ambiguous mappings must be reported and must block identity-dependent actions
on the affected records; do not discard metadata, downgrade it or silently
reassign IDs. Unrelated valid stores remain usable.

Ordinary SSH configuration and existing v1 `.hbshare` packages remain supported.
An identity-aware implementation must accept legacy input through a reviewed
admission/migration path, defaulting imports to private, without changing SSH
directives or treating identity-less input as a known transfer retry. Existing
package validation must not be bypassed to add identity fields. A future package
extension needs version negotiation and old-reader behavior; legacy exports may
omit identity only with an explicit loss-of-lineage explanation. Neither an
unrecognized package member nor a new required comment is silently assumed
compatible with today's readers.

The [format proposal](store-format.md) compares comments and sidecars and chooses
a guarded sidecar with explicit occurrence bindings. It maps multi-alias stanzas
to concrete selection names while preserving every contributing SSH block.
Association loss, manual changes and incompatible writers require explicit
reconciliation; generated aliases and silent block rewriting are not repairs.

## Invalid data and manual edits

Two distinct records with one host ID in a store are invalid even if their
content is identical. Two records with the same normalized alias are invalid
even if their IDs differ. Do not pick the first record, merge them or regenerate
one ID while reading. Report the conflicting records and block operations that
would resolve through the ambiguity until an explicit repair is reviewed.

Manual edits are supported input. Renaming a host while retaining a valid,
unambiguous identity association preserves the ID. Changing address, port or
login also preserves it. Copying a record with its ID creates a duplicate-ID
error; the user must choose whether to restore the original or explicitly create
an independent host with a new ID and a unique alias. Removing or corrupting
identity metadata does not silently turn a formerly managed host into a new one.
Where a manual edit makes association uncertain, require reconciliation rather
than guessing from filename, line number, alias or address. Reconciliation that
writes data must obey the store mode, including receive-only defaults.

## Concurrent revisions of one host

Each `(store_id, host_id)` has one accepted current revision. An edit carries the
revision it started from. After the usual [write checks](store-mutations.md#proposed-admission-rules),
accept an edit only if its base still matches the current revision for that key.
For a shared store, a local save can be pending. The first publication accepted
at the bound remote/ref advances the shared state. Use the existing [publication
protocol](store-transfer.md#publication-admission-and-transition-barrier) and
revalidate against fresh remote state if the branch tip moved. A different host's
edit can be merged if the target host's revision and other preconditions still hold.

When the same host has advanced, preserve the accepted state and the pending edit
for comparison. Require the user to choose or combine the changes before retrying
with the current revision as the new base. A retry must pass the same checks again.
The two versions represent one logical host. Duplicate live records for one key
remain invalid. The revision is distinct from the sidecar's schema `version` and
its store-wide `generation`. The numbers below are illustrative. Durable revision
encoding and the conflict UI belong to downstream work.

Run this documentation fixture with `python3`. It models only the revision
decision after authorization, with no file, checkout or network access.

```python
current = {('shared-a', 'db-id'): (7, 'original'),
           ('shared-a', 'web-id'): (3, 'original'),
           ('shared-b', 'db-id'): (4, 'original')}

def apply(key, base, edit):
    revision, accepted = current[key]
    if base != revision:
        return ('conflict', (revision, accepted), (base, edit))
    current[key] = (revision + 1, edit)
    return ('accepted', current[key])

db = ('shared-a', 'db-id')
assert apply(db, 7, 'A') == ('accepted', (8, 'A'))
assert apply(db, 7, 'B') == ('conflict', (8, 'A'), (7, 'B'))
assert current[db] == (8, 'A')
assert apply(('shared-a', 'web-id'), 3, 'C') == ('accepted', (4, 'C'))
assert current[('shared-b', 'db-id')] == (4, 'original')
assert apply(db, 8, 'reviewed A+B') == ('accepted', (9, 'reviewed A+B'))
print('PASS: stale same-host edit preserved, independent hosts unaffected, reviewed retry accepted')
```

## Transfer identity and duplicate decisions

A cross-store transfer **moves**, rather than copies, a host. It preserves its
stable host ID while changing the store component of its lookup key. Store-bound
references must be reconciled explicitly; an old source key must not silently
resolve by alias in another store. Cross-store move semantics differ from the
current move between project files. The source is removed only after destination
success is established; [Cross-store transfer transaction](store-transfer.md)
defines the proposed ordering, publication evidence and recovery contract.

Retry recognition uses the preserved host ID, the source/destination store IDs
and recorded transfer intent. If the destination already has that host ID,
classify it as the same-identity candidate: reconcile the prior operation and
content, never create a second record or silently overwrite. ID equality alone
does not prove that a transfer committed or that the source may be deleted. A
different payload with that ID is a collision/conflict requiring an explicit
decision, not permission to replace it. Durable retry evidence, concurrency and
crash recovery are specified in the proposed transfer transaction contract.

Different IDs with equal effective (address, port, login) are **potential
duplicates**, not the same identity. Use resolved SSH values (including defaults)
for this comparison; do not equate DNS aliases through a network lookup. Offer
an explicit choice to keep both with valid names, reconcile with the existing
record, or cancel. A matching alias with different IDs is likewise a name
collision requiring a choice, even when endpoints differ. Never silently mint a
new ID, rename, duplicate or overwrite to make a transfer succeed. Credential
names are scoped to the destination vault; a name match must never silently
overwrite credentials or prove credential equivalence. Credential mapping and
any reuse/rename decision need explicit review without exposing secret material.

## Synthetic examples and remaining work

[Store mutation and resource ownership](store-mutations.md) maps current writer
families to proposed per-store authorization and resource isolation. Its synthetic
permission examples do not assert that the current single-store code enforces them.
[Legacy store migration and recovery](store-migration.md) defines classification,
protected backup, interruption/rollback and old/new writer compatibility gates.

All fixture values are synthetic; addresses use documentation ranges. The files
describe expected decisions, including deliberately invalid cases. They contain
no credentials and must not be loaded as runtime configuration.

| Fixture | Contract examples |
| --- | --- |
| [stores.json](fixtures/stores.json) | Private plus two shared stores; equal aliases across stores and selection by complete key. |
| [rename.json](fixtures/rename.json) | Rename preserves identity; stale alias does not select a replacement host. |
| [invalid-records.json](fixtures/invalid-records.json) | Same-store duplicate aliases, case variants and duplicate IDs are rejected. |
| [transfer.json](fixtures/transfer.json) | Preserved transfer identity, retry, conflicting same ID and independently created potential duplicate. |

The [format and admission slice](store-format.md) selects durable v1 identity
encoding and legacy SSH/package admission, including multi-alias mapping.
The [journal/retention slice](store-journal.md) selects device-local transaction
authority, protected recovery payloads and explicit collection policy.
Within #35, still deferred:
finish the implementation-level write-boundary audit beyond the bounded mutation
inventory; specify durable cache/vault/lock/status and SSH resolution mechanisms;
and complete the parent acceptance-to-fixture mapping. The migration contract
leaves concrete backup/restore, writer isolation and upgrade representations open.
Runtime implementation and regression suites belong to downstream #36–#46,
not this design slice or #35. No fixture asserts that these behaviors exist at
the baseline.
