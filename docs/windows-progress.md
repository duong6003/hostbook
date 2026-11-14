# Windows support progress

Branch: `feat/windows-support`; isolated worktree: `hostbook-windows`; baseline: `5606bb3`.
User approved Windows as a product platform, using a managed/detected Git for Windows
runtime underneath the existing Bash app. No main checkout or real user data edits.

## Plan and scope

1. Probe official runtime/dependency packaging and write failing platform tests.
2. Add per-user PowerShell installer, quoted launcher, update/uninstall; verified assets.
3. Adapt shared Bash boundaries: fzf shell/callback quoting, native paths, UTF-8 clipboard,
   ACL-protected temporary storage, no persistent Windows identity cache, SSH/agent behavior.
4. Add real Windows runner tests alongside unchanged macOS/Linux checks.
5. Document supported capabilities, limits, install/update/remove; run checks and Windows CI.

Touches: `hostbook`, installer/launcher assets, tests and workflow, README and canonical wiki.
Risks: native Go tools vs MSYS paths/console, inherited NTFS ACLs (chmod is insufficient),
SSH password askpass without persistent identity cache, environment refresh and argument quoting.
Multi-host tmux on Windows is unsupported initially; single-host SSH must work.

## Evidence

- Initial tree clean on `feat/windows-support`; no AGENTS.md in project or parent roots.
- Inspected setup/update, every tmpd/kc/askpass caller, fzf callback sites, test fixtures.
- No Windows/PowerShell runtime on development Mac; real Windows evidence must come from CI.

## State

Working: official-source research, security seam audit, platform tests before implementation.

## Implemented, pending Windows execution

- Verified actual official PortableGit2.56.1 x64 archive includes Bash/awk/SSH/agent/cygpath;
  no tmux. Official fzf0.75/gum2.0.2/age1.3.2 ZIP contents and SHA256 verified.
- Added managed per-user runtime installer (always private runtime, no Git Bash prerequisite),
  CMD/PowerShell launcher, same-installer update/remove, user PATH only.
- Shared Bash callbacks now quote apostrophes/spaces and explicitly select Bash for native
  fzf; gum uses Bash for app subprocesses. Native paths/clipboard converted at boundaries.
- Private NTFS DACLs plus per-invocation DPAPI ciphertext keep askpass functional without
  a plaintext identity cache. Plaintext temp-directory failures stop callers.
- Real Windows CI includes portable install, launcher args, native clipboard/ACL/DPAPI,
  allocated-console fzf preview/action callbacks, and real SSH key/password checks.
- Mac evidence so far: platform5/5; core142/142 (before latest path quoting); UI109/109;
  real tmux TUI15/15. Latest core rerun in progress. No Windows pass claim yet.

Official references:
https://gitforwindows.org/zip-archives-extracting-the-released-archives.html
https://github.com/git-for-windows/git/releases/tag/v2.56.1.windows.1
https://github.com/junegunn/fzf/releases/tag/v0.75.0
https://github.com/charmbracelet/gum/releases/tag/v2.0.2
https://github.com/FiloSottile/age/releases/tag/v1.3.2
https://www.msys2.org/docs/filesystem-paths/

## CI iteration 1

Run 43602375742 (05ed793): Ubuntu and macOS complete green, including Ubuntu real Docker SSH.
Windows failed at runtime extraction, before app install. The current official Git SFX
source recognizes `-y -o`; its older official wiki still describes obsolete
`-gm2 -InstallPath` flags, which returned zero while extracting elsewhere. Updated
against https://github.com/git-for-windows/7-Zip (current SFXSetup.cpp), keeping the
same Unicode/apostrophe destination probe.

Independent review fixes: installer refuses unrelated nonempty roots and removes only
owned files; checksum files are decoded explicitly; slash-leading age passwords are
excluded from MSYS env conversion. Public launcher is now a tiny native .NET executable
compiled with the built-in Windows C# compiler, because a CMD hop interprets fzf search
operators such as `|`. Runtime remains internal Bash; no app rewrite or new framework.
Versioned runtime directories allow runtime updates without replacing a running Bash.

## Windows evidence and remaining probes

Run 43602986617 (4d7ad55) proved managed extraction/install under a spaced Unicode/
apostrophe path, native launcher lookup/argv (including literal `|`/`&`), UTF-8 clipboard,
owner-only plaintext temp file ACL inheritance, DPAPI encryption/decryption, and native
age interoperability for slash-leading passphrases. Windows fzf harness initially used
HWND existence as readiness; headless consoles can be attached without a HWND.
Run 43603552810 (5238024) additionally passed CRLF hosts/settings/groups. Its added
first-machine sourced-function probe incorrectly imposed `set -e` on an app written
without errexit; corrected the harness to match app semantics, retaining commit assertion.
Real SSH, native interactive callbacks, update/uninstall still pending full runner pass.

## Combined Windows run

Run 43604623891 (9964fa7): managed install, argv/search operators, native security/
clipboard, first machine without global Git identity, update/PATH idempotence,
**real native interactive fzf start and preview callbacks**, and safe uninstall all
passed. The only remaining failure was the SSH test's Add-LocalGroupMember pipeline
parameter binding before server startup; changed to an explicit group argument.
Native Git/age paths are now converted explicitly: MSYS heuristics failed for a literal
apostrophe in the installation path. Platform checks now also exercise quoted legacy
migration and index refresh inside a spaced directory (11 total).
