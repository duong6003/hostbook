# CI progress

Base: ef847ee. Worktree: feature/ci-efficiency.

Ruling: Cache is opt-in via a runner-only directory, keyed by expected archive SHA256 and filename. This keeps normal installs unchanged and avoids caching user data; runner cache integrity is never trusted.
Ruling: PR and main push events are separate concurrency groups. This retains main verification and avoids cancelling a PR when main changes.

Efficiency checks: `git diff --check`, `ruby -e 'require "yaml"; YAML.load_file(ARGV[0])' .github/workflows/test.yml`, and `bash -n install.sh tests/*.sh` passed locally. PowerShell is unavailable on this macOS host; the new Windows regression cannot be run red or green locally and requires Windows CI. Install timing deliberately includes replacing one seeded corrupt age archive; update timing is warm and does not re-extract Portable Git. Actions' restore/save step durations expose cache overhead. Comparable truly cold/warm install and restore/save numbers remain pending hosted runs; no savings claimed.

Ruling: Add Unix install/update coverage with a disposable HOME and local Git repository, including an installed callback from a different working directory. Unix uninstall is a documented manual removal, not an installer command; do not invent one. Ruling: macOS real SSH remains a documented gap: the existing fixture requires Docker, and enabling the system SSH daemon or changing account credentials is not a safe substitute. A standalone unprivileged fixture remains future work.

Coverage local checks: `bash tests/install.sh` passed on macOS with installed fzf/gum/age, including an actual local Git update and host-data preservation. `bash -n tests/install.sh`, `git diff --check` and workflow YAML parsing passed. Unix dependency download behavior is not exercised when suitable tools are already installed. The Windows callback regression and archive tests await Windows CI.

macOS SSH fixture blocker: the existing test provisions a disposable Linux account and sshd in Docker. A native macOS equivalent needs a disposable account/password and sshd privilege-separation setup; the runner's actual account and SSH settings must not be repurposed. No verified unprivileged password-auth fixture exists here. The matrix marks key and password SSH as uncovered on macOS until such a fixture is established.

Review follow-up: `tests/install.sh` now overrides inherited `HOSTBOOK_HOME` and creates a named fixture branch before installation. Before the fix, a disposable inherited directory's sentinel was removed and the test failed; after the fix, the sentinel survives and the test passes. A detached synthetic checkout was also tested; the pre-fix run happened to pass locally, so the suspected detached-pull failure was not reproduced here. The post-fix detached checkout passed with the named branch. Commands used temporary cloned repositories and an external sentinel, never user data.
