# Store mutation and resource ownership

Status: **proposed**, documentation only; third bounded slice of
[#35](https://github.com/duong6003/hostbook/issues/35) under
[#33](https://github.com/duong6003/hostbook/issues/33). Extends
[identity](store-identity.md) and [transfer](store-transfer.md); neither the
inventory nor the examples claim runtime enforcement or completion of #35.

## Observed baseline and mapping limits

Inspected `c53a35cfe252be03b16a782777246a82b952f4f3` (base main
`718890f697f0460999f1a8ae2d6a684f505a3d66`): `hostbook`, `lib/hosts.sh`,
`lib/import.sh`, `lib/settings.sh`, including bottom-level dispatch, `home`,
`tools`, host/group/secret actions, first-run paths and their shared writers.
Current code selects one `DIR`, `SEC`, `STATE`, index and status per process;
aliases identify hosts. It has no proposed per-store receive-only/two-way policy.
Existing validation, rollback and sync locks are observed mechanisms, not evidence
that the future authorization boundary already exists.

The following is a caller-followed inventory of current managed mutation families,
not an exhaustive syscall audit. Rows group related symbols; code paths below are
relative to the repository root. External editors, manual Git/SSH/file changes,
installers/updaters and platform-helper internals are not mediated by this future
application gate; admission must detect changed inputs rather than promise to
prevent independent edits. Current connection-key installation is called out
separately from repository publication. New downstream writers must join the same
contract. Durable encoding and a complete platform/file-level audit remain open.

## Proposed ownership

Private and **each** independently registered shared store own all resources below.
There is no singleton shared directory, vault, unlock entry, gate or sync status.
Multiple checkouts of one logical store need one active-checkout generation and
the transfer document's transition barrier; they do not become independent stores.
Paths below are logical resource names, not a selected disk layout.

| Resource | Owner and isolation/invalidation rule |
| --- | --- |
| Directory, host/project files, groups, Git worktree/index | Store ID + active-checkout generation; resolve paths under that registered directory, never the current UI default. Git index is mutable repository state, distinct from the derived search index. |
| Vault and credential bindings | Store ID + vault generation; private, shared A and shared B never reuse ciphertext or infer equality from secret names. Credential mutation scans references only within the intended store snapshot. |
| Unlock cache and loaded-key bookkeeping | Store ID + vault generation + device/account; validate cached identity against that vault. Forget/lock one store cannot forget another; an OS SSH agent may be shared, so track ownership/refcounts before unloading a key used by another store. |
| Derived index, checks, history and presentation cache | Namespace by store ID + checkout/revision; include applicable SSH config, language/theme/config generations in cache keys. Rebuild/invalidate on relevant changes; aggregated rows retain the full host key. |
| Writer gate and subordinate vault/recovery locks | Per logical store, owner checked; all writers and settings transitions use the common gate. Cross-store lock ordering, reservations and recovery follow transfer. |
| Sync status, pending work and publication reservations | Per store + connection generation + attempt; no success, failure, force marker or unsettled publication from A updates B's status. |
| Device-local connection and effective/requested mode | Per store registration; remote/ref, active checkout, generation, rights observations and transition fence remain local. Changing one shared store does not change another; remote content cannot enable two-way mode. |
| Global display preferences and installation | Device/account, outside store content; language/theme may be global. They cannot retarget a captured store operation or grant rights. |

## Proposed admission rules

**USER** means any user data write: hosts/settings/groups, vault envelopes or
credentials, metadata, commits, undo, repair, import and compensation. New hosts
and imports default to private, even while a shared row is selected. Writing to
a shared destination normally requires explicit destination intent, effective
two-way mode and current actual remote write rights. Selection alone is not destination intent;
confirmation does not grant rights. Existing-host actions bind their selected
store explicitly. Apply the rule to every affected store and every write boundary,
including internal/sourced callbacks and partial-failure restore paths. A cached
rights observation cannot override denial at the remote. Explicit current denial
rejects new shared USER writes; unknown rights block them except for the bounded
ordinary-edit outage admission below. Neither rejection nor outage discards or
rewrites existing unsent work; retain it visibly pending and recoverable.

Ordinary host/group edits may be saved **locally pending** during an outage in an
explicitly selected, previously authorized shared store with effective two-way
mode. First attempt live sync/rights preflight; network unavailability is distinct
from permission denial. Under the store gate, require fresh captured context,
unchanged registration/checkout/connection generations, no transition fence and
a prior verified write-access snapshot for that same store and those generations.
Revalidate record/vault revisions and preserve unrelated pending edits. Missing
or stale evidence blocks new writes. This limited local admission is not a claim
of current remote authorization: show pending, never published. On reconnect,
fresh sync, rights, revision and conflict checks precede publication admission;
remote rejection is authoritative and pending work remains recoverable. Transfers,
compensation/rollback, migration, imports, credential/vault writes and vault
recovery retain their stricter preconditions and never inherit this exception.
An internal callback qualifies only for the same explicitly bound ordinary edit;
its invocation mechanism cannot expand the exception. Receive-only never qualifies.

Receive-only forbids **all USER writes**, even local-only edits that might be
published later. **RECEIVE** may fetch and apply validated remote content in that
store, including received vault records, with read rights and fresh preconditions.
It must not auto-commit local edits, publish, synthesize user records, create a
missing shared vault or rewrite merge metadata. Dirty/conflicting local content
blocks receive pending explicit reconciliation; a user-authored merge/repair is
USER. Keep the accepted snapshot intact when receive validation fails.

**DERIVED** allows store-scoped search/cache/status/history/check bookkeeping and
unlock-cache updates for an existing validated vault; **LOCAL** allows device
preferences and connection configuration. Neither may change synchronized user
content or smuggle a commit/push/bootstrap into receive-only. A permission/ACL
repair of existing files must not alter their contents or initiate publication.
Creating or recovering vault envelopes is USER, even if prompted during login.
Read/unlock failure must remain a failure; never bootstrap shared content to fix it.

**PUBLISH** requires USER authorization where applicable and the
[publication admission and transition barrier](store-transfer.md#publication-admission-and-transition-barrier):
under the store gate check effective mode/connection/fence, durably reserve the
exact attempt before sending, then settle quiescence and remote outcome. Every
ordinary/background/direct publisher obeys it. Mode, remote/ref, disconnect,
registration and active-checkout changes fence new admissions and cannot become
effective while an admitted send is in flight or uncertain. Read-only connection
changes may be requested in receive-only; they never implicitly grant USER rights.

Capture store ID, registration/checkout/connection generations, selected host ID,
record/settings revision, vault generation and relevant mode/rights inputs before
prompting or launching a child. Reacquire the proper gate and revalidate at the
actual write, after prompts/network waits and before any restore. Do not hold a
gate across an editor, user prompt or network wait. A managed editor must edit a
private staged copy, then apply its reviewed diff through the guard; opening the
live receive-only file is already too late. Recovery evidence may be retained
without modifying user content; blocked rollback remains visibly recoverable.

Copied executables, internal callbacks, sourced functions and background children
must receive/resolve this explicit store snapshot; missing, stale or mismatched
context rejects a mutation. Environment `DIR` overrides, an alias, row position,
staging path or a later default store cannot substitute for identity. A staging
writer may operate on disposable data, but promotion to a store rechecks USER
authority; credentials acquired during staging are already vault USER writes.

## Current entrypoints → proposed boundary mapping

Guards in the last column are requirements, **not present behavior**. All USER
rows also revalidate the captured snapshot under the owning gate; shared targets
require explicit intent, two-way and rights, subject only to ordinary local-pending
admission above. Publication always adds PUBLISH and needs fresh online checks.

| Current path + symbol(s) | Followed callers / dispatch | Scope and actual resource written | Proposed guard |
| --- | --- | --- | --- |
| `lib/hosts.sh`: `add_host`; `hostbook`: `write_host` | CLI `add`, `home` Ctrl-A; internal `_write` calls writer directly | Current `DIR/<project>.conf`; add's validation restore also writes/removes it; credentials may be acquired via `pick_login` | USER before first host/vault write and restore; default private, explicit shared destination. Guard `_write` and sourced writer too. |
| `lib/hosts.sh`: `edit_host` | `home` Ctrl-E, `host_actions`, `explain` edit | External editor writes entire selected file; invalid-edit revert copies backup; `save_and_sync` commits | USER staged edit + guarded apply/revert; whole-file diff can affect other hosts/settings. |
| `lib/hosts.sh`: `delete_server`, `rename_server`, `move_server`; `hostbook`: `change_login` | `host_actions`; login also from `explain`; `_delete`, `_rename`, `_move`, `_set_host_secret` | `apply_change` → `change_locked` → raw host/group/history writers; project move stays within one current store | USER on all touched files, rollback and credential binding; preserve host identity. Cross-store move uses transfer instead. |
| `hostbook`: `delete_host_raw`, `rename_host_raw`, `move_host_raw`, `set_host_secret`, `groups_rename` | Above transaction wrapper; raw functions also reachable when sourced | Host blocks; rename rewrites other ProxyJump references, groups/history; move materializes inherited settings | USER at raw write/promotion boundary, validated resource set; no new jump-host restrictions or automatic cross-store transfer. |
| `hostbook`: `save_group`, `set_group_mode`, `group_actions` | `home` Ctrl-G, `batch_actions`; `open_many` persists chosen group mode; group delete action | `groups.tsv` append/rewrite/delete then commit | USER even when reached as a side effect of opening connections. Group session mode is not store sync mode. |
| `lib/import.sh`: `import_flow`, `import_source`, `import_content`, `import_write` | CLI `import`, Tools import; `_import_write`; first-run `import_config_file` | Bulk/legacy configuration parsing → `prepare_import_list` / `prepare_import_config` → stage/review → `apply_import_config` | Disposable staging allowed; default private; USER recheck at promotion, not just source picker. Existing format validation remains required. |
| `lib/import.sh`: `import_config_keys`, `import_commit_secret`, `import_discard_secret` | Configuration import before host promotion; `secret_add_key` / `pick_login` | Destination encrypted credentials and Git index can change before host commit; cleanup removes new credential files | USER before vault init/acquisition, targeted commit and discard; failed import cannot bypass mode with cleanup. |
| `lib/import.sh`: `import_pack`, `apply_import_package`, `pack_apply_raw`, `pack_write_host`, `pack_delete_host`, `import_touch` | `import_content` package route; `_import_apply` opens/classifies/decides then applies | Re-encrypted secrets, host updates/replacements, groups, touched-file manifest; `pack_state_signature` / base index revalidated | USER for every destination effect; stage/review snapshot binds destination store and vault, not only current file signature. |
| `lib/import.sh`: `apply_import_config`, `apply_import_package` failure branches | Failed application/validation/commit | Touched-file and Git-index restoration, derived index rebuild; rollback data may remain | USER for content restore/removal under same store gate; DERIVED for rebuild. Mode change blocks restoration safely, retaining recovery evidence. |
| `hostbook`: `undo_last` | Tools undo | `g revert`, abort, invalid-result hard reset; may modify hosts, groups, vault and metadata; force-sync marker | USER including revert/reset, fresh expected revision and store; receive-only cannot undo an earlier two-way commit. Subsequent send uses PUBLISH. |
| `hostbook`: `vault_init`, `vault_create`, `vault_passwd`, `vault_new_recovery`, `vault_recover`, `vault_recovery_code` | Startup/default dispatch, `first_run`, login/key acquisition, Secrets vault menu, CLI `vault init/passwd/recover/recovery-code`; `_vault_init`, `_vault_recover` | `secrets/vault.*`, unlock cache, merge setup and commits; recovery rewrites envelopes | USER for vault creation/rekey/recovery, including automatic calls and direct callbacks. Existing-vault unlock-cache write alone is DERIVED. |
| `hostbook`: `secret_add_key`, `secret_write_key`, `secret_add_password`, `secret_set_password`, `secret_edit`, `secret_replace` | `pick_login`, Secrets actions, imports, `explain` set-password; `_secret_add_key`, `_secret_add_password`, `_secret_replace` | Credential ciphertext/public/meta files and commits; replacement may invoke remote key installation | USER at vault writes, not only after `save_and_sync`; staged key output cannot authorize vault promotion. |
| `hostbook`: `secret_rename_raw`, `secret_delete_raw`, `migrate_keys_raw`, `migrate_keys` | Secrets rename/delete/migrate; first-run migration; `_secret_rename`, `_secret_delete`, `_migrate` | Credentials plus all referencing config files; agent removal on deletion; migration replaces legacy bindings | USER for every raw write and transaction restore; migration is not DERIVED or RECEIVE. |
| `hostbook`: `kc_set`, `kc_del`, `vault_identity_run`, `cmd_unlock`, `cmd_lock`, `load_secret`, `agent_ready` | Login/askpass, Secrets menu, `_vault_id`, `_load`; unlock/lock CLI dispatch | Device unlock cache, agent/key bookkeeping; `vault_identity_run` can offer `vault_recover` | DERIVED scoped to existing store/vault; recovery branch requires USER. Shared OS agent does not imply shared vault ownership. |
| `hostbook`: `sync_ui`, `sync_quiet`, `sync_locked`, `cmd_bg_sync` | CLI `sync` → `_sync_now`; home/background `_bg_sync`; connection setup invokes `sync_locked` | Fetch/pull/rebase/reset/index/status; also `ensure_merge_setup`, auto-`commit_all` and both push branches | Split RECEIVE/DERIVED from USER/PUBLISH; receive-only cannot run current auto-commit/setup/push behavior unchanged. Revalidate network results before applying. |
| `hostbook`: `resolve_conflict`, `merge_driver` | Tools conflict; `_resolve`; Git merge-driver `_merge` | Merge/editor/checkout/add/commit/reset and direct push; merge driver writes its supplied output | User choice/repair is USER + PUBLISH; validated remote-only application may use RECEIVE. Merge output must bind the intended store/operation, not fallback to default. |
| `hostbook`: `commit_all`, `save_and_sync`, `apply_change`, `change_locked`, `g` | Shared by above flows; `save_and_sync` commits and schedules later sync, not immediate push | Git index/commits, rollback copies/removals, force marker; `g` routes Git through current `DIR` | Guard content writer **before** effect; commit wrapper alone is too late. Explicit store-aware Git routing; PUBLISH for any send. |
| `hostbook`: `connect_kho`, `restore_origin`, `make_kho_here` | CLI `kho`, Tools store/hub, first-run | Remote/upstream config and restoration; merge/reset; hub creates bare repo and directly pushes | LOCAL transition fence for connection changes/restores; USER for content merge/bootstrap; direct send needs PUBLISH. No current public disconnect or store sync-mode command found; future disconnect/mode uses same barrier. |
| `hostbook`: `ensure_merge_setup`, `ensure_git_identity` | `sync_locked`, `resolve_conflict`, `connect_kho`, `vault_create`, `first_run` | Local Git config; synchronized `.gitignore` / `.gitattributes` and setup commit | LOCAL Git config isolated by store; USER for tracked metadata/init commit. Receive-only cannot silently repair tracked setup. |
| `hostbook`: top-level state/history setup, `cfg_init`, `first_run`, `setup` | Boot/default dispatch; CLI `setup`; `first_run` clone/init → vault → legacy import → migration | State/history migration; Git init/clone, ignore commit, vault; SSH Include/backups, shell/terminal integration | DERIVED/LOCAL for device files; RECEIVE for validated clone into registered shared store; USER for generated records/vault/migration, default private. Installation cannot silently change store selection. |
| `hostbook`: `build_index`, `ensure_index`, `record`, `set_status`, `check_run`, `sweep_tmp`, `doctor`; `lib/settings.sh`: settings writers | `_list`, `_index`, connect/check callbacks; bootstrap sweep; CLI/Tools doctor; settings, Ctrl-T → `cfg_set`, `apply_preset`, custom theme | Derived index/cache/history/status/temp cleanup; doctor chmods public keys; device config/theme files | DERIVED/LOCAL with per-store namespaces and safe invalidation. Cleanup owns its paths; no host/vault-content rewrite or publish under this allowance. |
| `hostbook`: `ensure_key`, `push_new_key`, `secret_replace`; `share_flow`, `share_build`, `share_pack` | Store connection setup; key replacement; selection/batch share and `_share_build` / `_share_pack` | SSH account key generation/authorized_keys changes; export temp/package files outside store | External target authorization remains separate; shared credential mutation still USER. Export reads do not create store records, auto-transfer jumps, or grant destination rights. |

The current `host_shared` name denotes a multi-alias Host stanza, not membership
in a shared store. Preserve existing ProxyJump values, reference rewrites and SSH
resolution behavior; this slice introduces no store-specific jump restrictions.

## Synthetic examples and remaining work

[mutation-permissions.json](fixtures/mutation-permissions.json) describes private
plus two independent shared stores, resource separation, denied receive-only
callback/undo/import/recovery, permitted receiving/derived state and explicit
two-way rights checks, ordinary outage pending admission and preserved unsent work
after permission denial. Run from the repository root:

```bash
python3 docs/design/fixtures/validate-mutations.py
```

It reads adjacent JSON only and checks conceptual consistency, including stale
snapshot and publication-fence counterexamples. It never sources the app, touches
real configuration, unlocks a vault, invokes SSH/Git or proves runtime enforcement.
The earlier transfer fixture owns detailed reservation/drain/restart examples.

Remaining #35 work: durable identity/registration/permission encoding and upgrade
policy; migration/backup/rollback design and old/new writer compatibility;
legacy SSH/share-package admission (including multi-alias mapping);
transfer journal and recovery retention; reference reconciliation/update-existing
lineage; implementation-level lock/cache/vault/SSH-resolution design; complete
platform writer mapping and mapping all parent #33 acceptance scenarios to fixtures.
This bounded inventory does not choose those mechanisms or close #35. Downstream
#36–#46 own runtime enforcement, migration and isolated regression coverage.
