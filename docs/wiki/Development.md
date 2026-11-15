# Development

## Codebase

`hostbook` is a Bash 3.2-compatible executable shared by macOS, Linux, and Windows
(Git for Windows runtime). The UI uses fzf and gum; tmux handles multi-host sessions, Git synchronizes host files, and age
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
| `install.sh` | macOS/Linux user-local installation and dependency downloads; `HOSTBOOK_REPO` (source) and `HOSTBOOK_HOME` (install directory) override its defaults. |
| `install.ps1`, `windows/` | Managed Windows runtime, native launcher, and OS boundaries. |
| `tests/platform.sh` | Shared callback, path, CRLF, and Git identity regressions. |
| `tests/install.sh` | Isolated Unix installation and update using a local Git repository. |
| `tests/windows.ps1` | Native Windows install, security, fzf, SSH, update, and uninstall checks. |
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

## Run checks

```bash
bash -n hostbook
bash -n install.sh
bash tests/install.sh
for t in test platform hosts-module settings-module ui file-input menu-empty controls open-mode \
         home-recovery import import-keys import-input import-package import-review import-tui \
         onboarding tui screens; do bash tests/$t.sh || break; done
bash tests/e2e.sh
```

This is the order used by `.github/workflows/test.yml` (`tests/e2e.sh` runs on Ubuntu only; the
Windows job runs `tests/windows.ps1`). Tests run with `HOSTBOOK_LANG` set explicitly (`vi` for the
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

TUI tests (`tui`, `screens`, `controls`, `import-tui`, …) skip if tmux is missing; E2E tests skip if Docker is unavailable. A skipped
check is not evidence of a pass. CI runs on macOS, Ubuntu, and Windows; a change that touches only
Markdown, `docs/` or `LICENSE` skips it. Ubuntu runs
Docker SSH tests;
Windows installs its managed portable runtime, verifies launcher/native tool boundaries,
checks NTFS/DPAPI/clipboard behavior, and provisions an isolated native SSH server for
key/password tests. Only the disposable CI server fixture needs administrator access.
Windows deliberately does not run the tmux TUI suite; this is a capability limit,
not a passing test. Automated Windows execution uses a Windows Server 2025 runner;
Windows 10/11 desktop rendering and keyboard behavior still need a human check.
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

| Coverage | Ubuntu | macOS | Windows |
| --- | --- | --- | --- |
| Core, platform, UI, import | Shared suites | Shared suites | Platform callbacks, import headless suites and Windows-specific checks; full shared suites not run |
| Interactive UI | tmux, fzf screens | tmux, fzf screens | Native fzf callbacks; no tmux suite or physical terminal rendering |
| Install / update | Fresh isolated install and Git update | Fresh isolated install and Git update | Fresh managed install, update and uninstall |
| Uninstall | Manual documented removal; not automated | Manual documented removal; not automated | Automated; preserves host data and unrelated files |
| Real SSH key and password | Disposable Docker sshd | Not covered | Disposable native sshd |
| Native boundaries | Unix shared checks | Unix shared checks; real Keychain not exercised | Launcher arguments, paths, DPAPI, clipboard, private agent |

The macOS real SSH gap remains: the Linux fixture requires Docker, and enabling a
system SSH server or changing user credentials on a runner would require a separate
isolated fixture. Unix has no uninstall command; its documented manual removal is
not exercised by the install test. Windows download cache contains only archives,
verified by SHA256 before extraction, and the Windows install test deliberately
corrupts one archive to check recovery. Actions step durations show restore/save
overhead; the install and warm update log their elapsed times. Compare runs with
the same cache key and note that install includes that corruption recovery.
