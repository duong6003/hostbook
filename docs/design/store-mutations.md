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

Comparison with main `6310dcd` updates the Undo and conflict-push entries below.
[#58](https://github.com/duong6003/hostbook/pull/58) and
[#59](https://github.com/duong6003/hostbook/pull/59) serialize native lock metadata
and stale-owner recovery. These locks still govern the current single checkout.
[#57](https://github.com/duong6003/hostbook/pull/57) removes Windows helpers.
The [file-level inventory](#file-level-write-boundaries-on-current-main)
maps the managed write entrypoints at this revision. Rows group related symbols.
Paths are relative to the repository root. OS tools and user commands acting
outside Hostbook remain external writers. Store admission must detect changed
inputs from them. Dependency installers and transfer destinations have their
own authority, as mapped below. New writers must follow the same contract.

## Proposed ownership

Private and **each** independently registered shared store own all resources below.
There is no singleton shared directory, vault, unlock entry, gate or sync status.
Multiple checkouts of one logical store need one active-checkout generation and
the transfer document's transition barrier; they do not become independent stores.
The [registration contract](store-registration.md) defines the device-local registry and callback context.

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
a shared destination requires explicit destination intent, effective two-way mode,
a successful live preflight and current write rights on the bound remote/ref.
Selection alone is not destination intent;
confirmation does not grant rights. Existing-host actions bind their selected
store explicitly. Apply the rule to every affected store and every write boundary,
including internal/sourced callbacks and partial-failure restore paths. A cached
rights observation cannot substitute for a successful current preflight. Denial,
unknown rights and network unavailability block local shared USER writes.

For an ordinary host edit, the online preflight verifies the exact target and
fresh record revision through an authenticated read. With explicit intent,
effective two-way mode, a matching connection generation and no fence, prepare
the edit in disposable private staging. If write access to the exact ref is
still unknown after that read, this narrow path may reserve and send one exact
conditional request using the registered Git connection. Explicit denial or
unavailability blocks admission. Reserve the original ref, base and proposed
revision durably under the store gate before sending. The remote checks the
sending identity's write rights before updating that ref. This admission permits
only the request, leaving local shared content unchanged until acceptance is
established. Preserve the exact attempt on an ambiguous outcome and settle it
before replay. Fetch or dry-run success cannot establish write rights.
This path requires no separate provider token or API. Other shared operations,
including migration, transfers, import and vault writes, retain their stricter
write-rights preflight.

Private edits can be saved offline. For a shared edit that loses connectivity
before admission, keep the input in the open editor/form and report the failed
save. The user retries explicitly with fresh online checks and the original
base revision. Closing the editor does not create a durable draft or enqueue a
shared write. Preserve any already-admitted unsent work if publishing fails or
its outcome is uncertain. It remains visibly pending and recoverable under the
existing publication rules. A successful preflight cannot guarantee acceptance.

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

**PUBLISH** requires USER authorization where applicable, with the ordinary
conditional-request admission above, and the
[publication admission and transition barrier](store-transfer.md#publication-admission-and-transition-barrier):
under the store gate check effective mode/connection/fence, durably reserve the
exact attempt before sending, then settle quiescence and remote outcome. Every
ordinary/background/direct publisher obeys it. Mode, remote/ref, disconnect,
registration and active-checkout changes fence new admissions and cannot become
effective while an admitted send is in flight or uncertain. Read-only connection
changes may be requested in receive-only; they never implicitly grant USER rights.
After a fully drained migration fence, an explicit durable
[ownership handoff](store-migration.md#writers-ordered-gates-and-upgrade-boundary)
permits only that intent's exact authorized target/generation admissions and
blocks ordinary publishers/settings changes. No pending fence admits a new send;
ownership does not waive USER rights, two-way mode or fresh snapshot checks.

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
require explicit intent, two-way, live preflight and current rights. Publication
always adds PUBLISH and needs fresh online checks.

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
| `hostbook`: `undo_last`, `undo_locked` | Tools undo | Locked `revert --no-commit`, validation, commit or path-sensitive rollback that preserves unrelated work. May modify hosts, groups, vault and metadata. A partial revert can block sync and a successful undo sets the force-sync marker. | USER before revert and rollback, with fresh expected revision and store. Receive-only cannot undo an earlier two-way commit. Subsequent send uses PUBLISH. |
| `hostbook`: `vault_init`, `vault_create`, `vault_passwd`, `vault_new_recovery`, `vault_recover`, `vault_recovery_code` | Startup/default dispatch, `first_run`, login/key acquisition, Secrets vault menu, CLI `vault init/passwd/recover/recovery-code`; `_vault_init`, `_vault_recover` | `secrets/vault.*`, unlock cache, merge setup and commits; recovery rewrites envelopes | USER for vault creation/rekey/recovery, including automatic calls and direct callbacks. Existing-vault unlock-cache write alone is DERIVED. |
| `hostbook`: `secret_add_key`, `secret_write_key`, `secret_add_password`, `secret_set_password`, `secret_edit`, `secret_replace` | `pick_login`, Secrets actions, imports, `explain` set-password; `_secret_add_key`, `_secret_add_password`, `_secret_replace` | Credential ciphertext/public/meta files and commits; replacement may invoke remote key installation | USER at vault writes, not only after `save_and_sync`; staged key output cannot authorize vault promotion. |
| `hostbook`: `secret_rename_raw`, `secret_delete_raw`, `migrate_keys_raw`, `migrate_keys` | Secrets rename/delete/migrate; first-run migration; `_secret_rename`, `_secret_delete`, `_migrate` | Credentials plus all referencing config files; agent removal on deletion; migration replaces legacy bindings | USER for every raw write and transaction restore; migration is not DERIVED or RECEIVE. |
| `hostbook`: `kc_set`, `kc_del`, `vault_identity_run`, `cmd_unlock`, `cmd_lock`, `load_secret`, `agent_ready` | Login/askpass, Secrets menu, `_vault_id`, `_load`; unlock/lock CLI dispatch | Device unlock cache, agent/key bookkeeping; `vault_identity_run` can offer `vault_recover` | DERIVED scoped to existing store/vault; recovery branch requires USER. Shared OS agent does not imply shared vault ownership. |
| `hostbook`: `sync_ui`, `sync_quiet`, `sync_locked`, `cmd_bg_sync` | CLI `sync` → `_sync_now`; home/background `_bg_sync`; connection setup invokes `sync_locked` | Fetch/pull/rebase/reset/index/status; also `ensure_merge_setup`, auto-`commit_all` and both push branches | Split RECEIVE/DERIVED from USER/PUBLISH; receive-only cannot run current auto-commit/setup/push behavior unchanged. Revalidate network results before applying. |
| `hostbook`: `resolve_conflict`, `merge_driver` | Tools conflict; `_resolve`; Git merge-driver `_merge` | Merge/editor/checkout/add/commit/reset and direct push. A rejected push retains pending work. The merge driver writes its supplied output. | User choice/repair is USER + PUBLISH; validated remote-only application may use RECEIVE. Merge output must bind the intended store/operation, not fallback to default. |
| `hostbook`: `commit_all`, `save_and_sync`, `apply_change`, `change_locked`, `g` | Shared by above flows; `save_and_sync` commits and schedules later sync, not immediate push | Git index/commits, rollback copies/removals, force marker; `g` routes Git through current `DIR` | Guard content writer **before** effect; commit wrapper alone is too late. Explicit store-aware Git routing; PUBLISH for any send. |
| `hostbook`: `connect_kho`, `restore_origin`, `make_kho_here` | CLI `kho`, Tools store/hub, first-run | Remote/upstream config and restoration; merge/reset; hub creates bare repo and directly pushes | LOCAL transition fence for connection changes/restores; USER for content merge/bootstrap; direct send needs PUBLISH. No current public disconnect or store sync-mode command found; future disconnect/mode uses same barrier. |
| `hostbook`: `ensure_merge_setup` | `sync_locked`, `resolve_conflict`, `connect_kho`, `vault_create`, `first_run` | Local Git config; synchronized `.gitignore` / `.gitattributes` and setup commit | LOCAL Git config isolated by store; USER for tracked metadata/init commit. Receive-only cannot silently repair tracked setup. |
| `hostbook`: top-level state/history setup, `cfg_init`, `first_run`, `setup` | Boot/default dispatch; CLI `setup`; `first_run` clone/init → vault → legacy import → migration | State/history migration; Git init/clone, ignore commit, vault; SSH Include/backups, shell/terminal integration | DERIVED/LOCAL for device files; RECEIVE for validated clone into registered shared store; USER for generated records/vault/migration, default private. Installation cannot silently change store selection. |
| `hostbook`: `build_index`, `ensure_index`, `record`, `set_status`, `check_run`, `sweep_tmp`, `doctor`; `lib/settings.sh`: settings writers | `_list`, `_index`, connect/check callbacks; bootstrap sweep; CLI/Tools doctor; settings, Ctrl-T → `cfg_set`, `apply_preset`, custom theme | Derived index/cache/history/status/temp cleanup; doctor chmods public keys; device config/theme files | DERIVED/LOCAL with per-store namespaces and safe invalidation. Cleanup owns its paths; no host/vault-content rewrite or publish under this allowance. |
| `hostbook`: `ensure_key`, `push_new_key`, `secret_replace`; `share_flow`, `share_build`, `share_pack` | Store connection setup; key replacement; selection/batch share and `_share_build` / `_share_pack` | SSH account key generation/authorized_keys changes; export temp/package files outside store | External target authorization remains separate; shared credential mutation still USER. Export reads do not create store records, auto-transfer jumps, or grant destination rights. |

The current `host_shared` name denotes a multi-alias Host stanza, not membership
in a shared store. Preserve existing ProxyJump values, reference rewrites and SSH
resolution behavior; this slice introduces no store-specific jump restrictions.

## File-level write boundaries on current main

Followed the paths below at main `6310dcdd8bd2ec9f753fc6673079f367c4848cf0`.
Paths describe the legacy checkout and its state directory. The future gate
column specifies where a store-bound operation must be validated.

| Path | Current file or ref effects | Future gate |
| --- | --- | --- |
| [Import callbacks](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L3006) | `_import_apply` can create `secrets/vault.*` before package apply. `_split` writes a caller-supplied output directory and global file. `_import_write` can promote staged input into the checkout. | Bind the target before any vault provisioning. Keep split outputs in bound disposable staging. Import promotion requires USER independently of staging. |
| [Import apply and rollback](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/lib/import.sh#L323) | Config apply changes selected `.conf` files. Package apply can also change `groups.tsv` and credential `.age/.meta/.pub` files. Both save the exact Git index and attempt to restore touched paths and the index after mutation, validation or commit failure. A confirmed imported key can commit separately before host promotion. Copying results to `STATE/last-import` can fail after a successful commit, leaving committed data intact and returning failure before the `status.force` marker. | Cover vault, host promotion, separate key commit and actual index/file restoration under the affected store. Keep report/status files in that store's state. |
| [Vault identity and recovery](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L924) | `_vault_id`, `_secret_get`, askpass and connection helpers can reach vault unlock. Unlock can write the device cache, and the recovery branch can rewrite `vault.key.age` and `vault.recovery.age`, then commit locally and schedule sync. | Scope cache access to the device/store/vault. Recovery requires USER before the first envelope write even from an otherwise read-oriented caller. |
| [Raw transaction and Undo](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2511) | `change_locked` snapshots declared files, temporary siblings and the Git index before raw commands. Undo can create `REVERT_HEAD` after a partial failure. Its rollback uses `read-tree -m -u` and `revert --quit` after checking the affected paths. | Bind the touched paths, Git worktree/index, expected HEAD and recovery state before mutation and recheck before restoration. Recovery markers cannot authorize another operation. |
| [Merge driver and setup](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L642) | `_merge` writes the supplied ours file via a sibling `.hbmerge`; merge setup writes Git config, `.gitattributes` and `.gitignore`, and may commit those tracked files. | Bind callback outputs to the verified Git operation and scratch paths. Device-local Git config and tracked repository setup require their respective LOCAL and USER gates. |
| [Connect, hub and direct pushes](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2409) | `connect_kho` can install a login key and create a remote bare repo before the local lock. It then changes origin/upstream, fetches refs and can merge the worktree. `restore_origin` restores connection settings, while checkout restoration is separate. `make_kho_here` can create a local bare repo before repointing origin and pushing. `sync_locked` and both conflict-resolution paths can also push. | Require explicit authority for key installation and repository provisioning before those effects. Bind config change and restoration to the transition. Verify incoming data separately. Require PUBLISH admission for each direct or scheduled push to the exact ref. |
| [Native writer and derived state](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L700) | macOS `lockf` and Linux `flock` serialize access to `STATE/sync.lock.gate`; `sync.lock/pid` and `owner` identify the writer. Acquisition can rename stale state. Sync status, derived index variants and history use the same process `STATE`. | Keep lock, status and derived files under the registered store namespace. Gate identity must survive checkout and connection generation changes. |
| [Install and update](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/install.sh#L75) | `install.sh` can replace a non-Git application directory, clone or fast-forward the application, install dependencies and invoke `setup`. The app's `update` fast-forwards its own checkout. `need_tmux` can invoke Homebrew after confirmation. | Device installation authority. Verify application/dependency destinations are disjoint from store worktrees, Git directories and protected state before replacing or updating them. Treat `setup` as its own device operation. |
| [Setup and first run](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2862) | Setup writes the launcher symlink, shell startup files, Ghostty config and the account SSH config and backup. It can chmod the checkout and build the index. First run initializes or clones the repository, configures tracked metadata, initializes the vault, and can import hosts and keys before replacing the SSH Include with a backed-up configuration. It can also connect or provision a hub. | Device LOCAL for explicitly bound account files and launcher. Verify source SSH config and approved destinations before replacement. Separate private bootstrap/USER effects from validated shared RECEIVE. Bind new registration before checkout effects. Apply provisioning and publication checks to the hub/connect branch. |
| [Group actions and opening](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2138) | Save appends to `groups.tsv`. Group actions rewrite it via `groups.tsv.tmp` to delete a group or reset its mode before opening. `open_many` saves and commits a group mode after selection, and writes `STATE/lastmode` for the local preference. | USER before the first group write, including reset or remember-mode during Open. A receive-only open can choose an ephemeral mode. LOCAL for the device/session preference alone. |
| [Preferences and bootstrap](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L189) | `cfg_init` can create defaults before command dispatch. Settings, presets and first run replace the device config via a temporary file. Custom theme edits write `CONF/themes/*.theme`. | Device LOCAL under the verified account config namespace. Store selection and credentials use their own authority. Scope editor inputs and prevent derived display callbacks from promoting tracked changes. |
| [History, checks and permissions](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L1946) | Bootstrap can copy legacy history and create/chmod state. Connect, group open and file actions append or trim history. Check replaces its run directory and updates `last-check.tsv`; it can also unlock the vault. Doctor rebuilds the index and chmods credential public files. | DERIVED for scoped results, index and keyed history. Bind legacy history after migration classification and retain unresolved entries as archival. Check/Doctor resolve their target before state changes; any vault recovery uses USER. Bind chmod to an existing verified store path and preserve content. |
| [Session state and cleanup](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2963) | Home and footer callbacks write selection and session scratch state. App exit cleans its PID-named temp files and releases its own lock. Startup sweeps temp entries when their PID cannot be signalled. | Use scoped session/derived storage. Cleanup verifies ownership and liveness under the verified temporary root. Journal and rollback payloads use separate recovery authority. |
| [SSH trust repair and checks](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L1547) | The connection-failure action invokes `ssh-keygen -R` against the default known-hosts file and can rewrite it and its backup. Connection and check SSH processes can add or update host keys according to their effective configuration. Check uses `StrictHostKeyChecking=accept-new`. | Device LOCAL trust effect. Resolve the selected endpoint or hop and its approved known-hosts file before SSH can modify trust. Require explicit repair intent for removing entries and preserve unrelated endpoint trust. A managed host alias alone cannot name the entry to remove. |
| [Share packages, clipboard and file transport](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L2048) | Share builds a protected staging tree and writes an encrypted package in Downloads or the account home. `_share_build` accepts an output path. Share also copies the encrypted package to the account clipboard. Copying its password or a vault recovery code is a separate action. File send/get writes to an SSH destination or the current local directory, respectively. | Bind export credentials to the selected store and require explicit intent for copying secret material. Restrict scratch and external output to authorized destinations outside managed checkout, Git and recovery roots. Generic export/download cannot substitute for a guarded store write. Remote file transfer uses the resolved SSH endpoint and its own user intent. |

On native arm64 macOS (Darwin 27.2.0, Git 2.42.1), the existing isolated
`tests/sync.sh` (18), `tests/import-review.sh` (28), `tests/import-package.sh` (2)
and `tests/test.sh` (151) cases passed. Git transport was restricted to
disposable local repositories. Tests used temporary state and a file-backed
test vault cache. The core suite used a private test SSH agent. These results
cover current behavior, including failed push, import/Undo recovery and vault
recovery. The same native environment also passed isolated install/update,
onboarding (25 cases), UI/config (111 checks), settings loading, and open-mode
tests. Install/update used a local repo and existing dependencies. The open-mode
suite used fake tmux. A separate `ssh-keygen -R -f` probe against a fixture
known-hosts file preserved the unrelated line and created the expected backup.
These are current-main checks. The proposed gates require
downstream runtime validation.

## Save one existing shared host

On main `6310dcdd8bd2ec9f753fc6673079f367c4848cf0`, Home Ctrl-E, the
host actions menu and the connection failure screen all call
[`edit_host`](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/lib/hosts.sh#L154).
It resolves an alias to a project file and line, opens the live file, validates
after the editor closes and restores the backup on the invalid-edit revert path.
[`save_and_sync`](https://github.com/duong6003/hostbook/blob/6310dcdd8bd2ec9f753fc6673079f367c4848cf0/hostbook#L849)
then takes the sync lock, calls `commit_all` (`git add -A`) and schedules
later sync. A native macOS probe with a disposable checkout, a scripted editor
and a local bare remote confirmed that the editor received the live file before
the lock, the commit included a second host and the unstaged version of an
unrelated staged file, and the remote tip stayed at the original commit.

For the first shared save in [#38](https://github.com/duong6003/hostbook/issues/38),
limit the operation to an in-place edit of one existing host stanza. Validate
that the staged change affects only the selected host. Use a private Git index
so unrelated worktree and index changes cannot enter the proposed commit.

1. Capture `(store_id, host_id)`, the accepted host revision, and the checkout,
   registration, connection and policy bindings at selection. Keep the editor's
   input in private staging.
2. On Save, read the current host revision and Git tip from the exact registered
   remote and full branch ref in staging, leaving checkout refs untouched.
   Compare the host revision with the captured base. Build the proposed commit
   as a direct child of the observed tip. Under the store gate, recheck the
   local bindings, effective two-way mode, absence of a transition fence and
   captured host base. Durably reserve the exact ref, observed tip and proposed
   commit, then release the gate before sending. Git enforces write access and
   the conditional ref update described in [admission](#proposed-admission-rules).
3. Apply the accepted result only to its original store after revalidation.
   If the attempt crossed the send boundary and its outcome is uncertain, keep
   the attempt pending under the [publication rules](store-transfer.md#publication-admission-and-transition-barrier).

| Acceptance case | Expected result |
| --- | --- |
| Two-way, current host revision and Git accepts the exact update | Only the chosen host's revision advances. The committed change excludes other local edits. If the branch advanced on another host, rebuilding on the fresh tip preserves both edits. |
| Effective receive-only at Save, including a mode change while editing | No user write to the checkout, Git index, local branch or remote ref. Keep existing editor input available. |
| Same host already advanced, or the registered remote/ref changed while editing | For the same-host race, report a version conflict and retain the input for comparison. For a changed binding, retain the input and require a fresh selection and confirmation before a new attempt. The attempt makes no change to the checkout, index, local branch or either remote ref. |

A new Git tip on the same bound branch can reflect a different host's edit.
Re-read the target host and repeat preflight before any new attempt. If its
revision still matches, rebuild the proposal on the new tip. Definitive denial or unavailability before admission leaves input
in staging and creates no pending shared write.

## Synthetic examples and remaining work

[mutation-permissions.json](fixtures/mutation-permissions.json) describes private
plus two independent shared stores, resource separation, denied receive-only
callback/undo/import/recovery, permitted receiving/derived state and explicit
two-way rights checks, offline rejection for new shared edits, offline private
edits and preserved unsent work after an admitted change fails to publish. Run
from the repository root:

```bash
python3 docs/design/fixtures/validate-mutations.py
```

It reads adjacent JSON only and checks conceptual consistency, including stale
snapshot and publication-fence counterexamples. It never sources the app, touches
real configuration, unlocks a vault, invokes SSH/Git or proves runtime enforcement.
The earlier transfer fixture owns detailed reservation/drain/restart examples.

The [migration contract](store-migration.md) defines classification, verified
backup/restore, rollback boundaries and honest old/new writer compatibility gates.
The [format/admission proposal](store-format.md) selects durable identity encoding
and legacy SSH/share-package mapping; [journal/retention](store-journal.md) selects
local authority and protected payloads. Remaining #35 work includes concrete
upgrade verification and restore mechanisms and implementation-level
lock/cache/vault contracts. The [acceptance map](store-acceptance.md) records the
parent #33 fixture plan.
The managed writer inventory is mapped at the cited main revision. Final #35
review and runtime enforcement remain separate steps. Downstream
#36–#46 own runtime enforcement, migration and isolated regression coverage.
