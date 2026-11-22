# Writer exclusion verification

This checkpoint verifies only the writer-exclusion requirements in the
[upgrade boundary](store-migration.md#first-supported-upgrade). No fixture
migrates a store or installs a write policy on an existing remote.

| Boundary | Observed result |
| --- | --- |
| Local resources | In a Linux container, a process with the legacy UID was denied when it tried to edit, create files in, or rename the checkout, Git common directory and local state. The upgrade UID could write each resource. The original fixture data stayed unchanged. |
| Local counterexamples | A process using the upgrade UID could write through mode `0700`. A distinct legacy UID could rename a `0700` checkout when its parent was writable. A target directory's mode alone is insufficient evidence. |
| Linux group and ACL routes | The fixture blocked the local handoff check and named the file when a supplementary group could open the destination checkout sentinel for writing or a named POSIX ACL could open the state sentinel for writing. Removing each grant cleared the check. Both sentinels and the original file kept their contents. |
| Remote branch | The legacy Git identity could publish to the original fixture ref. An authenticated attempt by that identity to update the exact upgrade ref was rejected by the server hook. Both refs and the accepted checkout stayed unchanged. The upgrade identity could then update the upgrade ref without changing the original. |
| No evidence | The [migration decision fixtures](fixtures/migration-decisions.json) reject unknown local or remote exclusion, a stale binding and unsettled publication. These are design checks. |

## Run evidence

The [POSIX fixture](fixtures/verify-writer-exclusion.py) uses two numeric kernel
UIDs and disposable paths. It does not create system accounts. Run on an
isolated root runner. The Linux run used Docker 24.0.6, LinuxKit kernel
`6.4.16-linuxkit` on aarch64, and a disposable ext4 volume. Build the test
image with `setfacl` before running the fixture with networking disabled:

```bash
docker build -t hostbook-writer-fixture:local - <<'DOCKERFILE'
FROM python@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
RUN apk add --no-cache acl
DOCKERFILE

docker run --rm --pull never --network none --read-only \
  --mount type=volume,destination=/fixture,volume-nocopy --env TMPDIR=/fixture \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges \
  --mount "type=bind,source=$PWD/docs/design/fixtures/verify-writer-exclusion.py,target=/verify.py,readonly" \
  hostbook-writer-fixture:local python -B /verify.py
```

All five POSIX tests passed on Linux with Python 3.12.16 and acl 2.3.2.
The two file probes open only regular fixture sentinels for writing without
truncating or changing their contents. They use kernel credentials, including
the supplied supplementary group, and treat unexpected probe errors as failures.
The directory-replacement test covers a writable ancestor.

The Git Smart HTTP fixture passed all four tests with Git 2.42.1 on macOS arm64. It used an ephemeral loopback server,
synthetic identities and a bare repository. Its source is retained in the
local experimental branch `codex/shared-write-fixture` at `30c4381`. Run
`PYTHONDONTWRITEBYTECODE=1 python3 tests/shared-write-authority.py` there.

## Remaining verification

On native macOS arm64 (Darwin 27.2.0), an unprivileged child sharing the
target owner's UID could overwrite a `0600` file in a `0700` directory.
The original synthetic file stayed unchanged. The migration decision validator
also passed, including the `stop-writer-access` decision for unknown legacy
access. That assertion covers the design gate only.

The native two-UID POSIX fixture returned skip status 77 because the local
process could not use noninteractive sudo. GitHub Actions denied both the
[first attempt](https://github.com/duong6003/hostbook/actions/runs/45000332023/job/137140246792)
and [rerun](https://github.com/duong6003/hostbook/actions/runs/45000332023/job/137280677976)
before allocating a macOS runner, citing billing or a spending limit.
Distinct-UID denial on macOS remains unverified.

The Linux run covers ordinary mode, ancestry, supplementary group access and
one direct named-user POSIX ACL on a file. ACL inheritance, delegated access,
evidence collection on a real target, crash behavior and runtime restoration
remain open. Before cutover, each production remote requires authoritative
evidence for its own ref and credential binding.
