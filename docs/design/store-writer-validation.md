# Writer exclusion verification

This checkpoint verifies only the writer-exclusion requirements in the
[upgrade boundary](store-migration.md#first-supported-upgrade). No fixture
migrates a store or installs a write policy on an existing remote.

| Boundary | Observed result |
| --- | --- |
| Local resources | In a Linux container, a process with the legacy UID was denied when it tried to edit, create files in, or rename the checkout, Git common directory and local state. The upgrade UID could write each resource. The original fixture data stayed unchanged. |
| Local counterexamples | A process using the upgrade UID could write through mode `0700`. A distinct legacy UID could rename a `0700` checkout when its parent was writable. A target directory's mode alone is insufficient evidence. |
| Remote branch | The legacy Git identity could publish to the original fixture ref. An authenticated attempt by that identity to update the exact upgrade ref was rejected by the server hook. Both refs and the accepted checkout stayed unchanged. The upgrade identity could then update the upgrade ref without changing the original. |
| No evidence | The [migration decision fixtures](fixtures/migration-decisions.json) reject unknown local or remote exclusion, a stale binding and unsettled publication. These are design checks. |

## Run evidence

The [POSIX fixture](fixtures/verify-writer-exclusion.py) uses two numeric kernel
UIDs and disposable paths. It does not create system accounts. Run on an
isolated root runner. The Linux run used Docker 24.0.6, LinuxKit kernel
`6.4.16-linuxkit` on aarch64, and this image:

```bash
docker run --rm --pull never --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges \
  --mount "type=bind,source=$PWD/docs/design/fixtures/verify-writer-exclusion.py,target=/verify.py,readonly" \
  python@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 \
  python -B /verify.py
```

All three POSIX tests passed. The separate Git Smart HTTP fixture passed all
four tests with Git 2.42.1 on macOS arm64. It used an ephemeral loopback server,
synthetic identities and a bare repository. Its source is retained in the
local experimental branch `codex/shared-write-fixture` at `30c4381`. Run
`PYTHONDONTWRITEBYTECODE=1 python3 tests/shared-write-authority.py` there.

## Remaining verification

The native macOS process lacked the privilege to switch UIDs. The POSIX
fixture returned skip status 77 there. The Linux run verifies ordinary mode
and ancestry checks on its fixture filesystem. Platform ACL inheritance,
delegated access, evidence collection on a real target, crash behavior and
restoration remain open. The Git run verifies one server-side policy. Each
production remote still requires authoritative evidence for its own ref and
credential binding before cutover can be admitted.
