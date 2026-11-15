# CI progress

Base: ef847ee. Worktree: feature/ci-efficiency.

Ruling: Cache is opt-in via a runner-only directory, keyed by expected archive SHA256 and filename. This keeps normal installs unchanged and avoids caching user data; runner cache integrity is never trusted.
Ruling: PR and main push events are separate concurrency groups. This retains main verification and avoids cancelling a PR when main changes.

Efficiency checks: `git diff --check`, `ruby -e 'require "yaml"; YAML.load_file(ARGV[0])' .github/workflows/test.yml`, and `bash -n install.sh tests/*.sh` passed locally. PowerShell is unavailable on this macOS host; the new Windows regression cannot be run red or green locally and requires Windows CI. Install timing deliberately includes replacing one seeded corrupt age archive; update timing is warm and does not re-extract Portable Git. Actions' restore/save step durations expose cache overhead. Comparable truly cold/warm install and restore/save numbers remain pending hosted runs; no savings claimed.
