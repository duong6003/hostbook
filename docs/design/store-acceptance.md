# Store design acceptance map

This is the #35 checkpoint for [the parent feature #33](https://github.com/duong6003/hostbook/issues/33).
The current comparison target and completed writer inventory use main `6310dcd`.
The draft branch still descends from `718890f`. The existing examples are synthetic design checks.
The runtime fixture column describes evidence still to collect.

## #35 design gates

| Gate | Evidence in the draft | Design decision or evidence |
| --- | --- | --- |
| Stable identity, versions, duplicates, manual edits and transfer | [Identity](store-identity.md), [sidecar v1](store-format.md), [rename](fixtures/rename.json), [invalid records](fixtures/invalid-records.json), [transfer](fixtures/transfer.json) and [store-aware SSH resolution](store-identity.md#ssh-resolution-for-a-selected-host), with [existing-destination transfer rules](store-transfer.md#existing-destination-host), [source references](store-transfer.md#source-references-before-removal) and [destination routes](store-transfer.md#destination-routes-before-writing). | Update-existing decisions and identity retention are recorded below. |
| Store resources and write boundaries | [Mutation and resource inventory](store-mutations.md), [callback contexts](store-registration.md) and [permission decisions](fixtures/mutation-permissions.json). | Managed writers are mapped at [current main](store-mutations.md#file-level-write-boundaries-on-current-main). Use the map for downstream enforcement. |
| Transfer states, concurrency and recovery | [Transfer contract](store-transfer.md), [recovery cases](fixtures/transfer-recovery.json) and [journal](store-journal.md). | The [local replacement sequence](store-journal.md#local-selector-and-registry-replacement) and the [recovery passphrase pipe](store-journal.md#recovery-passphrase-transport) have isolated tool evidence. |
| Migration, backups, rollback and old writers | [Upgrade boundary](store-migration.md#first-supported-upgrade), [migration cases](fixtures/migration-decisions.json), [writer exclusion checkpoint](store-writer-validation.md), [staged restore checkpoint](store-migration.md#staged-restore-checkpoint), [restore metadata checkpoint](store-migration.md#restore-permissions-and-ownership-checkpoint) and [local cutover interruption checkpoint](store-migration.md#local-cutover-interruption-checkpoint). | The contract specifies local path and ACL evidence, exact remote policy evidence and protected staging before cutover. |
| Parent acceptance mapped with independent review | Runtime plan below and independent review on exact draft commits. | Require independent review of the assembled PR head before integration. |

## Readiness review

The five gates above cover the design contract and its synthetic fixtures.
Integration of the independently reviewed [PR #47](https://github.com/duong6003/hostbook/pull/47)
is the handoff to #36. The [#33 scope clarification](https://github.com/duong6003/hostbook/issues/33#issuecomment-7041503475)
places product and platform verification in the downstream work.

| Downstream evidence | Work items |
| --- | --- |
| Hostbook archive integration, broader external dependencies, cross-account ownership, distinct-UID writer exclusion on macOS, and actual local ACL/ancestor and remote-provider policy collection. | [#43](https://github.com/duong6003/hostbook/issues/43), [#46](https://github.com/duong6003/hostbook/issues/46) |
| Runtime transfer recovery, integrated crash tests, and system crash or power-loss durability. | [#42](https://github.com/duong6003/hostbook/issues/42), [#43](https://github.com/duong6003/hostbook/issues/43), [#46](https://github.com/duong6003/hostbook/issues/46) |

The Linux writer fixture covers ordinary permissions, supplementary groups,
direct ACL grants and a writable parent. The native macOS two-UID run skipped
and the remote runner was blocked before allocation. See
[writer validation](store-writer-validation.md) for scope and results.

## First transfer scope

Creating a destination record keeps the moving host ID. For distinct IDs with
the same effective endpoint, compare the records and offer
[updating the chosen destination](store-transfer.md#update-an-existing-destination-with-a-different-id),
keeping both with valid distinct aliases, or cancellation. Updating keeps the
destination ID and name and uses the original transfer intent on retry.
The synthetic cases cover a verified update, an interrupted attempt and a
changed destination revision. [#41](https://github.com/duong6003/hostbook/issues/41)
owns implementation, with restart coverage in [#42](https://github.com/duong6003/hostbook/issues/42).

## First #36 slice after #35

[#36](https://github.com/duong6003/hostbook/issues/36) starts after the
reviewed #35 design is integrated.

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
