# Development

## Codebase

`hostbook` is a Bash 3.2-compatible executable shared by macOS, Linux, and Windows
(Git for Windows runtime). The UI uses fzf and gum; tmux handles multi-host sessions, Git synchronizes host files, and age
protects vault credentials. The app interface is currently Vietnamese; documentation
and code comments are English.

Core files:

| Path | Purpose |
| --- | --- |
| `hostbook` | Application, configuration, index, SSH flows, vault, sync, and UI. |
| `install.sh` | macOS/Linux user-local installation and dependency downloads. |
| `install.ps1`, `windows/` | Managed Windows runtime, native launcher, and OS boundaries. |
| `tests/platform.sh` | Shared callback, path, CRLF, and Git identity regressions. |
| `tests/windows.ps1` | Native Windows install, security, fzf, SSH, update, and uninstall checks. |
| `tests/test.sh` | Core regression tests with isolated data. |
| `tests/ui.sh` | Configuration, themes, rendering, and search tests. |
| `tests/tui.sh` | Real fzf interaction in isolated tmux sessions. |
| `tests/screens.sh` | Screen-system rules S1-S11 on one screen of each type, at 60, 100 and 200 columns, in vi and en. |
| `tests/e2e.sh` | Real SSH tests using a Docker fixture. |
| `docs/wiki/` | Versioned source for this wiki. |
| `docs/archive/` | Historical design and implementation notes; not current user instructions. |

## Run checks

```bash
bash -n hostbook
bash -n install.sh
bash tests/platform.sh
bash tests/test.sh
bash tests/ui.sh
bash tests/tui.sh
bash tests/screens.sh
bash tests/e2e.sh
```

Changes to anything that runs per key press (preview, fzf binds, list, header, footer) must follow the
performance rules and budgets in [AGENTS.md](https://github.com/duong6003/hostbook/blob/main/AGENTS.md).
Screens follow the screen system in [docs/design/screens.md](https://github.com/duong6003/hostbook/blob/main/docs/design/screens.md),
and user-facing text lives in `lang/vi.sh` and `lang/en.sh`.

TUI tests skip if tmux is missing; E2E tests skip if Docker is unavailable. A skipped
check is not evidence of a pass. CI runs on macOS, Ubuntu, and Windows. Ubuntu runs
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
isolate development data from your real SSH setup. Follow the test scripts for a
complete fixture; do not point experiments at a production vault. Internal commands
prefixed with `_` are implementation/testing helpers, not a stable public API.

## Update documentation

Edit the pages under `docs/wiki/` in the app repository. After review, publish the
same Markdown files to the separate `hostbook.wiki.git` repository. GitHub requires
an initial wiki page before that Git repository can be cloned.

Keep README focused on the product, installation, and links. Detailed usage belongs
here. Screenshots in `docs/assets/` should come from the real app with synthetic
hosts and no private data. Preserve exact command names and explain any remaining
Vietnamese menu labels rather than inventing an English UI.
