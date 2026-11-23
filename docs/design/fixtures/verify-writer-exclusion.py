#!/usr/bin/env python3
"""Verify POSIX writer separation with disposable files and kernel identities."""
import errno
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


LEGACY_UID = 61001
UPGRADE_UID = 61002
DENIED = {errno.EACCES, errno.EPERM}


def as_identity(uid, operation, groups=()):
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups(list(groups))
            os.setgid(uid)
            os.setuid(uid)
            assert os.geteuid() == uid and os.getegid() == uid
            assert set(os.getgroups()) - {uid} == set(groups) - {uid}
        except (OSError, AssertionError):
            os._exit(99)
        try:
            operation()
        except OSError as exc:
            os._exit(13 if exc.errno in DENIED else 98)
        except BaseException:
            os._exit(98)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status):
        raise RuntimeError("Fixture worker did not exit normally")
    return os.WEXITSTATUS(status)


def make_directory(path, uid, mode):
    path.mkdir()
    os.chown(path, uid, uid)
    path.chmod(mode)
    return path


class WriterExclusionTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="hostbook-writer-exclusion-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.root.chmod(0o755)
        self.original = make_directory(self.root / "legacy", LEGACY_UID, 0o700)
        self.original_data = self.original / "original.conf"
        self.original_data.write_bytes(b"Host original\n")
        os.chown(self.original_data, LEGACY_UID, LEGACY_UID)
        self.original_data.chmod(0o600)

    def require_legacy_write_denied(self, path, groups=()):
        def open_for_write():
            os.close(os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK))

        self.require_legacy_denial(path, open_for_write, "write", groups=groups)

    def require_legacy_denial(self, path, operation, action, *, groups=()):
        label = path.relative_to(self.root)
        result = as_identity(LEGACY_UID, operation, groups)
        if result == 0:
            raise PermissionError(f"{label}: legacy writer can {action}")
        if result != 13:
            raise RuntimeError(f"{label}: legacy permission check failed ({result})")

    def target(self, parent, name):
        directory = make_directory(parent / name, UPGRADE_UID, 0o700)
        file = directory / "sentinel"
        file.write_bytes(b"accepted\n")
        os.chown(file, UPGRADE_UID, UPGRADE_UID)
        file.chmod(0o600)
        return directory, file

    def test_legacy_is_denied_on_checkout_common_dir_and_state(self):
        parent = make_directory(self.root / "sealed", UPGRADE_UID, 0o755)
        original = self.original_data.read_bytes()
        self.assertEqual(as_identity(LEGACY_UID, self.original_data.read_bytes), 0)
        for name in ("checkout", "git-common", "state"):
            with self.subTest(resource=name):
                directory, file = self.target(parent, name)
                for operation in (
                        lambda: file.write_bytes(b"legacy edit\n"),
                        lambda: (directory / "created").touch(),
                        lambda: directory.rename(parent / "legacy-replacement")):
                    self.assertEqual(as_identity(LEGACY_UID, operation), 13)
                self.assertEqual(file.read_bytes(), b"accepted\n")
                self.assertFalse((directory / "created").exists())
                self.assertEqual(as_identity(UPGRADE_UID, lambda: file.write_bytes(b"upgrade\n")), 0)
                self.assertEqual(file.read_bytes(), b"upgrade\n")
        self.assertEqual(self.original_data.read_bytes(), original)

    def test_writable_ancestor_blocks_handoff_until_sealed(self):
        parent = make_directory(self.root / "writable-ancestor", UPGRADE_UID, 0o777)
        directory, file = self.target(parent, "checkout")
        self.assertEqual(as_identity(LEGACY_UID, lambda: file.write_bytes(b"legacy\n")), 13)
        original = self.original_data.read_bytes()
        moved = parent / "moved"

        with self.assertRaisesRegex(PermissionError, r"^writable-ancestor: legacy writer can replace checkout$"):
            self.require_legacy_denial(parent, lambda: directory.rename(moved), "replace checkout")
        self.assertEqual((moved / file.name).read_bytes(), b"accepted\n")

        moved.rename(directory)
        parent.chmod(0o755)
        self.require_legacy_denial(parent, lambda: directory.rename(moved), "replace checkout")
        self.assertFalse(moved.exists())
        self.assertEqual(file.read_bytes(), b"accepted\n")
        self.assertEqual(self.original_data.read_bytes(), original)

    def test_supplementary_group_write_blocks_handoff_until_removed(self):
        parent = make_directory(self.root / "sealed", UPGRADE_UID, 0o755)
        directory, file = self.target(parent, "checkout")
        directory.chmod(0o711)
        shared_gid = 61003
        os.chown(file, UPGRADE_UID, shared_gid)
        file.chmod(0o620)
        original = self.original_data.read_bytes()

        self.require_legacy_write_denied(file)
        with self.assertRaisesRegex(PermissionError, r"^sealed/checkout/sentinel: legacy writer can write$"):
            self.require_legacy_write_denied(file, groups=(shared_gid,))
        file.chmod(0o600)
        self.require_legacy_write_denied(file, groups=(shared_gid,))
        self.assertEqual(file.read_bytes(), b"accepted\n")
        self.assertEqual(self.original_data.read_bytes(), original)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux POSIX ACLs")
    def test_named_acl_write_blocks_handoff_until_removed(self):
        parent = make_directory(self.root / "sealed", UPGRADE_UID, 0o755)
        directory, file = self.target(parent, "state")
        directory.chmod(0o711)
        original = self.original_data.read_bytes()

        subprocess.run(["setfacl", "-m", f"u:{LEGACY_UID}:-w-", str(file)], check=True)
        with self.assertRaisesRegex(PermissionError, r"^sealed/state/sentinel: legacy writer can write$"):
            self.require_legacy_write_denied(file)
        subprocess.run(["setfacl", "-x", f"u:{LEGACY_UID}", str(file)], check=True)
        self.require_legacy_write_denied(file)
        self.assertEqual(file.read_bytes(), b"accepted\n")
        self.assertEqual(self.original_data.read_bytes(), original)

    def test_legacy_with_same_uid_can_write_through_mode_0700(self):
        parent = make_directory(self.root / "same-owner", UPGRADE_UID, 0o755)
        _, file = self.target(parent, "checkout")
        self.assertEqual(as_identity(UPGRADE_UID, lambda: file.write_bytes(b"legacy same uid\n")), 0)
        self.assertEqual(file.read_bytes(), b"legacy same uid\n")


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("This fixture requires Python assertions. Remove -O and PYTHONOPTIMIZE.")
    if os.geteuid() != 0:
        print("SKIP: this fixture needs an isolated root runner to drop to two kernel identities",
              file=sys.stderr)
        sys.exit(77)
    print(f"Kernel: {platform.system()} {platform.release()} ({platform.machine()})", flush=True)
    unittest.main(verbosity=2)
