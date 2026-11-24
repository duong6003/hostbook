# Shared-store transfer and recovery

**Goal** — Prepare and verify credential transfers, move hosts without source loss, recover interrupted transfers, upgrade legacy data and resolve scoped Git conflicts.

**Base** — `dde7856`, the merge of phase 1. Use a single active writer. Review and run Ubuntu and macOS CI on the final commit before requesting merge.

**Contracts** — [Transfer](../../design/store-transfer.md), [journal](../../design/store-journal.md), [migration](../../design/store-migration.md), and [acceptance](../../design/store-acceptance.md). Use isolated fixtures with synthetic credentials and no active SSH agent or Keychain.

- [ ] #40 — Use the verified vault contexts to re-encrypt and verify a credential in private staging. Pin both vault generations. Refuse occupied names unless an explicitly reviewed mapping permits reuse. Verify password and key records with real age tools. Preserve the source and unrelated staged work on failure or cancellation.
- [ ] #41 — Resolve both host identities and the explicit duplicate choice. Preview the plan, verify both write boundaries and persist recovery evidence. Complete destination effects before removing the source.
- [ ] #42 — Exercise interruption, concurrent writers and retry. Reconcile the original Git binding and evidence before replay. Retain the source and recovery material when remote acceptance is unknown.
- [ ] #43 — Build the guided legacy upgrade with verified backup, independent recovery access, cutover and rollback. Exercise partial, independent and locked vault cases.
- [ ] #44 — Resolve conflicting host, credential and vault changes per store. Preserve independent changes and keep secrets out of comparisons.
- [ ] Verify macOS and Linux and review the exact integration SHA. Record material scope limits in the draft PR before requesting merge.
