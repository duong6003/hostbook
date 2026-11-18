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
version. No metadata filename, SSH comment spelling, generated alias or sidecar
layout is selected here.

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

SSH ignores comments, which makes them a possible encoding, but editors and
exporters can strip them. A sidecar avoids altering SSH blocks but can lose its
association after manual edits. Generated aliases could avoid collisions but
would change SSH-visible names and references. A representation decision must
show how these compatibility problems are handled before implementation. Legacy
multi-alias `Host` stanzas also need an explicit mapping policy; do not silently
collapse their aliases into one identity or split/rewrite their SSH behavior.

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

All fixture values are synthetic; addresses use documentation ranges. The files
describe expected decisions, including deliberately invalid cases. They contain
no credentials and must not be loaded as runtime configuration.

| Fixture | Contract examples |
| --- | --- |
| [stores.json](fixtures/stores.json) | Private plus two shared stores; equal aliases across stores and selection by complete key. |
| [rename.json](fixtures/rename.json) | Rename preserves identity; stale alias does not select a replacement host. |
| [invalid-records.json](fixtures/invalid-records.json) | Same-store duplicate aliases, case variants and duplicate IDs are rejected. |
| [transfer.json](fixtures/transfer.json) | Preserved transfer identity, retry, conflicting same ID and independently created potential duplicate. |

Within #35, still deferred: select and version the durable identity encoding;
map legacy SSH and share-package admission/migration (including multi-alias
stanzas); select the transfer journal representation and recovery retention policy;
finish the implementation-level write-boundary audit beyond the bounded mutation
inventory; specify durable cache/vault/lock/status and SSH resolution mechanisms;
and complete the parent acceptance-to-fixture mapping. Migration/backup/rollback
design and old/new writer compatibility remain #35 contracts to define.
Runtime implementation and regression suites belong to downstream #36–#46,
not this design slice or #35. No fixture asserts that these behaviors exist at
the baseline.
