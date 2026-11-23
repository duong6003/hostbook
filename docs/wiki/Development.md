# Development

## Codebase

`hostbook` is a Bash 3.2-compatible executable for macOS and Linux.
The UI uses fzf and gum. tmux handles multi-host sessions, Git synchronizes host files, and age
protects vault credentials. The interface supports Vietnamese and English (`lang/vi.sh`, `lang/en.sh`); documentation and code comments are English.

Core files:

| Path | Purpose |
| --- | --- |
| `hostbook` | Bootstrap, configuration, index, rendering, SSH flows, vault, sync, and dispatch. |
| `lib/ui.sh` | Shared UI helpers and the screen builders (`ui_menu`, `ui_field`, `ui_confirm`, `ui_choice`, `file_pick`, …), loaded eagerly. |
| `lang/vi.sh`, `lang/en.sh` | Every user-facing string as `L_<key>`, read through `t`. `vi.sh` is complete, `en.sh` overrides it; both define every key. |
| `lib/import.sh` | Bulk and share-package import, loaded for human flows and import callbacks. |
| `lib/settings.sh` | Interactive settings and language choices, loaded for human flows and sourced fixtures. |
| `lib/hosts.sh` | Add, edit, rename, move and delete host interactions, loaded for human flows and sourced fixtures. |
| `lib/store_vault.py` | Process-local unlock cache for registered store contexts. Uses an inherited pipe for the passphrase and verifies the age recipient. |
| `install.sh` | macOS/Linux user-local installation and dependency downloads; `HOSTBOOK_REPO` (source) and `HOSTBOOK_HOME` (install directory) override its defaults. |
| `python3 -B tests/store-context.py` | Read-only v1 registry resolution with one private and two shared fixture stores. Checks verified account and checkout bindings and the v1 recovery root. The `_store_context` callback supports an isolated fixture request over a pipe or a pre-approved local account lookup. |
| `tests/platform.sh` | Shared callback, path, CRLF, and rollback regressions. |
| `tests/sync.sh`, `tests/discovery.sh` | Isolated sync/locking failures and synthetic VPN discovery/address controls. |
| `tests/install.sh` | Isolated Unix installation and update using a local Git repository, including the fixture store callback from another directory. |
| `tests/test.sh` | Core regression tests with isolated data. |
| `tests/ui.sh` | Configuration, themes, rendering, and search tests. |
| `tests/onboarding.sh` | Vault setup and first-run flows with synthetic credentials. |
| `tests/hosts-module.sh`, `tests/settings-module.sh` | Selective loading of `lib/hosts.sh` and `lib/settings.sh`. |
| `tests/open-mode.sh`, `tests/menu-empty.sh` | Group open-mode safety, and no-match recovery in menus and pickers. |
| `tests/import-package.sh` | Real encrypted export/import packages. |
| `tests/import.sh`, `tests/import-keys.sh`, `tests/import-input.sh`, `tests/import-review.sh` | Import staging, rollback, credentials, source routing and review regressions. |
| `tests/file-input.sh`, `tests/import-tui.sh` | Shared file/multiline inputs and real import screens in private tmux sessions. |
| `tests/tui.sh` | Real fzf interaction in isolated tmux sessions. |
| `tests/controls.sh`, `tests/home-recovery.sh` | Compact controls, selection and home recovery in private tmux sessions. |
| `tests/screens.sh` | Screen-system rules S1-S11 on one screen of each type, at 60, 100 and 200 columns, in vi and en. |
| `tests/e2e.sh` | Real SSH tests using a Docker fixture (`tests/sshd/`). |
| `tests/performance.py`, `tests/pointer-performance.py`, `tests/import-performance.py` | Manual timing tools for the budgets in AGENTS.md; not run in CI. |
| `docs/wiki/` | Versioned source for this wiki. |
| `docs/archive/` | Historical design and implementation notes; not current user instructions. |

### Pre-approved store lookup

`_store_context STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY` reads an
existing approval from `~/.config/hostbook/store-account-v1.json`. The home
comes from the effective OS account. `HOME`, XDG overrides and the working
directory have no role in approval lookup. Expect a read failure when the
approval is absent.

For first local approval of a prepared v1 account, run
`_approve_store_account ACCOUNT_ROOT` and review the stores and checkout paths
in the returned JSON. Run the same command with the returned confirmation as a
second argument to save the approval. The absolute prepared root must contain
the existing private `state/stores/v1/registry.json` and `recovery/root.json`
directories and the registered checkouts and Git directories. The approved
OS home must already contain an owned `.config/hostbook` directory. Connected
shared entries must already be receive-only.

The command rechecks the plan and refuses a changed checkout or overlapping Git
directory. It publishes a private file without replacing an existing approval,
then resolves each approved checkout. A file or directory sync failure blocks
completion. If publication is uncertain, retain the observed approval and
reconcile before retrying. This operation creates local approval for prepared
metadata. Registry creation, replacement approval, vault changes and sync
have their own flows.

The approval is UTF-8 JSON with exactly `format` (`hostbook-store-account`),
`version` (integer 1), `home` and `account`. The `home` binding contains
`path`, `device` and `inode`. `account` holds the approved root, state
and recovery bindings, device and recovery IDs, and checkout bindings.
Use a private owner-only approval file outside all checkout, Git, state and
recovery directories. macOS approval paths with ACL grants are rejected.
The reader validates the approval and returns context metadata without
opening a vault or starting sync.

`_refresh_store_index STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY`
explicitly rebuilds that store's local alias inventory. The index lives
under the registered state, keyed by store ID and checkout generation.
Rows contain tab-separated store ID, host ID and alias from the v1 sidecar.
Each explicit refresh checks the declared managed `.conf` source bytes and
their simple `Host` alias bindings. It rechecks the current approval,
binding, generations and sidecar before publishing via atomic replacement.
Quoted or escaped `Host` lists require explicit reconciliation. A stale association leaves the previous
index intact and the command reports failure.

`store-hosts STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY` refreshes the
selected store's effective route index. Rows contain the store ID, stable
host ID, alias and a snapshot stamp. Pass the IDs, same expected generations
and stamp to `store-preview STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY HOST_ID STAMP`.
Preview reads the derived route and rejects a changed selection without
running SSH. Refresh if the identity, admitted source bytes, approved defaults
or expected binding change.

The route view reads inventoried sources in sidecar order, followed by the
optional device-approved file `state/stores/v1/STORE_ID/ssh/config`. Prepare
this file inside the verified account state with a private `ssh` directory
and an owner-only config file. Use it for applicable local defaults and
standalone jump aliases. Hostbook opens this file without following links.
The scoped route view accepts data-only SSH settings, applies OpenSSH's
first-value precedence and excludes ambient SSH configuration and other
stores' files. It rejects external Includes, command directives and
external identity files. Registered routing uses the system OpenSSH at `/usr/bin/ssh`.
The combined admitted source limit is 16 MiB. `_store_hosts` and `_store_preview` also support the isolated
fixture pipe used by `_store_context`.

`store-connect STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY HOST_ID STAMP`
opens the selected registered route. It rechecks the current listing
snapshot and runs native OpenSSH with a private copy of the same configuration
for the target and its ProxyJump children. The scoped connection disables
ambient keys, the ambient agent and control-socket reuse. Before connecting,
provision `known_hosts` in that store's private state `ssh` directory with the
approved host keys for the destination and any jumps. You can separately stage
`global_known_hosts` there to add approved defaults. Use owner-only regular
files. Hostbook snapshots both files, requires strict OpenSSH host-key checks
and rejects host key changes. The scoped view takes its host keys from these
files and leaves device SSH trust databases untouched.

A `# secret: NAME` marker above a concrete managed Host binds a key from
that store's vault. A matching legacy `IdentityFile .../secrets/NAME.pub`
is a hint only. The launcher takes the public key from the verified vault,
loads the matching encrypted private key into a short-lived private agent
and offers that key only for the selected destination or bound hop.
It checks the credential and route again after unlocking. Plaintext key
material and the vault identity travel in process memory or inherited pipes.
The vault tools must come from the OS package, the OS account's local
installer directory or a canonical Homebrew package. The connection's
private agent closes with its owner even if the launcher is terminated.
An unbound route uses interactive authentication without ambient keys.
A password marker uses OpenSSH's native terminal prompt. Registered
connections never read or supply the stored password or use an external
askpass program.

Legacy history, groups and undo still use checkout-local aliases and paths.
Registered list, preview and connect neither read nor rewrite those references.
A registered selection cannot be used as a legacy group target or undo action.
Changes to registered mutation and group workflows have their own admission path.

`_refresh_store_status STORE_ID REGISTRATION CHECKOUT CONNECTION POLICY`
records local connection status as `unchecked`. It writes only the
selected store and connection generation's local status file after
revalidating the binding. The status supplies no remote result.

Registered stores use a native descriptor lock on the private `sync.lock`
directory. Its scope is the store ID across generations. A second operation
requiring that store's writer lock fails as busy while another store can
progress. Closing the descriptor releases the lock and leaves its directory in place. This lock
serializes cooperating local operations and does not grant mutation or
publication authority. The legacy single-checkout lock runs independently.

The registered store vault cache lives in the unlock consumer's process.
It scopes identities to the effective account, device and store. Each
lookup checks the vault directory, public recipient, password envelope and
recovery envelope against the cached snapshot. Replacing or rewrapping any of
those inputs invalidates the cache, even if the public recipient stays the
same. Unlock verifies the decrypted identity with `age-keygen` and rechecks
the snapshot before caching. The passphrase uses an inherited pipe and the
identity stays in memory. This API is for scoped consumers. The CLI and the
legacy Keychain, Secret Service and SSH agent flows still require integration.

The test suite's installed account case requires a disposable system
account whose effective home is an empty `/fixture-home`. Set
`HOSTBOOK_TEST_ACCOUNT_HOME=/fixture-home` only in that isolated fixture.

## Run checks

```bash
bash -n hostbook
bash -n install.sh
bash tests/install.sh
python3 -B tests/store-context.py
for t in test platform sync discovery hosts-module settings-module ui file-input menu-empty controls open-mode \
         home-recovery import import-keys import-input import-package import-review import-tui \
         onboarding tui screens; do bash tests/$t.sh || break; done
bash tests/e2e.sh
```

This is the order used by `.github/workflows/test.yml`. `tests/e2e.sh` runs on Ubuntu only.
Tests run with `HOSTBOOK_LANG` set explicitly (`vi` for the
older suites, `en` for the newer ones); `HOSTBOOK_LANG_DIR` points copied fixtures at `lang/`.

Changes to anything that runs per key press (preview, fzf binds, list, header, footer) must follow the
performance rules and budgets in [AGENTS.md](https://github.com/duong6003/hostbook/blob/main/AGENTS.md).
Screens follow the screen system in [docs/design/screens.md](https://github.com/duong6003/hostbook/blob/main/docs/design/screens.md):
build them from the `ui_*` helpers, put every string in `lang/vi.sh` and `lang/en.sh` (never literal UI text in
`hostbook`), and run `bash tests/screens.sh`, which opens one screen of each type in a private tmux
server at 60, 100 and 200 columns in both languages and checks rules S1-S11 (breadcrumb, key hints in
the UI language, no line wider than the terminal, field rules and inline errors, destructive items
last, no absolute home paths, PROD deletes need the typed name, progress screens show elapsed time
and a way to cancel, no git words, vi/en key parity).

TUI tests (`tui`, `screens`, `controls`, `import-tui`, …) skip if tmux is missing.
E2E tests skip if Docker is unavailable. CI runs on macOS and Ubuntu and skips changes
limited to Markdown, `docs/` or `LICENSE`. Ubuntu runs the Docker SSH tests.
Tests use temporary host lists, state, credentials, and SSH configuration.

## Test data and overrides

`HOSTBOOK_DIR`, `HOSTBOOK_STATE`, `HOSTBOOK_SSH_CONFIG`, and `XDG_CONFIG_HOME` can
isolate development data from your real SSH setup. `HOSTBOOK_LANG=en|vi` forces the UI language,
`HOSTBOOK_KEYCHAIN=file:<path>` keeps the vault identity out of the real Keychain, and
`HOSTBOOK_MASTER` supplies a synthetic vault password (tests only). Follow the test scripts for a
complete fixture; do not point experiments at a production vault. `HOSTBOOK_LIB_DIR` overrides the installed module directory for copied test scripts; export it to callbacks too. Internal commands
prefixed with `_` are implementation/testing helpers, not a stable public API.

## Update documentation

Edit the pages under `docs/wiki/` in the app repository. After review, publish the
same Markdown files to the separate `hostbook.wiki.git` repository. GitHub requires
an initial wiki page before that Git repository can be cloned.

Keep README focused on the product, installation, and links. Detailed usage belongs
here. Screenshots in `docs/assets/` should come from the real app with synthetic
hosts and no private data. Quote UI labels from `lang/en.sh`, adding the
Vietnamese label from `lang/vi.sh` in parentheses where a reader of that UI needs it, and keep command names exact.

## CI coverage matrix

| Coverage | Ubuntu | macOS |
| --- | --- | --- |
| Core, platform, UI, import | Shared suites | Shared suites |
| Interactive UI | tmux, fzf screens | tmux, fzf screens |
| Install / update | Fresh isolated install and Git update | Fresh isolated install and Git update |
| Uninstall | Manual removal | Manual removal |
| Real SSH key and password | Disposable Docker sshd | Not covered |
| Native boundaries | Unix shared checks | Unix shared checks with a Keychain fixture |

The macOS real SSH gap remains: the Linux fixture requires Docker, and enabling a
system SSH server or changing user credentials on a runner would require a separate
isolated fixture. See [Getting started](Getting-started) for manual removal steps.
