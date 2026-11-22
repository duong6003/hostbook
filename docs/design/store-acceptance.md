# Store design acceptance map

This is the #35 checkpoint for [the parent feature #33](https://github.com/duong6003/hostbook/issues/33).
The current comparison target and completed writer inventory use main `6310dcd`.
The draft branch still descends from `718890f`. The existing examples are synthetic design checks.
The runtime fixture column describes evidence still to collect.

## #35 design gates

| Gate | Evidence in the draft | Remaining work before #35 can close |
| --- | --- | --- |
| Stable identity, versions, duplicates, manual edits and transfer | [Identity](store-identity.md), [sidecar v1](store-format.md), [rename](fixtures/rename.json), [invalid records](fixtures/invalid-records.json), [transfer](fixtures/transfer.json) and [store-aware SSH resolution](store-identity.md#ssh-resolution-for-a-selected-host), with [existing-destination transfer rules](store-transfer.md#existing-destination-host), [source references](store-transfer.md#source-references-before-removal) and [destination routes](store-transfer.md#destination-routes-before-writing). | The first transfer scope is recorded below. Different-ID merge is deferred. |
| Store resources and write boundaries | [Mutation and resource inventory](store-mutations.md), [callback contexts](store-registration.md) and [permission decisions](fixtures/mutation-permissions.json). | Managed writers are mapped at [current main](store-mutations.md#file-level-write-boundaries-on-current-main). Carry the map into final design review and downstream enforcement. |
| Transfer states, concurrency and recovery | [Transfer contract](store-transfer.md), [recovery cases](fixtures/transfer-recovery.json) and [journal](store-journal.md). | Validate complete transaction durability and restoration. The [local replacement sequence](store-journal.md#local-selector-and-registry-replacement) and the [recovery passphrase pipe](store-journal.md#recovery-passphrase-transport) have isolated tool evidence. |
| Migration, backups, rollback and old writers | [Upgrade boundary](store-migration.md#first-supported-upgrade), [migration cases](fixtures/migration-decisions.json), [writer exclusion checkpoint](store-writer-validation.md), [staged restore checkpoint](store-migration.md#staged-restore-checkpoint), [restore metadata checkpoint](store-migration.md#restore-permissions-and-ownership-checkpoint) and [local cutover interruption checkpoint](store-migration.md#local-cutover-interruption-checkpoint). | Verify native macOS exclusion, and choose ACL, remote-policy and restoration evidence for the supported migration path. |
| Parent acceptance mapped with independent review | Runtime plan below and independent review on exact draft commits. | Complete the remaining design decisions and review the assembled final SHA. |

## Readiness review

Reviewed the assembled design at `89ba62a7cf151f46e920ac4c49d1665406f67ec8`
against main `6310dcd`. Identity, the managed writer inventory and the parent
acceptance mapping have design coverage. The subsequent
[legacy shared admission](store-migration.md#legacy-shared-admission)
defines migration-only write authority before an admitted v1 registration.
The following design decisions and evidence
remain open before final review.

| Remaining item | Evidence needed |
| --- | --- |
| Protected recovery and durability | The local selector and registry replacement sequence is selected and exercised on APFS and ext4. Isolated restoration now covers native mode, ACL and owner metadata. Actual archive integration, macOS cross-account ownership and external dependency restoration remain open. System crash and power-loss durability remain open. |
| Old-writer exclusion | Run the distinct-UID fixture on a privileged macOS test runner. Specify how migration verifies the target ACL/ancestor rights and the authoritative remote policy for the exact ref. Current Linux permissions and synthetic Git policy evidence remain scoped to those fixtures. |

On native macOS, the three static decision validators and the cutover, OpenSSH
and encrypted restore fixtures passed at the reviewed revision. The native
two-UID run skipped and the remote retry was blocked before a runner started.
Keep #35 open and #36 waiting. Runtime transfer, restoration and integration
suites are mapped below.

## First transfer scope

Preserve the moving host ID. For distinct IDs with the same effective endpoint,
show the comparison and allow keeping both with valid distinct names or
cancelling. If a name is occupied, continuing requires an explicit free alias.
Same-ID reconciliation still follows the existing-destination contract.
Defer merging into a different destination ID until there is a reviewed rule
for the surviving ID and both stores' references. This is a first-slice scope
decision. [#33](https://github.com/duong6003/hostbook/issues/33) and
[#41](https://github.com/duong6003/hostbook/issues/41) still list the broader
update-existing outcome, which remains pending.

## First #36 slice after #35

[#36](https://github.com/duong6003/hostbook/issues/36) depends on #35. The
remaining #35 work is tracked above. Native macOS two-UID writer exclusion
still lacks evidence. Durability and migration requirements remain open, along
with independent review of the assembled draft.

Start #36 with one read-only [v1 context resolver](store-registration.md#registration-authority)
against an explicitly isolated, verified fixture account root. Resolve an
explicit store ID and expected registration, checkout, connection and policy
generations to a validated checkout and resource namespaces. Verify the
checkout filesystem binding and its sidecar store ID before returning the
context.

Use a private + A + B fixture with distinct checkout, Git common directory,
vault/cache, index, lock and status paths. Verify each selection returns only
its own paths. Reject unknown IDs, stale bindings, copied registry authority and duplicate
or overlapping checkout/Git directories. Compare fixture data before and after each read.
This slice ends at the resolver and its isolated fixtures. Runtime connection,
publication, vault access and UI are later work.

The experimental shared-read branch at `6117882` uses a separate
`snapshots.json` registry. Its Git snapshot and identity validation are
reference material. #36 uses the device-local v1 registration contract.

## #33 acceptance and downstream fixtures

| Parent behavior | Runtime fixture to retain | Work items |
| --- | --- | --- |
| Two machines share one vault and keep private data and history separate | Start two devices with different private commits and vaults. Connect both to one seeded shared vault. Inspect both histories, lock one vault and repeat the receive. | [#36](https://github.com/duong6003/hostbook/issues/36), [#43](https://github.com/duong6003/hostbook/issues/43), [#45](https://github.com/duong6003/hostbook/issues/45) |
| One connected shared store in the first UI and two isolated shared stores underneath | Exercise private/A/B with independent checkout, index, vault/cache, mode, lock and status. Open the first-release UI with one connected shared store. | [#36](https://github.com/duong6003/hostbook/issues/36), [#45](https://github.com/duong6003/hostbook/issues/45) |
| Stable rename and retry identity with duplicate names | Use the same aliases and secret names in private/A/B. Confirm lookup, preview and login target the captured store. Reject stale selections after disconnect or changed configuration. Rename, interrupt a transfer, then retry without generating a duplicate host. | [#37](https://github.com/duong6003/hostbook/issues/37), [#41](https://github.com/duong6003/hostbook/issues/41), [#42](https://github.com/duong6003/hostbook/issues/42) |
| Private create/import defaults and shared write restrictions | Create and import with a shared row selected and verify the default destination is private. Attempt shared host, import, vault, undo, callback and rollback writes with stale context, receive-only mode and denied remote access. Check local data, index and remote ref remain intact. Start with the [single-host save cases](store-mutations.md#save-one-existing-shared-host) and verify an authorized edit with an exact ref check. | [#38](https://github.com/duong6003/hostbook/issues/38), [#45](https://github.com/duong6003/hostbook/issues/45) |
| Transfers both ways and credentials preserved on failure | Transfer matching and distinct identities in both directions with independent vaults. Even after successful source removal, retain a credential referenced by another source host. Inject re-encryption or publication failure and recover without losing the source or creating duplicates. | [#40](https://github.com/duong6003/hostbook/issues/40), [#41](https://github.com/duong6003/hostbook/issues/41), [#42](https://github.com/duong6003/hostbook/issues/42) |
| Existing ProxyJump behavior | Compare effective SSH configuration and jump chains before and after store-aware selection and transfer, including scoped aliases and wildcard precedence. Exercise a qualified hop that would resolve differently after source removal and an existing destination hop shadowed by the proposed alias. | [#37](https://github.com/duong6003/hostbook/issues/37), [#41](https://github.com/duong6003/hostbook/issues/41) |
| Concurrent edits, delete/edit and secret conflicts | Use two writers to edit different hosts, the same host and delete versus edit. Inject a secret collision and a locked vault. For a fresh offline save, preserve input in the open form without a shared write or queue. For offline or rejected publication after admission, retain recoverable work and reconcile the exact attempt. This follows the approved online-only rule in place of the parent's earlier offline-save wording. | [#38](https://github.com/duong6003/hostbook/issues/38), [#44](https://github.com/duong6003/hostbook/issues/44) |
| Mode send/discard/cancel | Race an admitted publisher with each mode choice. Cover lost acknowledgement, explicit discard, cancellation and restart. Verify discarded work stays discarded. | [#39](https://github.com/duong6003/hostbook/issues/39), [#45](https://github.com/duong6003/hostbook/issues/45) |
| Disconnect retaining or removing only the local shared checkout | Exercise both choices with clean and pending states. Verify other checkouts, private data and the remote remain intact. Reconnect with fresh generations and receive-only mode. | [#39](https://github.com/duong6003/hostbook/issues/39), [#45](https://github.com/duong6003/hostbook/issues/45) |
| Migration, backup and recovery | Cover remote/no-remote, independent/locked/partial vaults, attainable and blocked writer isolation, staged recovery, interrupted cutover and conditional rollback. Compare working tree, index and refs as distinct state. | [#43](https://github.com/duong6003/hostbook/issues/43) |
| Platform, UI, performance and failure coverage | Run isolated failure, concurrency, permissions and retry suites on macOS and Linux. Run vi/en screen checks and record macOS before/after hot-path samples for affected flows. Record missing coverage explicitly. The parent still mentions native Windows, which [#57](https://github.com/duong6003/hostbook/pull/57) removed from the supported platforms. | [#46](https://github.com/duong6003/hostbook/issues/46) |
