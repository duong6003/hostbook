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

### SSH resolution for a selected host

On main `6310dcdd8bd2ec9f753fc6673079f367c4848cf0`, the index uses
`ssh -G` under one process-wide SSH configuration and preview selects the
first indexed row for an alias. Connect resolves credentials and launches
SSH by that same alias. In an isolated macOS probe, the real `_preview h:db`
showed the shared row's description with the private endpoint, user and jump
when both stores were included. With a shared-only Include it showed the
shared endpoint, user and jump.
[Index and preview](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L303)
and [connect](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L1472).

For [#37](https://github.com/duong6003/hostbook/issues/37), use an explicit
`ssh -F` view for a selected shared store. Assemble it from that store's
validated configuration and the approved device-local SSH inputs, with
deterministic Include order and existing SSH precedence. Keep managed
configuration from other stores out of the view. An overlapping device-local
alias must not override a managed alias unless that override was explicitly
approved for this store. Include applicable approved defaults explicitly because `-F` bypasses the default SSH config lookup.
OpenSSH resolves the original alias and existing jump values in this view,
including standalone jump settings supplied by approved local configuration.
Keep wildcard precedence, jump chains and host-key verification.
Use a private snapshot of the approved inputs for the launch and its jump
children. The generated path must survive OpenSSH's implicit ProxyJump
command as one argument, including when a checkout path contains spaces.
See [ssh(1)](https://man.openbsd.org/ssh) and
[ssh_config(5)](https://man.openbsd.org/ssh_config).

- Preview reads the store-scoped derived row by `(store_id, host_id)`. Its
  effective settings come from the same configuration inputs used for
  connection. The cache must invalidate when those inputs change.
- On explicit Connect, verify the selected key, registration, checkout and
  connection generations, current configuration inputs and applicable
  credential binding. Repeat the check after prompts and before launch.
  A stale selection requires a fresh choice before starting SSH. A lost
  shared binding cannot fall back to a private host with the same alias.
- Resolve credentials in the selected context and carry it to installed
  callbacks and jump children. Constrain the effective agent and identity
  files for each destination and hop to its bound credentials or explicitly
  approved device-local authentication. Disable ambient agent and default
  identity fallback. Disable control-socket reuse (`ControlPath none`) for
  the shared launch and its jump children to keep other sessions' credentials
  out of the connection. If an askpass prompt cannot be attributed to the
  planned destination or a known hop, use an interactive prompt without
  supplying a saved secret. Use only the approved SSH input set.
  Synchronized text must not silently enroll external Include paths or
  local command directives. Preview must never run those commands, wait for
  SSH or open a connection.

Run the [isolated resolution fixture](fixtures/verify-store-ssh.py) with
Python 3 and OpenSSH:

```bash
python3 -B docs/design/fixtures/verify-store-ssh.py
```

Passed on native arm64 macOS (Darwin 27.2.0, OpenSSH 10.5p1) and in a
network-disabled arm64 Linux container (LinuxKit 6.4.16, OpenSSH 10.3p1).
The fixture resolves duplicate `db` and `relay` aliases, a wildcard port and
an approved local jump using real OpenSSH with the agent and default identities
disabled and an isolated known-hosts file. A stub intercepts the first jump
child before it opens a socket and verifies the exact `-F` argument and
forward target. A separate model check rejects a changed connection
generation, changed config and disconnect before handing a config to SSH.
The fixture does not exercise Hostbook's future resolver or vault, actual
authentication, a completed jump chain or installed callbacks.

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
For a shared store, an edit admitted after successful online preflight can
remain pending if the send fails. The first publication accepted at the bound
remote/ref advances the shared state. Use the existing [publication
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
if not __debug__:
    raise SystemExit("This check requires Python assertions. Remove -O and PYTHONOPTIMIZE.")

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

A cross-store transfer moves a host. Creating a destination record keeps the
host ID while changing the store component of its key. An explicit update of
an existing record keeps that destination's ID and name. Store-bound
references follow the [source-removal checks](store-transfer.md#source-references-before-removal).
An old source key must not silently resolve by alias in another store. Cross-store move semantics differ from the
current move between project files. The source is removed only after destination
success is established; [Cross-store transfer transaction](store-transfer.md)
defines the proposed ordering, publication evidence and recovery contract.

Retry recognition uses the recorded transfer intent and both (store ID, host ID)
keys. If the destination already has that host ID,
classify it as the same-identity candidate: reconcile the prior operation and
content, never create a second record or silently overwrite. ID equality alone
does not prove that a transfer committed or that the source may be deleted. A
different payload with that ID is a collision/conflict requiring an explicit
decision, not permission to replace it. Durable retry evidence, concurrency and
crash recovery are specified in the [existing-destination rules](store-transfer.md#existing-destination-host).

Different IDs with equal effective (address, port, login) are **potential
duplicates**, not the same identity. Use resolved SSH values (including defaults)
for this comparison; do not equate DNS aliases through a network lookup.
Compare both records and offer [updating the chosen destination](store-transfer.md#update-an-existing-destination-with-a-different-id),
keeping both with distinct valid names, or cancelling. A matching alias with
different IDs requires a free alias when creating a separate destination record,
even when endpoints differ. Changes to identity, name or existing records
require explicit review. Credential
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
The [acceptance map](store-acceptance.md) tracks the design gates and the
parent fixtures. Downstream #36–#46 own the runtime store resources, migration
verification and regression suites.
