#!/usr/bin/env python3
"""Verify POSIX writer separation with disposable files and kernel identities."""
import errno
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest


LEGACY_UID = 61001
UPGRADE_UID = 61002
DENIED = {errno.EACCES, errno.EPERM}


def as_identity(uid, operation):
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            assert os.geteuid() == uid and os.getegid() == uid and set(os.getgroups()) <= {uid}
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

    def test_writable_ancestor_allows_directory_replacement_despite_mode_0700(self):
        parent = make_directory(self.root / "writable-ancestor", UPGRADE_UID, 0o777)
        directory, file = self.target(parent, "checkout")
        self.assertEqual(as_identity(LEGACY_UID, lambda: file.write_bytes(b"legacy\n")), 13)
        moved = parent / "moved"
        self.assertEqual(as_identity(LEGACY_UID, lambda: directory.rename(moved)), 0)
        self.assertEqual((moved / file.name).read_bytes(), b"accepted\n")

    def test_legacy_with_same_uid_can_write_through_mode_0700(self):
        parent = make_directory(self.root / "same-owner", UPGRADE_UID, 0o755)
        _, file = self.target(parent, "checkout")
        self.assertEqual(as_identity(UPGRADE_UID, lambda: file.write_bytes(b"legacy same uid\n")), 0)
        self.assertEqual(file.read_bytes(), b"legacy same uid\n")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("SKIP: this fixture needs an isolated root runner to drop to two kernel identities",
              file=sys.stderr)
        sys.exit(77)
    print(f"Kernel: {platform.system()} {platform.release()} ({platform.machine()})", flush=True)
    unittest.main(verbosity=2)
